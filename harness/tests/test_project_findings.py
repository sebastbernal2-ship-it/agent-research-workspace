import tempfile
import unittest
from pathlib import Path

from harness.event_store import SQLiteEventStore
from harness.control_plane import ProjectControlPlane
from harness.evidence import EvidenceRegistrar
from harness.projection import ProjectionError, Projector
from harness.run_ledger import RunLedger
from harness.testing import USER, seed_synthetic_project

PROJECT = "project.findings"
WORK = "work.findings"
NOW = "2026-04-16T12:00:00Z"
AGENT = {"actorType": "agent", "actorId": "agent.findings", "role": "specialist"}


class FindingAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteEventStore(Path(self.temp.name) / "canonical.sqlite3")
        seed_synthetic_project(self.store, project_id=PROJECT, work_id=WORK, occurred_at=NOW)
        self.run = RunLedger(self.store, clock=lambda: NOW).start(
            PROJECT, WORK, run_id="run.findings", agent=AGENT,
            adapter={"name": "synthetic", "version": "1"},
            context_hash="a" * 64, occurred_at=NOW,
        )
        source = b"A frozen synthetic source snapshot."
        self.evidence = EvidenceRegistrar(self.store, clock=lambda: NOW).register(
            project_id=PROJECT, work_id=WORK, evidence_id="evidence.findings",
            claim_text="The source supports the stated synthetic finding.", stance="supports",
            source_uri="https://example.test/source", source_title="Synthetic source",
            source_content=source, actor=AGENT, run_id=self.run["runId"],
        ).record

    def tearDown(self):
        self.temp.cleanup()

    def append_record_event(self, event_type, event_id, actor, payload, record):
        record_hash = self.store.put_record(record)
        self.store.append({
            "recordType": "event", "schemaVersion": "0.2", "eventId": event_id,
            "projectId": PROJECT, "eventType": event_type, "actor": actor,
            "occurredAt": NOW, "causationId": None, "correlationId": "run.findings",
            "idempotencyKey": f"idem.{event_id}",
            "payload": {**payload, "recordHash": record_hash},
        })
        return record_hash

    def test_v02_project_visibility_and_policy_scope_survive_charter_replay(self):
        project = {
            "recordType": "project", "schemaVersion": "0.2", "projectId": "project.scoped",
            "revision": 1, "name": "Scoped synthetic project", "status": "active",
            "visibility": "private", "policyScope": ["policy.research.private"],
            "charter": {"revision": 1, "goals": ["Test scope"], "nonGoals": [], "acceptanceCriteria": ["Keep scope stable"]},
            "createdBy": USER, "createdAt": NOW, "updatedAt": NOW,
        }
        control = ProjectControlPlane(self.store, clock=lambda: NOW)
        control.create_project(project)
        revised = control.revise_project_charter(
            "project.scoped", {"revision": 2, "goals": ["Revised goal"], "nonGoals": [], "acceptanceCriteria": ["Keep scope stable"]},
            actor=USER, occurred_at=NOW,
        )
        self.assertEqual(revised["visibility"], "private")
        self.assertEqual(revised["policyScope"], ["policy.research.private"])
        self.assertEqual(Projector(self.store).replay("project.scoped")["project"], revised)

    def test_finding_cannot_claim_a_hash_unrelated_to_linked_evidence(self):
        finding = {
            "recordType": "finding", "schemaVersion": "0.2", "projectId": PROJECT,
            "findingId": "finding.forged-source", "workId": WORK, "runId": self.run["runId"],
            "title": "Forged source link", "statement": "The source hash must match evidence.",
            "sourceEvidenceIds": [self.evidence["evidenceId"]], "sourceHashes": ["f" * 64],
            "createdBy": AGENT, "createdAt": NOW,
        }
        self.append_record_event(
            "finding.recorded", "event.finding.forged", AGENT,
            {"findingId": finding["findingId"], "workId": WORK}, finding,
        )
        with self.assertRaisesRegex(ProjectionError, "source hashes do not match"):
            Projector(self.store).replay(PROJECT)

    def test_finding_and_adoption_provenance_replays_from_immutable_records(self):
        source_hash = self.evidence["source"]["contentHash"]
        finding = {
            "recordType": "finding", "schemaVersion": "0.2", "projectId": PROJECT,
            "findingId": "finding.source-support", "workId": WORK, "runId": self.run["runId"],
            "title": "Source supports the hypothesis",
            "statement": "The frozen source supports the bounded research claim.",
            "sourceEvidenceIds": [self.evidence["evidenceId"]], "sourceHashes": [source_hash],
            "createdBy": AGENT, "createdAt": NOW,
        }
        finding_hash = self.append_record_event(
            "finding.recorded", "event.finding.recorded", AGENT,
            {"findingId": finding["findingId"], "workId": WORK}, finding,
        )
        adoption = {
            "recordType": "adoption_decision", "schemaVersion": "0.2", "projectId": PROJECT,
            "adoptionId": "adoption.finding", "workId": WORK,
            "findingId": finding["findingId"], "findingHash": finding_hash,
            "decision": "adopted", "rationale": "Accepted for the synthetic project after review.",
            "decidedBy": USER, "decidedAt": NOW, "resultArtifactIds": [],
        }
        adoption_hash = self.append_record_event(
            "adoption.decided", "event.adoption.decided", USER,
            {"adoptionId": adoption["adoptionId"], "findingId": finding["findingId"], "workId": WORK},
            adoption,
        )
        state = Projector(self.store).replay(PROJECT)
        self.assertEqual(state["findings"][finding["findingId"]], {"record": finding, "recordHash": finding_hash})
        self.assertEqual(state["adoptionDecisions"][adoption["adoptionId"]], {"record": adoption, "recordHash": adoption_hash})
        self.assertEqual(state["findings"][finding["findingId"]]["record"]["sourceHashes"], [source_hash])
        self.assertTrue(self.store.verify(PROJECT))
        roundtrip = SQLiteEventStore(Path(self.temp.name) / "roundtrip.sqlite3")
        roundtrip.import_project_bundle(self.store.export_project_bundle(PROJECT))
        self.assertEqual(Projector(roundtrip).replay(PROJECT), state)


if __name__ == "__main__":
    unittest.main()
