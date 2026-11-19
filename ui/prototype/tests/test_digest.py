import tempfile
import unittest
from pathlib import Path

from ui.prototype.tests.test_phases import PhaseBase
from ui.prototype.tests.test_vp import VPBase


class DigestTests(PhaseBase):
    def test_digest_reports_what_changed_in_product_terms_and_resets_when_marked_seen(self):
        self.rt.plan_phases(self.pid)
        first = self.rt.project_digest(self.pid)
        self.assertEqual(first["finished"], [])
        self.assertEqual([p["title"] for p in first["phases"]], ["Data readiness", "Model inputs"])
        self.assertTrue(any("phase" in n["questionId"] for n in first["needsYou"]))            # the plan is waiting for the owner
        self.assertIn("decision", first["headline"])
        self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(4)
        d = self.rt.project_digest(self.pid)
        self.assertEqual([f["title"] for f in d["finished"]], ["Build splitter"])
        self.assertEqual(d["finished"][0]["phase"], "Data readiness")
        self.assertIn("Built it", d["finished"][0]["summary"])                                  # the worker's own words, shortened
        self.assertIn("1 task finished", d["headline"])
        self.assertFalse(d["empty"])
        self.rt.mark_seen(self.pid)
        after = self.rt.project_digest(self.pid)
        self.assertEqual(after["finished"], [])
        self.assertEqual(after["newIssues"], [])
        self.assertGreaterEqual(len(after["needsYou"]), 1)                                      # open decisions are never hidden by "seen"
        self.assertEqual(after["phases"][0]["status"], "verifying")

    def test_marker_is_per_project_and_survives_a_restart(self):
        self.rt.mark_seen(self.pid)
        again = type(self.rt)(Path(self.rt.store.path), workspace_root=self.rt.workspace_root, runner=self.runner)
        self.assertIsNotNone(again.last_seen.get(self.pid))
        self.assertIsNone(again.last_seen.get("project.other"))

    def test_unknown_project_cannot_be_marked(self):
        with self.assertRaises(Exception):
            self.rt.mark_seen("project.nope")


class StaleQuestionTests(VPBase):
    def test_an_open_question_whose_blocked_task_already_finished_is_hidden_from_needs_you_and_the_digest(self):
        from ui.prototype.vp import SUBAGENT, VP
        self.align()
        w = self.rt.vp._new_work(self.pid, "Blocked once", "obj", "research", "ready", SUBAGENT, [], ["done"], None, VP)
        orch = {"actorType": "agent", "actorId": "agent.orchestrator", "role": "orchestrator"}
        self.rt.control.transition_work(self.pid, w["workId"], "running", actor=orch)
        self.rt.vp._ask(self.pid, w, "Which repo should this target?")
        open_ids = lambda: [q["questionId"] for q in self.rt.list_questions(self.pid)["questions"] if q["questionId"].startswith("question.vp-")]
        self.assertEqual(len(open_ids()), 1)                                       # a live blocking question is shown
        self.assertEqual(len(self.rt.project_digest(self.pid)["needsYou"]), 1)
        # the task finishes some other way, leaving the ledger's question open and un-closable (the control plane forbids closing it)
        store = self.rt.control.projector.replay(self.pid)["workItems"][w["workId"]]
        self.assertEqual(store["status"], "waiting_human")
        stale = self.rt._is_stale_question({"blocking": True, "workIds": [w["workId"]]}, {w["workId"]: {"status": "completed"}})
        live = self.rt._is_stale_question({"blocking": True, "workIds": [w["workId"]]}, {w["workId"]: {"status": "waiting_human"}})
        self.assertTrue(stale)
        self.assertFalse(live)
        self.assertFalse(self.rt._is_stale_question({"blocking": False, "workIds": [w["workId"]]}, {w["workId"]: {"status": "completed"}}))  # non-blocking stays


if __name__ == "__main__":
    unittest.main()
