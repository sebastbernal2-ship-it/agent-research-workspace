import asyncio
import tempfile
import unittest
from pathlib import Path

from harness.agent_router import AgentRouteProfile, AgentRouter
from harness.codex_cli import CodexCliWorker, CodexInvocation, CodexInvocationError
from harness.codex_execution import CodexExecutionFactory
from harness.codex_runtime_bridge import CodexRuntimeBridge, CodexRuntimeBridgeError
from harness.event_store import SQLiteEventStore
from harness.organization_runtime import OrganizationCoordinator, WorkflowStatus
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import USER, seed_synthetic_project


PROJECT = "project.codex-bridge-test"
WORK = "work.codex-bridge-test"
AGENT = {"actorType": "agent", "actorId": "agent.codex", "role": "specialist"}
ADAPTER = {"name": "codex-cli", "version": "0.1"}


class AllowTestPolicy:
    def authorize(self, invocation, *, codex_home, workspace):
        if invocation.data_scope_ref != "scope.synthetic-only":
            raise PermissionError("test policy allows synthetic data only")


class DenyTestPolicy:
    def authorize(self, invocation, *, codex_home, workspace):
        raise PermissionError("test invocation denied")


class CodexRuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-runtime-bridge-")
        self.root = Path(self.temp.name)
        self.store = SQLiteEventStore(self.root / "canonical.sqlite3")
        seed_synthetic_project(self.store, project_id=PROJECT, work_id=WORK)
        self.ledger = RunLedger(self.store, clock=lambda: "2026-04-16T12:00:00Z")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.codex_home = self.root / "codex-home"
        self.codex_home.mkdir()
        self.executable = self.root / "fake-codex"
        self.counter = self.root / "invocation-count"
        self.write_fake_cli()
        self.worker = CodexCliWorker(
            executable=self.executable,
            codex_home=self.codex_home,
            policy=AllowTestPolicy(),
        )
        self.bridge = CodexRuntimeBridge(self.store, self.worker)

    def tearDown(self):
        self.temp.cleanup()

    def write_fake_cli(self, *, exit_code=0):
        script = f'''#!/usr/bin/env python3
import pathlib, sys
counter = pathlib.Path({str(self.counter)!r})
count = int(counter.read_text() if counter.exists() else "0") + 1
counter.write_text(str(count))
args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output-last-message") + 1])
output.write_text("Synthetic Codex adapter result.", encoding="utf-8")
print("{{\\"type\\":\\"turn.completed\\"}}")
sys.exit({exit_code})
'''
        self.executable.write_text(script, encoding="utf-8")
        self.executable.chmod(0o700)

    def start_run(self, run_id="run.codex.bridge"):
        self.ledger.start(
            PROJECT, WORK, run_id=run_id, agent=AGENT, adapter=ADAPTER,
            context_hash="a" * 64, correlation_id="correlation.codex.bridge",
            occurred_at="2026-04-16T12:00:00Z",
        )
        return run_id

    def invocation(self, run_id="run.codex.bridge", **changes):
        values = {
            "project_id": PROJECT,
            "work_id": WORK,
            "run_id": run_id,
            "agent_id": AGENT["actorId"],
            "model_id": "gpt-synthetic-test-v1",
            "workspace": self.workspace,
            "prompt": "Only synthetic test content.",
            "approval_ref": "approval.fixture",
            "data_scope_ref": "scope.synthetic-only",
            "provider_terms_ref": "terms.fixture",
            "budget_ref": "budget.fixture",
            "sandbox_mode": "read-only",
        }
        values.update(changes)
        return CodexInvocation(**values)

    def test_active_canonical_run_receives_durable_codex_message_once(self):
        run_id = self.start_run()
        invocation = self.invocation(run_id)
        first = self.bridge.run(invocation)
        second = self.bridge.run(invocation)
        state = Projector(self.store).replay(PROJECT)
        self.assertEqual(first["invocationId"], second["invocationId"])
        self.assertEqual(first["eventIds"], second["eventIds"])
        self.assertEqual(self.counter.read_text(), "1", "an imported receipt must not invoke Codex again")
        message = next(row for row in state["messages"] if row["runId"] == run_id)
        self.assertEqual(message["content"], "Synthetic Codex adapter result.")
        self.assertEqual(message["messageType"], "status", "Codex output is not proof of verified completion")
        self.assertEqual(message["actor"]["actorId"], AGENT["actorId"])
        self.assertEqual(self.store.get_adapter_invocation(first["invocationId"])["state"], "imported")
        self.assertNotIn(invocation.prompt, repr(first))
        self.assertNotIn(str(self.workspace), repr(self.store.list_adapter_invocations(PROJECT)))

    def test_policy_denial_happens_before_durable_dispatch_marker(self):
        run_id = self.start_run()
        denied_worker = CodexCliWorker(
            executable=self.executable,
            codex_home=self.codex_home,
            policy=DenyTestPolicy(),
        )
        denied_bridge = CodexRuntimeBridge(self.store, denied_worker)
        with self.assertRaisesRegex(PermissionError, "denied"):
            denied_bridge.run(self.invocation(run_id))
        self.assertEqual(self.store.list_adapter_invocations(PROJECT), [])
        self.assertFalse(self.counter.exists())

    def test_request_must_match_active_canonical_run_before_cli_dispatch(self):
        self.start_run()
        different = self.invocation(run_id="run.not-active")
        with self.assertRaisesRegex(CodexRuntimeBridgeError, "active canonical Codex run"):
            self.bridge.run(different)
        self.assertFalse(self.counter.exists())

    def test_agent_route_runs_codex_through_canonical_coordinator_and_verifier(self):
        route = AgentRouteProfile(
            profile_id="route.codex.research",
            agent_id=AGENT["actorId"], role=AGENT["role"],
            adapter_name="codex-cli", adapter_version="0.1",
            endpoint_class="remote", work_types=frozenset({"research"}),
        )
        router = AgentRouter(
            profiles=[route], candidates_by_work_type={"research": [route.profile_id]},
        )
        work = Projector(self.store).replay(PROJECT)["workItems"][WORK]
        factory = CodexExecutionFactory(router, self.bridge)
        execution = factory.build(
            work, run_id="run.codex.coordinated",
            correlation_id="correlation.codex.coordinated",
            context_hash="b" * 64, verification_actor=USER,
            invocation_factory=lambda context: self.invocation(context.run_id),
            verify=lambda context: any(
                message["workId"] == context.work_id
                and message.get("runId") == context.run_id
                and message["content"] == "Synthetic Codex adapter result."
                for message in Projector(self.store).replay(context.project_id)["messages"]
            ),
        )
        outcome = asyncio.run(OrganizationCoordinator(self.store, ledger=self.ledger).run_work(execution))
        state = Projector(self.store).replay(PROJECT)
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        self.assertEqual(state["workItems"][WORK]["status"], "completed")
        self.assertEqual(state["agentRuns"][execution.run_id]["status"], "succeeded")
        self.assertEqual(self.counter.read_text(), "1")

    def test_dispatched_call_without_receipt_is_ambiguous_and_not_replayed(self):
        run_id = self.start_run()
        invocation = self.invocation(run_id)
        self.write_fake_cli(exit_code=9)
        with self.assertRaisesRegex(CodexInvocationError, "status 9"):
            self.bridge.run(invocation)
        invocation_id = self.store.list_adapter_invocations(PROJECT)[0]["invocationId"]
        self.assertEqual(self.store.get_adapter_invocation(invocation_id)["state"], "executing")
        with self.assertRaisesRegex(CodexRuntimeBridgeError, "ambiguous"):
            self.bridge.run(invocation)
        self.assertEqual(self.counter.read_text(), "1")


if __name__ == "__main__":
    unittest.main()
