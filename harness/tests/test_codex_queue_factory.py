import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness.agent_router import AgentRouteProfile, AgentRouter
from harness.control_plane import ProjectControlPlane
from harness.codex_cli import CodexCliWorker, CodexInvocation
from harness.codex_execution import CodexExecutionFactory
from harness.codex_queue_factory import (
    CodexQueueExecutionFactory,
    CodexQueueExecutionFactoryError,
)
from harness.codex_runtime_bridge import CodexRuntimeBridge
from harness.durable_queue import SQLiteWorkQueue
from harness.event_store import SQLiteEventStore
from harness.organization_runtime import OrganizationCoordinator, WorkflowStatus
from harness.projection import Projector
from harness.testing import USER, seed_synthetic_project

PROJECT = "project.codex.queue"
WORK = "work.codex.queue"
AGENT = {"actorType": "agent", "actorId": "agent.codex", "role": "specialist"}
VERIFIER = USER
ADAPTER = {"name": "codex-cli", "version": "0.1"}
class SyntheticPolicy:
    def authorize(self, invocation, *, codex_home, workspace):
        if invocation.data_scope_ref != "scope.synthetic-only":
            raise PermissionError("test policy permits only synthetic data")


class CodexQueueFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-queue-factory-")
        self.root = Path(self.temp.name)
        self.store = SQLiteEventStore(self.root / "canonical.sqlite3")
        seed_synthetic_project(self.store, project_id=PROJECT, work_id=WORK)
        self.queue = SQLiteWorkQueue(self.store)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.codex_home = self.root / "codex-home"
        self.codex_home.mkdir()
        self.executable = self.root / "fake-codex"
        self.marker = self.root / "cli-started"
        self.executable.write_text(
            f'''#!/usr/bin/env python3
import pathlib, sys
pathlib.Path({str(self.marker)!r}).write_text("started")
args = sys.argv[1:]
out = pathlib.Path(args[args.index("--output-last-message") + 1])
out.write_text("Synthetic queued Codex output.", encoding="utf-8")
print("{{}}")
''',
            encoding="utf-8",
        )
        self.executable.chmod(0o700)
        worker = CodexCliWorker(
            executable=self.executable, codex_home=self.codex_home, policy=SyntheticPolicy(),
        )
        self.bridge = CodexRuntimeBridge(self.store, worker)
        self.profile = AgentRouteProfile(
            profile_id="route.codex.research", agent_id=AGENT["actorId"],
            role=AGENT["role"], adapter_name=ADAPTER["name"],
            adapter_version=ADAPTER["version"], endpoint_class="remote",
            work_types=frozenset({"research"}),
        )
        router = AgentRouter(
            profiles=[self.profile],
            candidates_by_work_type={"research": [self.profile.profile_id]},
        )
        self.execution_factory = CodexExecutionFactory(router, self.bridge)
        self.coordinator = OrganizationCoordinator(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def descriptor(self, *, execution_key=None, actor=None):
        descriptor = self.factory().build_descriptor(
            PROJECT, WORK, correlation_id="correlation.codex.queue",
        )
        if execution_key is not None:
            descriptor["executionKey"] = execution_key
        if actor is not None:
            descriptor["actor"] = actor
        return descriptor

    def enqueue(self, *, payload=None):
        if payload is None:
            return self.factory().enqueue(
                self.queue, "job.codex.queue", PROJECT, WORK,
                correlation_id="correlation.codex.queue",
                now=100,
            )
        return self.queue.enqueue("job.codex.queue", PROJECT, WORK, payload, now=100)

    def factory(self):
        return CodexQueueExecutionFactory(
            Projector(self.store), self.execution_factory,
            run_id_factory=lambda job: f"run.codex.queue.g{job['leaseGeneration']}",
            invocation_factory=lambda context: CodexInvocation(
                project_id=context.project_id, work_id=context.work_id, run_id=context.run_id,
                agent_id=AGENT["actorId"], model_id="gpt-synthetic-test-v1",
                workspace=self.workspace, prompt="Only synthetic queued work.",
                approval_ref="approval.fixture", data_scope_ref="scope.synthetic-only",
                provider_terms_ref="terms.fixture", budget_ref="budget.fixture",
                sandbox_mode="read-only",
            ),
            verification_actor_factory=lambda work: dict(VERIFIER),
            verify=lambda context: any(
                message.get("runId") == context.run_id
                and message["content"] == "Synthetic queued Codex output."
                for message in Projector(self.store).replay(context.project_id)["messages"]
            ),
        )

    def test_canonical_queue_route_runs_codex_and_completes_only_after_verification(self):
        self.enqueue()
        outcome = asyncio.run(self.coordinator.run_queued(
            self.queue, "worker.codex.synthetic", self.factory(), lease_seconds=10,
        ))
        state = Projector(self.store).replay(PROJECT)
        run = state["agentRuns"]["run.codex.queue.g1"]
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        self.assertEqual(state["workItems"][WORK]["status"], "completed")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["queueJobId"], "job.codex.queue")
        self.assertEqual(run["leaseGeneration"], 1)
        self.assertEqual(self.queue.get("job.codex.queue")["state"], "completed")
        self.assertTrue(self.marker.is_file())
        self.assertEqual(len(self.store.list_adapter_invocations(PROJECT)), 1)

    def test_async_codex_keeps_queue_lease_renewals_running(self):
        self.executable.write_text(
            f'''#!/usr/bin/env python3
import pathlib, sys, time
args = sys.argv[1:]
pathlib.Path({str(self.marker)!r}).write_text("started")
time.sleep(1.2)
out = pathlib.Path(args[args.index("--output-last-message") + 1])
out.write_text("Synthetic queued Codex output.", encoding="utf-8")
print("{{}}")
''',
            encoding="utf-8",
        )
        self.executable.chmod(0o700)
        self.enqueue()
        outcome = asyncio.run(self.coordinator.run_queued(
            self.queue, "worker.codex.synthetic", self.factory(), lease_seconds=1,
        ))
        self.assertEqual(outcome.status, WorkflowStatus.COMPLETED)
        self.assertEqual(self.queue.get("job.codex.queue")["state"], "completed")
        self.assertEqual(Projector(self.store).replay(PROJECT)["workItems"][WORK]["status"], "completed")

    @unittest.skipUnless(os.name == "posix", "process-group cancellation is POSIX-specific")
    def test_cancelling_codex_kills_process_group_and_records_canonical_cancellation(self):
        child_pid_file = self.root / "child.pid"
        self.executable.write_text(
            f'''#!/usr/bin/env python3
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))
while True:
    time.sleep(1)
''',
            encoding="utf-8",
        )
        self.executable.chmod(0o700)
        self.enqueue()

        async def cancel_running_job():
            task = asyncio.create_task(self.coordinator.run_queued(
                self.queue, "worker.codex.synthetic", self.factory(), lease_seconds=5,
            ))
            deadline = asyncio.get_running_loop().time() + 5
            while not child_pid_file.exists() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            if not child_pid_file.exists():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.fail("Codex child process did not start")
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return child_pid

        child_pid = asyncio.run(cancel_running_job())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            stat_path = Path(f"/proc/{child_pid}/stat")
            if stat_path.exists() and stat_path.read_text(encoding="utf-8").split()[2] == "Z":
                break
            time.sleep(0.02)
        else:
            self.fail("Codex process group child remained alive after cancellation")
        state = Projector(self.store).replay(PROJECT)
        self.assertEqual(state["agentRuns"]["run.codex.queue.g1"]["status"], "cancelled")
        self.assertEqual(state["workItems"][WORK]["status"], "cancelled")
        self.assertEqual(self.queue.get("job.codex.queue")["state"], "failed")
        invocations = self.store.list_adapter_invocations(PROJECT)
        self.assertEqual(invocations[0]["state"], "executing", "dispatched remote work remains ambiguous")

    def test_mismatched_execution_key_fails_before_cli_dispatch(self):
        self.enqueue(payload=self.descriptor(execution_key="route.other"))
        with self.assertRaisesRegex(CodexQueueExecutionFactoryError, "executionKey"):
            asyncio.run(self.coordinator.run_queued(
                self.queue, "worker.codex.synthetic", self.factory(), lease_seconds=10,
            ))
        self.assertFalse(self.marker.exists())
        self.assertEqual(Projector(self.store).replay(PROJECT)["agentRuns"], {})
        self.assertEqual(self.store.list_adapter_invocations(PROJECT), [])

    def test_untrusted_verification_actor_fails_before_cli_dispatch(self):
        descriptor = self.descriptor()
        descriptor["verificationActor"] = AGENT
        self.enqueue(payload=descriptor)
        with self.assertRaisesRegex(CodexQueueExecutionFactoryError, "verificationActor"):
            asyncio.run(self.coordinator.run_queued(
                self.queue, "worker.codex.synthetic", self.factory(), lease_seconds=10,
            ))
        self.assertFalse(self.marker.exists())
        self.assertEqual(Projector(self.store).replay(PROJECT)["agentRuns"], {})

    def test_stale_canonical_context_fails_before_cli_dispatch(self):
        descriptor = self.descriptor()
        ProjectControlPlane(self.store).revise_work(
            PROJECT, WORK, {"objective": "Changed after queue planning."}, actor=VERIFIER,
        )
        self.enqueue(payload=descriptor)
        with self.assertRaisesRegex(CodexQueueExecutionFactoryError, "contextHash"):
            asyncio.run(self.coordinator.run_queued(
                self.queue, "worker.codex.synthetic", self.factory(), lease_seconds=10,
            ))
        self.assertFalse(self.marker.exists())
        self.assertEqual(Projector(self.store).replay(PROJECT)["agentRuns"], {})

    def test_descriptor_construction_uses_the_exact_current_route_and_context(self):
        descriptor = self.descriptor()
        self.assertEqual(descriptor["executionKey"], self.profile.profile_id)
        self.assertEqual(descriptor["actor"], AGENT)
        self.assertEqual(descriptor["adapter"], ADAPTER)
        self.assertEqual(len(descriptor["contextHash"]), 64)
        self.assertEqual(descriptor["descriptorVersion"], 1)

    def test_project_charter_changes_invalidate_the_prepared_context_hash(self):
        before = self.descriptor()["contextHash"]
        state = Projector(self.store).replay(PROJECT)
        charter = dict(state["project"]["charter"])
        charter["goals"] = ["A revised synthetic goal."]
        ProjectControlPlane(self.store).revise_project_charter(
            PROJECT, charter, actor=VERIFIER,
        )
        after = self.descriptor()["contextHash"]
        self.assertNotEqual(before, after)

    def test_descriptor_preflight_rejects_incomplete_dependencies(self):
        ProjectControlPlane(self.store).create_work({
            "recordType": "work_item", "schemaVersion": "0.1", "projectId": PROJECT,
            "workId": "work.codex.downstream", "revision": 1, "parentWorkId": None,
            "title": "Dependent synthetic work", "objective": "Wait for its dependency.",
            "workType": "research", "status": "ready", "owner": AGENT,
            "dependsOn": [WORK], "capabilityGrantIds": [], "acceptanceCriteria": [],
            "createdBy": VERIFIER, "createdAt": "2026-04-16T12:00:00Z",
            "updatedAt": "2026-04-16T12:00:00Z",
        }, actor=VERIFIER)
        with self.assertRaisesRegex(CodexQueueExecutionFactoryError, "incomplete dependency"):
            self.factory().build_descriptor(
                PROJECT, "work.codex.downstream", correlation_id="correlation.downstream",
            )

    def test_mismatched_actor_fails_before_cli_dispatch(self):
        wrong_actor = {"actorType": "agent", "actorId": "agent.other", "role": "specialist"}
        self.enqueue(payload=self.descriptor(actor=wrong_actor))
        with self.assertRaisesRegex(CodexQueueExecutionFactoryError, "actor/adapter"):
            asyncio.run(self.coordinator.run_queued(
                self.queue, "worker.codex.synthetic", self.factory(), lease_seconds=10,
            ))
        self.assertFalse(self.marker.exists())
        self.assertEqual(Projector(self.store).replay(PROJECT)["agentRuns"], {})


if __name__ == "__main__":
    unittest.main()
