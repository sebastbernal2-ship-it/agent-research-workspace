import tempfile
import unittest
from pathlib import Path

from ui.prototype.runtime import PrototypeRuntime
from ui.prototype.vp import NullRunner, project_memory


class IdeaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "c.sqlite3"
        self.rt = PrototypeRuntime(self.db, workspace_root=Path(self.temp.name) / "ws", runner=NullRunner())

    def test_capture_list_edit_archive_and_survive_a_restart(self):
        a = self.rt.add_idea({"title": "A dashboard for my reading", "note": "Track books and pull out the best quotes.\nMaybe a weekly digest."})["idea"]
        b = self.rt.add_idea({"title": "Garden planner"})["idea"]
        self.assertEqual([i["title"] for i in self.rt.list_ideas()["ideas"]], ["Garden planner", "A dashboard for my reading"])  # newest first
        self.assertEqual((a["status"], a["projectId"]), ("idea", None))
        edited = self.rt.update_idea(a["id"], {"title": "Reading dashboard", "note": "Quotes + digest."})["idea"]
        self.assertEqual((edited["title"], edited["note"]), ("Reading dashboard", "Quotes + digest."))
        self.assertEqual(self.rt.update_idea(b["id"], {"action": "archive"})["idea"]["status"], "archived")
        self.assertEqual(self.rt.update_idea(b["id"], {"action": "unarchive"})["idea"]["status"], "idea")
        restarted = PrototypeRuntime(self.db, workspace_root=Path(self.temp.name) / "ws", runner=NullRunner())
        self.assertEqual({i["title"] for i in restarted.list_ideas()["ideas"]}, {"Reading dashboard", "Garden planner"})  # persisted

    def test_validation(self):
        for bad in ({}, {"note": "x"}, {"title": ""}, {"title": "x" * 141}, {"title": "ok", "note": "n" * 4001}, {"title": "ok", "extra": 1}):
            with self.assertRaises(ValueError):
                self.rt.add_idea(bad)
        idea = self.rt.add_idea({"title": "ok"})["idea"]
        for bad in ({}, {"action": "explode"}, {"status": "started"}, {"title": ""}):
            with self.assertRaises(ValueError):
                self.rt.update_idea(idea["id"], bad)
        with self.assertRaises(Exception):
            self.rt.update_idea("idea-missing", {"title": "x"})

    def test_starting_an_idea_creates_a_project_that_carries_the_owners_words(self):
        idea = self.rt.add_idea({"title": "Reading dashboard", "note": "Track books and pull out the best quotes.\nWeekly digest by email, nothing fancy."})["idea"]
        out = self.rt.start_idea(idea["id"])
        project, started = out["project"], out["idea"]
        self.assertEqual(project["name"], "Reading dashboard")
        self.assertEqual(project["charter"]["goals"], ["Track books and pull out the best quotes."])  # the first line becomes the goal
        self.assertEqual((started["status"], started["projectId"]), ("started", project["projectId"]))
        state = self.rt.control.projector.replay(project["projectId"])
        self.assertTrue(any(q["questionId"] == "question.alignment.01" and q["status"] == "open" for q in state["questions"].values()))  # the interview starts
        logs = project_memory(state)["detailedLogs"]
        self.assertEqual(logs[0]["title"], "The original idea: Reading dashboard")
        self.assertIn("Weekly digest by email, nothing fancy.", logs[0]["detail"])  # everything they wrote, in full, reaches every agent
        with self.assertRaises(Exception):
            self.rt.start_idea(idea["id"])  # an idea becomes a project once

    def test_an_idea_with_no_note_uses_its_title_as_the_goal_and_archived_ideas_cannot_start(self):
        idea = self.rt.add_idea({"title": "Garden planner"})["idea"]
        project = self.rt.start_idea(idea["id"])["project"]
        self.assertEqual(project["charter"]["goals"], ["Garden planner"])
        other = self.rt.add_idea({"title": "Old thought"})["idea"]
        self.rt.update_idea(other["id"], {"action": "archive"})
        with self.assertRaises(Exception):
            self.rt.start_idea(other["id"])


if __name__ == "__main__":
    unittest.main()
