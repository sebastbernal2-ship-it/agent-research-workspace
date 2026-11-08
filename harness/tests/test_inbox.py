import tempfile
import unittest
from pathlib import Path

from harness.event_store import SQLiteEventStore
from harness.inbox import InboxApplication, InboxForbidden, InboxNotFound, InboxReadModel
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


class StaticPrincipalProvider:
    def __init__(self, actor):
        self.actor = actor

    def current_actor(self):
        return self.actor


class AllowlistProjectAccess:
    def __init__(self, allowed_projects):
        self.allowed_projects = set(allowed_projects)

    def require_project_access(self, actor, project_id, action):
        if project_id not in self.allowed_projects:
            raise InboxForbidden("project access denied")


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
        self.principal = StaticPrincipalProvider(USER)
        self.access = AllowlistProjectAccess({"project.inbox"})
        self.application = InboxApplication(self.inbox, self.control, self.principal, self.access)

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

    def test_application_uses_injected_principal_and_requires_project_access(self):
        item = self.application.get_question("project.inbox", "question.normal")
        answered = self.application.answer_question(
            "project.inbox", "question.normal", expected_etag=item["etag"],
            text="Proceed with the scoped review.", occurred_at="2026-04-16T12:01:00Z",
        )
        self.assertEqual(answered["status"], "answered")
        with self.assertRaises(InboxForbidden):
            self.application.list_questions("project.other")

        self.principal.actor = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}
        with self.assertRaisesRegex(InboxForbidden, "authenticated human user"):
            self.application.list_questions("project.inbox")

    def test_application_requires_current_etag_for_answer_and_closure(self):
        item = self.application.get_question("project.inbox", "question.high")
        with self.assertRaisesRegex(ValueError, "ETag precondition"):
            self.application.answer_question(
                "project.inbox", "question.high", expected_etag="",
                text="Proceed.",
            )
        closed = self.application.close_question(
            "project.inbox", "question.high", new_status="rejected",
            expected_etag=item["etag"], reason="Not needed for this project.",
            occurred_at="2026-04-16T12:01:00Z",
        )
        self.assertEqual(closed["status"], "rejected")

    def test_invalid_filters_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "nonempty subset"):
            self.inbox.list_questions("project.inbox", statuses=())
        with self.assertRaisesRegex(ValueError, "high, normal, or low"):
            self.inbox.list_questions("project.inbox", priority="urgent")


if __name__ == "__main__":
    unittest.main()
