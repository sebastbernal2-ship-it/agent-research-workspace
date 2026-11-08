import hashlib
import multiprocessing
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.control_plane import ControlPlaneError
from harness.durable_queue import QueueError, SQLiteWorkQueue
from harness.organization_runtime import (
    ExecutionSignal, OrganizationCoordinator, WorkflowStatus, WorkExecution,
)
from harness.event_store import SQLiteEventStore
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


def _claim_in_process(database_path, worker_id, result_queue):
    try:
        queue = SQLiteWorkQueue(SQLiteEventStore(database_path))
        claim = queue.claim_next(worker_id, now=175, lease_seconds=30)
        result_queue.put((worker_id, None if claim is None else claim["leaseGeneration"], None))
    except Exception as exc:  # Report child failure through the parent test process.
        result_queue.put((worker_id, None, repr(exc)))


PROJECT_ID = "project.queue"
WORK_ID = "work.queue"
NOW = "2026-04-16T12:00:00Z"
VP = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}
SPECIALIST = {"actorType": "agent", "actorId": "agent.worker", "role": "specialist"}


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
            job_id, PROJECT_ID, WORK_ID, {"adapter": "offline-test", "version": "1"}, now=100,
        )

    def test_enqueue_is_idempotent_and_one_open_job_per_work(self):
        first = self.enqueue()
        repeated = self.enqueue()
        self.assertEqual(first["jobId"], repeated["jobId"])
        with self.assertRaisesRegex(QueueError, "already has an open scheduled job"):
            self.enqueue("job.duplicate")

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
        run = ledger.start(
            PROJECT_ID, WORK_ID, run_id="run.completion", agent=SPECIALIST,
            adapter={"name": "test", "version": "1"},
            context_hash=hashlib.sha256(b"context").hexdigest(),
            correlation_id="queue.test", occurred_at=NOW,
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
            "job.runtime", PROJECT_ID, WORK_ID, {"executionKey": "offline"}, now=100,
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_coordinator_claims_runs_and_acknowledges_canonical_completion(self):
        def resolve(job):
            self.assertEqual(job["payload"]["executionKey"], "offline")
            return WorkExecution(
                project_id=PROJECT_ID, work_id=WORK_ID,
                run_id=f"run.runtime.g{job['leaseGeneration']}", correlation_id="queue.integration",
                actor=SPECIALIST, adapter={"name": "offline-queue-test", "version": "1"},
                context_hash=hashlib.sha256(b"queue context").hexdigest(),
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
        self.assertEqual(
            self.queue.claim_next("worker.next", now=201, lease_seconds=10), None,
        )

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
                run_id=f"run.queued.g{generation}", correlation_id="queue.resume",
                actor=SPECIALIST, adapter={"name": "offline-queue-test", "version": "1"},
                context_hash=hashlib.sha256(f"attempt-{generation}".encode()).hexdigest(),
                verification_actor=VP, execute=execute, verify=lambda _: True,
            )

        first = await coordinator.run_queued(
            self.queue, "worker.runtime", resolve, lease_seconds=10, clock=lambda: 500,
        )
        self.assertEqual(first.status, WorkflowStatus.WAITING_HUMAN)
        self.assertEqual(self.queue.get("job.runtime")["state"], "queued")
        self.assertIsNone(self.queue.claim_next("worker.next", now=501, lease_seconds=10))

        self.control.answer_question(
            PROJECT_ID, "question.queued", "Proceed.", actor={
                "actorType": "human", "actorId": "user.synthetic", "role": "user",
            },
        )
        second = await coordinator.run_queued(
            self.queue, "worker.next", resolve, lease_seconds=10, clock=lambda: 502,
        )
        self.assertEqual(second.status, WorkflowStatus.COMPLETED)
        self.assertEqual(self.queue.get("job.runtime")["state"], "completed")
        state = coordinator.projector.replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.queued.g1"]["status"], "succeeded")
        self.assertEqual(state["agentRuns"]["run.queued.g2"]["status"], "succeeded")

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
