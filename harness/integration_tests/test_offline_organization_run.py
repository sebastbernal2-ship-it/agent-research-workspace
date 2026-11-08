"""One deterministic end-to-end project run through the reusable coordinator."""

import asyncio
import hashlib
import json
import shutil
import tempfile
import unittest
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ag2 import Agent, Context
from ag2.config import LLMClient, ModelConfig, ModelProvider
from ag2.events import BaseEvent, ModelMessage, ModelResponse
from ag2.knowledge import DiskKnowledgeStore
from ag2.network import DISCUSSION_TYPE, EV_TEXT, Hub, ORDERING_ROUND_ROBIN

from harness.ag2_agenticx import AgenticXTestModelConfig
from harness.ag2_mirror import CanonicalAG2Mirror
from harness.agenticx_bridge import AgenticXSubprocessWorker
from harness.control_plane import ProjectControlPlane
from harness.event_store import SQLiteEventStore
from harness.inbox import InboxApplication, InboxReadModel
from harness.meldwork_runtime_bridge import MeldworkAgenticXRuntimeBridge
from harness.organization_runtime import (
    ExecutionContext,
    ExecutionSignal,
    OrganizationCoordinator,
    WorkExecution,
    WorkflowStatus,
)
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


PROJECT_ID = "project.organization-run"
WORK_ID = "work.organization-run"
DISCUSSION_WORK_ID = "work.organization-debate"
VERIFICATION_WORK_ID = "work.organization-verification"
TEAM_RUN_ID = "run.organization.weather-research"
VP = {"actorType": "agent", "actorId": "agent.project-vp", "role": "vp"}
ORCHESTRATOR = {"actorType": "agent", "actorId": "agent.orchestrator", "role": "orchestrator"}
CRITIC = {"actorType": "agent", "actorId": "agent.critic", "role": "reviewer"}
SPECIALIST = {"actorType": "agent", "actorId": "agent.specialist", "role": "specialist"}
CODEX = {"actorType": "agent", "actorId": "agent.codex", "role": "specialist"}
HERMES = {"actorType": "agent", "actorId": "agent.hermes", "role": "orchestrator"}
USER = {"actorType": "human", "actorId": "user.synthetic", "role": "user"}


class ScriptedConfig(ModelConfig):
    def __init__(self, reply: str):
        self.reply = reply

    @property
    def provider(self):
        return ModelProvider.OPENAI  # Test metadata only; no provider client is created.

    @property
    def model(self) -> str:
        return "organization-scripted-mock"

    def copy(self):
        return self

    def create(self):
        return ScriptedClient(self.reply)

    def create_files_client(self):
        raise NotImplementedError


class ScriptedClient(LLMClient):
    def __init__(self, reply: str):
        self.reply = reply

    async def __call__(self, messages: Sequence[BaseEvent], context: Context, **kwargs: Any) -> ModelResponse:
        message = ModelMessage(self.reply)
        await context.send(message)
        return ModelResponse(message)


class SyntheticPrincipal:
    def current_actor(self) -> dict[str, str]:
        return dict(USER)


class SyntheticProjectAccess:
    def require_project_access(self, actor: dict[str, str], project_id: str, action: str) -> None:
        if actor != USER or project_id != PROJECT_ID or action not in {"inbox.read", "inbox.answer", "inbox.close"}:
            raise PermissionError("synthetic test access denied")


async def wait_for_text_count(hub: Hub, channel_id: str, expected: int, timeout: float = 15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        messages = [event for event in await hub.read_wal(channel_id) if event.event_type == EV_TEXT]
        if len(messages) >= expected:
            return messages
        await asyncio.sleep(0.02)
    raise TimeoutError(f"AG2 channel did not reach {expected} text messages")


class OfflineOrganizationRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_human_gate_agent_debate_meldwork_verification_and_restart_replay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "canonical.sqlite3"
            ag2_path = Path(temp_dir) / "ag2-wal"
            store = SQLiteEventStore(db_path)
            now = lambda: datetime.now(timezone.utc).isoformat()
            control = seed_synthetic_project(
                store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=now(),
            )
            for work_id, title, objective, owner, dependencies in (
                (
                    DISCUSSION_WORK_ID, "Challenge and revise the initial explanation",
                    "Coordinate an orchestrator proposal, independent critique, and specialist revision.",
                    ORCHESTRATOR, [],
                ),
                (
                    VERIFICATION_WORK_ID, "Verify the revised conclusion",
                    "Run an independent Meldwork V4 synthesis and verification over the revised discussion.",
                    HERMES, [DISCUSSION_WORK_ID],
                ),
            ):
                control.create_work({
                    "recordType": "work_item", "schemaVersion": "0.1", "projectId": PROJECT_ID,
                    "workId": work_id, "revision": 1, "parentWorkId": WORK_ID,
                    "title": title, "objective": objective, "workType": "research", "status": "ready",
                    "owner": owner, "dependsOn": dependencies, "capabilityGrantIds": [],
                    "acceptanceCriteria": ["Persist the result and its review evidence in the project."],
                    "createdBy": USER, "createdAt": now(), "updatedAt": now(),
                })
            inbox = InboxApplication(
                InboxReadModel(store), control, SyntheticPrincipal(), SyntheticProjectAccess(),
            )
            ledger = RunLedger(store, clock=now)
            coordinator = OrganizationCoordinator(store, control=control, ledger=ledger)

            # The VP requests an explicit source-policy decision before execution.
            question_id = "question.organization.sources"
            control.open_question({
                "recordType": "human_question",
                "schemaVersion": "0.1",
                "projectId": PROJECT_ID,
                "questionId": question_id,
                "questionType": "decision",
                "priority": "high",
                "blocking": True,
                "requestedBy": VP,
                "workIds": [WORK_ID, DISCUSSION_WORK_ID, VERIFICATION_WORK_ID],
                "runId": None,
                "questionText": "May the team use synthetic primary-source excerpts for this dry run?",
                "impact": "This choice governs the evidence boundary for the delegated research task.",
                "options": [
                    {"optionId": "option.synthetic-only", "label": "Use synthetic excerpts only"},
                    {"optionId": "option.no-sources", "label": "Proceed without source claims"},
                ],
                "status": "open",
                "createdAt": now(),
                "dueAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            })

            state = Projector(store).replay(PROJECT_ID)
            context = {
                "project": state["project"],
                "work": state["workItems"][WORK_ID],
                "delegatedWork": [state["workItems"][DISCUSSION_WORK_ID], state["workItems"][VERIFICATION_WORK_ID]],
                "approvedQuestion": state["questions"][question_id],
            }
            context_hash = hashlib.sha256(
                json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()

            async def run_discussion(context: ExecutionContext) -> ExecutionSignal:
                ledger.start(
                    PROJECT_ID, DISCUSSION_WORK_ID, run_id="run.organization.critic", agent=CRITIC,
                    adapter={"name": "agenticx-subprocess", "version": "0.6.5"},
                    context_hash=context_hash, correlation_id=TEAM_RUN_ID,
                    parent_run_id="run.organization.orchestrator", occurred_at=now(),
                )
                ledger.start(
                    PROJECT_ID, DISCUSSION_WORK_ID, run_id="run.organization.specialist", agent=SPECIALIST,
                    adapter={"name": "ag2-network", "version": "1.0"},
                    context_hash=context_hash, correlation_id=TEAM_RUN_ID,
                    parent_run_id="run.organization.orchestrator", occurred_at=now(),
                )
                hub = await Hub.open(DiskKnowledgeStore(str(ag2_path)), ttl_sweep_interval=0)
                try:
                    proposer = await hub.register(Agent(name="project-orchestrator", config=ScriptedConfig("")))
                    critic = await hub.register(Agent(name="independent-critic", config=AgenticXTestModelConfig(
                        AgenticXSubprocessWorker(),
                        "Challenge: opposite-hemisphere seasons contradict the distance-only hypothesis; require a source-backed comparison.",
                        task_id="task.organization.critic",
                    )))
                    reviser = await hub.register(Agent(name="research-specialist", config=ScriptedConfig(
                        "Revision: withdraw the distance-only claim and compare axial tilt against the hemisphere observations; report the limitation that this is a synthetic dry run."
                    )))
                    actors_by_id = {
                        proposer.agent_id: ORCHESTRATOR,
                        critic.agent_id: CRITIC,
                        reviser.agent_id: SPECIALIST,
                    }
                    mirror = CanonicalAG2Mirror(
                        store,
                        project_id=PROJECT_ID,
                        work_id=DISCUSSION_WORK_ID,
                        actors_by_ag2_id=actors_by_id,
                        message_types_by_ag2_id={
                            proposer.agent_id: "proposal",
                            critic.agent_id: "challenge",
                            reviser.agent_id: "revision",
                        },
                        correlation_id=TEAM_RUN_ID,
                        run_ids_by_ag2_id={
                            proposer.agent_id: "run.organization.orchestrator",
                            critic.agent_id: "run.organization.critic",
                            reviser.agent_id: "run.organization.specialist",
                        },
                    )
                    hub.register_listener(mirror)
                    channel = await proposer.open(
                        type=DISCUSSION_TYPE,
                        target=[critic.agent_id, reviser.agent_id],
                        knobs={"ordering": ORDERING_ROUND_ROBIN},
                        intent="Orchestrator proposal, independent challenge, specialist revision.",
                    )
                    await channel.send("Proposal: compare the distance hypothesis with axial tilt using opposite-hemisphere observations.")
                    wal_messages = await wait_for_text_count(hub, channel.channel_id, 3)
                    mirror.raise_if_degraded()
                    self.assertIn("Proposal:", wal_messages[0].event_data["text"])
                    self.assertIn("Challenge:", wal_messages[1].event_data["text"])
                    self.assertIn("Revision:", wal_messages[2].event_data["text"])
                finally:
                    await hub.close()
                ledger.finish(
                    PROJECT_ID, "run.organization.critic", terminal_status="succeeded",
                    actor=CRITIC, occurred_at=now(),
                )
                ledger.finish(
                    PROJECT_ID, "run.organization.specialist", terminal_status="succeeded",
                    actor=SPECIALIST, occurred_at=now(),
                )
                return ExecutionSignal.COMPLETED

            def verify_discussion(context: ExecutionContext) -> bool:
                messages = [
                    message for message in Projector(store).replay(context.project_id)["messages"]
                    if message["workId"] == context.work_id
                ]
                return (
                    [message["messageType"] for message in messages] == ["proposal", "challenge", "revision"]
                    and [message.get("runId") for message in messages] == [
                        "run.organization.orchestrator",
                        "run.organization.critic",
                        "run.organization.specialist",
                    ]
                )

            async def run_verification(context: ExecutionContext) -> ExecutionSignal:
                ledger.start(
                    PROJECT_ID, VERIFICATION_WORK_ID, run_id="run.organization.codex", agent=CODEX,
                    adapter={"name": "meldwork-v4-agenticx", "version": "0.1"},
                    context_hash=context_hash, correlation_id=TEAM_RUN_ID,
                    parent_run_id="run.organization.hermes", occurred_at=now(),
                )
                report = await asyncio.to_thread(
                    MeldworkAgenticXRuntimeBridge(store).run,
                    project_id=context.project_id,
                    work_id=context.work_id,
                    correlation_id=context.correlation_id,
                    run_ids_by_agent={
                        "codex": "run.organization.codex",
                        "hermes": "run.organization.hermes",
                    },
                )
                if report["orchestrationPhase"] != "completed" or report["invocationCount"] != 8:
                    raise RuntimeError("Meldwork V4 deterministic verification flow did not complete")
                ledger.finish(
                    PROJECT_ID, "run.organization.codex", terminal_status="succeeded",
                    actor=CODEX, occurred_at=now(),
                )
                return ExecutionSignal.COMPLETED

            def verify_meldwork(context: ExecutionContext) -> bool:
                events = store.events(context.project_id)
                for event in events:
                    if (
                        event["eventType"] != "discussion.message_added"
                        or event["payload"]["workId"] != context.work_id
                    ):
                        continue
                    try:
                        content = json.loads(store.get_blob(event["payload"]["contentHash"]))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if (
                        content.get("format") == "meldwork-v4-phase-v1"
                        and content.get("phase") == "verification"
                        and content.get("receipt", {}).get("verdict") == "support"
                    ):
                        return True
                return False

            discussion_task = WorkExecution(
                PROJECT_ID, DISCUSSION_WORK_ID, "run.organization.orchestrator", TEAM_RUN_ID,
                ORCHESTRATOR, {"name": "ag2-network", "version": "1.0"}, context_hash,
                ORCHESTRATOR, run_discussion, verify_discussion, parent_run_id=TEAM_RUN_ID,
            )
            verification_task = WorkExecution(
                PROJECT_ID, VERIFICATION_WORK_ID, "run.organization.hermes", TEAM_RUN_ID,
                HERMES, {"name": "meldwork-v4-agenticx", "version": "0.1"}, context_hash,
                HERMES, run_verification, verify_meldwork, parent_run_id=TEAM_RUN_ID,
            )

            async def run_project(_context: ExecutionContext) -> ExecutionSignal:
                # Deliberately list the dependent first; the scheduler must honor the canonical DAG.
                outcomes = await coordinator.run_plan((verification_task, discussion_task))
                if any(outcome.status == WorkflowStatus.WAITING_HUMAN for outcome in outcomes):
                    return ExecutionSignal.WAITING_HUMAN
                if any(outcome.status != WorkflowStatus.COMPLETED for outcome in outcomes):
                    raise RuntimeError("a delegated work item did not pass its lifecycle gates")
                return ExecutionSignal.COMPLETED

            def verify_project(_context: ExecutionContext) -> bool:
                state = Projector(store).replay(PROJECT_ID)
                return all(
                    state["workItems"][work_id]["status"] == "completed"
                    for work_id in (DISCUSSION_WORK_ID, VERIFICATION_WORK_ID)
                ) and verify_meldwork(ExecutionContext(
                    PROJECT_ID, VERIFICATION_WORK_ID, "run.organization.hermes", TEAM_RUN_ID, context_hash,
                ))

            project_task = WorkExecution(
                PROJECT_ID, WORK_ID, TEAM_RUN_ID, TEAM_RUN_ID, VP,
                {"name": "organization-coordinator", "version": "0.1"}, context_hash,
                VP, run_project, verify_project,
            )
            paused = await coordinator.run_work(project_task)
            self.assertEqual(paused.status, WorkflowStatus.WAITING_HUMAN)
            self.assertEqual(paused.question_ids, (question_id,))
            self.assertFalse(Projector(store).replay(PROJECT_ID)["agentRuns"])

            detail = inbox.get_question(PROJECT_ID, question_id)
            answered = inbox.answer_question(
                PROJECT_ID, question_id, expected_etag=detail["etag"],
                text="Use synthetic excerpts only; do not access external sources.",
                selected_option_id="option.synthetic-only", occurred_at=now(),
            )
            self.assertEqual(answered["answer"]["resumedWorkIds"], [WORK_ID, DISCUSSION_WORK_ID, VERIFICATION_WORK_ID])
            outcome = await coordinator.run_work(project_task)
            self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)

            events = store.events(PROJECT_ID)
            debate_messages = [
                event for event in events
                if event["eventType"] == "discussion.message_added"
                and event["payload"]["workId"] == DISCUSSION_WORK_ID
            ]
            verification_messages = [
                event for event in events
                if event["eventType"] == "discussion.message_added"
                and event["payload"]["workId"] == VERIFICATION_WORK_ID
            ]
            self.assertEqual([event["payload"]["messageType"] for event in debate_messages], ["proposal", "challenge", "revision"])
            self.assertEqual([event["payload"]["messageType"] for event in verification_messages], [
                "proposal", "proposal", "challenge", "challenge", "status", "status", "decision", "evidence",
            ])
            self.assertTrue(all(event["payload"].get("runId") for event in debate_messages + verification_messages))
            self.assertTrue(all(
                event["correlationId"] == TEAM_RUN_ID
                for event in debate_messages + verification_messages
            ))
            meldwork_roles = {
                event["actor"]["actorId"]: event["actor"]["role"]
                for event in verification_messages
            }
            self.assertEqual(meldwork_roles["agent.codex"], "specialist")
            self.assertEqual(meldwork_roles["agent.hermes"], "orchestrator")
            self.assertTrue(all(
                event["correlationId"] == TEAM_RUN_ID
                for event in events if event["eventType"].startswith("run.")
            ))

            final = Projector(store).replay(PROJECT_ID)
            self.assertEqual(
                [final["workItems"][work_id]["status"] for work_id in (WORK_ID, DISCUSSION_WORK_ID, VERIFICATION_WORK_ID)],
                ["completed", "completed", "completed"],
            )
            self.assertEqual(final["questions"][question_id]["status"], "answered")
            self.assertEqual(len(final["messages"]), 11)
            self.assertEqual({run["status"] for run in final["agentRuns"].values()}, {"succeeded"})
            self.assertEqual(len(final["agentRuns"]), 6)
            self.assertEqual(final["agentRuns"]["run.organization.orchestrator"]["parentRunId"], TEAM_RUN_ID)
            self.assertEqual(final["agentRuns"]["run.organization.critic"]["parentRunId"], "run.organization.orchestrator")
            self.assertEqual(final["agentRuns"]["run.organization.specialist"]["parentRunId"], "run.organization.orchestrator")
            self.assertEqual(final["agentRuns"]["run.organization.hermes"]["parentRunId"], TEAM_RUN_ID)
            self.assertEqual(final["agentRuns"]["run.organization.codex"]["parentRunId"], "run.organization.hermes")
            for run_id, run in final["agentRuns"].items():
                expected_outputs = [
                    message["eventId"] for message in final["messages"]
                    if message.get("runId") == run_id
                ]
                self.assertEqual(run["outputEventIds"], expected_outputs)
            self.assertTrue(store.verify(PROJECT_ID))

            # Candidate WALs/runtime directories are disposable; canonical replay survives alone.
            await asyncio.sleep(0)
            shutil.rmtree(ag2_path)
            reopened = SQLiteEventStore(db_path)
            recovered = Projector(reopened).replay(PROJECT_ID)
            self.assertTrue(reopened.verify(PROJECT_ID))
            self.assertEqual(
                [recovered["workItems"][work_id]["status"] for work_id in (WORK_ID, DISCUSSION_WORK_ID, VERIFICATION_WORK_ID)],
                ["completed", "completed", "completed"],
            )
            self.assertEqual(
                recovered["questions"][question_id]["answer"]["resumedWorkIds"],
                [WORK_ID, DISCUSSION_WORK_ID, VERIFICATION_WORK_ID],
            )
            self.assertEqual(len(recovered["messages"]), 11)
            self.assertEqual({run["status"] for run in recovered["agentRuns"].values()}, {"succeeded"})


if __name__ == "__main__":
    unittest.main()
