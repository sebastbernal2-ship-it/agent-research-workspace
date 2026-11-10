import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timezone
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ag2 import Agent, Context
from ag2.config import LLMClient, ModelConfig, ModelProvider
from ag2.events import BaseEvent, ModelMessage, ModelResponse
from ag2.knowledge import DiskKnowledgeStore
from ag2.network import (
    DISCUSSION_TYPE,
    EV_TEXT,
    Hub,
    ORDERING_ROUND_ROBIN,
)

from harness.ag2_agenticx import AgenticXTestModelConfig
from harness.ag2_mirror import CanonicalAG2Mirror
from harness.agenticx_bridge import AgenticXSubprocessWorker
from harness.event_store import SQLiteEventStore
from harness.meldwork_bridge import MeldworkReceiptBridge
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


class ScriptedConfig(ModelConfig):
    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.cursor = 0

    @property
    def provider(self):
        return ModelProvider.OPENAI  # test metadata only; no OpenAI client is constructed

    @property
    def model(self) -> str:
        return "harness-scripted"

    def copy(self):
        return self

    def create(self):
        return ScriptedClient(self)

    def create_files_client(self):
        raise NotImplementedError


class ScriptedClient(LLMClient):
    def __init__(self, config: ScriptedConfig):
        self.config = config

    async def __call__(self, messages: Sequence[BaseEvent], context: Context, **kwargs: Any) -> ModelResponse:
        if self.config.cursor >= len(self.config.replies):
            text = ""
        else:
            text = self.config.replies[self.config.cursor]
            self.config.cursor += 1
        message = ModelMessage(text)
        await context.send(message)
        return ModelResponse(message)


async def wait_for_text_count(hub: Hub, channel_id: str, expected: int, timeout: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        wal = await hub.read_wal(channel_id)
        messages = [event for event in wal if event.event_type == EV_TEXT]
        if len(messages) >= expected:
            return messages
        await asyncio.sleep(0.02)
    raise TimeoutError(f"AG2 channel did not reach {expected} text messages")


class AG2AgenticXCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_agenticx_worker_participates_in_ag2_debate_and_canonical_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            event_store = SQLiteEventStore(Path(temp_dir) / "canonical.sqlite3")
            seed_synthetic_project(event_store, project_id="project.composed", work_id="work.composed")
            ag2_path = Path(temp_dir) / "ag2-state"
            hub = await Hub.open(DiskKnowledgeStore(str(ag2_path)), ttl_sweep_interval=0)
            try:
                proposer = await hub.register(
                    Agent(name="proposer", config=ScriptedConfig()),
                )
                critic_config = AgenticXTestModelConfig(
                    AgenticXSubprocessWorker(),
                    "Challenge: opposite hemisphere seasons contradict the distance-only hypothesis.",
                    task_id="task.agenticx.critic",
                )
                critic = await hub.register(Agent(name="agenticx-critic", config=critic_config))
                reviser = await hub.register(
                    Agent(name="reviser", config=ScriptedConfig(
                        "Revision: withdraw the distance claim; test axial tilt against hemisphere observations."
                    )),
                )
                actors = {
                    proposer.agent_id: {"actorType": "agent", "actorId": "agent.proposer", "role": "orchestrator"},
                    critic.agent_id: {"actorType": "agent", "actorId": "agent.critic", "role": "reviewer"},
                    reviser.agent_id: {"actorType": "agent", "actorId": "agent.reviser", "role": "specialist"},
                }
                run_ledger = RunLedger(event_store, clock=lambda: "2026-04-16T12:00:00Z")
                run_ledger.start("project.composed", "work.composed", run_id="run.composed.proposer", agent=actors[proposer.agent_id], adapter={"name": "ag2", "version": "1"}, context_hash="a" * 64, correlation_id="trace.composed", occurred_at="2026-04-16T12:00:00Z")
                run_ledger.start("project.composed", "work.composed", run_id="run.composed.critic", agent=actors[critic.agent_id], adapter={"name": "ag2-agenticx", "version": "1"}, context_hash="b" * 64, parent_run_id="run.composed.proposer", correlation_id="trace.composed", occurred_at="2026-04-16T12:00:00Z")
                run_ledger.start("project.composed", "work.composed", run_id="run.composed.reviser", agent=actors[reviser.agent_id], adapter={"name": "ag2", "version": "1"}, context_hash="c" * 64, parent_run_id="run.composed.proposer", correlation_id="trace.composed", occurred_at="2026-04-16T12:00:00Z")
                run_ids = {proposer.agent_id: "run.composed.proposer", critic.agent_id: "run.composed.critic", reviser.agent_id: "run.composed.reviser"}
                mirror = CanonicalAG2Mirror(
                    event_store,
                    project_id="project.composed",
                    work_id="work.composed",
                    actors_by_ag2_id=actors,
                    message_types_by_ag2_id={
                        proposer.agent_id: "proposal",
                        critic.agent_id: "challenge",
                        reviser.agent_id: "revision",
                    },
                    correlation_id="trace.composed", run_ids_by_ag2_id=run_ids,
                )
                hub.register_listener(mirror)

                channel = await proposer.open(
                    type=DISCUSSION_TYPE,
                    target=[critic.agent_id, reviser.agent_id],
                    knobs={"ordering": ORDERING_ROUND_ROBIN},
                    intent="synthetic composition test: proposal, AgenticX critique, revision",
                )
                await channel.send("Proposal: Earth seasons are caused by changing distance from the Sun.")
                wal_messages = await wait_for_text_count(hub, channel.channel_id, 3)
                mirror.raise_if_degraded()

                contents = [event.event_data["text"] for event in wal_messages[:3]]
                self.assertEqual(contents[0], "Proposal: Earth seasons are caused by changing distance from the Sun.")
                self.assertIn("Challenge:", contents[1])
                self.assertIn("Revision:", contents[2])

                # Feed a deterministic V4 verification receipt through the
                # real pinned Meldwork parser/sanitizer adapter into the same
                # canonical project stream. This is a receipt-module bridge
                # test, not a claim that Electron LocalWorkspace ran.
                MeldworkReceiptBridge(event_store).persist({
                    "projectId": "project.composed",
                    "workId": "work.composed",
                    "sourceReceiptId": "meldwork.synthetic.verification.1",
                    "occurredAt": datetime.now(timezone.utc).isoformat(),
                    "phase": "verification",
                    "text": "Meldwork V4 synthetic verification: the revised explanation is internally consistent.",
                    "actor": {"actorType": "agent", "actorId": "agent.meldwork-verifier", "role": "reviewer"},
                    "visibleTo": ["agent.vp", "agent.critic", "agent.reviser", "agent.meldwork-verifier"],
                    "collaboration": {
                        "version": 1,
                        "phase": "verification",
                        "summary": "The revised explanation is internally consistent.",
                        "verdict": "support",
                    },
                })

                canonical_events = event_store.events("project.composed")
                message_events = [event for event in canonical_events if event["eventType"] == "discussion.message_added"]
                self.assertEqual(len(message_events), 4)
                self.assertEqual(
                    [event["payload"]["messageType"] for event in message_events],
                    ["proposal", "challenge", "revision", "evidence"],
                )
                self.assertEqual(
                    [event_store.get_blob(event["payload"]["contentHash"]).decode() for event in message_events[:3]],
                    contents,
                )
                self.assertTrue(event_store.verify("project.composed"))
                self.assertEqual(len(Projector(event_store).replay("project.composed")["messages"]), 4)

                # Reopen AG2 from its durable WAL and replay the projection
                # through a new adapter instance. Canonical events remain
                # authoritative and idempotent across the process boundary.
                channel_id = channel.channel_id
                await hub.close()
                hub = None
                recovered_hub = await Hub.open(DiskKnowledgeStore(str(ag2_path)), ttl_sweep_interval=0)
                recovered_wal = await recovered_hub.read_wal(channel_id)
                recovered_messages = [event for event in recovered_wal if event.event_type == EV_TEXT]
                self.assertEqual(len(recovered_messages), 3)
                recovery_mirror = CanonicalAG2Mirror(
                    event_store,
                    project_id="project.composed",
                    work_id="work.composed",
                    actors_by_ag2_id=actors,
                    message_types_by_ag2_id={
                        proposer.agent_id: "proposal",
                        critic.agent_id: "challenge",
                        reviser.agent_id: "revision",
                    },
                    correlation_id="trace.composed", run_ids_by_ag2_id=run_ids,
                )
                recovered_hub.register_listener(recovery_mirror)
                metadata = await recovered_hub.get_channel(channel_id)
                for envelope in recovered_messages:
                    await recovery_mirror.on_envelope_posted(envelope, metadata)
                recovery_mirror.raise_if_degraded()
                self.assertEqual(len(event_store.events("project.composed")), len(canonical_events))
                self.assertTrue(event_store.verify("project.composed"))
                self.assertEqual(len(Projector(event_store).replay("project.composed")["messages"]), 4)
                await recovered_hub.close()
                hub = None
                shutil.rmtree(ag2_path)
                reopened = SQLiteEventStore(Path(temp_dir) / "canonical.sqlite3")
                self.assertEqual(len(Projector(reopened).replay("project.composed")["messages"]), 4)
            finally:
                if hub is not None:
                    await hub.close()


if __name__ == "__main__":
    unittest.main()
