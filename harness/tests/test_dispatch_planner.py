import tempfile
import unittest
from pathlib import Path

from harness.agent_router import AgentRouteProfile, AgentRouter
from harness.capabilities import CapabilityAuthority
from harness.control_plane import ProjectControlPlane
from harness.dispatch_planner import DispatchPlanner, DispatchPlannerError
from harness.event_store import SQLiteEventStore
from harness.projection import Projector
from harness.testing import USER, VP, seed_synthetic_project

PROJECT = "project.dispatch-plan"
WORK = "work.dispatch"
AGENT = {"actorType": "agent", "actorId": "agent.dispatch", "role": "specialist"}
VERIFIER = USER
ADAPTER = {"name": "codex-cli", "version": "0.1"}


def profile(profile_id):
    return AgentRouteProfile(
        profile_id=profile_id, agent_id=AGENT["actorId"], role=AGENT["role"],
        adapter_name=ADAPTER["name"], adapter_version=ADAPTER["version"],
        endpoint_class="remote", work_types=frozenset({"research"}),
    )


class DispatchPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dispatch-planner-")
        self.store = SQLiteEventStore(Path(self.temp.name) / "canonical.sqlite3")
        self.control = seed_synthetic_project(self.store, project_id=PROJECT, work_id=WORK)
        self.projector = Projector(self.store)
        self.routes = AgentRouter(
            profiles=[profile("route.codex")],
            candidates_by_work_type={"research": ["route.codex"]},
        )

    def tearDown(self):
        self.temp.cleanup()

    def planner(self, *, router=None, max_items=2_000):
        return DispatchPlanner(
            self.projector, router or self.routes,
            lambda work: dict(VERIFIER), max_items=max_items,
        )

    def create_work(self, work_id, *, status="ready", work_type="research", depends_on=None):
        return self.control.create_work({
            "recordType": "work_item", "schemaVersion": "0.1", "projectId": PROJECT,
            "workId": work_id, "revision": 1, "parentWorkId": None,
            "title": f"Synthetic {work_id}", "objective": "Exercise deterministic preflight.",
            "workType": work_type, "status": status, "owner": VP,
            "dependsOn": list(depends_on or []), "capabilityGrantIds": [],
            "acceptanceCriteria": ["No worker starts during planning."],
            "createdBy": USER, "createdAt": "2026-04-16T12:00:00Z",
            "updatedAt": "2026-04-16T12:00:00Z",
        }, actor=USER)

    def test_plans_ready_work_from_one_canonical_snapshot_without_side_effects(self):
        before = len(self.store.events(PROJECT))
        plan = self.planner().plan(PROJECT)
        self.assertEqual(len(self.store.events(PROJECT)), before)
        self.assertEqual(plan.project_id, PROJECT)
        self.assertEqual(plan.event_sequence, before)
        self.assertEqual(len(plan.items), 1)
        item = plan.items[0]
        self.assertTrue(item.preflight_passed)
        self.assertEqual(item.work_id, WORK)
        self.assertEqual(item.profile_id, "route.codex")
        self.assertEqual(len(item.context_hash), 64)
        self.assertEqual(item.blockers, ())

    def test_ambiguous_and_unroutable_items_are_reported_not_selected(self):
        two_routes = AgentRouter(
            profiles=[profile("route.codex"), profile("route.local")],
            candidates_by_work_type={"research": ["route.codex", "route.local"]},
        )
        self.create_work("work.other", work_type="other")
        plan = self.planner(router=two_routes).plan(PROJECT)
        by_id = {item.work_id: item for item in plan.items}
        self.assertEqual(by_id[WORK].route_state, "ambiguous")
        self.assertEqual(by_id[WORK].alternatives, ("route.codex", "route.local"))
        self.assertFalse(by_id[WORK].preflight_passed)
        self.assertIsNone(by_id[WORK].profile_id)
        self.assertEqual(by_id["work.other"].route_state, "unknown")
        self.assertFalse(by_id["work.other"].preflight_passed)

    def test_dependency_and_work_status_blockers_are_explained(self):
        self.create_work("work.dependent", depends_on=[WORK])
        self.create_work("work.proposed", status="proposed")
        plan = self.planner().plan(PROJECT)
        by_id = {item.work_id: item for item in plan.items}
        dependent = by_id["work.dependent"]
        self.assertEqual(dependent.route_state, "matched")
        self.assertFalse(dependent.preflight_passed)
        self.assertIn("incomplete dependency", dependent.blockers[0])
        proposed = by_id["work.proposed"]
        self.assertEqual(proposed.route_state, "unknown")
        self.assertIn("not canonically ready", proposed.blockers[0])

    def test_granted_capability_is_blocked_without_an_adapter_executor(self):
        authority = CapabilityAuthority(self.store, clock=lambda: "2026-04-16T12:00:00Z")
        authority.grant({
            "recordType": "capability_grant", "schemaVersion": "0.1", "projectId": PROJECT,
            "grantId": "grant.dispatch-test", "revision": 1, "subject": AGENT,
            "workIds": [WORK], "capability": "source.fetch",
            "resourceIds": ["resource.synthetic"], "grantedBy": USER,
            "createdAt": "2026-04-16T12:00:00Z", "expiresAt": "2026-04-16T12:10:00Z",
            "status": "active",
        }, actor=USER)
        item = self.planner().plan(PROJECT).items[0]
        self.assertEqual(item.route_state, "matched")
        self.assertFalse(item.preflight_passed)
        self.assertIn("capability executor", item.blockers[0])
        self.assertIsNone(item.context_hash)

    def test_project_work_order_and_item_limit_are_deterministic(self):
        self.create_work("work.z-last")
        self.create_work("work.a-first")
        plan = self.planner().plan(PROJECT)
        self.assertEqual([item.work_id for item in plan.items], ["work.a-first", WORK, "work.z-last"])
        with self.assertRaisesRegex(DispatchPlannerError, "exceeds"):
            self.planner(max_items=2).plan(PROJECT)


if __name__ == "__main__":
    unittest.main()
