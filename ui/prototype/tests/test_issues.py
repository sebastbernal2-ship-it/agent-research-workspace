import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.bounded_process import BoundedProcessResult
from ui.prototype import vp as V
from ui.prototype.issues import PREFIX, active_issues, issue_meta, is_issue
from ui.prototype.phases import MAX_QUALITY_PASSES, MAX_REVIEWS_PER_PHASE, PHASE_REVIEW_SYSTEM, QUALITY_SYSTEM, is_phase, phase_summaries
from ui.prototype.tests.test_phases import PhaseBase


def title_status(state):
    return {w["title"]: w["status"] for w in state["workItems"].values()}


class LedgerTests(PhaseBase):
    def setUp(self):
        super().setUp()
        self.rt.plan_phases(self.pid)
        self.phase_id = next(w["workId"] for w in self.state()["workItems"].values() if is_phase(w) and not w["dependsOn"])
        self.board = self.rt.vp.phases.board

    def file(self, kind="issue", title="Topics are single keywords", **kw):
        return self.board.file(self.pid, self.phase_id, kind, title, kw.pop("detail", "details"), **kw)

    def test_items_are_logged_with_kind_priority_source_and_deduplicated(self):
        made = self.file(priority="high", source="reviewer", evidence="sampled 20 speeches")
        self.assertTrue(made["title"].startswith(PREFIX["issue"]))
        self.assertIsNone(self.file(title="topics ARE single keywords!"))  # same item, different wording
        item = next(i for i in active_issues(self.state()) if i["id"] == made["workId"])
        self.assertEqual((item["kind"], item["priority"], item["source"], item["evidence"], item["status"]), ("issue", "high", "reviewer", "sampled 20 speeches", "proposed"))
        self.assertEqual(item["phaseId"], self.phase_id)
        self.assertIsNone(self.board.file(self.pid, None, "issue", "No phase", "x"))      # must belong to a phase
        self.assertIsNone(self.board.file(self.pid, self.phase_id, "bogus", "Bad kind", "x"))

    def test_issues_run_after_required_work_highest_priority_first_directions_wait_deferred_skip(self):
        self.file(kind="improvement", title="Medium thing", priority="medium")
        self.file(kind="improvement", title="Low thing", priority="low")
        self.file(kind="issue", title="High thing", priority="high")
        direction = self.file(kind="direction", title="Later idea", priority="high")
        deferred = self.file(kind="issue", title="Deferred thing", priority="high")
        self.board.act(self.pid, deferred["workId"], "defer")
        self.accept()
        self.rt.vp.phases.activate(self.pid, False)
        order = [w["title"] for w in self.rt.vp._eligible(self.state(), False)]
        self.assertEqual(order[0], "Build splitter")                       # required phase work first
        for _ in range(8):
            self.rt.vp.tick(self.pid)
        st = title_status(self.state())
        self.assertEqual(st["[Issue] High thing"], "completed")
        self.assertEqual(st["[Improvement] Medium thing"], "completed")
        self.assertEqual(st["[Improvement] Low thing"], "proposed")        # low priority: logged only, never run on its own
        self.assertEqual(st["[Direction] Later idea"], "proposed")        # future directions are held
        self.assertEqual(st["[Issue] Deferred thing"], "proposed")        # and so is anything the owner deferred
        starts = [c["yourTask"]["title"] for c in self.runner.worker_calls]
        self.assertLess(starts.index("Build splitter"), starts.index("[Issue] High thing"))
        self.assertLess(starts.index("[Issue] High thing"), starts.index("[Improvement] Medium thing"))
        self.board.act(self.pid, direction["workId"], "do_now")           # promoted by the owner: now it runs
        for _ in range(3):
            self.rt.vp.tick(self.pid)
        self.assertEqual(title_status(self.state())["[Direction] Later idea"], "completed")

    def test_owner_actions_and_validation(self):
        made = self.file()
        wid = made["workId"]
        self.assertTrue(self.board.act(self.pid, wid, "defer")["deferred"])
        self.assertFalse(self.board.act(self.pid, wid, "undefer")["deferred"])
        self.assertEqual(self.board.act(self.pid, wid, "wont_do")["status"], "cancelled")
        with self.assertRaises(ValueError):
            self.board.act(self.pid, wid, "explode")
        with self.assertRaises(KeyError):
            self.board.act(self.pid, "work.vp-nothing", "defer")

    def test_owner_can_log_items_through_the_api_and_they_go_on_the_current_phase(self):
        out = self.rt.add_issue(self.pid, {"kind": "direction", "title": "Try tonality", "detail": "from audio", "priority": "low"})["issue"]
        self.assertEqual((out["kind"], out["source"], out["priority"], out["phaseId"]), ("direction", "owner", "low", self.phase_id))
        self.assertIn(out["id"], [i["id"] for i in self.rt.project_summary(self.pid)["issues"]])
        for bad in ({}, {"kind": "issue"}, {"kind": "nope", "title": "x"}, {"kind": "issue", "title": " "}, {"kind": "issue", "title": "x", "extra": 1}):
            with self.assertRaises(ValueError):
                self.rt.add_issue(self.pid, bad)
        with self.assertRaises(Exception):
            self.rt.add_issue(self.pid, {"kind": "direction", "title": "Try tonality"})  # duplicate
        self.assertEqual(self.rt.act_on_issue(self.pid, out["id"], {"action": "do_now"})["issue"]["status"], "ready")

    def test_workers_log_issue_improve_and_future_lines_with_priorities(self):
        self.accept()
        self.runner.worker_reply = ("Built it.\nISSUE(high): Keyword topics :: topics are single words like 'right'\n"
                                    "IMPROVE: Smarter splitting :: handle U.S. and decimals\nFUTURE(low): Tonality :: derive stance from audio\nDONE: ok")
        self.run_ticks(3)
        items = {i["title"]: i for i in active_issues(self.state())}
        self.assertEqual((items["Keyword topics"]["kind"], items["Keyword topics"]["priority"], items["Keyword topics"]["source"]), ("issue", "high", "worker"))
        self.assertEqual(items["Smarter splitting"]["kind"], "improvement")
        self.assertEqual((items["Tonality"]["kind"], items["Tonality"]["priority"]), ("direction", "low"))
        self.assertIn("topics are single words", items["Keyword topics"]["detail"])


class VisionAndReviewTests(PhaseBase):
    def setUp(self):
        super().setUp()
        self.rt.plan_phases(self.pid)
        self.accept()

    def finish_first_round(self):
        self.run_ticks(5)

    def test_a_blocking_vision_gap_prevents_done_even_when_every_criterion_is_met(self):
        self.finish_first_round()
        self.steer([True, True], status="complete", tasks=[])
        self.runner.steers[-1]["visionGaps"] = [{"gap": "Topics are keywords, not the owner's few-word concepts", "severity": "blocking"},
                                               {"gap": "No opinion direction on ideas", "severity": "important"}]
        self.steer([True, True], status="complete")
        self.run_ticks(15)
        st = title_status(self.state())
        self.assertIn("Close gap: Topics are keywords, not the owner's few-word concepts", st)  # a blocking gap always gets work
        self.assertEqual(st["Close gap: Topics are keywords, not the owner's few-word concepts"], "completed")
        self.assertIn("[Issue] No opinion direction on ideas", st)                               # non-blocking gaps are logged, not blocking
        self.assertEqual(self.phases()[0]["status"], "verifying")                                # done only after the gap was closed

    def test_an_independent_reviewer_must_agree_and_its_blocking_findings_send_the_phase_back(self):
        self.finish_first_round()
        self.runner.reviews.append({"verdict": "gaps", "gaps": [
            {"title": "guide.md cites no source", "detail": "opened guide.md: no URLs", "severity": "blocking"},
            {"title": "Reads dry", "detail": "tone", "severity": "minor"}], "summary": "Not ready."})
        self.steer([True, True], status="complete")
        self.steer([True, True], status="complete")
        self.run_ticks(20)
        st = title_status(self.state())
        self.assertIn("Close gap: guide.md cites no source: opened guide.md: no URLs", st)
        self.assertIn("[Issue] Reads dry", st)                       # the reviewer's minor finding is logged for later
        self.assertEqual(len(self.runner.review_calls), 2)           # reviewed again after the fix, and then it agreed
        self.assertEqual(self.phases()[0]["status"], "verifying")
        self.assertNotIn("assessment", json.dumps(self.runner.review_calls[0]))  # it never sees the VP's claims
        self.assertIn("exitCriteria", json.dumps(self.runner.review_calls[0]))
        self.assertIn("owner", self.runner.review_calls[0])

    def test_review_is_capped_and_an_unavailable_reviewer_does_not_block_the_owner(self):
        self.finish_first_round()
        self.runner.review_error = True
        self.steer([True, True], status="complete")
        self.run_ticks(10)
        phase = self.phases()[0]
        self.assertEqual(phase["status"], "verifying")
        self.assertFalse(phase["reviewed"])
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phase-work.") and q["status"] == "open")
        self.assertIn("Independent review: not available this time.", q["questionText"])

    def test_the_checkpoint_shows_the_reviewers_verdict_and_logged_issues(self):
        self.finish_first_round()
        self.runner.reviews.append({"verdict": "pass", "gaps": [{"title": "Could cache results", "detail": "slow", "severity": "important"}], "summary": "Opened the outputs and sampled 20 records; they match the vision."})
        self.steer([True, True], status="complete")
        self.run_ticks(10)
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phase-work.") and q["status"] == "open")
        self.assertIn("Independent review: Opened the outputs and sampled 20 records", q["questionText"])
        self.assertIn("Could cache results", q["questionText"])      # filed as an issue the VP now works through


class QualityPassTests(PhaseBase):
    def setUp(self):
        super().setUp()
        self.rt.plan_phases(self.pid)
        self.accept()
        self.steer([True, True], status="complete")

    def test_after_a_phase_is_built_the_vp_keeps_improving_it_in_passes_until_nothing_important_is_left(self):
        self.runner.qualities.append({"issues": [{"title": "Slow on big files", "detail": "took 40s", "priority": "high", "kind": "issue"}], "summary": "one real problem"})
        self.runner.qualities.append({"issues": [{"title": "Docs are thin", "detail": "no examples", "priority": "medium", "kind": "improvement"}], "summary": "docs"})
        self.run_ticks(40)
        st = title_status(self.state())
        self.assertEqual(st["[Issue] Slow on big files"], "completed")        # logged by the pass, then fixed by the VP on its own
        self.assertEqual(st["[Improvement] Docs are thin"], "completed")
        self.assertEqual(self.phases()[0]["passes"], 3)                       # pass 1, pass 2, pass 3 finds nothing
        self.assertEqual(len(self.runner.quality_calls), MAX_QUALITY_PASSES if MAX_QUALITY_PASSES == 3 else 3)
        calls = self.runner.quality_calls
        self.assertEqual(calls[1]["issueLedger"][0]["title"], "Slow on big files")   # later passes see what's already logged

    def test_a_pass_waits_for_open_issues_and_does_not_run_again_when_nothing_changed(self):
        self.runner.qualities.append({"issues": [{"title": "Needs work", "detail": "d", "priority": "high", "kind": "issue"}], "summary": "s"})
        self.run_ticks(40)
        n = len(self.runner.quality_calls)
        self.run_ticks(10)
        self.assertEqual(len(self.runner.quality_calls), n)                   # idle phase: no endless passes
        self.assertLessEqual(n, MAX_QUALITY_PASSES)

    def test_deferred_items_and_future_directions_do_not_hold_up_quality_passes(self):
        self.runner.qualities.append({"issues": [{"title": "Some day", "detail": "d", "priority": "low", "kind": "direction"}], "summary": "s"})
        self.run_ticks(30)
        self.assertEqual(title_status(self.state())["[Direction] Some day"], "proposed")  # held, never run on its own
        self.assertGreaterEqual(len(self.runner.quality_calls), 1)

    def test_prompts_make_the_review_independent_concrete_and_vision_based(self):
        for must in ("did NOT do this work", "Inspect the ACTUAL outputs", "owner's vision", "never pass work you could not check"):
            self.assertIn(must, PHASE_REVIEW_SYSTEM)
        for must in ("ACTUAL outputs", "do not repeat any of them", "vision"):
            self.assertIn(must, QUALITY_SYSTEM)
        self.assertEqual(MAX_REVIEWS_PER_PHASE, 2)


class ContextAndSessionTests(PhaseBase):
    def test_workers_get_full_detail_context_files_outside_the_repository(self):
        self.rt.plan_phases(self.pid)
        self.runner.explore_log = []
        # the owner said something detailed; the earlier task left a long report
        q = self.rt.list_questions(self.pid)
        self.rt.control.add_discussion_message(self.pid, "work.alignment-intake", thread_id="thread.chat.x", message_type="evidence",
            content="LOG: " + json.dumps({"id": "log-1", "kind": "requirement", "title": "Relevance by time horizon", "detail": "A source from a week ago may still matter.", "crucial": True}),
            actor=V.VP, visible_to=[V.OWNER_ID, V.VP["actorId"]])
        self.accept()
        self.runner.worker_reply = "LONG REPORT " + "x" * 3000 + "\nDONE: first"
        self.run_ticks(3)
        first_phase = next(w["workId"] for w in self.state()["workItems"].values() if w["title"] == "Data readiness")
        ctx = self.rt.workspace_root / self.pid / "context"
        vision = (ctx / "VISION.md").read_text()
        self.assertIn("Relevance by time horizon", vision)
        self.assertIn("CRUCIAL", vision)
        self.assertIn("A source from a week ago may still matter.", vision)         # full detail, not a summary
        phase_md = (ctx / "PHASE.md").read_text()
        self.assertIn("Exit criteria", phase_md)
        self.assertIn("Splitter verified", phase_md)
        self.runner.worker_reply = "Second.\nDONE: second"
        self.runner.steers.append({"assessment": [{"criterion": "Splitter verified", "met": True, "evidence": "e"}, {"criterion": "Extraction reviewed", "met": False, "evidence": ""}],
                                   "status": "continue", "newTasks": [{"key": "n", "title": "Second task", "objective": "o", "workType": "implementation"}]})
        self.run_ticks(6)
        phase_md = (ctx / "PHASE.md").read_text()
        self.assertIn("Finished earlier in this phase", phase_md)
        self.assertIn("LONG REPORT " + "x" * 2000, phase_md)                       # earlier report in FULL (up to 6000 chars), not 1,200
        payload = self.runner.worker_calls[-1]
        self.assertEqual(payload["contextDir"], str(ctx))
        self.assertTrue((self.rt.workspace_root / self.pid / "context").is_dir())
        self.assertFalse(any(p.name == "context" for p in (self.rt.workspace_root / self.pid).glob("repo/*")))  # never inside a repo checkout

    def test_the_worker_prompt_demands_reading_context_verifying_and_logging_weaknesses(self):
        for must in ("contextDir", "VISION.md", "VERIFY YOUR OWN WORK", "`ISSUE(high|medium|low)", "`IMPROVE:", "`FUTURE:",
                     "existing library or approach", "web search", "virtual environment"):
            self.assertIn(must, V.WORKER_SYSTEM)


class SessionHarnessTests(unittest.TestCase):
    def run_claude(self, sessions_path, prompt, session, results):
        calls = []
        def fake_run(args, **kw):
            calls.append(list(args))
            res = results.pop(0)
            if res is None:
                return BoundedProcessResult(returncode=1, stdout=b"", stderr=b"")
            return BoundedProcessResult(returncode=0, stdout=json.dumps({"result": "ok", "session_id": res}).encode(), stderr=b"")
        with mock.patch("shutil.which", return_value="/usr/bin/claude"), mock.patch.object(V, "run_bounded_subprocess", fake_run):
            runner = V.CliRunner("claude", sessions_path=sessions_path)
            runner.work("sys", prompt, Path(tempfile.mkdtemp()), session=session)
        return calls[0], calls[1:] if len(calls) > 1 else []

    def test_claude_gets_the_full_toolset_and_one_persistent_session_per_phase(self):
        path = Path(tempfile.mkdtemp()) / "s.json"
        first, _ = self.run_claude(path, json.dumps({"contextDir": tempfile.mkdtemp()}), "p1:phaseA", ["sess-123"])
        joined = " ".join(first)
        for tool in ("WebSearch", "WebFetch", "Bash", "Edit"):
            self.assertIn(tool, joined)
        self.assertIn("--session-id", first)          # first task of the phase creates the session
        self.assertIn("--add-dir", first)             # and can read the context folder
        self.assertEqual(json.loads(path.read_text())["p1:phaseA"], "sess-123")
        second, _ = self.run_claude(path, "{}", "p1:phaseA", ["sess-123"])
        self.assertIn("--resume", second)             # the next task resumes it, with everything learned so far
        self.assertEqual(second[second.index("--resume") + 1], "sess-123")
        other, _ = self.run_claude(path, "{}", "p1:phaseB", ["sess-999"])
        self.assertIn("--session-id", other)          # a different phase starts its own

    def test_a_lost_session_falls_back_to_a_fresh_one_instead_of_failing_the_task(self):
        path = Path(tempfile.mkdtemp()) / "s.json"
        path.write_text(json.dumps({"p:ph": "old-session"}))
        first, rest = self.run_claude(path, "{}", "p:ph", [None, "fresh"])
        self.assertIn("--resume", first)
        self.assertIn("--session-id", rest[0])
        self.assertEqual(json.loads(path.read_text())["p:ph"], "fresh")

    def test_codex_has_no_sessions_and_default_runner_prefers_codex(self):
        with mock.patch("shutil.which", return_value="/bin/x"):
            self.assertFalse(V.CliRunner("codex").supports_sessions)
            with mock.patch.dict(os.environ, {"NORTHSTAR_WORKERS_CONFIG": "/nonexistent"}, clear=False):
                os.environ.pop("NORTHSTAR_RUNNER", None)
                self.assertEqual(V.default_runner().kind, "codex")


if __name__ == "__main__":
    unittest.main()


class StopConditionTests(PhaseBase):
    """The live run showed the VP polishing forever and never assessing the phase. These pin the fixes."""

    def setUp(self):
        super().setUp()
        self.rt.plan_phases(self.pid)
        self.phase_id = next(w["workId"] for w in self.state()["workItems"].values() if is_phase(w) and not w["dependsOn"])
        self.board = self.rt.vp.phases.board

    def test_the_phase_is_assessed_before_any_backlog_work_so_it_can_never_be_starved(self):
        for title in ("Tokeniser splits abbreviations", "Stop list misses archaic verbs", "Headings leak into counts",
                      "Encoding fallback is undocumented", "Plural forms are counted separately"):
            self.board.file(self.pid, self.phase_id, "issue", title, "d", priority="high")
        order = []
        original = self.runner.work
        self.runner.work = lambda s, p, c: (order.append((json.loads(p)["yourTask"]["title"], len(self.runner.steer_calls))), original(s, p, c))[1]
        self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(25)
        self.assertEqual(order[0], ("Build splitter", 0))                      # required work first, with no assessment yet
        backlog = [o for o in order if o[0].startswith("[Issue]")]
        self.assertGreaterEqual(len(backlog), 5)                                # the backlog does get worked...
        self.assertTrue(all(steers >= 1 for _t, steers in backlog))             # ...but only AFTER the phase was assessed
        self.assertEqual(self.phases()[0]["status"], "verifying")

    def test_low_priority_items_are_only_logged_and_the_automatic_backlog_is_capped_per_phase(self):
        from ui.prototype.vp import MAX_AUTO_ISSUES_PER_PHASE
        low = self.board.file(self.pid, self.phase_id, "issue", "Nice to have polish alpha", "d", priority="low")
        names = ["cache parsed files", "parallel file reading", "colourised output", "json export", "progress bar", "locale awareness",
                 "memory profiling", "unicode normalisation", "config file support", "shell completion", "benchmark suite"]
        for name in names[:MAX_AUTO_ISSUES_PER_PHASE + 3]:
            self.board.file(self.pid, self.phase_id, "improvement", name, "d", priority="medium")
        self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(40)
        st = title_status(self.state())
        self.assertEqual(st["[Issue] Nice to have polish alpha"], "proposed")  # low: logged, never run on its own
        ran = [t for t, s in st.items() if t.startswith("[Improvement]") and s == "completed"]
        self.assertEqual(len(ran), MAX_AUTO_ISSUES_PER_PHASE)                  # the cap: the VP stops polishing by itself
        self.board.act(self.pid, low["workId"], "do_now")                       # the owner can still promote anything
        self.run_ticks(3)
        self.assertEqual(title_status(self.state())["[Issue] Nice to have polish alpha"], "completed")

    def test_logged_only_items_do_not_block_quality_passes(self):
        self.board.file(self.pid, self.phase_id, "issue", "Nice to have polish beta", "d", priority="low")
        self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(30)
        self.assertGreaterEqual(len(self.runner.quality_calls), 1)  # the pass ran although a low item is still open

    def test_near_duplicates_are_folded_into_one_item(self):
        a = self.board.file(self.pid, self.phase_id, "issue", '"art" verb detection is a short pattern list', "d")
        self.assertIsNotNone(a)
        for dup in ('"art" is still a pattern list, so unusual verb uses without "thou" are counted',
                    "Pattern list for art verb detection is short"):
            self.assertIsNone(self.board.file(self.pid, self.phase_id, "issue", dup, "d"))
        self.assertIsNotNone(self.board.file(self.pid, self.phase_id, "issue", "Roman numerals leak into the top ten", "d"))  # genuinely different

    def test_a_worker_can_log_at_most_three_items_per_task_and_defaults_are_not_high(self):
        self.accept()
        lines = "\n".join(f"ISSUE: {name} :: detail" for name in ("quote handling", "slow startup", "wrong encoding", "missing docs", "flaky tests", "odd exit codes", "no logging"))
        self.runner.worker_reply = "Did it.\n" + lines + "\nDONE: ok"
        self.run_ticks(2)
        items = [i for i in active_issues(self.state()) if i["source"] == "worker"]
        self.assertEqual(len(items), 3)
        self.assertTrue(all(i["priority"] == "medium" for i in items))
        for must in ("AT MOST 3 items per task", "no nitpicks", "low items are only recorded"):
            self.assertIn(must, V.WORKER_SYSTEM)
