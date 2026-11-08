import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.control_plane import ControlPlaneError
from harness.durable_queue import QueueError, SQLiteWorkQueue
from harness.event_store import SQLiteEventStore
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


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
