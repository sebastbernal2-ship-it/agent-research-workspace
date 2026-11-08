import tempfile
import unittest
from pathlib import Path

from harness.event_store import SQLiteEventStore
from harness.inbox import InboxNotFound, InboxReadModel
from harness.testing import USER, seed_synthetic_project


NOW = "2026-04-16T12:00:00Z"
VP = {"actorType": "agent", "actorId": "agent.synthetic-vp", "role": "vp"}


def question(question_id, priority, *, blocking):
    return {
        "recordType": "human_question",
        "schemaVersion": "0.1",
        "projectId": "project.inbox",
        "questionId": question_id,
        "questionType": "clarification",
        "priority": priority,
        "blocking": blocking,
        "requestedBy": VP,
        "workIds": ["work.inbox"],
        "runId": None,
        "questionText": f"Resolve {question_id}?",
        "impact": "Affects project scope.",
        "options": [],
        "status": "open",
        "createdAt": NOW,
    }


class InboxReadModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteEventStore(Path(self.temp.name) / "canonical.sqlite3")
        self.control = seed_synthetic_project(
            self.store, project_id="project.inbox", work_id="work.inbox", occurred_at=NOW,
        )
        self.control.open_question(question("question.normal", "normal", blocking=True))
        self.control.open_question(question("question.high", "high", blocking=False))
        self.inbox = InboxReadModel(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_lists_questions_by_priority_and_includes_exact_work_status(self):
        items = self.inbox.list_questions("project.inbox")
        self.assertEqual([item["questionId"] for item in items], ["question.high", "question.normal"])
        normal = self.inbox.list_questions("project.inbox", priority="normal")
        self.assertEqual(len(normal), 1)
        self.assertEqual(normal[0]["affectedWork"][0]["status"], "waiting_human")
        self.assertEqual(normal[0]["workIds"], ["work.inbox"])

    def test_read_model_is_project_scoped_and_etag_tracks_answer_and_resume(self):
        before = self.inbox.get_question("project.inbox", "question.normal")
        self.control.answer_question(
            "project.inbox", "question.normal", "Use the current scope.", actor=USER,
            occurred_at="2026-04-16T12:01:00Z",
        )
        after = self.inbox.get_question("project.inbox", "question.normal")
        self.assertNotEqual(before["etag"], after["etag"])
        self.assertEqual(after["record"]["status"], "answered")
        self.assertEqual(after["affectedWork"][0]["status"], "ready")
        self.assertEqual([row["eventType"] for row in after["history"]], [
            "human.question_opened", "human.question_answered",
        ])
        self.assertEqual(
            [row["questionId"] for row in self.inbox.list_questions(
                "project.inbox", statuses=("answered",),
            )],
            ["question.normal"],
        )
        with self.assertRaises(InboxNotFound):
            self.inbox.get_question("project.other", "question.normal")

    def test_invalid_filters_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "nonempty subset"):
            self.inbox.list_questions("project.inbox", statuses=())
        with self.assertRaisesRegex(ValueError, "high, normal, or low"):
            self.inbox.list_questions("project.inbox", priority="urgent")


if __name__ == "__main__":
    unittest.main()
