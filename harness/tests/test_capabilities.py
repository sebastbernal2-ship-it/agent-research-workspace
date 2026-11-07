import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.capabilities import CapabilityAuthority, CapabilityDenied
from harness.control_plane import ControlPlaneError, ProjectControlPlane
from harness.event_store import EventStoreError, SQLiteEventStore
from harness.projection import Projector, ProjectionError
from harness.testing import USER, seed_synthetic_project


NOW = "2026-04-16T12:00:00Z"
LATER = "2026-04-16T12:05:00Z"
EXPIRY = "2026-04-16T12:10:00Z"
SUBJECT = {"actorType": "agent", "actorId": "agent.reader", "role": "specialist"}
OTHER_SUBJECT = {"actorType": "agent", "actorId": "agent.other", "role": "reviewer"}
NON_USER = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}


def grant_record(**changes):
    record = {
        "recordType": "capability_grant",
        "schemaVersion": "0.1",
        "projectId": "project.capability",
        "grantId": "grant.read-corpus",
        "revision": 1,
        "subject": SUBJECT,
        "workIds": ["work.scoped"],
        "capability": "source.fetch",
        "resourceIds": ["resource.public-domain"],
        "grantedBy": USER,
        "createdAt": NOW,
        "expiresAt": EXPIRY,
        "status": "active",
    }
    record.update(changes)
    return record


class CapabilityAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.path)
        seed_synthetic_project(self.store, project_id="project.capability", work_id="work.scoped")
        seed_synthetic_project(self.store, project_id="project.other", work_id="work.other")
        self.now = NOW
        self.authority = CapabilityAuthority(self.store, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_grant_is_exactly_scoped_to_project_work_agent_action_resource_and_time(self):
        grant = self.authority.grant(grant_record(), actor=USER)
        authorized = self.authority.authorize(
            "project.capability", "work.scoped", subject=SUBJECT,
            capability="source.fetch", resource_id="resource.public-domain",
        )
        self.assertEqual(authorized["grantId"], grant["grantId"])
        work = Projector(self.store).replay("project.capability")["workItems"]["work.scoped"]
        self.assertEqual(work["capabilityGrantIds"], ["grant.read-corpus"])

        for kwargs in [
            {"project_id": "project.other", "work_id": "work.other", "subject": SUBJECT, "capability": "source.fetch", "resource_id": "resource.public-domain"},
            {"project_id": "project.capability", "work_id": "work.scoped", "subject": OTHER_SUBJECT, "capability": "source.fetch", "resource_id": "resource.public-domain"},
            {"project_id": "project.capability", "work_id": "work.scoped", "subject": SUBJECT, "capability": "filesystem.write", "resource_id": "resource.public-domain"},
            {"project_id": "project.capability", "work_id": "work.scoped", "subject": SUBJECT, "capability": "source.fetch", "resource_id": "resource.private"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(CapabilityDenied):
                self.authority.authorize(**kwargs)

        self.now = EXPIRY
        with self.assertRaisesRegex(CapabilityDenied, "no active"):
            self.authority.authorize(
                "project.capability", "work.scoped", subject=SUBJECT,
                capability="source.fetch", resource_id="resource.public-domain",
            )

    def test_capability_authorization_waits_for_explicit_dependencies(self):
        control = ProjectControlPlane(self.store, clock=lambda: NOW)
        control.create_work({
            "recordType": "work_item", "schemaVersion": "0.1",
            "projectId": "project.capability", "workId": "work.prerequisite",
            "revision": 1, "parentWorkId": None, "title": "Prerequisite",
            "objective": "Complete prerequisite work.", "workType": "research",
            "status": "ready", "owner": {"actorType": "agent", "actorId": "agent.vp", "role": "vp"},
            "dependsOn": [], "capabilityGrantIds": [],
            "acceptanceCriteria": ["Complete prerequisite"], "createdBy": USER,
            "createdAt": NOW, "updatedAt": NOW,
        })
        control.revise_work(
            "project.capability", "work.scoped", {"dependsOn": ["work.prerequisite"]},
            actor={"actorType": "agent", "actorId": "agent.vp", "role": "vp"},
        )
        self.authority.grant(grant_record(), actor=USER)
        with self.assertRaisesRegex(CapabilityDenied, "dependencies"):
            self.authority.authorize(
                "project.capability", "work.scoped", subject=SUBJECT,
                capability="source.fetch", resource_id="resource.public-domain",
            )
        control.transition_work("project.capability", "work.prerequisite", "running", actor={"actorType": "agent", "actorId": "agent.vp", "role": "vp"})
        control.transition_work("project.capability", "work.prerequisite", "verifying", actor={"actorType": "agent", "actorId": "agent.vp", "role": "vp"})
        control.transition_work("project.capability", "work.prerequisite", "completed", actor={"actorType": "agent", "actorId": "agent.vp", "role": "vp"})
        self.assertEqual(
            self.authority.authorize(
                "project.capability", "work.scoped", subject=SUBJECT,
                capability="source.fetch", resource_id="resource.public-domain",
            )["grantId"],
            "grant.read-corpus",
        )

    def test_only_user_can_grant_or_revoke_and_revocation_is_durable(self):
        with self.assertRaisesRegex(ControlPlaneError, "only a user actor"):
            self.authority.grant(grant_record(), actor=NON_USER)
        self.authority.grant(grant_record(), actor=USER)
        with self.assertRaisesRegex(ControlPlaneError, "only a user actor"):
            self.authority.revoke(
                "project.capability", "grant.read-corpus", actor=NON_USER,
                reason="not authorized",
            )

        revoked = self.authority.revoke(
            "project.capability", "grant.read-corpus", actor=USER,
            reason="Work completed.", occurred_at=LATER,
        )
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(
            Projector(self.store).replay("project.capability")["workItems"]["work.scoped"]["capabilityGrantIds"],
            ["grant.read-corpus"],
        )
        retry = self.authority.revoke(
            "project.capability", "grant.read-corpus", actor=USER,
            reason="Work completed.", occurred_at="2026-04-16T12:06:00Z",
        )
        self.assertEqual(retry, revoked)
        with self.assertRaises(CapabilityDenied):
            self.authority.authorize(
                "project.capability", "work.scoped", subject=SUBJECT,
                capability="source.fetch", resource_id="resource.public-domain",
                at=LATER,
            )
        reopened = Projector(SQLiteEventStore(self.path)).replay("project.capability")
        self.assertEqual(reopened["capabilityGrants"]["grant.read-corpus"]["status"], "revoked")
        self.assertTrue(self.store.verify("project.capability"))

    def test_revocation_time_cannot_precede_grant_creation(self):
        self.authority.grant(grant_record(), actor=USER)
        with self.assertRaisesRegex(ControlPlaneError, "precede grant creation"):
            self.authority.revoke(
                "project.capability", "grant.read-corpus", actor=USER,
                reason="bad timestamp", occurred_at="2026-04-16T11:59:59Z",
            )
        self.assertEqual(
            Projector(self.store).replay("project.capability")["capabilityGrants"]["grant.read-corpus"]["status"],
            "active",
        )

    def test_unknown_work_and_non_runnable_work_cannot_use_grant(self):
        self.authority.grant(grant_record(), actor=USER)
        with self.assertRaisesRegex(CapabilityDenied, "unknown or non-runnable"):
            self.authority.authorize(
                "project.capability", "work.missing", subject=SUBJECT,
                capability="source.fetch", resource_id="resource.public-domain",
            )
        self.project_control = ProjectControlPlane(self.store, clock=lambda: LATER)
        self.project_control.transition_work(
            "project.capability", "work.scoped", "cancelled", actor=USER,
        )
        with self.assertRaisesRegex(CapabilityDenied, "unknown or non-runnable"):
            self.authority.authorize(
                "project.capability", "work.scoped", subject=SUBJECT,
                capability="source.fetch", resource_id="resource.public-domain",
            )

    def test_grant_and_work_reference_commit_atomically(self):
        with patch.object(self.store, "append_many", side_effect=EventStoreError("injected commit failure")):
            with self.assertRaisesRegex(EventStoreError, "injected"):
                self.authority.grant(grant_record(), actor=USER)
        state = Projector(self.store).replay("project.capability")
        self.assertEqual(state["capabilityGrants"], {})
        self.assertEqual(state["workItems"]["work.scoped"]["capabilityGrantIds"], [])

    def test_long_lived_grant_is_denied(self):
        with self.assertRaisesRegex(ControlPlaneError, "seven-day"):
            self.authority.grant(
                grant_record(expiresAt="2027-04-16T12:00:00Z"), actor=USER,
            )
        self.assertEqual(Projector(self.store).replay("project.capability")["capabilityGrants"], {})

    def test_forged_agent_grant_event_is_rejected_during_replay(self):
        grant = grant_record()
        record_hash = self.store.put_record(grant)
        self.store.append({
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": "event.forged-grant",
            "projectId": "project.capability",
            "eventType": "capability.granted",
            "actor": NON_USER,
            "occurredAt": NOW,
            "causationId": None,
            "correlationId": "project.capability",
            "idempotencyKey": "idem.forged-grant",
            "payload": {
                "grantId": grant["grantId"], "workIds": grant["workIds"],
                "capability": grant["capability"], "recordHash": record_hash,
            },
        })
        with self.assertRaisesRegex(ProjectionError, "authority"):
            Projector(self.store).replay("project.capability")

    def test_cross_project_grant_reference_is_rejected(self):
        with self.assertRaisesRegex(ControlPlaneError, "unknown work"):
            self.authority.grant(grant_record(workIds=["work.other"]), actor=USER)
        self.assertEqual(Projector(self.store).replay("project.capability")["capabilityGrants"], {})


if __name__ == "__main__":
    unittest.main()
