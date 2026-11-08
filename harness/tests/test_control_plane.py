import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.control_plane import ControlPlaneError, ProjectControlPlane
from harness.event_store import ConcurrentWriteConflict, EventStoreError, SQLiteEventStore
from harness.projection import Projector


NOW = "2026-04-16T12:00:00Z"
USER = {"actorType": "human", "actorId": "user.owner", "role": "user"}
VP = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}
SYSTEM = {"actorType": "system", "actorId": "system.clock", "role": "system"}


def make_project():
    return {
        "recordType": "project",
        "schemaVersion": "0.1",
        "projectId": "project.control",
        "revision": 1,
        "name": "Control plane test",
        "status": "active",
        "charter": {
            "revision": 1,
            "goals": ["Test durable question flow"],
            "nonGoals": ["No model calls"],
            "acceptanceCriteria": ["Only affected work pauses"],
        },
        "createdBy": USER,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def make_work(work_id, title):
    return {
        "recordType": "work_item",
        "schemaVersion": "0.1",
        "projectId": "project.control",
        "workId": work_id,
        "revision": 1,
        "parentWorkId": None,
        "title": title,
        "objective": f"Do {title}.",
        "workType": "research",
        "status": "ready",
        "owner": VP,
        "dependsOn": [],
        "capabilityGrantIds": [],
        "acceptanceCriteria": ["Record result"],
        "createdBy": USER,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def make_question(work_ids):
    return {
        "recordType": "human_question",
        "schemaVersion": "0.1",
        "projectId": "project.control",
        "questionId": "question.scope",
        "questionType": "clarification",
        "priority": "normal",
        "blocking": True,
        "requestedBy": VP,
        "workIds": work_ids,
        "runId": None,
        "questionText": "Should historical sources be included?",
        "impact": "Changes the research scope.",
        "options": [
            {"optionId": "option.include", "label": "Include historical sources"},
            {"optionId": "option.current", "label": "Use current sources only"},
        ],
        "status": "open",
        "createdAt": NOW,
    }


class ProjectControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.path)
        self.control = ProjectControlPlane(self.store, clock=lambda: "2026-04-16T12:03:00Z")
        self.control.create_project(make_project())
        self.control.create_work(make_work("work.primary", "Primary research"))
        self.control.create_work(make_work("work.independent", "Independent scan"))

    def tearDown(self):
        self.temp.cleanup()

    def test_user_question_pauses_only_affected_work_and_resumes_exactly_once(self):
        question = make_question(["work.primary"])
        opened = self.control.open_question(question)
        self.assertEqual(opened["status"], "open")

        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "waiting_human")
        self.assertEqual(state["workItems"]["work.independent"]["status"], "ready")
        self.assertIn("question.scope", state["questions"])

        answered = self.control.answer_question(
            "project.control",
            "question.scope",
            "Include historical sources.",
            actor=USER,
            selected_option_id="option.include",
        )
        before_retry = self.store.events("project.control")
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["answer"]["resumedWorkIds"], ["work.primary"])

        # A repeated identical answer is a harmless retry, not a second resume.
        self.control.answer_question(
            "project.control",
            "question.scope",
            "Include historical sources.",
            actor=USER,
            selected_option_id="option.include",
        )
        after_retry = self.store.events("project.control")
        self.assertEqual(len(after_retry), len(before_retry))

        state = Projector(SQLiteEventStore(self.path)).replay("project.control")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "ready")
        self.assertEqual(state["workItems"]["work.independent"]["status"], "ready")
        self.assertEqual(state["questions"]["question.scope"]["status"], "answered")
        self.assertTrue(self.store.verify("project.control"))

    def test_work_creation_cannot_append_before_project_creation(self):
        missing_store = SQLiteEventStore(Path(self.temp.name) / "missing-project.sqlite3")
        missing_control = ProjectControlPlane(missing_store)
        work = make_work("work.orphan", "Orphan work")
        work["projectId"] = "project.missing"
        with self.assertRaisesRegex(ControlPlaneError, "unknown project"):
            missing_control.create_work(work)
        self.assertEqual(missing_store.events("project.missing"), [])

    def test_non_user_cannot_answer_and_invalid_option_is_rejected(self):
        self.control.open_question(make_question(["work.primary"]))
        recovered = Projector(SQLiteEventStore(self.path)).replay("project.control")
        self.assertEqual(recovered["questions"]["question.scope"]["status"], "open")
        self.assertEqual(recovered["workItems"]["work.primary"]["status"], "waiting_human")
        with self.assertRaisesRegex(ControlPlaneError, "only the user"):
            self.control.answer_question(
                "project.control", "question.scope", "yes", actor=VP,
            )
        with self.assertRaisesRegex(ControlPlaneError, "does not belong"):
            self.control.answer_question(
                "project.control", "question.scope", "yes", actor=USER,
                selected_option_id="option.unknown",
            )
        self.assertEqual(Projector(self.store).replay("project.control")["questions"]["question.scope"]["status"], "open")

    def test_answer_and_resumes_are_an_atomic_event_batch(self):
        self.control.open_question(make_question(["work.primary"]))
        original_count = len(self.store.events("project.control"))
        with patch.object(self.store, "append_many", side_effect=EventStoreError("injected storage failure")):
            with self.assertRaisesRegex(EventStoreError, "injected"):
                self.control.answer_question(
                    "project.control", "question.scope", "yes", actor=USER,
                    occurred_at="2026-04-16T12:04:00Z",
                )
        state = Projector(self.store).replay("project.control")
        self.assertEqual(len(self.store.events("project.control")), original_count)
        self.assertEqual(state["questions"]["question.scope"]["status"], "open")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "waiting_human")

        # Retry after the storage fault applies the answer and all dependent
        # work transitions together.
        self.control.answer_question(
            "project.control", "question.scope", "yes", actor=USER,
            occurred_at="2026-04-16T12:04:00Z",
        )
        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["questions"]["question.scope"]["status"], "answered")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "ready")

    def test_rejection_closes_question_but_keeps_work_blocked_and_never_auto_approves(self):
        question = make_question(["work.primary"])
        question["questionId"] = "question.reject"
        self.control.open_question(question)
        rejected = self.control.close_question(
            "project.control", "question.reject", "rejected", actor=USER,
            reason="The proposed scope is not approved.", occurred_at="2026-04-16T12:04:00Z",
        )
        self.assertEqual(rejected["status"], "rejected")
        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "blocked")
        before_retry = len(self.store.events("project.control"))
        self.control.close_question(
            "project.control", "question.reject", "rejected", actor=USER,
            reason="The proposed scope is not approved.", occurred_at="2026-04-16T12:04:00Z",
        )
        self.assertEqual(len(self.store.events("project.control")), before_retry)
        with self.assertRaisesRegex(ControlPlaneError, "not open"):
            self.control.answer_question("project.control", "question.reject", "yes", actor=USER)
        self.assertEqual(Projector(self.store).replay("project.control")["workItems"]["work.primary"]["status"], "blocked")

    def test_open_question_freezes_dependency_definitions_for_exact_work(self):
        self.control.revise_work(
            "project.control", "work.primary", {"dependsOn": ["work.independent"]},
            actor=VP, occurred_at="2026-04-16T12:01:00Z",
        )
        question = make_question(["work.primary"])
        question["questionId"] = "question.dependency-closure"
        self.control.open_question(question)
        before = len(self.store.events("project.control"))
        with self.assertRaisesRegex(ControlPlaneError, "dependency closure"):
            self.control.revise_work(
                "project.control", "work.independent", {"objective": "Changed while blocked."},
                actor=VP,
            )
        self.assertEqual(len(self.store.events("project.control")), before)

    def test_answer_after_cancellation_is_denied(self):
        question = make_question(["work.primary"])
        question["questionId"] = "question.cancelled"
        self.control.open_question(question)
        self.control.close_question(
            "project.control", "question.cancelled", "cancelled", actor=USER,
            reason="The user withdrew the question.", occurred_at="2026-04-16T12:04:00Z",
        )
        with self.assertRaisesRegex(ControlPlaneError, "not open"):
            self.control.answer_question(
                "project.control", "question.cancelled", "yes", actor=USER,
            )
        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["questions"]["question.cancelled"]["status"], "cancelled")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "blocked")

    def test_expiry_requires_system_actor_and_passed_deadline(self):
        question = make_question(["work.primary"])
        question["questionId"] = "question.expiry"
        question["dueAt"] = "2026-04-16T12:04:00Z"
        self.control.open_question(question)
        with self.assertRaisesRegex(ControlPlaneError, "only the system"):
            self.control.close_question(
                "project.control", "question.expiry", "expired", actor=USER,
                reason="deadline", occurred_at="2026-04-16T12:05:00Z",
            )
        with self.assertRaisesRegex(ControlPlaneError, "has not passed"):
            self.control.close_question(
                "project.control", "question.expiry", "expired", actor=SYSTEM,
                reason="deadline", occurred_at="2026-04-16T12:03:00Z",
            )
        self.assertEqual(Projector(self.store).replay("project.control")["questions"]["question.expiry"]["status"], "open")
        expired = self.control.close_question(
            "project.control", "question.expiry", "expired", actor=SYSTEM,
            reason="deadline passed without user response", occurred_at="2026-04-16T12:05:00Z",
        )
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(Projector(self.store).replay("project.control")["workItems"]["work.primary"]["status"], "blocked")

    def test_superseding_question_preserves_pause_and_exact_successor(self):
        original = make_question(["work.primary"])
        original["questionId"] = "question.original"
        self.control.open_question(original)
        successor = make_question(["work.primary"])
        successor.update({
            "questionId": "question.revised",
            "questionText": "Should we instead include only peer-reviewed historical sources?",
            "createdAt": "2026-04-16T12:03:00Z",
        })
        closed = self.control.close_question(
            "project.control", "question.original", "superseded", actor=VP,
            reason="The original scope question was too broad.", successor_question=successor,
            occurred_at="2026-04-16T12:03:00Z",
        )
        self.assertEqual(closed["status"], "superseded")
        before_retry = len(self.store.events("project.control"))
        self.control.close_question(
            "project.control", "question.original", "superseded", actor=VP,
            reason="The original scope question was too broad.", successor_question=successor,
            occurred_at="2026-04-16T12:03:00Z",
        )
        self.assertEqual(len(self.store.events("project.control")), before_retry)
        recovered = Projector(SQLiteEventStore(self.path)).replay("project.control")
        self.assertEqual(recovered["questions"]["question.revised"]["status"], "open")
        self.assertEqual(recovered["questions"]["question.original"]["supersededByQuestionId"], "question.revised")
        self.assertEqual(recovered["workItems"]["work.primary"]["status"], "waiting_human")

    def test_replay_accepts_answered_successor_in_question_supersession_chain(self):
        original = make_question(["work.primary"])
        original["questionId"] = "question.chain.original"
        self.control.open_question(original)
        successor = make_question(["work.primary"])
        successor.update({
            "questionId": "question.chain.successor",
            "createdAt": "2026-04-16T12:03:00Z",
        })
        self.control.close_question(
            "project.control", original["questionId"], "superseded", actor=VP,
            reason="Clarify with a revised question.", successor_question=successor,
            occurred_at="2026-04-16T12:03:00Z",
        )
        self.control.answer_question(
            "project.control", successor["questionId"], "Use current sources only.",
            actor=USER, selected_option_id="option.current",
            occurred_at="2026-04-16T12:04:00Z",
        )
        state = Projector(SQLiteEventStore(self.path)).replay("project.control")
        self.assertEqual(state["questions"][original["questionId"]]["status"], "superseded")
        self.assertEqual(state["questions"][successor["questionId"]]["status"], "answered")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "ready")

    def test_changed_affected_work_state_prevents_answer_resume(self):
        question = make_question(["work.primary"])
        question["questionId"] = "question.changed-work"
        self.control.open_question(question)
        self.control.transition_work("project.control", "work.primary", "blocked", actor=VP)
        with self.assertRaisesRegex(ControlPlaneError, "not paused"):
            self.control.answer_question(
                "project.control", "question.changed-work", "yes", actor=USER,
            )
        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["questions"]["question.changed-work"]["status"], "open")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "blocked")

    def test_overlapping_blocking_questions_are_rejected(self):
        first = make_question(["work.primary"])
        first["questionId"] = "question.first"
        self.control.open_question(first)
        second = make_question(["work.primary"])
        second["questionId"] = "question.second"
        with self.assertRaisesRegex(ControlPlaneError, "already covers"):
            self.control.open_question(second)

    def test_maximum_length_ids_still_produce_valid_event_and_answer_refs(self):
        project_id = "p" + "x" * 127
        work_id = "w" * 128
        question_id = "q" * 128
        project = make_project()
        project["projectId"] = project_id
        control = ProjectControlPlane(self.store, clock=lambda: "2026-04-16T12:03:00Z")
        control.create_project(project)
        work = make_work(work_id, "Long identifier work")
        work["projectId"] = project_id
        control.create_work(work)
        question = make_question([work_id])
        question.update({"projectId": project_id, "questionId": question_id})
        control.open_question(question)
        control.answer_question(project_id, question_id, "Continue.", actor=USER)
        self.assertEqual(Projector(self.store).replay(project_id)["questions"][question_id]["status"], "answered")

    def test_project_charter_and_work_revisions_replay_and_reject_dependency_cycles(self):
        revised_project = self.control.revise_project_charter(
            "project.control",
            {
                "revision": 1,
                "goals": ["Test a revised durable contract"],
                "nonGoals": ["No model calls"],
                "acceptanceCriteria": ["Revision is replayable"],
            },
            actor=USER,
            occurred_at="2026-04-16T12:04:00Z",
        )
        self.assertEqual(revised_project["revision"], 2)
        self.assertEqual(revised_project["charter"]["revision"], 2)
        revised_work = self.control.revise_work(
            "project.control", "work.primary",
            {"dependsOn": ["work.independent"], "objective": "Use independent scan results."},
            actor=VP,
            occurred_at="2026-04-16T12:05:00Z",
        )
        self.assertEqual(revised_work["revision"], 2)
        self.assertEqual(revised_work["dependsOn"], ["work.independent"])
        event_count = len(self.store.events("project.control"))
        with self.assertRaisesRegex(ControlPlaneError, "cycle"):
            self.control.revise_work(
                "project.control", "work.independent", {"dependsOn": ["work.primary"]},
                actor=VP, occurred_at="2026-04-16T12:06:00Z",
            )
        self.assertEqual(len(self.store.events("project.control")), event_count)
        replayed = Projector(SQLiteEventStore(self.path)).replay("project.control")
        self.assertEqual(replayed["project"], revised_project)
        self.assertEqual(replayed["workItems"]["work.primary"], revised_work)

    def test_active_question_freezes_the_affected_work_scope(self):
        self.control.open_question(make_question(["work.primary"]))
        before = len(self.store.events("project.control"))
        with self.assertRaisesRegex(ControlPlaneError, "dependency closure"):
            self.control.revise_work(
                "project.control", "work.primary", {"dependsOn": ["work.independent"]},
                actor=VP,
            )
        self.assertEqual(len(self.store.events("project.control")), before)

    def test_work_cannot_run_until_explicit_dependencies_complete(self):
        self.control.revise_work(
            "project.control", "work.primary", {"dependsOn": ["work.independent"]},
            actor=VP, occurred_at="2026-04-16T12:01:00Z",
        )
        before = len(self.store.events("project.control"))
        with self.assertRaisesRegex(ControlPlaneError, "dependencies complete"):
            self.control.transition_work("project.control", "work.primary", "running", actor=VP)
        self.assertEqual(len(self.store.events("project.control")), before)

        self.control.transition_work("project.control", "work.independent", "running", actor=VP)
        self.control.transition_work("project.control", "work.independent", "verifying", actor=VP)
        self.control.transition_work("project.control", "work.independent", "completed", actor=VP)
        self.control.transition_work("project.control", "work.primary", "running", actor=VP)
        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["workItems"]["work.independent"]["status"], "completed")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "running")

    def test_stale_answer_batch_cannot_partially_resume_work(self):
        question = make_question(["work.primary"])
        question["questionId"] = "question.concurrent-answer"
        self.control.open_question(question)
        content_ref, content_hash = self.store.put_blob("Intervening status message.")
        intervening = self.control._event(
            "project.control", "discussion.message_added", VP,
            {
                "workId": "work.primary", "threadId": "thread.answer-race",
                "messageType": "status", "contentRef": content_ref,
                "contentHash": content_hash, "visibleTo": [VP["actorId"]],
            },
            event_id="event.answer-race.message",
        )
        original_append = self.store.append
        original_append_many = self.store.append_many
        raced = False

        def append_many_after_intervening(events, *, expected_sequence=None):
            nonlocal raced
            if not raced:
                raced = True
                original_append(intervening)
            return original_append_many(events, expected_sequence=expected_sequence)

        with patch.object(self.store, "append_many", side_effect=append_many_after_intervening):
            with self.assertRaises(ConcurrentWriteConflict):
                self.control.answer_question(
                    "project.control", question["questionId"], "Proceed.", actor=USER,
                )
        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["questions"][question["questionId"]]["status"], "open")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "waiting_human")
        self.assertEqual(len(state["messages"]), 1)

    def test_stale_control_command_cannot_append_after_intervening_event(self):
        content_ref, content_hash = self.store.put_blob("Concurrent but valid message.")
        intervening = self.control._event(
            "project.control", "discussion.message_added", VP,
            {
                "workId": "work.primary", "threadId": "thread.concurrent",
                "messageType": "status", "contentRef": content_ref,
                "contentHash": content_hash, "visibleTo": [VP["actorId"]],
            },
            event_id="event.concurrent.message",
        )
        original_append = self.store.append
        raced = False

        def append_after_intervening(event, *, expected_sequence=None):
            nonlocal raced
            if not raced:
                raced = True
                original_append(intervening)
            return original_append(event, expected_sequence=expected_sequence)

        with patch.object(self.store, "append", side_effect=append_after_intervening):
            with self.assertRaises(ConcurrentWriteConflict):
                self.control.transition_work(
                    "project.control", "work.primary", "running", actor=VP,
                )
        state = Projector(self.store).replay("project.control")
        self.assertEqual(state["workItems"]["work.primary"]["status"], "ready")
        self.assertEqual(len(state["messages"]), 1)

    def test_forbidden_work_transition_fails_without_state_change(self):
        self.control.transition_work("project.control", "work.primary", "running", actor=VP)
        self.control.transition_work("project.control", "work.primary", "verifying", actor=VP)
        self.control.transition_work("project.control", "work.primary", "completed", actor=VP)
        before = len(self.store.events("project.control"))
        with self.assertRaisesRegex(ControlPlaneError, "not allowed"):
            self.control.transition_work(
                "project.control", "work.primary", "running", actor=VP,
            )
        self.assertEqual(len(self.store.events("project.control")), before)


if __name__ == "__main__":
    unittest.main()
