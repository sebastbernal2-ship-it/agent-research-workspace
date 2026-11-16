import json
import tempfile
import unittest
from pathlib import Path

from ui.prototype.runtime import PrototypeRuntime
from ui.prototype.vp import VPError


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.board = {"tasks": [
            {"key": "a", "title": "Survey options", "objective": "Compare the candidates.", "workType": "research", "acceptanceCriteria": ["Written summary"]},
            {"key": "b", "title": "Write recommendation", "objective": "Recommend one.", "workType": "writing", "dependsOn": ["a", "zzz"]},
        ]}
        self.orchestrator = {"action": "execute"}
        self.worker_reply = "Did the thing.\nDONE: finished"
        self.review_verdict = {"verdict": "accept"}

    def available(self):
        return True

    def think(self, system, payload):
        self.calls.append(("think", system[:20]))
        if "independent reviewer" in system:
            return json.dumps(self.review_verdict)
        if "orchestrator" in system:
            return "```json\n" + json.dumps(self.orchestrator) + "\n```"
        return json.dumps(self.board)

    def work(self, system, prompt, cwd):
        self.calls.append(("work", str(cwd)))
        (Path(cwd) / "out.txt").write_text("x")
        return self.worker_reply


class VPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = FakeRunner()
        self.rt = PrototypeRuntime(Path(self.temp.name) / "c.sqlite3", workspace_root=Path(self.temp.name) / "ws", runner=self.runner)
        self.pid = self.rt.create_project({"mode": "new", "name": "Garden", "goal": "Grow tomatoes"})["projectId"]

    def align(self):
        for i in range(1, 6):
            q = self.rt.get_question(self.pid, f"question.alignment.{i:02d}")
            self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": q["etag"], "text": f"answer {i}"})

    def work(self):
        return {w["title"]: w for w in self.rt.project_summary(self.pid)["workItems"]}

    def test_new_project_gets_interview_then_board_draft(self):
        self.assertEqual(self.rt.project_summary(self.pid)["alignment"]["status"], "in_progress")
        self.align()
        self.assertEqual(self.rt.project_summary(self.pid)["alignment"]["status"], "completed")
        created = self.rt.draft_board(self.pid)["workItems"]
        self.assertEqual([w["status"] for w in created], ["proposed", "proposed"])
        self.assertEqual(created[1]["dependsOn"], [created[0]["workId"]])  # unknown dep dropped
        with self.assertRaises(Exception):
            self.rt.draft_board(self.pid)  # board no longer empty

    def test_nothing_runs_until_approved_then_in_dependency_order(self):
        self.align(); self.rt.draft_board(self.pid)
        self.assertIsNone(self.rt.vp.tick(self.pid))
        a = self.work()["Survey options"]
        self.rt.approve_work(self.pid, a["workId"], {"expectedRevision": a["revision"], "decision": "approve"})
        self.assertEqual(self.rt.vp.tick(self.pid), a["workId"])
        self.assertEqual(self.work()["Survey options"]["status"], "completed")
        self.assertTrue((self.rt.workspace_root / self.pid / "out.txt").exists())
        self.assertIsNone(self.rt.vp.tick(self.pid))  # b is only proposed
        activity = self.rt.activity(self.pid)
        self.assertIn("finished", activity[0]["text"])

    def test_auto_run_approves_and_runs_everything(self):
        self.align(); self.rt.draft_board(self.pid)
        self.rt.set_automation(self.pid, {"autoRun": True})
        while self.rt.vp.tick(self.pid):
            pass
        self.assertEqual({w["status"] for t, w in self.work().items() if t != "Guided repository alignment"}, {"completed"})

    def test_split_creates_child_steps(self):
        self.align(); self.rt.draft_board(self.pid)
        self.rt.set_automation(self.pid, {"autoRun": True})
        self.runner.orchestrator = {"action": "split", "subtasks": [{"title": "Step 1", "objective": "o1"}, {"title": "Step 2", "objective": "o2"}]}
        self.rt.vp.tick(self.pid)
        items = self.work()
        self.assertEqual(items["Survey options"]["status"], "completed")
        self.assertEqual(items["Step 1"]["parentWorkId"], items["Survey options"]["workId"])
        self.assertEqual(items["Step 2"]["status"], "completed")

    def test_needs_input_opens_question_and_resumes_after_answer(self):
        self.align(); self.rt.draft_board(self.pid)
        self.rt.set_automation(self.pid, {"autoRun": True})
        self.runner.worker_reply = "Stuck.\nNEEDS_INPUT: Which variety?"
        self.rt.vp.tick(self.pid)
        self.assertEqual(self.work()["Survey options"]["status"], "waiting_human")
        questions = self.rt.list_questions(self.pid)["questions"]
        self.assertEqual(len(questions), 1)
        q = self.rt.get_question(self.pid, questions[0]["questionId"])
        self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": q["etag"], "text": "Roma"})
        self.runner.worker_reply = "ok\nDONE: used Roma"
        self.rt.vp.tick(self.pid)
        self.assertEqual(self.work()["Survey options"]["status"], "completed")

    def test_worker_failure_marks_failed_not_stuck(self):
        self.align(); self.rt.draft_board(self.pid)
        self.rt.set_automation(self.pid, {"autoRun": True})
        def boom(*a):
            raise VPError("model timed out")
        self.runner.work = boom
        self.rt.vp.tick(self.pid)
        self.assertEqual(self.work()["Survey options"]["status"], "failed")

    def test_demo_project_never_runs_or_toggles(self):
        with self.assertRaises(ValueError):
            self.rt.set_automation("project.northstar", {"autoRun": True})
        with self.assertRaises(ValueError):
            self.rt.draft_board("project.northstar")


if __name__ == "__main__":
    unittest.main()


class RedModeTests(unittest.TestCase):
    def test_red_endpoint_must_be_loopback_and_unconfigured_is_unavailable(self):
        from ui.prototype.pi_chat import LocalModelChat, PiChatError
        with self.assertRaises(ValueError):
            LocalModelChat("http://example.com/v1", "m")
        red = LocalModelChat("", "")
        self.assertFalse(red.status()["available"])
        with self.assertRaises(PiChatError):
            red.send("hi", [])
        self.assertTrue(LocalModelChat("http://127.0.0.1:8080/v1", "qwen").status()["available"])


class AdaptiveInterviewTests(VPTests):
    def test_followups_are_asked_before_the_interview_completes(self):
        self.runner.think_orig = self.runner.think
        asked = []
        def think(system, payload):
            if "interviewing" in system:
                reply = {"questions": [] if asked else [{"title": "Budget", "prompt": "What is the budget?"}]}
                asked.append(1)
                return json.dumps(reply)
            return self.runner.think_orig(system, payload)
        self.runner.think = think
        self.align()
        summary = self.rt.project_summary(self.pid)
        self.assertEqual(summary["alignment"]["status"], "in_progress")
        q = self.rt.list_questions(self.pid)["questions"][0]
        self.assertIn("Follow-up", q["questionText"])
        full = self.rt.get_question(self.pid, q["questionId"])
        self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": full["etag"], "text": "100 dollars"})
        self.assertEqual(self.rt.project_summary(self.pid)["alignment"]["status"], "completed")
        self.assertEqual(self.rt.list_questions(self.pid)["questions"], [])


class RecoveryTests(VPTests):
    def test_restart_fails_interrupted_work_and_retry_requeues_it(self):
        self.align(); self.rt.draft_board(self.pid)
        a = self.work()["Survey options"]
        self.rt.approve_work(self.pid, a["workId"], {"expectedRevision": a["revision"], "decision": "approve"})
        self.rt.control.transition_work(self.pid, a["workId"], "running", actor={"actorType": "agent", "actorId": "agent.orchestrator", "role": "orchestrator"})
        self.assertEqual(self.rt.vp.recover(self.pid), 1)
        failed = self.work()["Survey options"]
        self.assertEqual(failed["status"], "failed")
        self.rt.approve_work(self.pid, failed["workId"], {"expectedRevision": failed["revision"], "decision": "retry"})
        self.assertEqual(self.work()["Survey options"]["status"], "ready")
        self.assertEqual(self.rt.vp.tick(self.pid), a["workId"])
        self.assertEqual(self.work()["Survey options"]["status"], "completed")


class WorktreeCommitTests(VPTests):
    def test_review_task_depends_on_previous_and_edits_are_committed_on_branch(self):
        import subprocess
        repo = Path(self.temp.name) / "repo"
        repo.mkdir()
        run = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True)
        run("init", "-q"); (repo / "README.md").write_text("old\n"); run("add", "-A"); run("commit", "-qm", "init")
        rep = self.rt.inspect_repository(str(repo))
        pid = self.rt.create_project({"mode": "existing", "name": "R", "goal": "g", "repositoryPath": str(repo), "inspectionFingerprint": rep["fingerprint"]})["projectId"]
        self.pid = pid
        self.runner.board = {"tasks": [
            {"key": "a", "title": "Edit README", "objective": "o", "workType": "writing"},
            {"key": "b", "title": "Check README", "objective": "o", "workType": "review"}]}
        while self.rt.list_questions(pid)["questions"]:
            q = self.rt.get_question(pid, self.rt.list_questions(pid)["questions"][0]["questionId"])
            self.rt.answer_question(pid, q["questionId"], {"expectedEtag": q["etag"], "text": "x"})
        tasks = self.rt.draft_board(pid)["workItems"]
        self.assertEqual(tasks[1]["dependsOn"], [tasks[0]["workId"]])
        self.rt.set_automation(pid, {"autoRun": True})
        def work(system, prompt, cwd):
            (Path(cwd) / "README.md").write_text("new\n")
            return "ok\nDONE: edited"
        self.runner.work = work
        self.rt.vp.tick(pid)
        worktree = self.rt.workspace_root / pid / "repo"
        log = run("-C", str(worktree), "log", "--oneline").stdout
        self.assertIn("Edit README", log)
        self.assertEqual((repo / "README.md").read_text(), "old\n")  # owner's checkout untouched
        self.assertIn("Committed", self.rt.activity(pid)[1]["text"] + self.rt.activity(pid)[0]["text"])


class ReviewerTests(VPTests):
    def test_reviewer_challenge_triggers_one_revision_and_is_visible(self):
        self.align(); self.rt.draft_board(self.pid)
        self.rt.set_automation(self.pid, {"autoRun": True})
        self.runner.review_verdict = {"verdict": "revise", "feedback": "Add the missing comparison table."}
        prompts = []
        orig = self.runner.work
        def work(system, prompt, cwd):
            prompts.append(json.loads(prompt)); return orig(system, prompt, cwd)
        self.runner.work = work
        self.rt.vp.tick(self.pid)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[1]["reviewerFeedback"], "Add the missing comparison table.")
        self.assertEqual(self.work()["Survey options"]["status"], "completed")
        self.assertTrue(any(a["actor"] == "Reviewer" for a in self.rt.activity(self.pid)))

    def test_downstream_task_receives_upstream_report(self):
        self.align(); self.rt.draft_board(self.pid)
        self.rt.set_automation(self.pid, {"autoRun": True})
        seen = []
        orig = self.runner.work
        def work(system, prompt, cwd):
            seen.append(json.loads(prompt)); return orig(system, prompt, cwd)
        self.runner.work = work
        self.rt.vp.tick(self.pid); self.rt.vp.tick(self.pid)
        self.assertEqual(seen[1]["reportsFromTasksYouDependOn"][0]["task"], "Survey options")
        self.assertIn("finished", seen[1]["reportsFromTasksYouDependOn"][0]["report"])


class ParallelTests(VPTests):
    def test_independent_tasks_run_concurrently_without_write_conflicts(self):
        import threading
        self.runner.board = {"tasks": [{"key": f"t{i}", "title": f"Task {i}", "objective": "o", "workType": "research"} for i in range(3)]}
        self.align(); self.rt.draft_board(self.pid)
        self.rt.set_automation(self.pid, {"autoRun": True})
        barrier = threading.Barrier(2, timeout=15)  # only passes if two workers are inside work() at once
        def work(system, prompt, cwd):
            barrier.wait()
            for _ in range(5):  # many writes from both threads
                pass
            return "ok\nDONE: parallel"
        self.runner.work = work
        self.rt.vp.tick(self.pid)  # first batch: two tasks in parallel (default NORTHSTAR_PARALLEL=2)
        done = [w for t, w in self.work().items() if t.startswith("Task") and w["status"] == "completed"]
        self.assertEqual(len(done), 2)
        self.runner.work = lambda s, p, c: "ok\nDONE: last"
        self.rt.vp.tick(self.pid)
        self.assertEqual(len([1 for t, w in self.work().items() if t.startswith("Task") and w["status"] == "completed"]), 3)
        self.rt.store.verified_events(self.pid)  # hash chain still verifies after concurrent writes
