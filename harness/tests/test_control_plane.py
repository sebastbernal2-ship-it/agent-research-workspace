import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.control_plane import ControlPlaneError, ProjectControlPlane
from harness.event_store import EventStoreError, SQLiteEventStore
from harness.projection import Projector


NOW = "2026-04-16T12:00:00Z"
USER = {"actorType": "human", "actorId": "user.owner", "role": "user"}
VP = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}


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
