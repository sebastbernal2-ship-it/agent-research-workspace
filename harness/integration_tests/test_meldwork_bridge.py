import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.event_store import IdempotencyConflict, SQLiteEventStore
from harness.meldwork_bridge import MeldworkReceiptBridge, MeldworkBridgeError
from harness.meldwork_runtime_bridge import MeldworkAgenticXRuntimeBridge, MeldworkRuntimeError
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


ACTORS = {
    "agent.proposer": {"actorType": "agent", "actorId": "agent.proposer", "role": "orchestrator"},
    "agent.critic": {"actorType": "agent", "actorId": "agent.critic", "role": "reviewer"},
}
VISIBLE_TO = ["agent.proposer", "agent.critic", "agent.vp"]


def request(source_id, phase, actor, collaboration, text, when):
    return {
        "projectId": "project.meldwork",
        "workId": "work.meldwork",
        "sourceReceiptId": source_id,
        "occurredAt": when,
        "phase": phase,
        "text": text,
        "actor": ACTORS[actor],
        "visibleTo": VISIBLE_TO,
        "collaboration": collaboration,
    }


class MeldworkReceiptBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteEventStore(Path(self.temp.name) / "canonical.sqlite3")
        seed_synthetic_project(self.store, project_id="project.meldwork", work_id="work.meldwork")
        self.bridge = MeldworkReceiptBridge(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def fake_node(self, body):
        executable = Path(self.temp.name) / "fake-node"
        executable.write_text("#!/usr/bin/env python3" + chr(10) + body, encoding="utf-8")
        executable.chmod(0o755)
        return str(executable)

    def test_receipt_adapter_output_limit_fails_closed(self):
        bridge = MeldworkReceiptBridge(self.store, node=self.fake_node("print('x' * 2100000)"))
        with self.assertRaisesRegex(MeldworkBridgeError, "output limit"):
            bridge.canonicalize({"synthetic": True})
        self.assertEqual(len(self.store.events("project.meldwork")), 2)

    def test_runtime_output_limit_fails_closed(self):
        bridge = MeldworkAgenticXRuntimeBridge(
            self.store, node=self.fake_node("print('x' * 2100000)"), timeout=10,
        )
        with self.assertRaisesRegex(MeldworkRuntimeError, "output limit"):
            bridge.run(project_id="project.meldwork", work_id="work.meldwork")
        self.assertEqual(len(self.store.events("project.meldwork")), 2)

    def test_runtime_commit_response_loss_retries_same_run_outputs_once(self):
        actor = {"actorType": "agent", "actorId": "agent.codex", "role": "specialist"}
        ledger = RunLedger(self.store, clock=lambda: "2026-04-16T12:00:00Z")
        run = ledger.start(
            "project.meldwork", "work.meldwork", run_id="run.meldwork.outbox", agent=actor,
            adapter={"name": "meldwork-v4", "version": "test"},
            context_hash="f" * 64, correlation_id="run.meldwork.team",
            occurred_at="2026-04-16T12:00:00Z",
        )
        response = {
            "orchestrationPhase": "completed",
            "invocationCount": 1,
            "runId": "candidate-private-run",
            "taskId": "candidate-private-task",
            "invocations": [{
                "sourceReceiptId": "volatile-candidate-receipt",
                "phase": "proposal",
                "agentKind": "codex",
                "text": "A deterministic synthetic proposal.",
                "collaboration": {
                    "version": 1,
                    "phase": "proposal",
                    "summary": "A synthetic proposal for retry reconciliation.",
                    "capabilities": ["synthetic review"],
                    "intendedWork": ["inspect the synthetic claim"],
                    "deliverables": ["review note"],
                    "dependencies": [],
                },
                "occurredAt": "2026-04-16T12:00:01Z",
                "operationId": "volatile-operation-id",
            }],
        }
        runtime_result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(response).encode("utf-8"),
            stderr=b"",
        )
        bridge = MeldworkAgenticXRuntimeBridge(self.store)
        original_commit_receipt = self.store.commit_adapter_receipt
        commit_calls = 0

        def commit_then_lose_response(invocation_id):
            nonlocal commit_calls
            result = original_commit_receipt(invocation_id)
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("simulated crash after canonical commit")
            return result

        with patch("harness.meldwork_runtime_bridge.run_bounded_subprocess", return_value=runtime_result) as runtime_call, patch.object(
            self.store, "commit_adapter_receipt", side_effect=commit_then_lose_response,
        ):
            arguments = {
                "project_id": "project.meldwork",
                "work_id": "work.meldwork",
                "correlation_id": "run.meldwork.team",
                "run_ids_by_agent": {"codex": run["runId"]},
            }
            with self.assertRaisesRegex(RuntimeError, "after canonical commit"):
                bridge.run(**arguments)
            self.assertEqual(len(Projector(self.store).replay("project.meldwork")["messages"]), 1)
            retry_report = bridge.run(**arguments)

        self.assertEqual(retry_report["duplicateEvents"], [True])
        self.assertEqual(runtime_call.call_count, 1, "an imported invocation must not re-execute the adapter")
        self.assertEqual(len(Projector(self.store).replay("project.meldwork")["messages"]), 1)
        finished = ledger.finish(
            "project.meldwork", run["runId"], terminal_status="succeeded", actor=actor,
            occurred_at="2026-04-16T12:00:02Z",
        )
        self.assertEqual(finished["outputEventIds"], retry_report["eventIds"])
        self.assertTrue(self.store.verify("project.meldwork"))

    def test_ambiguous_dispatched_invocation_is_never_automatically_reexecuted(self):
        actor = {"actorType": "agent", "actorId": "agent.codex", "role": "specialist"}
        run = RunLedger(self.store, clock=lambda: "2026-04-16T12:00:00Z").start(
            "project.meldwork", "work.meldwork", run_id="run.meldwork.ambiguous", agent=actor,
            adapter={"name": "meldwork-agenticx", "version": "test"},
            context_hash="e" * 64, correlation_id="run.meldwork.ambiguous-team",
            occurred_at="2026-04-16T12:00:00Z",
        )
        descriptor = {
            "projectId": "project.meldwork", "workId": "work.meldwork",
            "correlationId": "run.meldwork.ambiguous-team",
            "runIdsByAgent": {"codex": run["runId"]},
        }
        request_hash = hashlib.sha256(
            SQLiteEventStore._canonical_json(descriptor).encode("utf-8")
        ).hexdigest()
        invocation_id = f"inv.meldwork.{request_hash}"
        self.store.prepare_adapter_invocation(invocation_id, "project.meldwork", request_hash)
        self.store.start_adapter_invocation(invocation_id)

        bridge = MeldworkAgenticXRuntimeBridge(self.store)
        with patch("harness.meldwork_runtime_bridge.run_bounded_subprocess") as runtime_call:
            with self.assertRaisesRegex(MeldworkRuntimeError, "is ambiguous"):
                bridge.run(
                    project_id="project.meldwork", work_id="work.meldwork",
                    correlation_id="run.meldwork.ambiguous-team",
                    run_ids_by_agent={"codex": run["runId"]},
                )
        runtime_call.assert_not_called()
        self.assertEqual(self.store.list_adapter_invocations("project.meldwork")[0]["state"], "executing")

    def test_v4_proposal_challenge_synthesis_map_to_canonical_events(self):
        proposal = request(
            "receipt.proposal.1",
            "proposal",
            "agent.proposer",
            {
                "version": 1,
                "phase": "proposal",
                "summary": "Investigate seasonal variation using public evidence.",
                "capabilities": ["source review"],
                "intendedWork": ["compare hemisphere seasons"],
                "deliverables": ["sourced summary"],
                "dependencies": [],
            },
            "Initial proposal: compare public astronomy sources.",
            "2026-04-16T12:00:00Z",
        )
        challenge = request(
            "receipt.challenge.1",
            "challenge",
            "agent.critic",
            {
                "version": 1,
                "phase": "challenge",
                "summary": "The distance-only claim is contradicted by opposite hemispheric seasons.",
                "verdict": "contradict",
            },
            "Public critique.\n\n## Private Reasoning\nnot for the project log\n\ncredential=should-be-redacted",
            "2026-04-16T12:01:00Z",
        )
        synthesis = request(
            "receipt.synthesis.1",
            "synthesis",
            "agent.proposer",
            {
                "version": 1,
                "phase": "synthesis",
                "summary": "Withdraw the distance claim and investigate axial tilt.",
                "resolvedIssueIds": ["issue.distance-claim"],
            },
            "Revision: test axial tilt against the hemisphere observations.",
            "2026-04-16T12:02:00Z",
        )

        results = [self.bridge.persist(item) for item in (proposal, challenge, synthesis)]
        self.assertEqual([result.sequence for result in results], [3, 4, 5])
        self.assertFalse(any(result.duplicate for result in results))
        retry = self.bridge.persist(challenge)
        self.assertTrue(retry.duplicate)
        self.assertEqual(retry.sequence, 4)

        events = self.store.events("project.meldwork")
        messages = [event for event in events if event["eventType"] == "discussion.message_added"]
        self.assertEqual([event["payload"]["messageType"] for event in messages], ["proposal", "challenge", "decision"])
        challenge_content = json.loads(self.store.get_blob(messages[1]["payload"]["contentHash"]))
        self.assertEqual(challenge_content["visibleText"], "Public critique.")
        self.assertNotIn("not for the project log", challenge_content["visibleText"])
        self.assertNotIn("credential=", challenge_content["visibleText"])
        self.assertEqual(challenge_content["receipt"]["verdict"], "contradict")
        self.assertTrue(self.store.verify("project.meldwork"))
        self.assertEqual(len(Projector(self.store).replay("project.meldwork")["messages"]), 3)

    def test_v4_receipt_can_carry_organization_run_correlation(self):
        request_data = request(
            "receipt.correlated.proposal",
            "proposal",
            "agent.proposer",
            {
                "version": 1,
                "phase": "proposal",
                "summary": "Explore the synthetic question.",
                "capabilities": ["public-source analysis"],
                "intendedWork": ["compare explanations"],
                "deliverables": ["a sourced finding"],
                "dependencies": [],
            },
            "Start with a testable proposal.",
            "2026-04-16T12:00:00Z",
        )
        event, _ = self.bridge.canonicalize(request_data, correlation_id="run.organization")
        self.assertEqual(event["correlationId"], "run.organization")

    def test_invalid_v4_receipt_does_not_enter_canonical_log(self):
        invalid = request(
            "receipt.invalid.challenge",
            "challenge",
            "agent.critic",
            {"version": 1, "phase": "challenge", "summary": "Missing required verdict."},
            "This should be rejected.",
            "2026-04-16T12:00:00Z",
        )
        with self.assertRaises(MeldworkBridgeError):
            self.bridge.persist(invalid)
        self.assertEqual(len(self.store.events("project.meldwork")), 2)


if __name__ == "__main__":
    unittest.main()
