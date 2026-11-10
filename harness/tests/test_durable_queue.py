import asyncio
import hashlib
from copy import deepcopy
import multiprocessing
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.control_plane import ControlPlaneError, ProjectControlPlane
from harness.durable_queue import QueueError, SQLiteWorkQueue
from harness.organization_runtime import (
    ExecutionSignal, OrganizationCoordinator, WorkflowStatus, WorkExecution,
)
from harness.projection import Projector
from harness.event_store import ConcurrentWriteConflict, SQLiteEventStore
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


def _claim_in_process(database_path, worker_id, result_queue):
    try:
        queue = SQLiteWorkQueue(SQLiteEventStore(database_path))
        claim = queue.claim_next(worker_id, now=175, lease_seconds=30)
        result_queue.put((worker_id, None if claim is None else claim["leaseGeneration"], None))
    except Exception as exc:  # Report child failure through the parent test process.
        result_queue.put((worker_id, None, repr(exc)))


def _append_with_lease_in_process(database_path, lease, now, event, result_queue):
    try:
        store = SQLiteEventStore(database_path)
        with store.queued_write_context(lease, lambda: now):
            store.append(event)
        result_queue.put(None)
    except Exception as exc:
        result_queue.put(type(exc).__name__)


def _start_invocation_in_process(database_path, invocation_id, result_queue):
    try:
        SQLiteEventStore(database_path).start_adapter_invocation(invocation_id)
        result_queue.put(None)
    except Exception as exc:
        result_queue.put(type(exc).__name__)


def _run_queued_job_until_killed(database_path, ready_queue):
    async def scenario():
        store = SQLiteEventStore(database_path)
        coordinator = OrganizationCoordinator(store)
        queue = SQLiteWorkQueue(store)

        async def wait_for_kill(context):
            ready_queue.put((context.run_id, None))
            await asyncio.Event().wait()

        def resolve(job):
            descriptor = job["payload"]
            return WorkExecution(
                project_id=job["projectId"], work_id=job["workId"],
                run_id=f"run.crash-process.g{job['leaseGeneration']}",
                correlation_id=descriptor["correlationId"], actor=descriptor["actor"],
                adapter=descriptor["adapter"], context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"],
                execute=wait_for_kill, verify=lambda _: True,
            )

        await coordinator.run_queued(
            queue, "worker.crash-process", resolve, lease_seconds=2,
        )

    try:
        asyncio.run(scenario())
    except Exception as exc:
        ready_queue.put((None, repr(exc)))


def _inspect_pending_question_in_process(database_path, result_queue):
    try:
        store = SQLiteEventStore(database_path)
        state = Projector(store).replay(PROJECT_ID)
        question_status = state["questions"]["question.queued"]["status"]
        work_status = state["workItems"][WORK_ID]["status"]
        claim = SQLiteWorkQueue(store).claim_next("worker.after-restart", now=501, lease_seconds=10)
        result_queue.put((question_status, work_status, claim))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))


PROJECT_ID = "project.queue"
WORK_ID = "work.queue"
NOW = "2026-04-16T12:00:00Z"
VP = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}
USER = {"actorType": "human", "actorId": "user.synthetic", "role": "user"}
SPECIALIST = {"actorType": "agent", "actorId": "agent.worker", "role": "specialist"}
ADAPTER = {"name": "offline-queue-test", "version": "1"}
CONTEXT_HASH = hashlib.sha256(b"queue context").hexdigest()


def execution_descriptor(execution_key="offline"):
    return {
        "descriptorVersion": 1, "executionKey": execution_key, "actor": SPECIALIST,
        "verificationActor": VP, "adapter": ADAPTER, "contextHash": CONTEXT_HASH,
        "correlationId": "queue.integration",
    }


class SQLiteWorkQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.path)
        self.control = seed_synthetic_project(
            self.store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=NOW,
        )
        self.queue = SQLiteWorkQueue(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self, job_id="job.first"):
        return self.queue.enqueue(
            job_id, PROJECT_ID, WORK_ID, execution_descriptor(), now=100,
        )

    def test_enqueue_is_idempotent_and_one_open_job_per_work(self):
        first = self.enqueue()
        repeated = self.enqueue()
        self.assertEqual(first["jobId"], repeated["jobId"])
        with self.assertRaisesRegex(QueueError, "already has an open scheduled job"):
            self.enqueue("job.duplicate")

    def test_enqueue_rejects_unversioned_or_malformed_execution_descriptor(self):
        with self.assertRaisesRegex(QueueError, "missing or unsupported fields"):
            self.queue.enqueue("job.invalid", PROJECT_ID, WORK_ID, {"executionKey": "bad"}, now=125)
        invalid = execution_descriptor()
        invalid["descriptorVersion"] = 2
        with self.assertRaisesRegex(QueueError, "unsupported job descriptor version"):
            self.queue.enqueue("job.v2", PROJECT_ID, WORK_ID, invalid, now=125)
        for field in ("workerId", "placement", "machineCapabilities", "networkPolicy"):
            with self.subTest(field=field):
                agent_selected_policy = execution_descriptor()
                agent_selected_policy[field] = "untrusted-choice"
                with self.assertRaisesRegex(QueueError, "missing or unsupported fields"):
                    self.queue.enqueue(
                        f"job.untrusted.{field}", PROJECT_ID, WORK_ID,
                        agent_selected_policy, now=125,
                    )

    def test_claim_renew_release_and_reopen_are_durable(self):
        self.enqueue()
        claimed = self.queue.claim_next("worker.one", now=101, lease_seconds=30)
        self.assertEqual(claimed["state"], "leased")
        self.assertEqual(claimed["leaseGeneration"], 1)
        token = claimed["leaseToken"]
        self.assertTrue(token)
        self.assertIsNone(self.queue.get("job.first")["leaseToken"])

        backup_path = self.store.backup_to(Path(self.temp.name) / "queue-backup.sqlite3")
        peer = SQLiteWorkQueue(SQLiteEventStore(backup_path))
        renewed = peer.renew(
            "job.first", "worker.one", token, 1, now=110, lease_seconds=30,
        )
        self.assertEqual(renewed["leaseExpiresAt"], 140)
        with self.assertRaisesRegex(QueueError, "another worker"):
            peer.renew("job.first", "worker.two", token, 1, now=111, lease_seconds=30)
        released = peer.release("job.first", "worker.one", token, 1, now=112)
        self.assertEqual(released["state"], "queued")
        self.assertEqual(peer.claim_next("worker.two", now=113, lease_seconds=30)["leaseGeneration"], 2)

    def test_concurrent_workers_cannot_claim_one_open_job_twice(self):
        self.enqueue()
        queues = [SQLiteWorkQueue(SQLiteEventStore(self.path)) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(
                lambda item: item[0].claim_next(item[1], now=150, lease_seconds=30),
                zip(queues, ("worker.a", "worker.b")),
            ))
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["leaseGeneration"], 1)

    def test_separate_processes_cannot_claim_the_same_job(self):
        self.enqueue()
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        processes = [
            context.Process(target=_claim_in_process, args=(str(self.path), f"process.{index}", results))
            for index in range(2)
        ]
        for process in processes:
            process.start()
        reports = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
        self.assertTrue(all(error is None for _, _, error in reports), reports)
        self.assertEqual(sum(generation is not None for _, generation, _ in reports), 1)

    def test_expired_lease_requires_explicit_recovery_before_requeue(self):
        self.enqueue()
        first = self.queue.claim_next("worker.old", now=200, lease_seconds=10)
        self.assertIsNone(self.queue.claim_next("worker.new", now=211, lease_seconds=10))
        self.assertEqual(self.queue.get("job.first")["state"], "recovery_required")
        with self.assertRaisesRegex(QueueError, "explicit no-live-worker"):
            self.queue.recover_expired("job.first", now=212)
        with self.assertRaisesRegex(QueueError, "cannot precede the recorded lease-expiry observation"):
            self.queue.recover_expired(
                "job.first", now=210, attest_no_live_worker=True,
            )
        recovered = self.queue.recover_expired(
            "job.first", now=212, attest_no_live_worker=True,
        )
        self.assertEqual(recovered["state"], "queued")
        second = self.queue.claim_next("worker.new", now=213, lease_seconds=10)
        self.assertEqual(second["leaseGeneration"], 2)
        with self.assertRaisesRegex(QueueError, "stale, expired"):
            self.queue.renew(
                "job.first", "worker.old", first["leaseToken"], 1,
                now=214, lease_seconds=10,
            )

    def test_expiry_recovery_state_survives_store_restart(self):
        self.enqueue()
        first = self.queue.claim_next("worker.before-restart", now=1_000, lease_seconds=5)
        self.assertIsNone(self.queue.claim_next("worker.observe-expiry", now=1_006, lease_seconds=5))
        self.assertEqual(self.queue.get("job.first")["state"], "recovery_required")

        self.store = SQLiteEventStore(self.path)
        self.queue = SQLiteWorkQueue(self.store)
        self.assertEqual(self.queue.get("job.first")["state"], "recovery_required")
        with self.assertRaisesRegex(QueueError, "explicit no-live-worker"):
            self.queue.recover_expired("job.first", now=1_007)
        self.queue.recover_expired("job.first", now=1_007, attest_no_live_worker=True)
        second = self.queue.claim_next("worker.after-restart", now=1_008, lease_seconds=5)
        self.assertEqual(second["leaseGeneration"], first["leaseGeneration"] + 1)

    def test_claim_skips_canonically_running_work(self):
        self.enqueue()
        RunLedger(self.store, clock=lambda: NOW).start(
            PROJECT_ID, WORK_ID, run_id="run.already-started", agent=SPECIALIST,
            adapter={"name": "test", "version": "1"},
            context_hash=hashlib.sha256(b"context").hexdigest(),
            correlation_id="queue.test", occurred_at=NOW,
        )
        self.assertIsNone(self.queue.claim_next("worker", now=300, lease_seconds=10))
        self.assertEqual(self.queue.get("job.first")["state"], "queued")

    def test_job_completes_only_after_canonical_work_and_run_complete(self):
        self.enqueue()
        lease = self.queue.claim_next("worker", now=400, lease_seconds=100)
        with self.assertRaisesRegex(QueueError, "only after canonical work is completed"):
            self.queue.complete(
                "job.first", "worker", lease["leaseToken"], 1, now=401,
            )

        ledger = RunLedger(self.store, clock=lambda: NOW)
        queued_lease = {
            "jobId": "job.first", "workerId": "worker",
            "leaseToken": lease["leaseToken"], "leaseGeneration": 1,
        }
        with self.store.queued_write_context(queued_lease, lambda: 401):
            run = ledger.start(
                PROJECT_ID, WORK_ID, run_id="run.completion", agent=SPECIALIST,
                adapter={"name": "test", "version": "1"},
                context_hash=hashlib.sha256(b"context").hexdigest(),
                correlation_id="queue.test", occurred_at=NOW,
                queue_lease={**queued_lease, "now": 401},
            )
            with self.assertRaises(ControlPlaneError):
                self.control.transition_work(PROJECT_ID, WORK_ID, "completed", actor=VP, occurred_at=NOW)
            ledger.finish(PROJECT_ID, run["runId"], terminal_status="succeeded", actor=SPECIALIST, occurred_at=NOW)
            self.control.transition_work(PROJECT_ID, WORK_ID, "verifying", actor=VP, occurred_at=NOW)
            self.control.transition_work(PROJECT_ID, WORK_ID, "completed", actor=VP, occurred_at=NOW)
        completed = self.queue.complete(
            "job.first", "worker", lease["leaseToken"], 1, now=402,
        )
        self.assertEqual(completed["state"], "completed")
        self.assertIsNone(completed["leaseToken"])


class QueuedOrganizationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "queued-runtime.sqlite3"
        self.store = SQLiteEventStore(self.path)
        self.control = seed_synthetic_project(
            self.store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=NOW,
        )
        self.queue = SQLiteWorkQueue(self.store)
        self.queue.enqueue(
            "job.runtime", PROJECT_ID, WORK_ID, execution_descriptor(), now=100,
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_coordinator_claims_runs_and_acknowledges_canonical_completion(self):
        def resolve(job):
            self.assertEqual(job["payload"]["executionKey"], "offline")
            descriptor = job["payload"]
            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.runtime.g{job['leaseGeneration']}", correlation_id=descriptor["correlationId"],
                actor=descriptor["actor"], adapter=descriptor["adapter"],
                context_hash=descriptor["contextHash"],
                verification_actor=VP,
                execute=lambda _: ExecutionSignal.COMPLETED,
                verify=lambda _: True,
            )

        coordinator = OrganizationCoordinator(self.store)
        outcome = await coordinator.run_queued(
            self.queue, "worker.runtime", resolve, lease_seconds=10, clock=lambda: 200,
        )
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        self.assertEqual(self.queue.get("job.runtime")["state"], "completed")
        run = coordinator.projector.replay(PROJECT_ID)["agentRuns"]["run.runtime.g1"]
        self.assertEqual(run["queueJobId"], "job.runtime")
        self.assertEqual(run["leaseGeneration"], 1)
        self.assertEqual(
            self.queue.claim_next("worker.next", now=201, lease_seconds=10), None,
        )

    async def test_heartbeat_renews_lease_during_long_running_adapter(self):
        coordinator = OrganizationCoordinator(self.store)

        async def execute(_context):
            await asyncio.sleep(3.6)
            return ExecutionSignal.COMPLETED

        def resolve(job):
            descriptor = job["payload"]
            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.heartbeat.g{job['leaseGeneration']}",
                correlation_id=descriptor["correlationId"], actor=descriptor["actor"],
                adapter=descriptor["adapter"], context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"],
                execute=execute, verify=lambda _: True,
            )

        outcome = await coordinator.run_queued(
            self.queue, "worker.heartbeat", resolve, lease_seconds=3,
        )
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        self.assertEqual(self.queue.get("job.runtime")["state"], "completed")

    async def test_supervised_worker_loop_runs_jobs_until_stop_requested(self):
        coordinator = OrganizationCoordinator(self.store)
        stop = asyncio.Event()

        def resolve(job):
            descriptor = job["payload"]

            def execute(_context):
                stop.set()
                return ExecutionSignal.COMPLETED

            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.worker.g{job['leaseGeneration']}",
                correlation_id=descriptor["correlationId"], actor=descriptor["actor"],
                adapter=descriptor["adapter"], context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"],
                execute=execute, verify=lambda _: True,
            )

        await coordinator.serve_queue(
            self.queue, "worker.loop", resolve, stop,
            poll_interval=0.05, lease_seconds=10, clock=lambda: 250,
        )
        self.assertEqual(self.queue.get("job.runtime")["state"], "completed")

    async def test_forced_worker_cancellation_closes_canonical_cancelled_job(self):
        started = asyncio.Event()

        async def execute(_context):
            started.set()
            await asyncio.Event().wait()

        def resolve(job):
            descriptor = job["payload"]
            return WorkExecution(
                project_id=job["projectId"], work_id=job["workId"],
                run_id=f"run.cancelled.{job['leaseGeneration']}",
                correlation_id=descriptor["correlationId"], actor=descriptor["actor"],
                adapter=descriptor["adapter"], context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"], execute=execute,
                verify=lambda _: True,
            )

        coordinator = OrganizationCoordinator(self.store)
        worker = asyncio.create_task(coordinator.run_queued(
            self.queue, "worker.cancel", resolve, lease_seconds=10,
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker
        self.assertEqual(self.queue.get("job.runtime")["state"], "failed")
        state = coordinator.projector.replay(PROJECT_ID)
        self.assertEqual(state["workItems"][WORK_ID]["status"], "cancelled")
        self.assertEqual(state["agentRuns"]["run.cancelled.1"]["status"], "cancelled")

    async def test_worker_stop_drains_claimed_job_without_claiming_another(self):
        second_work = deepcopy(self.control.projector.replay(PROJECT_ID)["workItems"][WORK_ID])
        second_work.update(
            workId="work.queue.drain", title="Must remain queued after shutdown", revision=1,
            status="ready", createdAt=NOW, updatedAt=NOW,
        )
        self.control.create_work(second_work)
        self.queue.enqueue(
            "job.runtime.drain", PROJECT_ID, "work.queue.drain", execution_descriptor(), now=101,
        )
        started = asyncio.Event()
        finish = asyncio.Event()
        stop = asyncio.Event()

        async def execute(_context):
            started.set()
            await finish.wait()
            return ExecutionSignal.COMPLETED

        def resolve(job):
            descriptor = job["payload"]
            return WorkExecution(
                project_id=job["projectId"], work_id=job["workId"],
                run_id=f"run.drain.{job['leaseGeneration']}",
                correlation_id=descriptor["correlationId"], actor=descriptor["actor"],
                adapter=descriptor["adapter"], context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"],
                execute=execute, verify=lambda _: True,
            )

        coordinator = OrganizationCoordinator(self.store)
        worker = asyncio.create_task(coordinator.serve_queue(
            self.queue, "worker.drain", resolve, stop,
            poll_interval=0.05, lease_seconds=10,
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        stop.set()
        finish.set()
        await asyncio.wait_for(worker, timeout=5)
        self.assertEqual(self.queue.get("job.runtime")["state"], "completed")
        self.assertEqual(self.queue.get("job.runtime.drain")["state"], "queued")

    async def test_inbox_answer_resumes_persisted_job_with_a_fresh_run_generation(self):
        coordinator = OrganizationCoordinator(self.store)

        def resolve(job):
            generation = job["leaseGeneration"]

            def execute(context):
                if generation == 1:
                    self.control.open_question({
                        "recordType": "human_question", "schemaVersion": "0.1",
                        "projectId": PROJECT_ID, "questionId": "question.queued",
                        "questionType": "clarification", "priority": "normal", "blocking": True,
                        "requestedBy": VP, "workIds": [WORK_ID], "runId": context.run_id,
                        "questionText": "May the queued work proceed?",
                        "impact": "The worker needs the user's scope decision.",
                        "options": [], "status": "open", "createdAt": NOW,
                    })
                    return ExecutionSignal.WAITING_HUMAN
                return ExecutionSignal.COMPLETED

            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.queued.g{generation}", correlation_id=job["payload"]["correlationId"],
                actor=job["payload"]["actor"], adapter=job["payload"]["adapter"],
                context_hash=job["payload"]["contextHash"],
                verification_actor=VP, execute=execute, verify=lambda _: True,
            )

        first = await coordinator.run_queued(
            self.queue, "worker.runtime", resolve, lease_seconds=10, clock=lambda: 500,
        )
        self.assertEqual(first.status, WorkflowStatus.WAITING_HUMAN)
        self.assertEqual(self.queue.get("job.runtime")["state"], "queued")

        # A separate process must see the open question, paused work, and
        # unclaimable job before the parent reconstructs its command objects.
        context = multiprocessing.get_context("spawn")
        reports = context.Queue()
        child = context.Process(
            target=_inspect_pending_question_in_process,
            args=(str(self.path), reports),
        )
        child.start()
        recovered_question = reports.get(timeout=15)
        child.join(timeout=15)
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        self.assertEqual(child.exitcode, 0)
        self.assertEqual(recovered_question, ("open", "waiting_human", None))

        # A cold queue/coordinator/control-plane reconstruction must retain the
        # unanswered question and leave its dependent job unrunnable.
        reopened = SQLiteEventStore(self.path)
        resumed_control = ProjectControlPlane(reopened)
        resumed_queue = SQLiteWorkQueue(reopened)
        resumed_coordinator = OrganizationCoordinator(reopened)
        self.assertIsNone(resumed_queue.claim_next("worker.next", now=501, lease_seconds=10))

        actor = {"actorType": "human", "actorId": "user.synthetic", "role": "user"}
        answer = resumed_control.answer_question(
            PROJECT_ID, "question.queued", "Proceed.", actor=actor,
        )
        answered_event_count = len(reopened.events(PROJECT_ID))
        self.assertEqual(
            resumed_control.answer_question(
                PROJECT_ID, "question.queued", "Proceed.", actor=actor,
            ),
            answer,
        )
        self.assertEqual(len(reopened.events(PROJECT_ID)), answered_event_count)

        second = await resumed_coordinator.run_queued(
            resumed_queue, "worker.next", resolve, lease_seconds=10, clock=lambda: 502,
        )
        self.assertEqual(second.status, WorkflowStatus.COMPLETED)
        self.assertEqual(resumed_queue.get("job.runtime")["state"], "completed")
        self.assertIsNone(resumed_queue.claim_next("worker.third", now=503, lease_seconds=10))
        state = resumed_coordinator.projector.replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.queued.g1"]["status"], "succeeded")
        self.assertEqual(state["agentRuns"]["run.queued.g2"]["status"], "succeeded")

    async def test_resolved_runtime_must_match_persisted_descriptor(self):
        coordinator = OrganizationCoordinator(self.store)

        def mismatched_factory(job):
            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.mismatch.g{job['leaseGeneration']}", correlation_id="queue.integration",
                actor=SPECIALIST, adapter={"name": "unapproved-runtime", "version": "1"},
                context_hash=CONTEXT_HASH, verification_actor=VP,
                execute=lambda _: ExecutionSignal.COMPLETED, verify=lambda _: True,
            )

        with self.assertRaisesRegex(QueueError, "persisted run descriptor"):
            await coordinator.run_queued(
                self.queue, "worker.runtime", mismatched_factory,
                lease_seconds=10, clock=lambda: 350,
            )
        self.assertEqual(self.queue.get("job.runtime")["state"], "recovery_required")

    async def test_stale_lease_cannot_start_run_after_a_new_generation_is_claimed(self):
        old = self.queue.claim_next("worker.old", now=700, lease_seconds=10)
        self.assertIsNone(self.queue.claim_next("worker.new", now=711, lease_seconds=10))
        self.queue.recover_expired("job.runtime", now=712, attest_no_live_worker=True)
        current = self.queue.claim_next("worker.new", now=713, lease_seconds=30)
        ledger = RunLedger(self.store)
        run_args = dict(
            project_id=PROJECT_ID, work_id=WORK_ID, run_id="run.fenced", agent=SPECIALIST,
            adapter=ADAPTER, context_hash=CONTEXT_HASH, correlation_id="queue.integration",
            occurred_at=NOW,
        )
        with self.assertRaisesRegex(ConcurrentWriteConflict, "lease expired or changed"):
            ledger.start(
                **run_args,
                queue_lease={
                    "jobId": "job.runtime", "workerId": "worker.old",
                    "leaseToken": old["leaseToken"], "leaseGeneration": 1, "now": 714,
                },
            )
        self.assertNotIn("run.fenced", OrganizationCoordinator(self.store).projector.replay(PROJECT_ID)["agentRuns"])
        started = ledger.start(
            **run_args,
            queue_lease={
                "jobId": "job.runtime", "workerId": "worker.new",
                "leaseToken": current["leaseToken"], "leaseGeneration": 2, "now": 714,
            },
        )
        self.assertEqual(started["queueJobId"], "job.runtime")
        self.assertEqual(started["leaseGeneration"], 2)

    async def test_stale_lease_cannot_append_run_attributed_output(self):
        claim = self.queue.claim_next("worker.output", now=550, lease_seconds=10)
        RunLedger(self.store).start(
            PROJECT_ID, WORK_ID, run_id="run.output.g1", agent=SPECIALIST,
            adapter=ADAPTER, context_hash=CONTEXT_HASH, correlation_id="queue.integration",
            occurred_at=NOW,
            queue_lease={
                "jobId": "job.runtime", "workerId": "worker.output",
                "leaseToken": claim["leaseToken"], "leaseGeneration": 1, "now": 551,
            },
        )
        content_ref, content_hash = self.store.put_blob("leased output")

        def message(event_id):
            return {
                "recordType": "event", "schemaVersion": "0.1", "eventId": event_id,
                "projectId": PROJECT_ID, "eventType": "discussion.message_added",
                "actor": SPECIALIST, "occurredAt": NOW,
                "causationId": None, "correlationId": "queue.integration",
                "idempotencyKey": f"idem.{event_id}",
                "payload": {
                    "workId": WORK_ID, "threadId": "thread.queue", "messageType": "status",
                    "contentRef": content_ref, "contentHash": content_hash,
                    "runId": "run.output.g1", "visibleTo": ["agent.vp"],
                },
            }

        output_lease = {
            "jobId": "job.runtime", "workerId": "worker.output",
            "leaseToken": claim["leaseToken"], "leaseGeneration": 1,
        }
        unbound = message("event.queue.output.unbound")
        unbound["payload"].pop("runId")
        with self.assertRaisesRegex(ConcurrentWriteConflict, "agent write on queued work requires"):
            self.store.append(unbound)
        with self.store.queued_write_context(output_lease, lambda: 559):
            self.store.append(unbound)
            self.store.append(message("event.queue.output.before-expiry"))
            with ThreadPoolExecutor(max_workers=1) as pool:
                detached = pool.submit(self.store.append, message("event.queue.output.detached"))
                with self.assertRaisesRegex(
                    ConcurrentWriteConflict, "requires its active execution lease context",
                ):
                    detached.result()
        # Reopening reconstructs this operational fence from canonical run.started history.
        conn = self.store._connect()
        try:
            conn.execute("DELETE FROM queued_run_fences")
        finally:
            conn.close()
        self.store = SQLiteEventStore(self.path)
        backup = self.path.with_name("queued-run-backup.sqlite3")
        self.store.backup_to(backup)
        self.assertEqual(SQLiteEventStore(backup).verified_events(PROJECT_ID)[-1]["eventId"], "event.queue.output.before-expiry")
        self.assertIsNone(self.queue.claim_next("worker.new", now=561, lease_seconds=10))
        self.queue.recover_expired("job.runtime", now=562, attest_no_live_worker=True)
        with self.assertRaisesRegex(ConcurrentWriteConflict, "requires its active execution lease context"):
            self.store.append(message("event.queue.output.after-fence"))
        process_context = multiprocessing.get_context("spawn")
        result_queue = process_context.Queue()
        process = process_context.Process(
            target=_append_with_lease_in_process,
            args=(str(self.path), output_lease, 562, message("event.queue.output.detached-process"), result_queue),
        )
        process.start()
        self.assertEqual(result_queue.get(timeout=20), "ConcurrentWriteConflict")
        process.join(timeout=20)
        self.assertEqual(process.exitcode, 0)
        ledger = RunLedger(self.store)
        with self.store.queued_write_context(output_lease, lambda: 562):
            with self.assertRaisesRegex(ConcurrentWriteConflict, "queue lease expired or changed"):
                ledger.finish(
                    PROJECT_ID, "run.output.g1", terminal_status="succeeded",
                    actor=SPECIALIST, occurred_at=NOW,
                )
        self.assertEqual(ledger.recover_interrupted(PROJECT_ID, occurred_at=NOW), ["run.output.g1"])
        self.assertEqual(
            Projector(self.store).replay(PROJECT_ID)["agentRuns"]["run.output.g1"]["status"], "interrupted",
        )
        self.assertEqual(len(self.store.events(PROJECT_ID)), 7)

    async def test_adapter_dispatch_cannot_start_after_queue_lease_expiry(self):
        lease = self.queue.claim_next("worker.dispatch", now=800, lease_seconds=10)
        context = {
            "jobId": "job.runtime", "workerId": "worker.dispatch",
            "leaseToken": lease["leaseToken"], "leaseGeneration": 1,
        }
        with self.store.queued_write_context(context, lambda: 801):
            self.store.prepare_adapter_invocation("invocation.expired-dispatch", PROJECT_ID, "b" * 64)
        self.assertIsNone(self.queue.claim_next("worker.observe-expiry", now=811, lease_seconds=10))
        with self.store.queued_write_context(context, lambda: 811):
            with self.assertRaisesRegex(ConcurrentWriteConflict, "queue lease expired or changed"):
                self.store.start_adapter_invocation("invocation.expired-dispatch")
        process_context = multiprocessing.get_context("spawn")
        result_queue = process_context.Queue()
        process = process_context.Process(
            target=_start_invocation_in_process,
            args=(str(self.path), "invocation.expired-dispatch", result_queue),
        )
        process.start()
        self.assertEqual(result_queue.get(timeout=20), "ConcurrentWriteConflict")
        process.join(timeout=20)
        self.assertEqual(process.exitcode, 0)
        invocation = self.store.get_adapter_invocation("invocation.expired-dispatch")
        self.assertEqual(invocation["state"], "prepared")

    async def test_staged_receipt_import_requires_explicit_expired_lease_recovery(self):
        second_work = deepcopy(self.control.projector.replay(PROJECT_ID)["workItems"][WORK_ID])
        second_work.update(
            workId="work.queue.second", title="Second isolated work item",
            revision=1, status="ready", createdAt=NOW, updatedAt=NOW,
        )
        self.control.create_work(second_work)
        self.queue.enqueue(
            "job.runtime.second", PROJECT_ID, "work.queue.second", execution_descriptor(), now=649,
        )
        claim = self.queue.claim_next("worker.receipt", now=650, lease_seconds=10)
        second_claim = self.queue.claim_next("worker.receipt.second", now=650, lease_seconds=10)
        output_lease = {
            "jobId": "job.runtime", "workerId": "worker.receipt",
            "leaseToken": claim["leaseToken"], "leaseGeneration": 1,
        }
        RunLedger(self.store).start(
            PROJECT_ID, WORK_ID, run_id="run.receipt.g1", agent=SPECIALIST,
            adapter=ADAPTER, context_hash=CONTEXT_HASH, correlation_id="queue.integration",
            occurred_at=NOW, queue_lease={**output_lease, "now": 651},
        )
        content_ref, content_hash = self.store.put_blob("staged receipt output")
        receipt_event = {
            "recordType": "event", "schemaVersion": "0.1",
            "eventId": "event.receipt.queued", "projectId": PROJECT_ID,
            "eventType": "discussion.message_added", "actor": SPECIALIST,
            "occurredAt": NOW, "causationId": None, "correlationId": "queue.integration",
            "idempotencyKey": "idem.event.receipt.queued",
            "payload": {
                "workId": WORK_ID, "threadId": "thread.receipt", "messageType": "status",
                "contentRef": content_ref, "contentHash": content_hash,
                "runId": "run.receipt.g1", "visibleTo": ["agent.vp"],
            },
        }
        RunLedger(self.store).start(
            PROJECT_ID, "work.queue.second", run_id="run.receipt.second.g1", agent=SPECIALIST,
            adapter=ADAPTER, context_hash=CONTEXT_HASH, correlation_id="queue.integration",
            occurred_at=NOW, queue_lease={
                "jobId": "job.runtime.second", "workerId": "worker.receipt.second",
                "leaseToken": second_claim["leaseToken"], "leaseGeneration": 1, "now": 651,
            },
        )
        with self.store.queued_write_context(output_lease, lambda: 652):
            self.store.prepare_adapter_invocation("invocation.queued-receipt", PROJECT_ID, "a" * 64)
            self.store.start_adapter_invocation("invocation.queued-receipt")
            self.store.stage_adapter_receipt("invocation.queued-receipt", [receipt_event])
        backup_path = self.path.with_name("queued-invocation-backup.sqlite3")
        self.store.backup_to(backup_path)
        self.assertTrue(SQLiteEventStore(backup_path).verify(PROJECT_ID))
        self.assertIsNone(self.queue.claim_next("worker.recovery", now=661, lease_seconds=10))
        with self.assertRaisesRegex(ConcurrentWriteConflict, "requires its active execution lease context"):
            self.store.commit_adapter_receipt("invocation.queued-receipt")
        with self.assertRaisesRegex(ConcurrentWriteConflict, "not covered by explicit expired-lease recovery"):
            self.store.commit_adapter_receipt(
                "invocation.queued-receipt", recovery_job_ids={"wrong.job"},
                attest_no_live_worker=True,
            )
        self.assertEqual(
            RunLedger(self.store).recover_interrupted(
                PROJECT_ID, occurred_at=NOW, recovery_job_ids={"job.runtime"},
                attest_no_live_worker=True,
            ),
            ["run.receipt.g1"],
        )
        invocation = next(
            item for item in self.store.list_adapter_invocations(PROJECT_ID)
            if item["invocationId"] == "invocation.queued-receipt"
        )
        self.assertEqual(invocation["state"], "imported")
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.receipt.g1"]["status"], "interrupted")
        self.assertEqual(state["agentRuns"]["run.receipt.second.g1"]["status"], "running")
        self.assertEqual(len(state["messages"]), 1)

    async def test_queued_execution_rejects_canonical_write_after_expiry_before_recovery_poll(self):
        coordinator = OrganizationCoordinator(self.store)
        now = [700.0]

        def execute(context):
            now[0] = 711.0
            self.control.open_question({
                "recordType": "human_question", "schemaVersion": "0.1",
                "projectId": PROJECT_ID, "questionId": "question.after-expiry",
                "questionType": "clarification", "priority": "normal", "blocking": True,
                "requestedBy": VP, "workIds": [WORK_ID], "runId": context.run_id,
                "questionText": "Proceed?", "impact": "Must be gated.",
                "options": [], "status": "open", "createdAt": NOW,
            })
            return ExecutionSignal.WAITING_HUMAN

        def resolve(job):
            descriptor = job["payload"]
            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.expiry.g{job['leaseGeneration']}",
                correlation_id=descriptor["correlationId"], actor=descriptor["actor"],
                adapter=descriptor["adapter"], context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"],
                execute=execute, verify=lambda _: True,
            )

        with self.assertRaisesRegex(QueueError, "stale, expired"):
            await coordinator.run_queued(
                self.queue, "worker.expiring", resolve, lease_seconds=10,
                clock=lambda: now[0],
            )
        self.assertNotIn("question.after-expiry", coordinator.projector.replay(PROJECT_ID)["questions"])
        self.assertEqual(self.queue.get("job.runtime")["state"], "leased")
        self.assertIsNone(self.queue.claim_next("worker.recovery", now=711, lease_seconds=10))
        self.assertEqual(self.queue.get("job.runtime")["state"], "recovery_required")

    async def test_expired_active_job_requires_run_recovery_and_explicit_retry(self):
        coordinator = OrganizationCoordinator(self.store)
        ledger = RunLedger(self.store)
        old = self.queue.claim_next("worker.crashed", now=600, lease_seconds=10)
        self.assertEqual(old["leaseGeneration"], 1)
        ledger.start(
            PROJECT_ID, WORK_ID, run_id="run.crashed.g1", agent=SPECIALIST,
            adapter=ADAPTER, context_hash=CONTEXT_HASH, correlation_id="queue.integration",
            occurred_at=NOW,
            queue_lease={
                "jobId": "job.runtime", "workerId": "worker.crashed",
                "leaseToken": old["leaseToken"], "leaseGeneration": 1, "now": 601,
            },
        )
        self.assertIsNone(self.queue.claim_next("worker.replacement", now=611, lease_seconds=10))
        self.assertEqual(self.queue.get("job.runtime")["state"], "recovery_required")
        self.queue.recover_expired(
            "job.runtime", now=612, attest_no_live_worker=True,
        )
        self.assertEqual(self.queue.get("job.runtime")["state"], "recovery_required")

        coordinator.recover_project(PROJECT_ID, occurred_at=NOW)
        coordinator.retry_work(
            PROJECT_ID, WORK_ID, actor=USER, reason="Reviewed the interrupted attempt.", occurred_at=NOW,
        )
        self.assertEqual(
            self.queue.recover_expired("job.runtime", now=613, attest_no_live_worker=True)["state"],
            "queued",
        )

        def resolve(job):
            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.crashed.g{job['leaseGeneration']}",
                correlation_id=job["payload"]["correlationId"], actor=job["payload"]["actor"],
                adapter=job["payload"]["adapter"], context_hash=job["payload"]["contextHash"],
                verification_actor=job["payload"]["verificationActor"],
                execute=lambda _: ExecutionSignal.COMPLETED, verify=lambda _: True,
            )

        outcome = await coordinator.run_queued(
            self.queue, "worker.replacement", resolve,
            lease_seconds=10, clock=lambda: 614,
        )
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        state = coordinator.projector.replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.crashed.g1"]["status"], "interrupted")
        self.assertEqual(state["agentRuns"]["run.crashed.g2"]["status"], "succeeded")

    async def test_killed_worker_process_requires_explicit_run_and_lease_recovery(self):
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        process = context.Process(
            target=_run_queued_job_until_killed,
            args=(str(self.path), ready),
        )
        process.start()
        run_id, error = ready.get(timeout=20)
        self.assertIsNone(error, error)
        self.assertEqual(run_id, "run.crash-process.g1")
        process.terminate()
        process.join(timeout=20)
        self.assertFalse(process.is_alive())
        self.assertNotEqual(process.exitcode, 0)

        # The actual coordinator had dispatched its adapter. After lease expiry,
        # neither lease nor canonical run is silently replayed.
        time.sleep(2.2)
        self.assertIsNone(self.queue.claim_next(
            "worker.observer", now=time.time(), lease_seconds=10,
        ))
        self.assertEqual(self.queue.get("job.runtime")["state"], "recovery_required")
        self.assertEqual(Projector(self.store).replay(PROJECT_ID)["agentRuns"][run_id]["status"], "running")
        self.queue.recover_expired(
            "job.runtime", now=time.time(), attest_no_live_worker=True,
        )
        coordinator = OrganizationCoordinator(self.store)
        self.assertEqual(coordinator.recover_project(PROJECT_ID, occurred_at=NOW), (run_id,))
        coordinator.retry_work(
            PROJECT_ID, WORK_ID, actor=USER, reason="Reviewed the killed worker attempt.", occurred_at=NOW,
        )
        self.queue.recover_expired(
            "job.runtime", now=time.time(), attest_no_live_worker=True,
        )

        def resolve(job):
            descriptor = job["payload"]
            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.crash-process.g{job['leaseGeneration']}",
                correlation_id=descriptor["correlationId"], actor=descriptor["actor"],
                adapter=descriptor["adapter"], context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"],
                execute=lambda _: ExecutionSignal.COMPLETED, verify=lambda _: True,
            )

        outcome = await coordinator.run_queued(
            self.queue, "worker.replacement", resolve,
            lease_seconds=10, clock=time.time,
        )
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        runs = Projector(self.store).replay(PROJECT_ID)["agentRuns"]
        self.assertEqual(runs["run.crash-process.g1"]["status"], "interrupted")
        self.assertEqual(runs["run.crash-process.g2"]["status"], "succeeded")
        self.assertEqual(self.queue.get("job.runtime")["state"], "completed")
        self.assertTrue(self.store.verify(PROJECT_ID))

    async def test_missing_trusted_execution_factory_enters_recovery_not_replay(self):
        coordinator = OrganizationCoordinator(self.store)

        def fail_resolution(_job):
            raise LookupError("trusted adapter registration missing")

        with self.assertRaisesRegex(LookupError, "registration missing"):
            await coordinator.run_queued(
                self.queue, "worker.runtime", fail_resolution,
                lease_seconds=10, clock=lambda: 300,
            )
        self.assertEqual(self.queue.get("job.runtime")["state"], "recovery_required")
        self.assertIsNone(self.queue.claim_next("worker.next", now=301, lease_seconds=10))
