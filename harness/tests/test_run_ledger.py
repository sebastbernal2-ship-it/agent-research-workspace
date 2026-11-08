import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.control_plane import ControlPlaneError, ProjectControlPlane
from harness.event_store import ConcurrentWriteConflict, SQLiteEventStore
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project


PROJECT_ID = "project.runs"
WORK_ID = "work.runs"
NOW = "2026-04-16T12:00:00Z"
VP = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}
SPECIALIST = {"actorType": "agent", "actorId": "agent.specialist", "role": "specialist"}
USER = {"actorType": "human", "actorId": "user.synthetic", "role": "user"}


class RunLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.db_path)
        self.control = seed_synthetic_project(
            self.store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=NOW,
        )
        self.ledger = RunLedger(self.store, clock=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def start_work(self):
        return self.control.transition_work(
            PROJECT_ID, WORK_ID, "running", actor=VP, reason="Begin deterministic test run.", occurred_at=NOW,
        )

    def start_run(self, run_id="run.specialist"):
        return self.ledger.start(
            PROJECT_ID,
            WORK_ID,
            run_id=run_id,
            agent=SPECIALIST,
            adapter={"name": "test-agent-runtime", "version": "0.1"},
            context_hash=hashlib.sha256(b"project/work context").hexdigest(),
            correlation_id="run.team",
            occurred_at=NOW,
        )

    def test_start_atomically_starts_ready_work_and_run(self):
        started = self.start_run()
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["workItems"][WORK_ID]["status"], "running")
        events = self.store.events(PROJECT_ID)
        self.assertEqual([event["eventType"] for event in events[-2:]], ["work.status_changed", "run.started"])
        self.assertEqual([event["correlationId"] for event in events[-2:]], ["run.team", "run.team"])
        self.assertEqual(state["agentRuns"][started["runId"]]["status"], "running")
        self.assertTrue(self.store.verify(PROJECT_ID))

    def test_work_has_only_one_concurrent_root_run(self):
        def start(run_id):
            try:
                self.ledger.start(
                    PROJECT_ID, WORK_ID, run_id=run_id, agent=SPECIALIST,
                    adapter={"name": "test-agent-runtime", "version": "0.1"},
                    context_hash=hashlib.sha256(run_id.encode()).hexdigest(),
                    correlation_id="run.team", occurred_at=NOW,
                )
                return "started"
            except (ControlPlaneError, ConcurrentWriteConflict):
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, ("run.concurrent-a", "run.concurrent-b")))
        self.assertEqual(results.count("started"), 1)
        state = Projector(self.store).replay(PROJECT_ID)
        active = [run_id for run_id, run in state["agentRuns"].items() if run["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertEqual(state["workItems"][WORK_ID]["status"], "running")

    def test_new_root_run_cannot_be_started_while_another_run_owns_work(self):
        self.start_run("run.active-owner")
        with self.assertRaisesRegex(ControlPlaneError, "another active run tree"):
            self.start_run("run.second-owner")
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual([run_id for run_id, run in state["agentRuns"].items() if run["status"] == "running"], ["run.active-owner"])

    def test_running_work_without_active_run_requires_recovery_before_new_start(self):
        self.start_work()
        with self.assertRaisesRegex(ControlPlaneError, "no active canonical run"):
            self.start_run("run.without-owner")
        self.assertNotIn("run.without-owner", Projector(self.store).replay(PROJECT_ID)["agentRuns"])

    def test_start_and_finish_are_durable_and_identical_retries_are_idempotent(self):
        started = self.start_run()
        event_count = len(self.store.verified_events(PROJECT_ID))
        self.assertEqual(self.start_run(), started)
        self.assertEqual(len(self.store.verified_events(PROJECT_ID)), event_count)

        with self.assertRaisesRegex(ControlPlaneError, "assigned agent"):
            self.ledger.finish(
                PROJECT_ID, "run.specialist", terminal_status="succeeded", actor=VP, occurred_at=NOW,
            )
        finished = self.ledger.finish(
            PROJECT_ID, "run.specialist", terminal_status="succeeded", actor=SPECIALIST, occurred_at=NOW,
        )
        event_count = len(self.store.verified_events(PROJECT_ID))
        self.assertEqual(self.ledger.finish(
            PROJECT_ID, "run.specialist", terminal_status="succeeded", actor=SPECIALIST, occurred_at=NOW,
        ), finished)
        self.assertEqual(len(self.store.verified_events(PROJECT_ID)), event_count)
        self.assertEqual(self.start_run(), finished)
        self.assertEqual(len(self.store.verified_events(PROJECT_ID)), event_count)
        self.assertEqual(finished["status"], "succeeded")
        run_events = [event for event in self.store.events(PROJECT_ID) if event["eventType"].startswith("run.")]
        self.assertEqual([event["correlationId"] for event in run_events], ["run.team", "run.team"])
        self.assertEqual(run_events[1]["causationId"], run_events[0]["eventId"])
        self.assertTrue(self.store.verify(PROJECT_ID))

        reopened = SQLiteEventStore(self.db_path)
        self.assertEqual(Projector(reopened).replay(PROJECT_ID)["agentRuns"]["run.specialist"]["status"], "succeeded")

    def test_run_start_rechecks_dependencies_before_atomic_transition(self):
        self.control.create_work({
            "recordType": "work_item", "schemaVersion": "0.1", "projectId": PROJECT_ID,
            "workId": "work.dependent", "revision": 1, "parentWorkId": WORK_ID,
            "title": "Dependent work", "objective": "Wait for its prerequisite.",
            "workType": "research", "status": "ready", "owner": SPECIALIST,
            "dependsOn": [WORK_ID], "capabilityGrantIds": [],
            "acceptanceCriteria": ["The prerequisite is complete."],
            "createdBy": USER, "createdAt": NOW, "updatedAt": NOW,
        })
        with self.assertRaisesRegex(ControlPlaneError, "dependencies complete"):
            self.ledger.start(
                PROJECT_ID, "work.dependent", run_id="run.dependent", agent=SPECIALIST,
                adapter={"name": "test-agent-runtime", "version": "0.1"},
                context_hash="c" * 64, occurred_at=NOW,
            )
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["workItems"]["work.dependent"]["status"], "ready")
        self.assertNotIn("run.dependent", state["agentRuns"])

    def test_parent_child_runs_are_canonical_and_parent_cannot_finish_first(self):
        self.start_run("run.parent")
        child = self.ledger.start(
            PROJECT_ID, WORK_ID, run_id="run.child", agent=VP,
            adapter={"name": "nested-test-runtime", "version": "0.1"},
            context_hash=hashlib.sha256(b"child context").hexdigest(),
            correlation_id="run.team", parent_run_id="run.parent", occurred_at=NOW,
        )
        self.assertEqual(child["parentRunId"], "run.parent")
        with self.assertRaisesRegex(ControlPlaneError, "child runs are active"):
            self.ledger.finish(
                PROJECT_ID, "run.parent", terminal_status="succeeded", actor=SPECIALIST, occurred_at=NOW,
            )
        self.ledger.finish(
            PROJECT_ID, "run.child", terminal_status="succeeded", actor=VP, occurred_at=NOW,
        )
        self.ledger.finish(
            PROJECT_ID, "run.parent", terminal_status="succeeded", actor=SPECIALIST, occurred_at=NOW,
        )
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.child"]["parentRunId"], "run.parent")
        self.assertEqual(state["agentRuns"]["run.parent"]["status"], "succeeded")
        self.assertTrue(self.store.verify(PROJECT_ID))

    def test_recovery_marks_dispatched_invocation_without_receipt_ambiguous(self):
        run = self.start_run("run.ambiguous-invocation")
        self.store.prepare_adapter_invocation("invocation.ambiguous-run", PROJECT_ID, "8" * 64)
        self.store.start_adapter_invocation("invocation.ambiguous-run")
        self.ledger.recover_interrupted(PROJECT_ID, occurred_at=NOW)
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"][run["runId"]]["status"], "interrupted")
        self.assertEqual(self.store.list_adapter_invocations(PROJECT_ID)[0]["state"], "ambiguous")
        self.assertTrue(self.store.verify(PROJECT_ID))

    def test_recovery_imports_staged_adapter_receipt_before_interrupting_run(self):
        run = self.start_run("run.with-staged-output")
        content_ref, content_hash = self.store.put_blob("Durably staged output.")
        started_event = next(
            event for event in self.store.events(PROJECT_ID)
            if event["eventType"] == "run.started" and event["payload"]["runId"] == run["runId"]
        )
        message_event = {
            "recordType": "event", "schemaVersion": "0.1",
            "eventId": "event.staged.message", "projectId": PROJECT_ID,
            "eventType": "discussion.message_added", "actor": SPECIALIST,
            "occurredAt": NOW, "causationId": started_event["eventId"],
            "correlationId": "run.team", "idempotencyKey": "idem.staged.message",
            "payload": {
                "workId": WORK_ID, "threadId": "thread.recovery", "messageType": "status",
                "runId": run["runId"], "contentRef": content_ref, "contentHash": content_hash,
                "visibleTo": ["agent.vp"],
            },
        }
        self.store.prepare_adapter_invocation("invocation.staged", PROJECT_ID, "e" * 64)
        self.store.start_adapter_invocation("invocation.staged")
        self.store.stage_adapter_receipt("invocation.staged", [message_event])

        backup_path = self.store.backup_to(Path(self.temp.name) / "canonical-backup.sqlite3")
        reopened = SQLiteEventStore(backup_path)
        recovered_ledger = RunLedger(reopened, clock=lambda: NOW)
        self.assertEqual(recovered_ledger.recover_interrupted(PROJECT_ID, occurred_at=NOW), [run["runId"]])
        state = Projector(reopened).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"][run["runId"]]["status"], "interrupted")
        self.assertEqual(state["agentRuns"][run["runId"]]["outputEventIds"], ["event.staged.message"])
        self.assertEqual(state["messages"][0]["content"], "Durably staged output.")
        self.assertEqual(reopened.list_adapter_invocations(PROJECT_ID)[0]["state"], "imported")
        self.assertEqual(self.store.list_adapter_invocations(PROJECT_ID)[0]["state"], "receipt_staged")
        self.assertTrue(reopened.verify(PROJECT_ID))

    def test_recovery_closes_nested_runs_leaf_first(self):
        self.start_run("run.parent")
        self.ledger.start(
            PROJECT_ID, WORK_ID, run_id="run.child", agent=VP,
            adapter={"name": "nested-test-runtime", "version": "0.1"},
            context_hash="f" * 64, correlation_id="run.team", parent_run_id="run.parent",
            occurred_at=NOW,
        )
        self.assertEqual(
            self.ledger.recover_interrupted(PROJECT_ID, occurred_at=NOW),
            ["run.child", "run.parent"],
        )
        state = Projector(self.store).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.child"]["status"], "interrupted")
        self.assertEqual(state["agentRuns"]["run.parent"]["status"], "interrupted")
        self.assertTrue(self.store.verify(PROJECT_ID))

    def test_child_run_requires_active_parent_and_matching_correlation(self):
        with self.assertRaisesRegex(ControlPlaneError, "active before"):
            self.ledger.start(
                PROJECT_ID, WORK_ID, run_id="run.orphan", agent=SPECIALIST,
                adapter={"name": "test-agent-runtime", "version": "0.1"},
                context_hash="d" * 64, correlation_id="run.team", parent_run_id="run.missing",
                occurred_at=NOW,
            )
        self.start_run("run.parent")
        with self.assertRaisesRegex(ControlPlaneError, "same correlation ID"):
            self.ledger.start(
                PROJECT_ID, WORK_ID, run_id="run.wrong-correlation", agent=VP,
                adapter={"name": "test-agent-runtime", "version": "0.1"},
                context_hash="e" * 64, correlation_id="run.other", parent_run_id="run.parent",
                occurred_at=NOW,
            )

    def test_unenforced_grants_fail_without_starting_an_extra_run(self):
        self.start_run()
        with self.assertRaisesRegex(ControlPlaneError, "does not authorize capability"):
            self.ledger.start(
                PROJECT_ID,
                WORK_ID,
                run_id="run.with-grant",
                agent=SPECIALIST,
                adapter={"name": "test-agent-runtime", "version": "0.1"},
                context_hash="a" * 64,
                capability_grant_ids=["grant.unenforced"],
                occurred_at=NOW,
            )
        self.assertNotIn("run.with-grant", Projector(self.store).replay(PROJECT_ID)["agentRuns"])

    def test_restart_marks_left_running_runs_interrupted_without_resuming_work(self):
        self.start_run()
        reopened = SQLiteEventStore(self.db_path)
        restarted_ledger = RunLedger(reopened, clock=lambda: NOW)
        self.assertEqual(restarted_ledger.recover_interrupted(PROJECT_ID, occurred_at=NOW), ["run.specialist"])
        state = Projector(reopened).replay(PROJECT_ID)
        self.assertEqual(state["agentRuns"]["run.specialist"]["status"], "interrupted")
        self.assertEqual(state["workItems"][WORK_ID]["status"], "running")
        self.assertEqual(restarted_ledger.recover_interrupted(PROJECT_ID, occurred_at=NOW), [])
        self.assertTrue(reopened.verify(PROJECT_ID))

    def test_changed_or_invalid_run_completion_is_rejected(self):
        self.start_run()
        with self.assertRaises(ValueError):
            self.ledger.finish(
                PROJECT_ID, "run.specialist", terminal_status="running", actor=SPECIALIST, occurred_at=NOW,
            )
        self.ledger.finish(
            PROJECT_ID, "run.specialist", terminal_status="failed", actor=SPECIALIST, occurred_at=NOW,
        )
        with self.assertRaisesRegex(ControlPlaneError, "already terminal"):
            self.ledger.finish(
                PROJECT_ID, "run.specialist", terminal_status="succeeded", actor=SPECIALIST, occurred_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
