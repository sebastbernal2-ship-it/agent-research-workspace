import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from harness.capabilities import CapabilityAuthority
from harness.control_plane import ProjectControlPlane
from harness.event_store import SQLiteEventStore
from harness.inbox import InboxForbidden
from harness.project_application import ProjectApplication
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
        self.assertEqual(self.app.list_work(PROJECT_ID)[0]["workId"], WORK_ID)
        with self.assertRaisesRegex(InboxForbidden, "membership"):
            self.app.get_project("project.other")
        self.assertEqual(self.membership.calls[-1][2], "project.read")

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
        self.app.principal_provider = Principal(AGENT)
        with self.assertRaisesRegex(InboxForbidden, "authenticated human"):
            self.app.revise_work(PROJECT_ID, WORK_ID, {"title": "Spoof"}, occurred_at=NOW)
        self.app.principal_provider = Principal(USER)
        with self.assertRaisesRegex(ValueError, "only approve ready work or cancel"):
            self.app.transition_work(
                PROJECT_ID, WORK_ID, "completed", reason="attempted approval", occurred_at=NOW,
            )
        self.assertEqual(len(self.store.events(PROJECT_ID)), 2)
