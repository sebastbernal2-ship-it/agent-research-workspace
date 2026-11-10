import asyncio
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ag2 import Agent
from ag2.knowledge import DiskKnowledgeStore
from ag2.network import (
    DISCUSSION_TYPE,
    EV_CHANNEL_INVITE,
    EV_CHANNEL_INVITE_ACK,
    EV_TEXT,
    Envelope,
    Hub,
    ORDERING_ROUND_ROBIN,
)
from ag2.testing import TestConfig

from harness.ag2_mirror import CanonicalAG2Mirror
from harness.event_store import EventStoreError, SQLiteEventStore
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


class AG2CanonicalMirrorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_wal_replay_repairs_listener_append_failure_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            canonical = SQLiteEventStore(Path(temp_dir) / "project.sqlite3")
            seed_synthetic_project(canonical, project_id="project.repair", work_id="work.repair")
            hub = await Hub.open(DiskKnowledgeStore(str(Path(temp_dir) / "ag2-state")), ttl_sweep_interval=0)
            try:
                proposer = await hub.register(Agent(name="repair-proposer", config=TestConfig()), attach_plugin=False)
                critic = await hub.register(Agent(name="repair-critic", config=TestConfig()), attach_plugin=False)
                actors = {
                    proposer.agent_id: {"actorType": "agent", "actorId": "agent.repair-proposer", "role": "orchestrator"},
                    critic.agent_id: {"actorType": "agent", "actorId": "agent.repair-critic", "role": "reviewer"},
                }
                run_ledger = RunLedger(canonical, clock=lambda: "2026-04-16T12:00:00Z")
                run_ledger.start("project.repair", "work.repair", run_id="run.repair.proposer", agent=actors[proposer.agent_id], adapter={"name": "ag2", "version": "1"}, context_hash="a" * 64, correlation_id="trace.repair", occurred_at="2026-04-16T12:00:00Z")
                run_ledger.start("project.repair", "work.repair", run_id="run.repair.critic", agent=actors[critic.agent_id], adapter={"name": "ag2", "version": "1"}, context_hash="b" * 64, parent_run_id="run.repair.proposer", correlation_id="trace.repair", occurred_at="2026-04-16T12:00:00Z")
                run_ids = {proposer.agent_id: "run.repair.proposer", critic.agent_id: "run.repair.critic"}
                mirror = CanonicalAG2Mirror(
                    canonical, project_id="project.repair", work_id="work.repair",
                    actors_by_ag2_id=actors,
                    message_types_by_ag2_id={proposer.agent_id: "proposal", critic.agent_id: "challenge"},
                    correlation_id="trace.repair", run_ids_by_ag2_id=run_ids,
                )
                hub.register_listener(mirror)

                async def acknowledge(client, envelope):
                    if envelope.event_type == EV_CHANNEL_INVITE:
                        await client.send_envelope(Envelope(
                            channel_id=envelope.channel_id,
                            sender_id=client.agent_id,
                            audience=None,
                            event_type=EV_CHANNEL_INVITE_ACK,
                            event_data={"channel_id": envelope.channel_id},
                            causation_id=envelope.envelope_id,
                        ))

                critic.on_envelope(lambda envelope: acknowledge(critic, envelope))
                channel = await proposer.open(
                    type=DISCUSSION_TYPE, target=[critic.agent_id],
                    knobs={"ordering": ORDERING_ROUND_ROBIN},
                    intent="recover canonical mirror listener failure",
                )
                original_append = canonical.append
                failed = False

                def fail_first_message(event, **kwargs):
                    nonlocal failed
                    if event.get("eventType") == "discussion.message_added" and not failed:
                        failed = True
                        raise EventStoreError("injected canonical append outage")
                    return original_append(event, **kwargs)

                with patch.object(canonical, "append", side_effect=fail_first_message):
                    await proposer.send_envelope(Envelope(
                        channel_id=channel.channel_id, sender_id=proposer.agent_id, audience=None,
                        event_type=EV_TEXT, event_data={"text": "Proposal retained in AG2 WAL."},
                    ))
                    await critic.send_envelope(Envelope(
                        channel_id=channel.channel_id, sender_id=critic.agent_id, audience=None,
                        event_type=EV_TEXT, event_data={"text": "Challenge mirrored normally."},
                    ))
                self.assertTrue(failed)
                self.assertEqual(len(mirror.errors), 1)
                with self.assertRaisesRegex(RuntimeError, "canonical mirror degraded"):
                    mirror.raise_if_degraded()

                wal = [envelope for envelope in await hub.read_wal(channel.channel_id) if envelope.event_type == EV_TEXT]
                self.assertEqual(len(wal), 2)
                recovery = CanonicalAG2Mirror(
                    canonical, project_id="project.repair", work_id="work.repair",
                    actors_by_ag2_id=actors,
                    message_types_by_ag2_id={proposer.agent_id: "proposal", critic.agent_id: "challenge"},
                    correlation_id="trace.repair", run_ids_by_ag2_id=run_ids,
                )
                metadata = await hub.get_channel(channel.channel_id)
                for envelope in wal:
                    await recovery.on_envelope_posted(envelope, metadata)
                recovery.raise_if_degraded()
                messages = Projector(canonical).replay("project.repair")["messages"]
                self.assertEqual(len(messages), 2)
                self.assertEqual({message["content"] for message in messages}, {
                    "Proposal retained in AG2 WAL.", "Challenge mirrored normally.",
                })
                self.assertTrue(canonical.verify("project.repair"))
            finally:
                await hub.close()

    async def test_round_robin_discussion_mirrors_and_replays_to_canonical_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            canonical_path = Path(temp_dir) / "project.sqlite3"
            canonical = SQLiteEventStore(canonical_path)
            seed_synthetic_project(canonical, project_id="project.demo", work_id="work.demo")
            ag2_path = Path(temp_dir) / "ag2-state"
            hub = await Hub.open(DiskKnowledgeStore(str(ag2_path)), ttl_sweep_interval=0)
            try:
                proposer = await hub.register(Agent(name="proposer", config=TestConfig()), attach_plugin=False)
                critic = await hub.register(Agent(name="critic", config=TestConfig()), attach_plugin=False)
                reviser = await hub.register(Agent(name="reviser", config=TestConfig()), attach_plugin=False)

                actors = {
                    proposer.agent_id: {"actorType": "agent", "actorId": "agent.proposer", "role": "orchestrator"},
                    critic.agent_id: {"actorType": "agent", "actorId": "agent.critic", "role": "reviewer"},
                    reviser.agent_id: {"actorType": "agent", "actorId": "agent.reviser", "role": "specialist"},
                }
                run_ledger = RunLedger(canonical, clock=lambda: "2026-04-16T12:00:00Z")
                run_ledger.start("project.demo", "work.demo", run_id="run.demo.proposer", agent=actors[proposer.agent_id], adapter={"name": "ag2", "version": "1"}, context_hash="a" * 64, correlation_id="trace.demo", occurred_at="2026-04-16T12:00:00Z")
                run_ledger.start("project.demo", "work.demo", run_id="run.demo.critic", agent=actors[critic.agent_id], adapter={"name": "ag2", "version": "1"}, context_hash="b" * 64, parent_run_id="run.demo.proposer", correlation_id="trace.demo", occurred_at="2026-04-16T12:00:00Z")
                run_ledger.start("project.demo", "work.demo", run_id="run.demo.reviser", agent=actors[reviser.agent_id], adapter={"name": "ag2", "version": "1"}, context_hash="c" * 64, parent_run_id="run.demo.proposer", correlation_id="trace.demo", occurred_at="2026-04-16T12:00:00Z")
                run_ids = {proposer.agent_id: "run.demo.proposer", critic.agent_id: "run.demo.critic", reviser.agent_id: "run.demo.reviser"}
                mirror = CanonicalAG2Mirror(
                    canonical,
                    project_id="project.demo",
                    work_id="work.demo",
                    actors_by_ag2_id=actors,
                    message_types_by_ag2_id={
                        proposer.agent_id: "proposal",
                        critic.agent_id: "challenge",
                        reviser.agent_id: "revision",
                    },
                    correlation_id="trace.demo", run_ids_by_ag2_id=run_ids,
                )
                hub.register_listener(mirror)

                def add_invite_acker(client):
                    async def acknowledge(envelope):
                        if envelope.event_type != EV_CHANNEL_INVITE:
                            return
                        await client.send_envelope(
                            Envelope(
                                channel_id=envelope.channel_id,
                                sender_id=client.agent_id,
                                audience=None,
                                event_type=EV_CHANNEL_INVITE_ACK,
                                event_data={"channel_id": envelope.channel_id},
                                causation_id=envelope.envelope_id,
                            )
                        )
                    client.on_envelope(acknowledge)

                add_invite_acker(critic)
                add_invite_acker(reviser)
                channel = await proposer.open(
                    type=DISCUSSION_TYPE,
                    target=[critic.agent_id, reviser.agent_id],
                    knobs={"ordering": ORDERING_ROUND_ROBIN},
                    intent="synthetic evidence-based critique/revision integration test",
                )

                await proposer.send_envelope(Envelope(
                    channel_id=channel.channel_id,
                    sender_id=proposer.agent_id,
                    audience=None,
                    event_type=EV_TEXT,
                    event_data={
                        "text": "Initial proposal: seasons happen because Earth is closer to the Sun in summer.",
                        "canonical_message_type": "proposal",
                    },
                ))
                for client, text, message_type in [
                    (critic, "Challenge: opposite seasons across hemispheres contradict the distance-only hypothesis.", "challenge"),
                    (reviser, "Revision: axial tilt is the stronger explanation; cite and check hemisphere observations.", "revision"),
                    (proposer, "Accepted revision: withdraw the distance claim and investigate axial tilt with sources.", "decision"),
                ]:
                    await client.send_envelope(
                        Envelope(
                            channel_id=channel.channel_id,
                            sender_id=client.agent_id,
                            audience=None,
                            event_type=EV_TEXT,
                            event_data={"text": text, "canonical_message_type": message_type},
                        )
                    )

                mirror.raise_if_degraded()
                wal = await hub.read_wal(channel.channel_id)
                wal_text = [e for e in wal if e.event_type == EV_TEXT]
                canonical_events = canonical.events("project.demo")
                message_events = [event for event in canonical_events if event["eventType"] == "discussion.message_added"]
                self.assertEqual(len(wal_text), 4)
                self.assertEqual(len(message_events), 4)
                self.assertEqual([e["sequence"] for e in message_events], [7, 8, 9, 10])
                self.assertEqual(
                    [e["payload"]["messageType"] for e in message_events],
                    ["proposal", "challenge", "revision", "decision"],
                )
                run_id_by_actor = {
                    actors[ag2_id]["actorId"]: run_id for ag2_id, run_id in run_ids.items()
                }
                self.assertEqual(
                    [e["payload"]["runId"] for e in message_events],
                    [run_id_by_actor[e["actor"]["actorId"]] for e in message_events],
                )
                self.assertEqual(
                    [canonical.get_blob(e["payload"]["contentHash"]).decode("utf-8") for e in message_events],
                    [e.event_data["text"] for e in wal_text],
                )
                self.assertTrue(canonical.verify("project.demo"))
                self.assertEqual(len(Projector(canonical).replay("project.demo")["messages"]), 4)

                # Restart AG2 from its own durable store, then reconcile the
                # recovered WAL as if the process died between AG2 append and
                # canonical mirroring. A new listener instance proves the
                # deduplication key is durable rather than process-local.
                channel_id = channel.channel_id
                await hub.close()
                hub = None
                recovered_hub = await Hub.open(DiskKnowledgeStore(str(ag2_path)), ttl_sweep_interval=0)
                recovered_wal = await recovered_hub.read_wal(channel_id)
                recovered_text = [e for e in recovered_wal if e.event_type == EV_TEXT]
                self.assertEqual(len(recovered_text), 4)
                recovery_mirror = CanonicalAG2Mirror(
                    canonical,
                    project_id="project.demo",
                    work_id="work.demo",
                    actors_by_ag2_id=actors,
                    message_types_by_ag2_id={
                        proposer.agent_id: "proposal",
                        critic.agent_id: "challenge",
                        reviser.agent_id: "revision",
                    },
                    correlation_id="trace.demo", run_ids_by_ag2_id=run_ids,
                )
                recovered_hub.register_listener(recovery_mirror)
                metadata = await recovered_hub.get_channel(channel_id)
                for envelope in recovered_text:
                    await recovery_mirror.on_envelope_posted(envelope, metadata)
                recovery_mirror.raise_if_degraded()
                self.assertEqual(len(canonical.events("project.demo")), 10)
                self.assertTrue(canonical.verify("project.demo"))
                self.assertEqual(len(Projector(canonical).replay("project.demo")["messages"]), 4)

                # Canonical history remains readable independently of the
                # candidate Hub's live in-memory state/session objects.
                reopened = SQLiteEventStore(canonical_path)
                self.assertEqual(len(reopened.events("project.demo")), 10)
                self.assertEqual(len(reopened.export_jsonl("project.demo").splitlines()), 10)
                await recovered_hub.close()
                hub = None
                shutil.rmtree(ag2_path)
                canonical_after_ag2_removal = SQLiteEventStore(canonical_path)
                self.assertEqual(len(Projector(canonical_after_ag2_removal).replay("project.demo")["messages"]), 4)
            finally:
                if hub is not None:
                    await hub.close()


if __name__ == "__main__":
    unittest.main()
