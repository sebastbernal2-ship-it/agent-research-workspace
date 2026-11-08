import unittest

from harness.model_routing import (
    ChatMessage,
    DeterministicMockProvider,
    ModelProfile,
    ModelRouter,
    ModelRoutingError,
    ModelTask,
    ProviderResponse,
)


class AllowAllTestPolicy:
    def require_invocation(self, task, profile):
        return None


class DenyRemoteTestPolicy:
    def require_invocation(self, task, profile):
        if profile.endpoint_class == "remote":
            raise PermissionError("remote inference is not approved")


class WrongModelProvider:
    provider_id = "remote"

    def complete(self, profile, task):
        return ProviderResponse(model_id="unexpected-model", text="no", finish_reason="stop")


class ModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.local = DeterministicMockProvider("local", "local mock")
        self.remote = DeterministicMockProvider("remote", "remote mock")
        self.local_profile = ModelProfile(
            profile_id="profile.local.qwen",
            provider_id="local",
            model_id="qwen.synthetic-v1",
            endpoint_class="local",
            allowed_roles=frozenset({"vp", "orchestrator"}),
            max_input_chars=100,
            max_response_chars=100,
        )
        self.remote_profile = ModelProfile(
            profile_id="profile.remote.deepseek",
            provider_id="remote",
            model_id="deepseek.synthetic-v1",
            endpoint_class="remote",
            allowed_roles=frozenset({"orchestrator", "specialist"}),
            max_input_chars=100,
            max_response_chars=100,
        )
        self.router = ModelRouter(
            profiles=[self.local_profile, self.remote_profile],
            providers=[self.local, self.remote],
            role_defaults={"vp": "profile.local.qwen", "orchestrator": "profile.remote.deepseek"},
            project_role_overrides={("project.private", "orchestrator"): "profile.local.qwen"},
            invocation_policy=AllowAllTestPolicy(),
        )

    @staticmethod
    def task(*, project_id="project.default", role="orchestrator", content="Review this evidence."):
        return ModelTask(
            project_id=project_id,
            work_id="work.1",
            run_id="run.1",
            agent_id="agent.1",
            role=role,
            messages=(ChatMessage(role="user", content=content),),
        )

    def test_selects_by_role_and_project_override_and_records_actual_route(self):
        vp = self.router.complete(self.task(role="vp"))
        orchestrator = self.router.complete(self.task())
        private_override = self.router.complete(self.task(project_id="project.private"))
        self.assertEqual((vp.provider_id, vp.model_id), ("local", "qwen.synthetic-v1"))
        self.assertEqual((orchestrator.provider_id, orchestrator.model_id), ("remote", "deepseek.synthetic-v1"))
        self.assertEqual(private_override.profile_id, "profile.local.qwen")
        self.assertEqual(self.local.calls, [("profile.local.qwen", "run.1"), ("profile.local.qwen", "run.1")])
        self.assertEqual(self.remote.calls, [("profile.remote.deepseek", "run.1")])

    def test_missing_route_fails_closed(self):
        with self.assertRaisesRegex(ModelRoutingError, "no model profile configured"):
            self.router.complete(self.task(role="reviewer"))

    def test_policy_denial_happens_before_remote_provider_call(self):
        router = ModelRouter(
            profiles=[self.remote_profile],
            providers=[self.remote],
            role_defaults={"orchestrator": "profile.remote.deepseek"},
            invocation_policy=DenyRemoteTestPolicy(),
        )
        with self.assertRaisesRegex(PermissionError, "not approved"):
            router.complete(self.task())
        self.assertEqual(self.remote.calls, [])

    def test_input_and_provider_response_are_bounded_and_identity_checked(self):
        with self.assertRaisesRegex(ModelRoutingError, "input limit"):
            self.router.complete(self.task(content="x" * 101))

        router = ModelRouter(
            profiles=[self.remote_profile],
            providers=[WrongModelProvider()],
            role_defaults={"orchestrator": "profile.remote.deepseek"},
            invocation_policy=AllowAllTestPolicy(),
        )
        with self.assertRaisesRegex(ModelRoutingError, "identity"):
            router.complete(self.task())

    def test_provider_output_limit_is_enforced(self):
        tiny_profile = ModelProfile(
            profile_id="profile.tiny",
            provider_id="local",
            model_id="qwen.synthetic-v1",
            endpoint_class="local",
            allowed_roles=frozenset({"vp"}),
            max_response_chars=3,
        )
        router = ModelRouter(
            profiles=[tiny_profile],
            providers=[self.local],
            role_defaults={"vp": "profile.tiny"},
            invocation_policy=AllowAllTestPolicy(),
        )
        with self.assertRaisesRegex(ModelRoutingError, "output limit"):
            router.complete(self.task(role="vp"))

    def test_profile_role_mismatch_is_rejected_at_configuration(self):
        with self.assertRaisesRegex(ValueError, "not allowed"):
            ModelRouter(
                profiles=[self.local_profile],
                providers=[self.local],
                role_defaults={"specialist": "profile.local.qwen"},
                invocation_policy=AllowAllTestPolicy(),
            )

    def test_provider_failure_does_not_silently_fallback(self):
        class FailingProvider:
            provider_id = "remote"

            def complete(self, profile, task):
                raise TimeoutError("mock provider timeout")

        router = ModelRouter(
            profiles=[self.remote_profile, self.local_profile],
            providers=[FailingProvider(), self.local],
            role_defaults={"orchestrator": "profile.remote.deepseek"},
            invocation_policy=AllowAllTestPolicy(),
        )
        with self.assertRaisesRegex(TimeoutError, "mock provider timeout"):
            router.complete(self.task())
        self.assertEqual(self.local.calls, [])


if __name__ == "__main__":
    unittest.main()
