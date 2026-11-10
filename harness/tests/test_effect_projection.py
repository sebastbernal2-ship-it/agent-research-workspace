import tempfile
import unittest
from pathlib import Path

from harness.capabilities import CapabilityAuthority
from harness.effect_ledger import EffectLedger, EffectLedgerError
from harness.event_store import SQLiteEventStore
from harness.projection import Projector, ProjectionError
from harness.run_ledger import RunLedger
from harness.testing import USER, VP, seed_synthetic_project

NOW = "2026-04-16T12:00:00Z"
PROJECT = "project.effects"
WORK = "work.effect"
RUN = "run.effect"


class EffectProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.path)
        self.control = seed_synthetic_project(
            self.store, project_id=PROJECT, work_id=WORK, occurred_at=NOW,
        )
        self.authority = CapabilityAuthority(self.store, clock=lambda: NOW)
        self.grant = self.authority.grant({
            "recordType": "capability_grant", "schemaVersion": "0.1",
            "projectId": PROJECT, "grantId": "grant.effect.send", "revision": 1,
            "subject": VP, "workIds": [WORK], "capability": "tool.execute",
            "resourceIds": ["resource.team"], "grantedBy": USER,
            "createdAt": NOW, "expiresAt": "2026-04-17T12:00:00Z", "status": "active",
        }, actor=USER)
        self.run = RunLedger(self.store, clock=lambda: NOW).start(
            PROJECT, WORK, run_id=RUN, agent=VP,
            adapter={"name": "synthetic", "version": "1"},
            context_hash="a" * 64, occurred_at=NOW,
        )
        self.request_ref, self.request_hash = self.store.put_blob(b'{"to":"synthetic-recipient"}')
        self.intent = {
            "recordType": "effect_intent", "schemaVersion": "0.2",
            "projectId": PROJECT, "effectId": "effect.1", "workId": WORK,
            "runId": RUN, "adapter": {"name": "synthetic", "version": "1"},
            "principal": VP, "capabilityGrantId": self.grant["grantId"],
            "capabilityRevision": 1, "action": "tool.execute", "resourceId": "resource.team",
            "requestRef": self.request_ref, "requestHash": self.request_hash,
            "provider": "synthetic-provider", "endpoint": "https://provider.example.test/send",
            "idempotencyKey": "provider-key.effect.1", "dataClassification": "internal",
            "createdAt": NOW, "expiresAt": "2026-04-16T13:00:00Z",
            "correlationId": RUN,
        }

    def tearDown(self):
        self.temp.cleanup()

    def append(self, event_type, payload, event_id, *, actor=VP):
        return self.store.append({
            "recordType": "event", "schemaVersion": "0.2", "eventId": event_id,
            "projectId": PROJECT, "eventType": event_type, "actor": actor,
            "occurredAt": NOW, "causationId": None, "correlationId": RUN,
            "idempotencyKey": f"idem.{event_id}", "payload": payload,
        })

    def create_intent(self):
        return EffectLedger(self.store, clock=lambda: NOW).create_intent(
            self.intent, actor=VP, event_id="event.effect.created",
        )

    def approve(self):
        return EffectLedger(self.store, clock=lambda: NOW).transition(
            PROJECT, "effect.1", "approved", actor=USER,
            event_id="event.effect.approved",
        )

    def test_v02_effect_lifecycle_replays_without_mutating_intent(self):
        self.create_intent()
        self.approve()
        receipt_ref, receipt_hash = self.store.put_blob(b"synthetic receipt")
        ledger = EffectLedger(self.store, clock=lambda: NOW)
        ledger.transition(PROJECT, "effect.1", "dispatching", actor=VP, event_id="event.effect.dispatching")
        system = {"actorType": "system", "actorId": "system.reconciler", "role": "system"}
        ledger.transition(PROJECT, "effect.1", "confirmed", actor=system, event_id="event.effect.confirmed", receipt_hash=receipt_hash, receipt_ref=receipt_ref)
        ledger.transition(PROJECT, "effect.1", "recorded", actor=system, event_id="event.effect.recorded")

        state = Projector(self.store).replay(PROJECT)
        effect = state["effects"]["effect.1"]
        self.assertEqual(effect["status"], "recorded")
        self.assertEqual(effect["receiptHash"], receipt_hash)
        self.assertEqual(effect["receiptRef"], receipt_ref)
        self.assertEqual(effect["intent"], self.intent)
        self.assertEqual(len(effect["history"]), 5)
        self.assertEqual(effect["approval"]["actor"], USER)
        self.assertEqual(effect["approval"]["requestHash"], self.request_hash)
        self.assertTrue(self.store.verify(PROJECT))

    def test_effect_cannot_dispatch_before_human_approval(self):
        self.create_intent()
        with self.assertRaisesRegex(EffectLedgerError, "forbidden effect transition"):
            EffectLedger(self.store, clock=lambda: NOW).transition(
                PROJECT, "effect.1", "dispatching", actor=VP,
                event_id="event.effect.dispatching",
            )
        self.assertEqual(Projector(self.store).replay(PROJECT)["effects"]["effect.1"]["status"], "prepared")

    def test_ambiguous_effect_cannot_be_redispatched(self):
        self.create_intent()
        self.approve()
        ledger = EffectLedger(self.store, clock=lambda: NOW)
        ledger.transition(PROJECT, "effect.1", "dispatching", actor=VP, event_id="event.effect.dispatching")
        system = {"actorType": "system", "actorId": "system.recovery", "role": "system"}
        ledger.transition(PROJECT, "effect.1", "ambiguous", actor=system, event_id="event.effect.ambiguous", reason="worker crashed after dispatch marker")
        with self.assertRaisesRegex(EffectLedgerError, "forbidden effect transition"):
            ledger.transition(PROJECT, "effect.1", "dispatching", actor=VP, event_id="event.effect.replay")
        self.assertEqual(Projector(self.store).replay(PROJECT)["effects"]["effect.1"]["status"], "ambiguous")

    def test_provider_without_authoritative_lookup_stops_at_manual_review(self):
        class UnqueryableConnector:
            def __init__(self):
                self.dispatch_count = 0

            def dispatch(self, _key):
                self.dispatch_count += 1

            def lookup(self, _key):
                return None

        self.create_intent()
        self.approve()
        system = {"actorType": "system", "actorId": "system.reconciler", "role": "system"}
        ledger = EffectLedger(self.store, clock=lambda: NOW)
        ledger.transition(
            PROJECT, "effect.1", "dispatching", actor=VP,
            event_id="event.effect.dispatching",
        )
        connector = UnqueryableConnector()
        connector.dispatch(self.intent["idempotencyKey"])
        ledger.transition(
            PROJECT, "effect.1", "ambiguous", actor=system,
            event_id="event.effect.ambiguous", reason="response lost; provider state unknown",
        )
        self.assertIsNone(connector.lookup(self.intent["idempotencyKey"]))
        ledger.transition(
            PROJECT, "effect.1", "manual_review", actor=USER,
            event_id="event.effect.manual-review",
            reason="provider has no authoritative operation lookup",
        )
        self.assertEqual(connector.dispatch_count, 1)
        self.assertEqual(
            Projector(self.store).replay(PROJECT)["effects"]["effect.1"]["status"],
            "manual_review",
        )

    def test_confirmation_requires_exact_content_addressed_receipt(self):
        self.create_intent()
        self.approve()
        ledger = EffectLedger(self.store, clock=lambda: NOW)
        ledger.transition(
            PROJECT, "effect.1", "dispatching", actor=VP,
            event_id="event.effect.dispatching",
        )
        system = {"actorType": "system", "actorId": "system.reconciler", "role": "system"}
        receipt_ref, receipt_hash = self.store.put_blob(b"synthetic receipt")
        with self.assertRaisesRegex(EffectLedgerError, "match its hash"):
            ledger.transition(
                PROJECT, "effect.1", "confirmed", actor=system,
                event_id="event.effect.bad-reference", receipt_hash=receipt_hash,
                receipt_ref="blob." + "0" * 64,
            )
        with self.assertRaisesRegex(EffectLedgerError, "blob is missing"):
            ledger.transition(
                PROJECT, "effect.1", "confirmed", actor=system,
                event_id="event.effect.missing-receipt", receipt_hash="a" * 64,
                receipt_ref="blob." + "a" * 64,
            )
        self.assertEqual(Projector(self.store).replay(PROJECT)["effects"]["effect.1"]["status"], "dispatching")
        self.assertTrue(self.store.get_blob(receipt_hash) == b"synthetic receipt")

    def test_intent_rejects_revoked_or_wrong_scope_grant_on_replay(self):
        revoked = self.authority.revoke(
            PROJECT, self.grant["grantId"], actor=USER, reason="synthetic revoke",
            occurred_at="2026-04-16T12:01:00Z",
        )
        self.assertEqual(revoked["revision"], 2)
        with self.assertRaisesRegex(EffectLedgerError, "active exact-scope capability grant"):
            self.create_intent()
        self.assertNotIn("effect.1", Projector(self.store).replay(PROJECT)["effects"])

    def test_grant_revoked_after_intent_prevents_dispatch(self):
        self.create_intent()
        self.approve()
        self.authority.revoke(
            PROJECT, self.grant["grantId"], actor=USER, reason="revoke before dispatch",
            occurred_at="2026-04-16T12:01:00Z",
        )
        with self.assertRaisesRegex(EffectLedgerError, "current grant"):
            EffectLedger(self.store, clock=lambda: "2026-04-16T12:02:00Z").transition(
                PROJECT, "effect.1", "dispatching", actor=VP,
                event_id="event.effect.dispatching",
            )
        self.assertEqual(Projector(self.store).replay(PROJECT)["effects"]["effect.1"]["status"], "approved")

    def test_synthetic_lost_response_reconciles_by_same_idempotency_key_after_restart(self):
        class FakeConnector:
            def __init__(self):
                self.dispatch_keys = []
                self.receipts = {}

            def dispatch(self, key, _request):
                self.dispatch_keys.append(key)
                self.receipts[key] = b"synthetic provider operation receipt"
                raise TimeoutError("response lost after synthetic provider accepted request")

            def lookup(self, key):
                return self.receipts.get(key)

        self.create_intent()
        self.approve()
        connector = FakeConnector()
        ledger = EffectLedger(self.store, clock=lambda: NOW)
        ledger.transition(
            PROJECT, "effect.1", "dispatching", actor=VP,
            event_id="event.effect.dispatching",
        )
        with self.assertRaises(TimeoutError):
            connector.dispatch("provider-key.effect.1", self.store.get_blob(self.request_hash))
        system = {"actorType": "system", "actorId": "system.reconciler", "role": "system"}
        ledger.transition(
            PROJECT, "effect.1", "ambiguous", actor=system,
            event_id="event.effect.ambiguous", reason="synthetic response lost",
        )

        # A fresh process/store can query the fake provider under the original key;
        # it never redispatches the ambiguous operation.
        reopened = SQLiteEventStore(self.path)
        restarted = EffectLedger(reopened, clock=lambda: NOW)
        receipt = connector.lookup("provider-key.effect.1")
        self.assertEqual(receipt, b"synthetic provider operation receipt")
        receipt_ref, receipt_hash = reopened.put_blob(receipt)
        restarted.transition(
            PROJECT, "effect.1", "confirmed", actor=system,
            event_id="event.effect.confirmed", receipt_hash=receipt_hash,
            receipt_ref=receipt_ref,
        )
        restarted.transition(
            PROJECT, "effect.1", "recorded", actor=system,
            event_id="event.effect.recorded",
        )
        state = Projector(reopened).replay(PROJECT)["effects"]["effect.1"]
        self.assertEqual(state["status"], "recorded")
        self.assertEqual(state["intent"]["idempotencyKey"], "provider-key.effect.1")
        self.assertEqual(connector.dispatch_keys, ["provider-key.effect.1"])
        self.assertTrue(reopened.verify(PROJECT))

    def test_v01_event_vocabulary_remains_accepted_in_mixed_history(self):
        self.create_intent()
        events = self.store.verified_events(PROJECT)
        self.assertEqual(events[0]["schemaVersion"], "0.1")
        self.assertEqual(events[-1]["schemaVersion"], "0.2")


if __name__ == "__main__":
    unittest.main()
