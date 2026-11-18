import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ui.prototype.checks import CHECKS_FILE, load_checks, run_checks
from ui.prototype.tests.test_phases import PhaseBase


def register(directory: Path, checks) -> None:
    (directory / CHECKS_FILE).parent.mkdir(parents=True, exist_ok=True)
    (directory / CHECKS_FILE).write_text(json.dumps({"checks": checks}), encoding="utf-8")


class CheckRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def test_only_well_formed_checks_load_and_shell_strings_are_rejected(self):
        register(self.dir, [
            {"id": "ok", "criterion": "works", "command": ["python3", "-c", "pass"]},
            {"id": "shell", "criterion": "bad", "command": "rm -rf /"},          # a shell string, not an argv list
            {"id": "bad id!", "criterion": "bad", "command": ["true"]},
            {"id": "empty", "criterion": " ", "command": ["true"]},
            {"id": "args", "criterion": "bad", "command": ["x", 3]},
        ])
        self.assertEqual([c["id"] for c in load_checks(self.dir)], ["ok"])
        (self.dir / CHECKS_FILE).write_text("not json")
        self.assertEqual(load_checks(self.dir), [])
        self.assertEqual(load_checks(self.dir / "missing"), [])

    def test_results_show_pass_fail_output_and_a_timeout_counts_as_failure(self):
        register(self.dir, [
            {"id": "pass", "criterion": "p", "command": ["python3", "-c", "print('fine')"]},
            {"id": "fail", "criterion": "f", "command": ["python3", "-c", "import sys; print('broken'); sys.exit(3)"]},
            {"id": "missing", "criterion": "m", "command": ["definitely-not-a-program"]},
        ])
        by_id = {r["id"]: r for r in run_checks(self.dir)}
        self.assertTrue(by_id["pass"]["passed"])
        self.assertIn("fine", by_id["pass"]["output"])
        self.assertFalse(by_id["fail"]["passed"])
        self.assertIn("broken", by_id["fail"]["output"])
        self.assertFalse(by_id["missing"]["passed"])
        with mock.patch("ui.prototype.checks.TIMEOUT_SECONDS", 1):
            slow = run_checks(self.dir, [{"id": "slow", "criterion": "s", "command": ["python3", "-c", "import time; time.sleep(5)"]}])
        self.assertFalse(slow[0]["passed"])
        self.assertIn("timed out", slow[0]["output"])

    def test_checks_run_in_the_work_directory_without_secrets_in_the_environment(self):
        script = "import os,sys; sys.exit(1 if any(k.endswith(('KEY','TOKEN')) for k in os.environ) else 0)"
        register(self.dir, [{"id": "env", "criterion": "no secrets", "command": ["python3", "-c", script]},
                            {"id": "cwd", "criterion": "cwd", "command": ["python3", "-c", f"import os,sys; sys.exit(0 if os.path.realpath(os.getcwd())==os.path.realpath({str(self.dir)!r}) else 1)"]}])
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret", "SOME_TOKEN": "x"}):
            results = run_checks(self.dir)
        self.assertEqual([r["passed"] for r in results], [True, True])


class PhaseChecksTests(PhaseBase):
    def setUp(self):
        super().setUp()
        self.rt.plan_phases(self.pid)
        self.accept()

    def work_dir(self) -> Path:
        directory = self.rt.vp.work_directory(self.pid, self.state())
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def test_a_failing_executable_check_blocks_done_even_when_the_model_says_complete_and_passing_unblocks_it(self):
        register(self.work_dir(), [{"id": "tests", "criterion": "All tests pass", "command": ["python3", "-c", "import sys; print('1 failed'); sys.exit(1)"]}])
        self.steer([True, True], status="complete")
        self.steer([True, True], status="complete")
        self.run_ticks(3)
        first = self.runner.steer_calls[0]
        self.assertEqual([(c["id"], c["passed"]) for c in first["executableChecks"]], [("tests", False)])
        self.assertIn("1 failed", first["executableChecks"][0]["outputTail"])
        phase = self.phases()[0]
        self.assertNotEqual(phase["status"], "verifying")                       # the model said complete; the check overruled it
        self.assertEqual(phase["checks"], [{"id": "tests", "criterion": "All tests pass", "passed": False}])
        self.assertTrue(any("tests" in c and "failing" in c for c in phase["caveats"]))
        register(self.work_dir(), [{"id": "tests", "criterion": "All tests pass", "command": ["python3", "-c", "pass"]}])  # fixed
        self.run_ticks(8)
        self.assertEqual(self.phases()[0]["status"], "verifying")               # now it can be complete
        self.assertEqual(self.phases()[0]["checks"][0]["passed"], True)

    def test_the_independent_reviewer_is_shown_the_check_results_and_told_to_treat_them_as_ground_truth(self):
        from ui.prototype.phases import PHASE_REVIEW_SYSTEM
        register(self.work_dir(), [{"id": "tests", "criterion": "All tests pass", "command": ["python3", "-c", "print('12 passed')"]}])
        self.steer([True, True], status="complete")
        self.run_ticks(4)
        shown = self.runner.review_calls[0]["executableChecks"]
        self.assertEqual([(c["id"], c["passed"], c["command"]) for c in shown], [("tests", True, ["python3", "-c", "print('12 passed')"])])
        self.assertIn("12 passed", shown[0]["outputTail"])
        self.assertIn("ground truth", PHASE_REVIEW_SYSTEM)

    def test_no_registered_checks_changes_nothing(self):
        self.steer([True, True], status="complete")
        self.run_ticks(4)
        self.assertEqual(self.runner.steer_calls[0]["executableChecks"], [])
        self.assertEqual(self.phases()[0]["status"], "verifying")


class CheckpointReviewTests(PhaseBase):
    def test_once_reviews_are_used_up_the_checkpoint_still_shows_the_latest_real_review(self):
        self.rt.plan_phases(self.pid)
        self.accept()
        gap = lambda n, summary: {"verdict": "gaps", "summary": summary,
                                  "gaps": [{"title": f"Splitter mishandles case {n}", "detail": "seen in a real sample", "severity": "blocking"}]}
        self.runner.reviews = [gap(1, "First look found the splitter mishandles case 1."), gap(2, "Second look found case 2.")]
        for _ in range(3):
            self.steer([True, True], status="complete")
        self.run_ticks(30)
        self.assertEqual(len(self.runner.review_calls), 2)                  # capped: the third assessment does not review again
        text = next(q["questionText"] for q in self.rt.control.projector.replay(self.pid)["questions"].values()
                    if q["questionId"].startswith("question.phase-") and q["status"] == "open")
        self.assertIn("Second look found case 2.", text)
        self.assertNotIn("not available this time", text)


if __name__ == "__main__":
    unittest.main()
