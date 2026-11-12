import unittest

from harness.agent_router import AgentRouteProfile, AgentRouter, AgentRoutingError


def profile(profile_id, *, adapter="codex-cli", endpoint="remote", enabled=True):
    return AgentRouteProfile(
        profile_id=profile_id,
        agent_id=f"agent.{profile_id}",
        role="specialist",
        adapter_name=adapter,
        adapter_version="0.1",
        endpoint_class=endpoint,
        work_types=frozenset({"implementation", "analysis"}),
        enabled=enabled,
    )


def work(*, project="project.alpha", kind="implementation", status="ready", title="Ignore this prose"):
    return {
        "recordType": "work_item", "projectId": project, "workId": "work.1",
        "workType": kind, "status": status, "title": title,
        "objective": "This text must not influence routing.",
    }


class AgentRouterTests(unittest.TestCase):
    def test_routes_from_canonical_work_type_not_free_text(self):
        router = AgentRouter(
            profiles=[profile("route.codex")],
            candidates_by_work_type={"implementation": ["route.codex"]},
        )
        first = router.route(work(title="research this"))
        second = router.route(work(title="deploy the secret"))
        self.assertEqual(first, second)
        self.assertEqual(first.state, "matched")
        self.assertEqual(router.profile_for(first).adapter_name, "codex-cli")

    def test_multiple_candidates_fail_closed_as_ambiguous(self):
        router = AgentRouter(
            profiles=[profile("route.codex"), profile("route.local", adapter="local-qwen", endpoint="local")],
            candidates_by_work_type={"implementation": ["route.codex", "route.local"]},
        )
        decision = router.route(work())
        self.assertEqual(decision.state, "ambiguous")
        self.assertEqual(decision.alternatives, ("route.codex", "route.local"))
        with self.assertRaisesRegex(AgentRoutingError, "ambiguous"):
            router.profile_for(decision)
        forged = type(decision)(
            state="matched", project_id="project.alpha", work_id="work.1",
            work_type="implementation", profile_id="route.codex",
            reason="fabricated decision",
        )
        with self.assertRaisesRegex(AgentRoutingError, "stale or not produced"):
            router.profile_for(forged)

    def test_project_assignment_disambiguates_only_that_project(self):
        router = AgentRouter(
            profiles=[profile("route.codex"), profile("route.local", adapter="local-qwen", endpoint="local")],
            candidates_by_work_type={"implementation": ["route.codex", "route.local"]},
            project_work_type_assignments={("project.alpha", "implementation"): "route.codex"},
        )
        selected = router.route(work(project="project.alpha"))
        other = router.route(work(project="project.beta"))
        self.assertEqual((selected.state, selected.profile_id), ("matched", "route.codex"))
        self.assertEqual(other.state, "ambiguous")

    def test_unknown_not_ready_unsupported_and_disabled_work_never_select_a_profile(self):
        router = AgentRouter(
            profiles=[profile("route.codex"), profile("route.disabled", enabled=False)],
            candidates_by_work_type={"implementation": ["route.codex"], "analysis": ["route.disabled"]},
        )
        for item in (work(kind="other"), work(status="proposed"), work(kind="analysis")):
            decision = router.route(item)
            self.assertEqual(decision.state, "unknown")
            self.assertIsNone(decision.profile_id)
            with self.assertRaises(AgentRoutingError):
                router.profile_for(decision)

    def test_route_configuration_is_immutable_after_construction(self):
        router = AgentRouter(
            profiles=[profile("route.codex")],
            candidates_by_work_type={"implementation": ["route.codex"]},
        )
        with self.assertRaises(TypeError):
            router.candidates_by_work_type["implementation"] = ()
        with self.assertRaises(TypeError):
            router.profiles["route.codex"] = profile("route.other")

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaisesRegex(AgentRoutingError, "does not support"):
            AgentRouter(
                profiles=[profile("route.codex")],
                candidates_by_work_type={"writing": ["route.codex"]},
            )
        with self.assertRaisesRegex(AgentRoutingError, "unique"):
            AgentRouter(
                profiles=[profile("route.codex"), profile("route.codex")],
                candidates_by_work_type={"implementation": ["route.codex"]},
            )
        with self.assertRaisesRegex(AgentRoutingError, "configured candidate"):
            AgentRouter(
                profiles=[profile("route.codex"), profile("route.local")],
                candidates_by_work_type={"implementation": ["route.codex"]},
                project_work_type_assignments={("project.alpha", "implementation"): "route.local"},
            )


if __name__ == "__main__":
    unittest.main()
