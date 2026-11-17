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


class VPBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = FakeRunner()
        self.rt = PrototypeRuntime(Path(self.temp.name) / "c.sqlite3", workspace_root=Path(self.temp.name) / "ws", runner=self.runner)
        self.rt.sync_memory = True  # no background threads outliving a test
        self.pid = self.rt.create_project({"mode": "new", "name": "Garden", "goal": "Grow tomatoes"})["projectId"]

    def align(self):
        for i in range(1, 6):
            q = self.rt.get_question(self.pid, f"question.alignment.{i:02d}")
            self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": q["etag"], "text": f"answer {i}"})

    def work(self):
        return {w["title"]: w for w in self.rt.project_summary(self.pid)["workItems"]}



class VPTests(VPBase):
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


class AdaptiveInterviewTests(VPBase):
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


class RecoveryTests(VPBase):
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


class WorktreeCommitTests(VPBase):
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


class ReviewerTests(VPBase):
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


class ParallelTests(VPBase):
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


class RepoChatTests(VPBase):
    def test_chat_reads_from_the_repository_not_an_empty_directory(self):
        import subprocess
        repo = Path(self.temp.name) / "repo2"; repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], check=True)
        rep = self.rt.inspect_repository(str(repo))
        pid = self.rt.create_project({"mode": "existing", "name": "R", "goal": "g", "repositoryPath": str(repo), "inspectionFingerprint": rep["fingerprint"]})["projectId"]
        seen = {}
        self.runner.explore = lambda system, prompt, cwd: seen.update(cwd=cwd, system=system) or "I read README.md"
        reply = self.rt.send_project_chat(pid, {"message": "what exists?", "history": []})
        self.assertEqual(reply["response"], "I read README.md")
        self.assertEqual(Path(seen["cwd"]).resolve(), repo.resolve())
        self.assertIn("READ and SEARCH", seen["system"])
        self.assertTrue(self.rt.chat_status(pid)["available"])


class DurableChatTests(VPBase):
    def test_thread_survives_restart_stays_bounded_and_drafts_an_answer(self):
        self.align()
        self.runner.explore = lambda system, prompt, cwd: "VP reply"
        self.runner.summarized = 0
        orig = self.runner.think
        def think(system, payload):
            if "Summarize an owner-and-VP conversation" in system:
                self.runner.summarized += 1
                return "Owner wants a small guide; no photos."
            if "Write the owner's answer" in system:
                return "I decided: small guide. Open: sources."
            return orig(system, payload)
        self.runner.think = think
        q = self.rt.list_questions(self.pid)["questions"]
        for i in range(12):  # 24 persisted turns: forces a summary, never an unbounded prompt
            self.rt.chat_provider.fallback  # RepoChat in use
            self.rt.send_project_chat(self.pid, {"message": f"turn {i}", "history": []})
        sizes = []
        restarted = PrototypeRuntime(Path(self.temp.name) / "c.sqlite3", workspace_root=Path(self.temp.name) / "ws", runner=self.runner)
        turns = restarted.chat_history(self.pid)["turns"]
        self.assertEqual(len(turns), 24)  # every turn is still there after a restart
        summary, recent = restarted._chat_context(self.pid, "thread.chat.project")
        self.assertGreaterEqual(self.runner.summarized, 1)
        self.assertIn("small guide", summary)
        self.assertLessEqual(len(recent), 14)  # bounded window, however long the thread

    def _converse_with(self, data, message="hello"):
        self.runner.explore = lambda system, prompt, cwd: json.dumps(data) if isinstance(data, dict) else data
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        return q, self.rt.converse(self.pid, q, {"message": message})


class ConversationTests(VPBase):
    def test_btw_is_answered_saved_and_notes_reach_later_context(self):
        self.runner.explore = lambda s, p, c: json.dumps({"intent": "btw", "reply": "The repo has no topic model.", "learned": ["Owner wants no photos"]})
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        out = self.rt.converse(self.pid, q, {"message": "btw what exists?"})
        self.assertEqual((out["proposedAnswer"], out["notesSaved"]), (None, 1))
        self.assertEqual(self.rt.list_questions(self.pid)["questions"][0]["questionId"], q)  # still open
        self.assertEqual([t["role"] for t in self.rt.chat_history(self.pid, q)["turns"]], ["user", "assistant"])  # notes are memory, not chat bubbles
        from ui.prototype.vp import owner_notes
        self.assertEqual(owner_notes(self.rt.control.projector.replay(self.pid)), ["Owner wants no photos"])
        self.assertEqual(self.rt.vp._context(self.rt.control.projector.replay(self.pid))["ownerMemory"]["notYetMergedNotes"], ["Owner wants no photos"])

    def test_settling_message_only_proposes_the_owner_confirms_by_recording(self):
        self.runner.explore = lambda s, p, c: json.dumps({"intent": "answer", "reply": "Recording: keep the goal.",
            "answer": {"selectedOptionId": "option.alignment.keep-goal", "text": "and keep it simple"}, "learned": []})
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        out = self.rt.converse(self.pid, q, {"message": "keep it as is, simple"})
        self.assertEqual(out["proposedAnswer"]["selectedOptionId"], "option.alignment.keep-goal")
        self.assertIn("\n\nNote: and keep it simple", out["proposedAnswer"]["text"])
        self.assertEqual(self.rt.list_questions(self.pid)["questions"][0]["questionId"], q)  # nothing recorded yet
        full = self.rt.get_question(self.pid, q)
        self.rt.answer_question(self.pid, q, {"expectedEtag": full["etag"], **out["proposedAnswer"]})
        self.assertNotEqual(self.rt.list_questions(self.pid)["questions"][0]["questionId"], q)

    def test_unparseable_model_output_degrades_to_a_plain_reply_and_bad_option_ids_are_dropped(self):
        self.runner.explore = lambda s, p, c: "Just a plain sentence."
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        self.assertEqual(self.rt.converse(self.pid, q, {"message": "hi"})["reply"], "Just a plain sentence.")
        self.runner.explore = lambda s, p, c: json.dumps({"intent": "answer", "reply": "ok", "answer": {"selectedOptionId": "bogus", "text": "mine"}})
        out = self.rt.converse(self.pid, q, {"message": "mine"})
        self.assertEqual(out["proposedAnswer"], {"text": "mine", "selectedOptionId": None})


class MemoryTests(VPBase):
    MEMORY_V1 = {"vision": ["Build an extraction system first."], "decisions": ["No models yet."], "definitions": [],
                 "constraints": [], "deferred": ["Prediction inputs"], "open_questions": [], "corrections": []}

    def setUp(self):
        super().setUp()
        self.rt.sync_memory = True
        self.curator_calls = []
        self.curator_fail = False
        self.converse_reply = {"intent": "btw", "reply": "ok", "learned": []}
        self.next_memory = self.MEMORY_V1
        def explore(system, prompt, cwd):  # the curator and the converse step both use the repo-reading call
            if "curate the project's long-term memory" in system:
                if self.curator_fail:
                    raise VPError("model down")
                self.curator_calls.append(json.loads(prompt))
                return json.dumps(self.next_memory)
            return json.dumps(self.converse_reply)
        self.runner.explore = explore

    def _say(self, learned, text="ok", intent="btw", answer=None):
        self.converse_reply = {"intent": intent, "reply": text, "learned": learned, **({"answer": answer} if answer else {})}
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        return q, self.rt.converse(self.pid, q, {"message": "msg"})

    def test_recorded_answer_is_only_the_concise_consolidation_not_the_whole_thread(self):
        q, out = self._say([], intent="answer", answer={"selectedOptionId": None, "text": "Extraction system first; models later."})
        for i in range(10):
            self.rt.converse(self.pid, q, {"message": f"chatter {i}"})
        full = self.rt.get_question(self.pid, q)
        self.rt.answer_question(self.pid, q, {"expectedEtag": full["etag"], **out["proposedAnswer"]})
        recorded = self.rt.control.projector.replay(self.pid)["questions"][q]["answer"]["text"]
        self.assertEqual(recorded, "Extraction system first; models later.")
        self.assertNotIn("chatter", recorded)

    def test_notes_are_visible_immediately_then_merged_into_curated_memory_after_the_answer(self):
        from ui.prototype.vp import project_memory
        q, out = self._say(["Models are deferred."], intent="answer", answer={"selectedOptionId": None, "text": "Models later."})
        pm = project_memory(self.rt.control.projector.replay(self.pid))
        self.assertEqual(pm["notYetMergedNotes"], ["Models are deferred."])  # usable before curation runs
        full = self.rt.get_question(self.pid, q)
        self.rt.answer_question(self.pid, q, {"expectedEtag": full["etag"], **out["proposedAnswer"]})  # triggers curation
        pm = project_memory(self.rt.control.projector.replay(self.pid))
        self.assertEqual(pm["memory"]["decisions"], ["No models yet."])
        self.assertEqual(pm["notYetMergedNotes"], [])  # merged, so no longer duplicated
        self.assertEqual(self.curator_calls[0]["newNotes"], ["Models are deferred."])
        # every VP call (board drafting, orchestrator, workers) gets the memory
        self.assertEqual(self.rt.vp._context(self.rt.control.projector.replay(self.pid))["ownerMemory"]["memory"]["vision"], ["Build an extraction system first."])

    def test_change_of_mind_replaces_the_old_statement_and_output_is_bounded(self):
        from ui.prototype.vp import project_memory
        self.rt.vp.curate_memory(self.pid)  # first curation from recorded answers
        self.next_memory = {**self.MEMORY_V1, "decisions": ["Models start after extraction."] + [f"d{i}" + "x" * 400 for i in range(40)]}
        self._say(["Actually models can start once extraction exists."])
        self.rt.vp.curate_memory(self.pid)
        mem = project_memory(self.rt.control.projector.replay(self.pid))["memory"]
        self.assertNotIn("No models yet.", mem["decisions"])
        self.assertLessEqual(len(mem["decisions"]), 20)
        self.assertTrue(all(len(x) <= 300 for x in mem["decisions"]))

    def test_curator_failure_is_harmless(self):
        q, _ = self._say(["A note."])
        self.curator_fail = True
        self.assertFalse(self.rt.vp.curate_memory.__self__ is None)
        with self.assertRaises(VPError):
            self.rt.vp.curate_memory(self.pid)  # raises to the caller...
        self.rt._curate_soon(self.pid)  # ...but the runtime wrapper swallows it
        from ui.prototype.vp import uncurated_notes
        self.assertEqual(uncurated_notes(self.rt.control.projector.replay(self.pid)), ["A note."])  # note not lost

    def test_many_notes_per_message_are_kept(self):
        self.converse_reply = {"intent": "btw", "reply": "ok", "learned": [f"point {i}" for i in range(12)]}
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        self.assertEqual(self.rt.converse(self.pid, q, {"message": "long plan"})["notesSaved"], 8)

    def test_rebuild_ignores_stale_memory_and_curator_can_read_the_repo(self):
        self._say(["Real owner statement."])
        self.rt.vp.curate_memory(self.pid)
        self.next_memory = {**self.MEMORY_V1, "decisions": ["Fresh."]}
        self.rt.vp.curate_memory(self.pid, rebuild=True)
        self.assertEqual(self.curator_calls[-1]["currentMemory"]["decisions"], [])  # stale memory not trusted
        self.assertIn("Real owner statement.", self.curator_calls[-1]["newNotes"])  # re-derived from every saved note

    def test_ambiguity_becomes_a_question_to_the_owner_once_and_never_a_guess(self):
        self.next_memory = {**self.MEMORY_V1, "unclear": ["Did you mean topics replace ideas, or sit above them?", "Second?", "Third?"]}
        self._say(["Topics and ideas are related."])
        self.rt.vp.curate_memory(self.pid)
        asked = [q for q in self.rt.list_questions(self.pid)["questions"] if q["questionId"].startswith("question.memory-")]
        self.assertEqual(len(asked), 2)  # capped
        self.assertFalse(asked[0]["blocking"])
        self.assertIn("Did you mean topics replace ideas", " ".join(q["questionText"] for q in asked))
        self.next_memory = {**self.MEMORY_V1, "decisions": ["x"], "unclear": ["Did you mean topics replace ideas, or sit above them?"]}
        self._say(["another note"])
        self.rt.vp.curate_memory(self.pid)
        again = [q for q in self.rt.list_questions(self.pid)["questions"] if q["questionId"].startswith("question.memory-")]
        self.assertEqual(len(again), 2)  # same question is not asked twice
        from ui.prototype.vp import project_memory
        self.assertNotIn("unclear", project_memory(self.rt.control.projector.replay(self.pid))["memory"])  # never stored as memory

    def test_curator_is_told_drafted_answers_are_unconfirmed(self):
        from ui.prototype.vp import MEMORY_SYSTEM
        self.assertIn("[Drafted by Claude", MEMORY_SYSTEM)
        self.assertIn("DO NOT GUESS", MEMORY_SYSTEM)


class ChatLearningTests(VPBase):
    def test_a_correction_typed_in_project_chat_becomes_a_note(self):
        from ui.prototype.vp import uncurated_notes
        self.rt.sync_memory = True
        orig = self.runner.think
        self.runner.think = lambda system, payload: json.dumps({"learned": ["Models may start once extraction exists."]}) if "durable points the OWNER stated" in system else orig(system, payload)
        self.runner.explore = lambda s, p, c: "Got it."
        self.rt.send_project_chat(self.pid, {"message": "That's wrong, models can start once extraction exists.", "history": []})
        self.assertEqual(uncurated_notes(self.rt.control.projector.replay(self.pid)), ["Models may start once extraction exists."])


class RunningDraftTests(VPBase):
    def _converse(self, data):
        self.runner.explore = lambda s, p, c: json.dumps(data)
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        return q, self.rt.converse(self.pid, q, {"message": "my view, and also can you check the repo?"})

    def test_a_tentative_position_given_with_a_question_still_shows_a_draft_that_survives_reload(self):
        q, out = self._converse({"intent": "btw", "reply": "Repo agrees. I'd record your view; tap Record when right.",
                                 "answer": {"selectedOptionId": None, "text": "Topics are 1-3 word concepts (tentative)."}, "learned": []})
        self.assertFalse(out["settled"])
        self.assertIn("1-3 word", out["proposedAnswer"]["text"])
        hist = self.rt.chat_history(self.pid, q)
        self.assertEqual(hist["draft"]["text"], out["proposedAnswer"]["text"])
        self.assertFalse(hist["draft"]["settled"])
        self.assertEqual([t["role"] for t in hist["turns"]], ["user", "assistant"])  # drafts are not chat bubbles
        restarted = PrototypeRuntime(Path(self.temp.name) / "c.sqlite3", workspace_root=Path(self.temp.name) / "ws", runner=self.runner)
        self.assertIn("1-3 word", restarted.chat_history(self.pid, q)["draft"]["text"])  # survives a restart
        self.assertEqual(self.rt.list_questions(self.pid)["questions"][0]["questionId"], q)  # still nothing recorded

    def test_no_position_yet_means_no_draft_and_recording_clears_it(self):
        q, out = self._converse({"intent": "btw", "reply": "It's a classifier.", "learned": []})
        self.assertIsNone(out["proposedAnswer"])
        self.assertIsNone(self.rt.chat_history(self.pid, q)["draft"])
        q, out = self._converse({"intent": "answer", "reply": "ok", "answer": {"selectedOptionId": None, "text": "Final."}})
        self.assertTrue(out["settled"])
        full = self.rt.get_question(self.pid, q)
        self.rt.answer_question(self.pid, q, {"expectedEtag": full["etag"], **out["proposedAnswer"]})
        self.assertIsNone(self.rt.chat_history(self.pid, q)["draft"])  # answered questions show no stale draft

    def test_refresh_rebuilds_a_draft_from_an_existing_thread_without_a_new_owner_message(self):
        self.runner.explore = lambda s, p, c: json.dumps({"intent": "btw", "reply": "Checked.", "learned": []})
        q = self.rt.list_questions(self.pid)["questions"][0]["questionId"]
        self.rt.converse(self.pid, q, {"message": "I think topics are short concepts."})  # no draft was produced
        self.assertIsNone(self.rt.chat_history(self.pid, q)["draft"])
        orig = self.runner.think
        self.runner.think = lambda system, payload: json.dumps({"settled": False, "answer": {"selectedOptionId": None, "text": "Topics are short concepts."}}) if "CURRENT position" in system else orig(system, payload)
        out = self.rt.refresh_draft(self.pid, q)
        self.assertEqual(out["draft"]["text"], "Topics are short concepts.")
        self.assertEqual(len(self.rt.chat_history(self.pid, q)["turns"]), 2)  # no fake owner turn was added
