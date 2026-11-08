import asyncio
import tempfile
import unittest
from pathlib import Path

from harness.control_plane import ControlPlaneError
from harness.event_store import SQLiteEventStore
from harness.inbox import InboxApplication, InboxReadModel
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


PROJECT_ID = "project.workflow"
WORK_ID = "work.workflow"
NOW = "2026-04-16T12:00:00Z"
USER = {"actorType": "human", "actorId": "user.workflow", "role": "user"}
AGENT = {"actorType": "agent", "actorId": "agent.workflow", "role": "specialist"}
VERIFIER = {"actorType": "agent", "actorId": "agent.verifier", "role": "reviewer"}


class TestPrincipal:
    def current_actor(self):
        return dict(USER)


class TestAccess:
    def require_project_access(self, actor, project_id, action):
        if actor != USER or project_id != PROJECT_ID:
            raise PermissionError("test access denied")


class OrganizationCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.db_path)
        self.control = seed_synthetic_project(
            self.store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=NOW,
        )
        self.ledger = RunLedger(self.store, clock=lambda: NOW)
        self.coordinator = OrganizationCoordinator(
            self.store, control=self.control, ledger=self.ledger,
        )
        self.inbox = InboxApplication(
            InboxReadModel(self.store), self.control, TestPrincipal(), TestAccess(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def make_task(self, run_id, execute, verify=None):
        return WorkExecution(
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            run_id=run_id,
            correlation_id="run.workflow",
            actor=AGENT,
            adapter={"name": "test-adapter", "version": "1"},
            context_hash="a" * 64,
            verification_actor=VERIFIER,
            execute=execute,
            verify=verify or self.verify_output,
        )

    def write_output(self, context: ExecutionContext, text="A deterministic reviewed result.", *, store=None):
        target_store = store or self.store
        content_ref, content_hash = target_store.put_blob(text)
        target_store.append({
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": f"event.output.{context.run_id}",
            "projectId": context.project_id,
            "eventType": "discussion.message_added",
            "actor": AGENT,
            "occurredAt": NOW,
            "causationId": None,
            "correlationId": context.correlation_id,
            "idempotencyKey": f"idem.output.{context.run_id}",
            "payload": {
                "workId": context.work_id,
                "threadId": "thread.workflow",
                "messageType": "evidence",
                "runId": context.run_id,
                "contentRef": content_ref,
                "contentHash": content_hash,
                "visibleTo": [AGENT["actorId"], VERIFIER["actorId"]],
            },
        })

    def verify_output(self, context: ExecutionContext, *, store=None) -> bool:
        state = Projector(store or self.store).replay(context.project_id)
        return any(
            message["workId"] == context.work_id
            and message.get("runId") == context.run_id
            and message["messageType"] == "evidence"
            and message["content"].startswith("A deterministic reviewed result.")
            for message in state["messages"]
        )

    def make_question(self, question_id, *, run_id=None):
        return {
            "recordType": "human_question",
            "schemaVersion": "0.1",
            "projectId": PROJECT_ID,
            "questionId": question_id,
            "questionType": "decision",
            "priority": "high",
            "blocking": True,
            "requestedBy": AGENT,
            "workIds": [WORK_ID],
            "runId": run_id,
            "questionText": "May the specialist proceed with the next research step?",
            "impact": "This synthetic decision pauses the assigned work.",
            "options": [{"optionId": "option.proceed", "label": "Proceed"}],
            "status": "open",
            "createdAt": NOW,
        }

    async def answer(self, question_id):
        detail = self.inbox.get_question(PROJECT_ID, question_id)
        return self.inbox.answer_question(
            PROJECT_ID, question_id, expected_etag=detail["etag"],
            text="Proceed with the synthetic task.", selected_option_id="option.proceed",
            occurred_at=NOW,
        )

    async def test_blocking_question_prevents_start_then_inbox_answer_allows_completion(self):
        self.control.open_question(self.make_question("question.before-run"))
        calls = []

        def execute(context):
            calls.append(context.run_id)
            self.write_output(context)
            return ExecutionSignal.COMPLETED

        task = self.make_task("run.after-answer", execute)
        paused = await self.coordinator.run_work(task)
        self.assertEqual(paused.status, WorkflowStatus.WAITING_HUMAN)
        self.assertEqual(paused.question_ids, ("question.before-run",))
        self.assertEqual(calls, [])
        self.assertNotIn("run.after-answer", Projector(self.store).replay(PROJECT_ID)["agentRuns"])

        answered = await self.answer("question.before-run")
        self.assertEqual(answered["answer"]["resumedWorkIds"], [WORK_ID])
        result = await self.coordinator.run_work(task)
        self.assertEqual(result.status, WorkflowStatus.COMPLETED)
        self.assertEqual(calls, ["run.after-answer"])
        self.assertEqual(Projector(self.store).replay(PROJECT_ID)["workItems"][WORK_ID]["status"], "completed")
        self.assertTrue(self.store.verify(PROJECT_ID))

    async def test_adapter_failure_requires_explicit_user_requeue_and_fresh_run_id(self):
        calls = []

        def fail(_context):
            calls.append("failed")
            raise RuntimeError("synthetic adapter failure")

        failed = await self.coordinator.run_work(self.make_task("run.failed", fail))
        self.assertEqual(failed.status, WorkflowStatus.FAILED)
        self.assertEqual(Projector(self.store).replay(PROJECT_ID)["agentRuns"]["run.failed"]["status"], "failed")
        self.assertEqual((await self.coordinator.run_work(self.make_task("run.failed", fail))).status, WorkflowStatus.FAILED)
        self.assertEqual(calls, ["failed"])

        ready = self.coordinator.retry_work(
            PROJECT_ID, WORK_ID, actor=USER, reason="User authorized one bounded retry.", occurred_at=NOW,
        )
        self.assertEqual(ready["status"], "ready")
        retried = await self.coordinator.run_work(self.make_task(
            "run.retry-1", lambda context: (self.write_output(context), ExecutionSignal.COMPLETED)[1],
        ))
        self.assertEqual(retried.status, WorkflowStatus.COMPLETED)
        self.assertEqual(len(calls), 1)

    async def test_restart_recovery_never_replays_adapter_and_retry_is_explicit(self):
        calls = []

        def execute(context):
            calls.append(context.run_id)
            self.write_output(context)
            return ExecutionSignal.COMPLETED

        task = self.make_task("run.crashed", execute)
        self.ledger.start(
            PROJECT_ID, WORK_ID, run_id=task.run_id, agent=task.actor, adapter=task.adapter,
            context_hash=task.context_hash, correlation_id=task.correlation_id, occurred_at=NOW,
        )
        reopened = SQLiteEventStore(self.db_path)
        restarted = OrganizationCoordinator(reopened)
        self.assertEqual(restarted.recover_project(PROJECT_ID, occurred_at=NOW), ("run.crashed",))
        verify_reopened = lambda context: self.verify_output(context, store=reopened)
        result = await restarted.run_work(WorkExecution(
            **{**task.__dict__, "verify": verify_reopened},
        ))
        self.assertEqual(result.status, WorkflowStatus.FAILED)
        self.assertEqual(calls, [])
        ready = restarted.retry_work(
            PROJECT_ID, WORK_ID, actor=USER, reason="Resume after reviewing the interrupted run.", occurred_at=NOW,
        )
        self.assertEqual(ready["status"], "ready")
        def execute_retry(context):
            calls.append(context.run_id)
            self.write_output(context, store=reopened)
            return ExecutionSignal.COMPLETED

        retry = WorkExecution(
            PROJECT_ID, WORK_ID, "run.crashed.retry", "run.workflow", AGENT,
            {"name": "test-adapter", "version": "1"}, "a" * 64, VERIFIER,
            execute_retry,
            verify_reopened,
        )
        completed = await restarted.run_work(retry)
        self.assertEqual(completed.status, WorkflowStatus.COMPLETED)
        self.assertEqual(calls, ["run.crashed.retry"])
        self.assertEqual(Projector(reopened).replay(PROJECT_ID)["agentRuns"]["run.crashed"]["status"], "interrupted")

    async def test_restart_reconciles_committed_output_without_reexecuting_adapter(self):
        calls = []
        task = self.make_task(
            "run.output-before-crash",
            lambda context: (calls.append(context.run_id), self.write_output(context), ExecutionSignal.COMPLETED)[2],
        )
        self.ledger.start(
            PROJECT_ID, WORK_ID, run_id=task.run_id, agent=task.actor, adapter=task.adapter,
            context_hash=task.context_hash, correlation_id=task.correlation_id, occurred_at=NOW,
        )
        context = ExecutionContext(PROJECT_ID, WORK_ID, task.run_id, task.correlation_id, task.context_hash)
        task.execute(context)  # Simulate accepted canonical output followed by process death before run.finish.
        reopened = SQLiteEventStore(self.db_path)
        restarted = OrganizationCoordinator(reopened)
        self.assertEqual(restarted.recover_project(PROJECT_ID, occurred_at=NOW), (task.run_id,))
        resumed_task = WorkExecution(
            PROJECT_ID, WORK_ID, task.run_id, task.correlation_id, AGENT, task.adapter,
            task.context_hash, VERIFIER,
            lambda resumed_context: (calls.append("replayed"), ExecutionSignal.COMPLETED)[1],
            lambda resumed_context: self.verify_output(resumed_context, store=reopened),
        )
        outcome = await restarted.run_work(resumed_task)
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        self.assertEqual(calls, [task.run_id])
        state = Projector(reopened).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"][task.run_id]["status"], "interrupted")
        self.assertEqual(state["agentRuns"][task.run_id]["outputEventIds"], [f"event.output.{task.run_id}"])
        self.assertEqual(state["workItems"][WORK_ID]["status"], "completed")

    async def test_cancellation_interrupts_active_child_before_cancelling_parent(self):
        child_started = asyncio.Event()
        never_release = asyncio.Event()

        async def execute(context):
            self.ledger.start(
                PROJECT_ID, WORK_ID, run_id="run.cancelled.child", agent=VERIFIER,
                adapter={"name": "nested-test-adapter", "version": "1"},
                context_hash=context.context_hash, correlation_id=context.correlation_id,
                parent_run_id=context.run_id, occurred_at=NOW,
            )
            child_started.set()
            await never_release.wait()
            return ExecutionSignal.COMPLETED

        task = asyncio.create_task(self.coordinator.run_work(self.make_task("run.cancelled.parent", execute)))
        await asyncio.wait_for(child_started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        runs = Projector(self.store).replay(PROJECT_ID)["agentRuns"]
        self.assertEqual(runs["run.cancelled.child"]["status"], "interrupted")
        self.assertEqual(runs["run.cancelled.parent"]["status"], "cancelled")

    async def test_concurrent_same_run_request_does_not_invoke_adapter_twice(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def execute(context):
            calls.append(context.run_id)
            started.set()
            await release.wait()
            self.write_output(context)
            return ExecutionSignal.COMPLETED

        task = self.make_task("run.concurrent", execute)
        first = asyncio.create_task(self.coordinator.run_work(task))
        await asyncio.wait_for(started.wait(), timeout=2)
        concurrent = await self.coordinator.run_work(task)
        self.assertEqual(concurrent.status, WorkflowStatus.RECOVERY_REQUIRED)
        release.set()
        self.assertEqual((await first).status, WorkflowStatus.COMPLETED)
        self.assertEqual(calls, ["run.concurrent"])

    async def test_waiting_signal_requires_linked_question_and_continuation_uses_new_run(self):
        def ask(context):
            self.control.open_question(self.make_question("question.during-run", run_id=context.run_id))
            return ExecutionSignal.WAITING_HUMAN

        task = self.make_task("run.ask", ask)
        paused = await self.coordinator.run_work(task)
        self.assertEqual(paused.status, WorkflowStatus.WAITING_HUMAN)
        self.assertEqual(Projector(self.store).replay(PROJECT_ID)["agentRuns"]["run.ask"]["status"], "succeeded")
        await self.answer("question.during-run")
        self.assertEqual(
            (await self.coordinator.run_work(task)).status,
            WorkflowStatus.CONTINUATION_REQUIRED,
        )
        continuation = await self.coordinator.run_work(self.make_task(
            "run.ask.continuation",
            lambda context: (self.write_output(context), ExecutionSignal.COMPLETED)[1],
        ))
        self.assertEqual(continuation.status, WorkflowStatus.COMPLETED)

    async def test_cancellation_is_recorded_and_work_is_not_retried(self):
        async def cancel(_context):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.coordinator.run_work(self.make_task("run.cancelled", cancel))
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.cancelled"]["status"], "cancelled")
        self.assertEqual(state["workItems"][WORK_ID]["status"], "cancelled")
        with self.assertRaisesRegex(ControlPlaneError, "not eligible for retry"):
            self.coordinator.retry_work(
                PROJECT_ID, WORK_ID, actor=USER, reason="Do not restart a cancelled task.", occurred_at=NOW,
            )

    async def test_dependency_gate_is_reported_without_starting_a_run(self):
        self.control.create_work({
            "recordType": "work_item", "schemaVersion": "0.1", "projectId": PROJECT_ID,
            "workId": "work.dependent", "revision": 1, "parentWorkId": WORK_ID,
            "title": "Dependent work", "objective": "Wait for the root work.",
            "workType": "research", "status": "ready", "owner": AGENT,
            "dependsOn": [WORK_ID], "capabilityGrantIds": [],
            "acceptanceCriteria": ["Root work is complete."],
            "createdBy": USER, "createdAt": NOW, "updatedAt": NOW,
        })
        task = WorkExecution(
            PROJECT_ID, "work.dependent", "run.dependent", "run.workflow", AGENT,
            {"name": "test-adapter", "version": "1"}, "b" * 64, VERIFIER,
            lambda context: (self.write_output(context), ExecutionSignal.COMPLETED)[1],
            lambda _context: True,
        )
        result = await self.coordinator.run_work(task)
        self.assertEqual(result.status, WorkflowStatus.BLOCKED)
        self.assertEqual(result.blocked_by, (WORK_ID,))
        self.assertNotIn("run.dependent", Projector(self.store).replay(PROJECT_ID)["agentRuns"])


if __name__ == "__main__":
    unittest.main()
