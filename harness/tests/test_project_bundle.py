import json
import tempfile
import unittest
from pathlib import Path

from harness.durable_queue import SQLiteWorkQueue
from harness.event_store import EventStoreError, IntegrityFailure, SQLiteEventStore
from harness.evidence import EvidenceRegistrar
from harness.projection import Projector, ProjectionError
from harness.run_ledger import RunLedger
from harness.testing import VP, seed_synthetic_project

PROJECT = "project.portable"
WORK = "work.portable"
NOW = "2026-04-16T12:00:00Z"


class ProjectBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source_path = Path(self.temp.name) / "source.sqlite3"
        self.target_path = Path(self.temp.name) / "target.sqlite3"
        self.source = SQLiteEventStore(self.source_path)
        control = seed_synthetic_project(self.source, project_id=PROJECT, work_id=WORK, occurred_at=NOW)
        control.revise_work(PROJECT, WORK, {"title": "Revised portable work"}, actor=VP, occurred_at=NOW)
        EvidenceRegistrar(self.source, clock=lambda: NOW).register(
            project_id=PROJECT, work_id=WORK, evidence_id="evidence.portable",
            claim_text="The bundled source snapshot is preserved.", stance="supports",
            source_uri="https://example.test/portable", source_title="Portable fixture",
            source_content=b"Content-addressed source bytes in the portable bundle.", actor=VP,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_export_import_replays_identical_project_state_and_blob_closure(self):
        before = Projector(self.source).replay(PROJECT)
        bundle = self.source.export_project_bundle(PROJECT)
        target = SQLiteEventStore(self.target_path)
        imported = target.import_project_bundle(bundle)
        after = Projector(target).replay(PROJECT)
        self.assertEqual(after, before)
        self.assertTrue(target.verify(PROJECT))
        self.assertEqual(
            [result.record_hash for result in imported],
            [row["recordHash"] for row in json.loads(bundle)["events"]],
        )
        self.assertEqual(target.export_project_bundle(PROJECT), bundle)

    def test_tampered_bundle_object_or_event_chain_is_rejected(self):
        bundle = json.loads(self.source.export_project_bundle(PROJECT))
        tampered_object = json.loads(json.dumps(bundle))
        tampered_object["objects"][0]["data"] = tampered_object["objects"][0]["data"][:-4] + "AAAA"
        with self.assertRaises(IntegrityFailure):
            SQLiteEventStore(Path(self.temp.name) / "bad-object.sqlite3").import_project_bundle(
                json.dumps(tampered_object),
            )
        tampered_event = json.loads(json.dumps(bundle))
        tampered_event["events"][0]["event"]["payload"]["name"] = "forged"
        with self.assertRaises(IntegrityFailure):
            SQLiteEventStore(Path(self.temp.name) / "bad-event.sqlite3").import_project_bundle(
                json.dumps(tampered_event),
            )

        semantically_invalid = json.loads(json.dumps(bundle))
        semantically_invalid["events"][0]["event"]["payload"]["name"] = "Not the immutable project record"
        previous = "0" * 64
        for row in semantically_invalid["events"]:
            row["previousHash"] = previous
            event_json = SQLiteEventStore._canonical_json(row["event"])
            row["recordHash"] = SQLiteEventStore._record_hash(previous, event_json)
            previous = row["recordHash"]
        semantic_target = SQLiteEventStore(Path(self.temp.name) / "bad-projection.sqlite3")
        with self.assertRaises(ProjectionError):
            semantic_target.import_project_bundle(json.dumps(semantically_invalid))
        self.assertEqual(semantic_target.events(PROJECT), [])

    def test_import_rebuilds_historical_queue_run_fence_without_live_lease(self):
        queue = SQLiteWorkQueue(self.source)
        agent = {"actorType": "agent", "actorId": "agent.portable", "role": "specialist"}
        queue.enqueue("job.portable", PROJECT, WORK, {
            "descriptorVersion": 1, "executionKey": "offline", "actor": agent,
            "verificationActor": VP, "adapter": {"name": "portable", "version": "1"},
            "contextHash": "c" * 64, "correlationId": "trace.portable",
        }, now=100)
        claim = queue.claim_next("worker.portable", now=101, lease_seconds=30)
        lease = {
            "jobId": claim["jobId"], "workerId": claim["leaseOwner"],
            "leaseToken": claim["leaseToken"], "leaseGeneration": claim["leaseGeneration"],
            "now": 102,
        }
        with self.source.queued_write_context(lease, lambda: 102):
            RunLedger(self.source, clock=lambda: NOW).start(
                PROJECT, WORK, run_id="run.portable", agent=agent,
                adapter={"name": "portable", "version": "1"}, context_hash="c" * 64,
                correlation_id="trace.portable", occurred_at=NOW, queue_lease=lease,
            )
        target = SQLiteEventStore(self.target_path)
        target.import_project_bundle(self.source.export_project_bundle(PROJECT))
        self.assertEqual(Projector(target).replay(PROJECT), Projector(self.source).replay(PROJECT))
        target.backup_to(Path(self.temp.name) / "queue-import-backup.sqlite3")

    def test_import_is_only_allowed_into_an_empty_project(self):
        target = SQLiteEventStore(self.target_path)
        bundle = self.source.export_project_bundle(PROJECT)
        target.import_project_bundle(bundle)
        with self.assertRaisesRegex(EventStoreError, "empty destination"):
            target.import_project_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
