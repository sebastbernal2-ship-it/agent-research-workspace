import asyncio
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

        self.store.append(message("event.queue.output.before-expiry"))
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
        with self.assertRaisesRegex(ConcurrentWriteConflict, "lease was fenced"):
            self.store.append(message("event.queue.output.after-fence"))
        self.assertEqual(len(self.store.events(PROJECT_ID)), 5)

    async def test_expired_active_job_requires_run_recovery_and_explicit_retry(self):
        coordinator = OrganizationCoordinator(self.store)
        ledger = RunLedger(self.store)
        old = self.queue.claim_next("worker.crashed", now=600, lease_seconds=10)
        self.assertEqual(old["leaseGeneration"], 1)
        ledger.start(
            PROJECT_ID, WORK_ID, run_id="run.crashed.g1", agent=SPECIALIST,
            adapter=ADAPTER, context_hash=CONTEXT_HASH, correlation_id="queue.integration",
            occurred_at=NOW,
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
