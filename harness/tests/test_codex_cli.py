import json
import os
import tempfile
import unittest
from pathlib import Path

from harness.codex_cli import (
    CodexCliWorker,
    CodexInvocation,
    CodexInvocationError,
)


class AllowFakePolicy:
    def __init__(self):
        self.calls = []

    def authorize(self, invocation, *, codex_home, workspace):
        self.calls.append((invocation, codex_home, workspace))


class DenyPolicy:
    def authorize(self, invocation, *, codex_home, workspace):
        raise PermissionError("not approved")


class CodexCliAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-cli-adapter-test-")
        self.root = Path(self.temp.name)
        self.executable = self.root / "fake-codex"
        self.codex_home = self.root / "codex-home"
        self.workspace = self.root / "workspace"
        self.codex_home.mkdir()
        self.workspace.mkdir()
        self.write_fake_cli()
        self.policy = AllowFakePolicy()
        self.worker = CodexCliWorker(
            executable=self.executable,
            codex_home=self.codex_home,
            policy=self.policy,
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_fake_cli(self, *, final_expression="json.dumps(observed)", exit_code=0):
        script = f'''#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output-last-message") + 1])
prompt = sys.stdin.read()
observed = {{"type": "turn.completed", "prompt": prompt, "args": args, "cwd": str(pathlib.Path.cwd()), "env": sorted(k for k in __import__("os").environ if "KEY" in k or "TOKEN" in k)}}
output.write_text({final_expression}, encoding="utf-8")
print(json.dumps(observed))
sys.exit({exit_code})
'''
        self.executable.write_text(script, encoding="utf-8")
        self.executable.chmod(0o700)

    def invocation(self, **changes):
        values = {
            "project_id": "project.synthetic",
            "work_id": "work.synthetic",
            "run_id": "run.synthetic",
            "agent_id": "agent.codex",
            "model_id": "gpt-synthetic-test-v1",
            "workspace": self.workspace,
            "prompt": "Return only the synthetic fixture response.",
            "approval_ref": "approval.fixture",
            "data_scope_ref": "scope.synthetic-only",
            "provider_terms_ref": "terms.not-live-approved",
            "budget_ref": "budget.test-only",
            "sandbox_mode": "workspace-write",
        }
        values.update(changes)
        return CodexInvocation(**values)

    def test_runs_fixed_argv_with_synthetic_executable_and_scoped_environment(self):
        previous = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "must-not-reach-child"
        try:
            invocation = self.invocation()
            result = self.worker.invoke(invocation)
        finally:
            if previous is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous

        observed = json.loads(result.text)
        self.assertEqual(observed["prompt"], invocation.prompt)
        self.assertEqual(observed["cwd"], str(self.workspace.resolve()))
        args = observed["args"]
        self.assertEqual(args[0], "exec")
        self.assertIn("--json", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("--ignore-user-config", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(args[args.index("--model") + 1], "gpt-synthetic-test-v1")
        self.assertEqual(args[args.index("--cd") + 1], str(self.workspace.resolve()))
        self.assertNotIn("--add-dir", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn(invocation.prompt, args)
        self.assertEqual(observed["env"], [], "ambient API keys/tokens must not be inherited")
        self.assertTrue(Path(args[args.index("--output-last-message") + 1]).is_relative_to(Path(tempfile.gettempdir())))
        self.assertEqual(result.audit_metadata()["modelId"], invocation.model_id)
        self.assertEqual(len(self.policy.calls), 1)
        self.assertEqual(self.policy.calls[0][0], invocation)

    def test_policy_denial_prevents_process_start(self):
        marker = self.root / "started"
        self.executable.write_text(
            f"#!/usr/bin/env python3\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n",
            encoding="utf-8",
        )
        self.executable.chmod(0o700)
        denied = CodexCliWorker(
            executable=self.executable,
            codex_home=self.codex_home,
            policy=DenyPolicy(),
        )
        with self.assertRaisesRegex(PermissionError, "not approved"):
            denied.invoke(self.invocation())
        self.assertFalse(marker.exists())

    def test_nonzero_exit_does_not_expose_child_stderr(self):
        self.write_fake_cli(exit_code=7)
        with self.assertRaisesRegex(CodexInvocationError, "status 7") as caught:
            self.worker.invoke(self.invocation())
        self.assertNotIn("Return only", str(caught.exception))

    def test_oversized_final_message_is_rejected(self):
        self.write_fake_cli(final_expression='"x" * 100')
        with self.assertRaisesRegex(CodexInvocationError, "oversized"):
            self.worker.invoke(self.invocation(max_response_bytes=10))

    def test_explicit_model_and_safe_sandbox_are_required(self):
        with self.assertRaisesRegex(ValueError, "model_id"):
            self.invocation(model_id="--dangerously-bypass-approvals-and-sandbox")
        with self.assertRaisesRegex(ValueError, "sandbox_mode"):
            self.invocation(sandbox_mode="danger-full-access")
        with self.assertRaisesRegex(ValueError, "budget_ref"):
            self.invocation(budget_ref="")


if __name__ == "__main__":
    unittest.main()
