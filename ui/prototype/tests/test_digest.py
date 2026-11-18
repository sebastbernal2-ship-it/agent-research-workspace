import tempfile
import unittest
from pathlib import Path

from ui.prototype.tests.test_phases import PhaseBase


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


if __name__ == "__main__":
    unittest.main()
