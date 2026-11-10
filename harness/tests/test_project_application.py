import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from harness.capabilities import CapabilityAuthority
from harness.control_plane import ProjectControlPlane
from harness.event_store import ConcurrentWriteConflict, SQLiteEventStore
from harness.inbox import InboxForbidden
from harness.project_application import ProjectApplication
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


PROJECT_ID = "project.auth-boundary"
WORK_ID = "work.auth-boundary"
NOW = "2026-04-16T12:00:00Z"
USER = {"actorType": "human", "actorId": "user.authenticated", "role": "user"}
AGENT = {"actorType": "agent", "actorId": "agent.spoof", "role": "vp"}


class Principal:
    def __init__(self, actor):
        self.actor = actor

    def current_actor(self):
        return self.actor


class Membership:
    def __init__(self, allowed_projects):
        self.allowed_projects = set(allowed_projects)
        self.calls = []

    def require_project_access(self, actor, project_id, action):
        self.calls.append((actor, project_id, action))
        if project_id not in self.allowed_projects:
            raise InboxForbidden("project membership is required")


class ProjectApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteEventStore(Path(self.temp.name) / "project.sqlite3")
        self.control = seed_synthetic_project(
            self.store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=NOW,
        )
        self.membership = Membership({PROJECT_ID})
        self.app = ProjectApplication(
            self.control, Principal(USER), self.membership,
            CapabilityAuthority(self.store, clock=lambda: NOW),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_project_reads_and_commands_require_membership(self):
        self.assertEqual(self.app.get_project(PROJECT_ID)["projectId"], PROJECT_ID)
        state, events = self.app.get_project_snapshot(PROJECT_ID)
        self.assertEqual(state["project"]["projectId"], PROJECT_ID)
        self.assertEqual(state["_eventSequence"], len(events))
        self.assertEqual(self.app.list_work(PROJECT_ID)[0]["workId"], WORK_ID)
        with self.assertRaisesRegex(InboxForbidden, "membership"):
            self.app.get_project("project.other")
        self.assertEqual(self.membership.calls[-1][2], "project.read")

    def test_project_scoped_reads_use_distinct_authorization_actions(self):
        self.assertEqual(self.app.list_agent_runs(PROJECT_ID), [])
        self.assertEqual(self.app.list_evidence(PROJECT_ID), [])
        self.assertEqual(self.app.list_artifacts(PROJECT_ID), [])
        self.assertEqual(self.app.list_messages(PROJECT_ID), [])
        self.assertEqual(self.app.list_capability_grants(PROJECT_ID), [])
        self.assertEqual(
            [call[2] for call in self.membership.calls],
            ["run.read", "evidence.read", "artifact.read", "discussion.read", "capability.read"],
        )
        self.membership.allowed_projects.clear()
        with self.assertRaisesRegex(InboxForbidden, "membership"):
            self.app.list_evidence(PROJECT_ID)

    def create_effect(self):
        self.app.effects.clock = lambda: NOW
        grant = self.app.capabilities.grant({
            "recordType": "capability_grant", "schemaVersion": "0.1",
            "projectId": PROJECT_ID, "grantId": "grant.effect.application", "revision": 1,
            "subject": AGENT, "workIds": [WORK_ID], "capability": "tool.execute",
            "resourceIds": ["resource.application"], "grantedBy": USER,
            "createdAt": NOW, "expiresAt": "2026-04-17T12:00:00Z", "status": "active",
        }, actor=USER)
        run = RunLedger(self.store, clock=lambda: NOW).start(
            PROJECT_ID, WORK_ID, run_id="run.effect.application", agent=AGENT,
            adapter={"name": "test", "version": "1"}, context_hash="b" * 64,
            occurred_at=NOW,
        )
        request_ref, request_hash = self.store.put_blob(b"approved payload")
        intent = {
            "recordType": "effect_intent", "schemaVersion": "0.2",
            "projectId": PROJECT_ID, "effectId": "effect.application", "workId": WORK_ID,
            "runId": run["runId"], "adapter": {"name": "test", "version": "1"},
            "principal": AGENT, "capabilityGrantId": grant["grantId"],
            "capabilityRevision": 1, "action": "tool.execute", "resourceId": "resource.application",
            "requestRef": request_ref, "requestHash": request_hash,
            "provider": "synthetic", "endpoint": "https://provider.example.test/effect",
            "idempotencyKey": "provider-key.application", "dataClassification": "internal",
            "createdAt": NOW, "expiresAt": "2026-04-16T13:00:00Z",
            "correlationId": run["runId"],
        }
        self.app.effects.create_intent(intent, actor=AGENT, event_id="event.effect.application.created")

    def test_effect_approval_is_authorized_server_side_and_sequence_fenced(self):
        self.create_effect()
        sequence = self.app.projector.replay(PROJECT_ID)["_eventSequence"]
        self.membership.allowed_projects.clear()
        before = len(self.store.events(PROJECT_ID))
        with self.assertRaisesRegex(InboxForbidden, "membership"):
            self.app.approve_effect(PROJECT_ID, "effect.application", expected_sequence=sequence)
        self.assertEqual(len(self.store.events(PROJECT_ID)), before)
        with self.assertRaises(TypeError):
            self.app.approve_effect(
                PROJECT_ID, "effect.application", expected_sequence=sequence,
                actor=AGENT,
            )
        self.assertEqual(len(self.store.events(PROJECT_ID)), before)

        self.membership.allowed_projects.add(PROJECT_ID)
        with self.assertRaisesRegex(ConcurrentWriteConflict, "stale project sequence"):
            self.app.approve_effect(PROJECT_ID, "effect.application", expected_sequence=sequence - 1)
        approved = self.app.approve_effect(
            PROJECT_ID, "effect.application", expected_sequence=sequence,
        )
        self.assertEqual(approved["approval"]["actor"], USER)
        self.assertEqual(approved["approval"]["requestHash"], approved["intent"]["requestHash"])
        self.assertEqual(self.membership.calls[-1][2], "effect.approve")

    def test_work_approval_binds_to_current_revision_and_rejects_replay(self):
        record = deepcopy(self.control.projector.replay(PROJECT_ID)["workItems"][WORK_ID])
        record.update(
            workId="work.needs-approval", title="Approval-bound work", revision=1,
            status="proposed", createdAt=NOW, updatedAt=NOW,
        )
        self.app.create_work(PROJECT_ID, record)
        approved = self.app.transition_work(
            PROJECT_ID, "work.needs-approval", "ready", reason="Approved exact revision.",
            expected_revision=1, occurred_at=NOW,
        )
        self.assertEqual(approved["status"], "ready")
        approval_event = self.store.events(PROJECT_ID)[-1]
        self.assertEqual(approval_event["actor"], USER)
        self.assertEqual(approval_event["payload"]["from"], "proposed")
        with self.assertRaisesRegex(ConcurrentWriteConflict, "stale revision"):
            self.app.transition_work(
                PROJECT_ID, "work.needs-approval", "ready", reason="Replay.",
                expected_revision=1, occurred_at=NOW,
            )
        with self.assertRaisesRegex(ConcurrentWriteConflict, "stale revision"):
            self.control.transition_work(
                PROJECT_ID, "work.needs-approval", "ready", actor=USER,
                expected_revision=1, occurred_at=NOW,
            )

    def test_work_creation_derives_creator_from_authenticated_principal(self):
        record = deepcopy(self.control.projector.replay(PROJECT_ID)["workItems"][WORK_ID])
        record.update(
            workId="work.created-through-application", title="Authenticated work",
            revision=1, status="ready", createdBy=AGENT,
        )
        created = self.app.create_work(PROJECT_ID, record)
        self.assertEqual(created["createdBy"], USER)
        event = self.store.events(PROJECT_ID)[-1]
        self.assertEqual(event["eventType"], "work.created")
        self.assertEqual(event["actor"], USER)
        self.assertEqual(self.membership.calls[-1][2], "work.create")

    def test_command_actor_comes_from_injected_principal(self):
        revised = self.app.revise_work(
            PROJECT_ID, WORK_ID, {"title": "Authorized revision"}, occurred_at=NOW,
        )
        self.assertEqual(revised["title"], "Authorized revision")
        event = self.store.events(PROJECT_ID)[-1]
        self.assertEqual(event["eventType"], "work.revised")
        self.assertEqual(event["actor"], USER)
        self.assertEqual(self.membership.calls[-1][2], "work.revise")

    def test_capability_approval_derives_authenticated_actor(self):
        grant = self.app.grant_capability(PROJECT_ID, {
            "recordType": "capability_grant", "schemaVersion": "0.1",
            "projectId": PROJECT_ID, "grantId": "grant.application",
            "revision": 1,
            "subject": {"actorType": "agent", "actorId": "agent.researcher", "role": "specialist"},
            "workIds": [WORK_ID], "capability": "source.fetch",
            "resourceIds": ["resource.public"], "grantedBy": AGENT,
            "createdAt": NOW, "expiresAt": "2026-04-17T12:00:00Z", "status": "active",
        })
        self.assertEqual(grant["grantedBy"], USER)
        revoked = self.app.revoke_capability(
            PROJECT_ID, "grant.application", reason="Scope no longer needed.", occurred_at=NOW,
        )
        self.assertEqual(revoked["revokedBy"], USER)
        self.assertEqual(self.membership.calls[-2][2], "capability.grant")
        self.assertEqual(self.membership.calls[-1][2], "capability.revoke")

    def test_agent_principal_and_unapproved_transitions_fail_closed(self):
        for malformed in (
            AGENT, None,
            {"actorType": "human", "role": "user"},
            {"actorType": "human", "actorId": "", "role": "user"},
        ):
            self.app.principal_provider = Principal(malformed)
            with self.assertRaisesRegex(InboxForbidden, "authenticated human"):
                self.app.revise_work(PROJECT_ID, WORK_ID, {"title": "Spoof"}, occurred_at=NOW)
        self.app.principal_provider = Principal(USER)
        with self.assertRaisesRegex(ValueError, "only approve ready work or cancel"):
            self.app.transition_work(
                PROJECT_ID, WORK_ID, "completed", reason="attempted approval",
                expected_revision=1, occurred_at=NOW,
            )
        self.assertEqual(len(self.store.events(PROJECT_ID)), 2)
