import hashlib
import tempfile
import unittest
from pathlib import Path

from harness.event_store import SQLiteEventStore
from harness.evidence import EvidenceRegistrar, EvidenceRegistrationError
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


PROJECT_ID = "project.evidence"
WORK_ID = "work.evidence"
NOW = "2026-04-16T12:00:00Z"
VP = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}
SPECIALIST = {"actorType": "agent", "actorId": "agent.researcher", "role": "specialist"}


class EvidenceRegistrarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.path)
        seed_synthetic_project(
            self.store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=NOW,
        )
        self.registrar = EvidenceRegistrar(self.store, clock=lambda: NOW)
        self.base = {
            "project_id": PROJECT_ID,
            "work_id": WORK_ID,
            "evidence_id": "evidence.source-one",
            "claim_text": "The synthetic source supports the stated claim.",
            "stance": "supports",
            "source_uri": "https://example.test/research/source",
            "source_title": "Synthetic source snapshot",
            "source_content": b"Versioned source bytes, frozen at retrieval time.",
            "actor": VP,
            "content_media_type": "text/plain",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_registers_hash_verified_source_snapshot_and_exact_retry(self):
        receipt = self.registrar.register(**self.base)
        self.assertFalse(receipt.duplicate)
        content_hash = hashlib.sha256(self.base["source_content"]).hexdigest()
        self.assertEqual(receipt.record["source"]["contentHash"], content_hash)
        self.assertEqual(self.store.get_blob(content_hash), self.base["source_content"])
        self.assertEqual(receipt.record["source"]["contentMediaType"], "text/plain")

        retry = EvidenceRegistrar(
            self.store, clock=lambda: "2026-04-16T12:05:00Z",
        ).register(**self.base)
        self.assertTrue(retry.duplicate)
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["evidence"]["evidence.source-one"], receipt.record)
        self.assertEqual(len([event for event in self.store.events(PROJECT_ID) if event["eventType"] == "evidence.registered"]), 1)
        self.assertTrue(self.store.verify(PROJECT_ID))

    def test_run_attribution_is_checked_and_run_completion_lists_evidence(self):
        run = RunLedger(self.store, clock=lambda: NOW).start(
            PROJECT_ID, WORK_ID, run_id="run.evidence", agent=SPECIALIST,
            adapter={"name": "evidence-test", "version": "1"},
            context_hash="a" * 64, correlation_id="trace.evidence", occurred_at=NOW,
        )
        receipt = self.registrar.register(
            **{**self.base, "evidence_id": "evidence.run-linked", "actor": SPECIALIST, "run_id": run["runId"]},
        )
        self.assertEqual(receipt.event["correlationId"], "trace.evidence")
        finished = RunLedger(self.store, clock=lambda: NOW).finish(
            PROJECT_ID, run["runId"], terminal_status="succeeded", actor=SPECIALIST, occurred_at=NOW,
        )
        self.assertEqual(finished["outputEvidenceIds"], ["evidence.run-linked"])
        retry = self.registrar.register(
            **{**self.base, "evidence_id": "evidence.run-linked", "actor": SPECIALIST, "run_id": run["runId"]},
        )
        self.assertTrue(retry.duplicate)
        self.assertEqual(Projector(self.store).replay(PROJECT_ID)["agentRuns"][run["runId"]]["status"], "succeeded")

    def test_rejects_unknown_or_misattributed_run_before_append(self):
        with self.assertRaisesRegex(EvidenceRegistrationError, "active assigned run"):
            self.registrar.register(**{**self.base, "run_id": "run.missing"})
        with self.assertRaisesRegex(EvidenceRegistrationError, "active assigned run"):
            self.registrar.register(**{**self.base, "run_id": "run.missing", "actor": SPECIALIST})
        self.assertFalse(any(event["eventType"] == "evidence.registered" for event in self.store.events(PROJECT_ID)))

    def test_rejects_non_http_uri_credentials_fragments_and_oversize_snapshot(self):
        for uri in ("file:///etc/passwd", "ftp://example.test/source", "https://user:pass@example.test/x", "https://example.test/x#fragment"):
            with self.subTest(uri=uri), self.assertRaises(EvidenceRegistrationError):
                self.registrar.register(**{**self.base, "source_uri": uri})
        small_registrar = EvidenceRegistrar(self.store, clock=lambda: NOW, max_source_bytes=8)
        with self.assertRaisesRegex(EvidenceRegistrationError, "1..8 bytes"):
            small_registrar.register(**self.base)
        self.assertFalse(any(event["eventType"] == "evidence.registered" for event in self.store.events(PROJECT_ID)))

    def test_source_registration_cannot_self_assert_review_status(self):
        for status in ("corroborated", "contested", "human_verified"):
            with self.subTest(status=status), self.assertRaisesRegex(
                EvidenceRegistrationError, "must start unreviewed",
            ):
                self.registrar.register(**self.base, review_status=status)
        self.assertFalse(any(
            event["eventType"] == "evidence.registered"
            for event in self.store.events(PROJECT_ID)
        ))

    def test_evidence_identity_cannot_be_reused_for_changed_source_or_claim(self):
        self.registrar.register(**self.base)
        with self.assertRaisesRegex(EvidenceRegistrationError, "different immutable content"):
            self.registrar.register(**{**self.base, "source_content": b"changed source bytes"})
        with self.assertRaisesRegex(EvidenceRegistrationError, "different immutable content"):
            self.registrar.register(**{**self.base, "claim_text": "A different claim."})
        self.assertEqual(len([event for event in self.store.events(PROJECT_ID) if event["eventType"] == "evidence.registered"]), 1)

    def test_reopened_store_preserves_the_registered_snapshot(self):
        receipt = self.registrar.register(**self.base)
        reopened = SQLiteEventStore(self.path)
        evidence = Projector(reopened).replay(PROJECT_ID)["evidence"][receipt.record["evidenceId"]]
        self.assertEqual(reopened.get_blob(evidence["source"]["contentHash"]), self.base["source_content"])
