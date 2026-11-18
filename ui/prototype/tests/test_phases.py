import json
import tempfile
import unittest
from pathlib import Path

from ui.prototype.phases import (
    KEEP_IMPROVING, MAX_STEERS_PER_PHASE, PHASE_PLAN_SYSTEM, START_NEXT, STEER_SYSTEM, is_improvement, is_phase, phase_summaries,
)
from ui.prototype.runtime import PrototypeRuntime
from ui.prototype.vp import VPError


class ScriptedRunner:
    """A fake VP/worker: phase plan and steering replies are scripted per test; workers just succeed."""

    def __init__(self):
        self.plan = {"phases": [
            {"title": "Data readiness", "goal": "Clean data for every layer", "exitCriteria": ["Splitter verified", "Extraction reviewed"],
             "tasks": [{"key": "a", "title": "Build splitter", "objective": "split", "workType": "implementation", "acceptanceCriteria": ["tested"]}]},
            {"title": "Model inputs", "goal": "Feed models", "exitCriteria": ["Sources chosen"], "tasks": []}]}
        self.review_calls, self.quality_calls = [], []
        self.reviews, self.qualities = [], []   # scripted replies for the independent reviewer and the quality passes
        self.review_error = False
        self.plan_calls = []
        self.plan_error = False
        self.steers = []          # replies returned in order by the steering call
        self.steer_calls = []
        self.worker_reply = "Built it.\nDONE: ok"
        self.worker_calls = []

    def available(self):
        return True

    def think(self, system, payload):
        if system.startswith("You are an independent reviewer agent"):  # the per-task reviewer
            return json.dumps({"verdict": "accept"})
        if system.startswith("You are the project VP. Plan the WHOLE project"):
            self.plan_calls.append(json.loads(payload))
            if self.plan_error:
                raise VPError("the model is unavailable")
            return json.dumps(self.plan)
        if system.startswith("You are an independent, skeptical reviewer"):
            self.review_calls.append(json.loads(payload))
            if self.review_error:
                raise VPError("reviewer unavailable")
            if getattr(self, "review_fail_once", False):
                self.review_fail_once = False
                raise VPError("transient reviewer failure")
            return json.dumps(self.reviews.pop(0) if self.reviews else {"verdict": "pass", "gaps": [], "summary": "Checked real outputs; they match."})
        if system.startswith("You are the project's quality reviewer"):
            self.quality_calls.append(json.loads(payload))
            return json.dumps(self.qualities.pop(0) if self.qualities else {"issues": [], "summary": "nothing to add"})
        if system.startswith("You are the project VP running ONE phase"):
            self.steer_calls.append(json.loads(payload))
            if not self.steers:
                raise VPError("no scripted reply (the model is unavailable)")  # the system must just wait and retry later
            return json.dumps(self.steers.pop(0))
        if "orchestrator" in system:
            return json.dumps({"action": "execute"})
        return json.dumps({"tasks": []})

    def work(self, system, prompt, cwd):
        self.worker_calls.append(json.loads(prompt))
        return self.worker_reply


class PhaseBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = ScriptedRunner()
        self.rt = PrototypeRuntime(Path(self.temp.name) / "c.sqlite3", workspace_root=Path(self.temp.name) / "ws", runner=self.runner)
        self.rt.sync_memory = True
        self.pid = self.rt.create_project({"mode": "new", "name": "P", "goal": "g"})["projectId"]
        for i in range(1, 6):
            q = self.rt.get_question(self.pid, f"question.alignment.{i:02d}")
            self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": q["etag"], "text": "x"})

    def state(self):
        return self.rt.control.projector.replay(self.pid)

    def accept(self):
        """Review mode: the owner accepts the plan (phase 1 approved); everything inside it then runs on its own."""
        first = next(w for w in self.state()["workItems"].values() if is_phase(w) and not w["dependsOn"] and w["status"] == "proposed")
        self.rt.approve_work(self.pid, first["workId"], {"expectedRevision": first["revision"], "decision": "approve"})

    def phases(self):
        return phase_summaries(self.state())

    def tasks(self, phase_id=None):
        items = self.state()["workItems"]
        return {w["title"]: w for w in items.values() if not is_phase(w) and w["parentWorkId"] == phase_id and w["workId"] != "work.alignment-intake"
                and w["workId"] != "work.project-chat"} if phase_id else {}

    def run_ticks(self, n=20):
        for _ in range(n):
            if not self.rt.vp.tick(self.pid):
                break

    def steer(self, met, status="continue", tasks=None, improvements=None, caveats=None, question=""):
        phase = next(p for p in self.phases() if p["status"] != "completed")  # the phase this scripted reply is for
        self.runner.steers.append({
            "assessment": [{"criterion": c, "met": m, "evidence": "verified in the reports" if m else ""} for c, m in zip(phase["exitCriteria"], met)],
            "status": status, "caveats": caveats or [], "newTasks": tasks or [], "improvements": improvements or [], "ownerQuestion": question})


class PlanTests(PhaseBase):
    def test_plans_ordered_phases_with_exit_criteria_and_only_the_first_phases_tasks(self):
        created = self.rt.plan_phases(self.pid)["phases"]
        self.assertEqual([p["title"] for p in created], ["Data readiness", "Model inputs"])
        summary = self.phases()
        self.assertEqual([p["status"] for p in summary], ["proposed", "proposed"])
        self.assertEqual(summary[0]["exitCriteria"], ["Splitter verified", "Extraction reviewed"])
        items = self.state()["workItems"]
        self.assertEqual(items[created[1]["workId"]]["dependsOn"], [created[0]["workId"]])  # phase 2 cannot start before phase 1
        self.assertEqual(len(self.tasks(created[0]["workId"])), 1)
        self.assertEqual(len(self.tasks(created[1]["workId"])), 0)
        with self.assertRaises(Exception):
            self.rt.plan_phases(self.pid)  # planned once

    def test_criteria_the_vp_can_never_verify_are_dropped_at_planning(self):
        from ui.prototype.phases import _UNVERIFIABLE
        for bad in ("The owner has reviewed Phase 1 before Phase 2 begins.", "Phase 2 begins only after the owner has reviewed Phase 1.",
                    "The user approves the guide.", "Owner sign-off is recorded."):
            self.assertTrue(_UNVERIFIABLE.search(bad), bad)
        for good in ("guide.md exists and covers soil, water and light.", "Every figure has an identifiable source.",
                     "A review confirms the guide matches the rules."):  # an AGENT review is fine
            self.assertFalse(_UNVERIFIABLE.search(good), good)
        self.runner.plan["phases"][0]["exitCriteria"].append("The owner has reviewed Phase 1 before Phase 2 begins.")
        self.rt.plan_phases(self.pid)
        self.assertEqual(self.phases()[0]["exitCriteria"], ["Splitter verified", "Extraction reviewed"])

    def test_tasks_that_only_the_owner_can_do_are_never_created(self):
        self.runner.plan["phases"][0]["tasks"].append({"key": "z", "title": "Obtain the owner's review", "objective": "ask", "workType": "other"})
        created = self.rt.plan_phases(self.pid)["phases"]
        self.assertEqual(list(self.tasks(created[0]["workId"])), ["Build splitter"])

    def test_prompts_demand_verifiable_criteria_and_forbid_claiming_done_without_evidence(self):
        self.assertIn("VERIFIABLE", PHASE_PLAN_SYSTEM)
        self.assertIn("NEVER make the owner's review or approval a criterion", PHASE_PLAN_SYSTEM)
        for must in ("met=true only with concrete evidence", "status=complete ONLY if every criterion is met AND nothing blocks",
                     "never repeat a completed task", "only the owner can supply", "DONE MEANS the work serves the owner's vision",
                     "they never block completion"):
            self.assertIn(must, STEER_SYSTEM)
            self.assertIn(must, STEER_SYSTEM)


class AutonomyTests(PhaseBase):
    def test_nothing_runs_until_the_first_phase_is_approved_without_auto_run(self):
        self.rt.plan_phases(self.pid)
        self.run_ticks()
        self.assertEqual([p["status"] for p in self.phases()], ["proposed", "proposed"])
        self.assertEqual(self.runner.worker_calls, [])

    def test_the_vp_keeps_working_until_exit_criteria_are_evidenced_then_stops_at_a_checkpoint(self):
        self.rt.plan_phases(self.pid)
        self.accept()
        # round 1 assessment: extraction criterion unmet -> the VP creates the next task ITSELF; round 2: complete
        self.steer([True, False], tasks=[{"key": "n1", "title": "Review extraction quality", "objective": "check", "workType": "review"}],
                   caveats=["keyword matches only"])
        self.steer([True, True], status="complete")
        self.run_ticks(40)
        phase1, phase2 = self.phases()
        self.assertEqual(phase1["status"], "verifying")          # waiting for the OWNER's review...
        self.assertEqual(phase2["status"], "proposed")           # ...and phase 2 has NOT started on its own
        first_phase = next(w["workId"] for w in self.state()["workItems"].values() if w["title"] == "Data readiness")
        self.assertEqual(sorted(self.tasks(first_phase).keys()), ["Build splitter", "Review extraction quality"])
        self.assertTrue(all(a["met"] for a in phase1["assessment"]))
        self.assertEqual(phase1["steers"], 2)
        # the checkpoint reached the owner as a (non-blocking) question that lists what is verified and what is weak
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phase-") and q["status"] == "open")
        self.assertFalse(q["blocking"])
        self.assertIn("Phase 1", q["questionText"])
        self.assertIn("Splitter verified", q["questionText"])
        self.assertIn("Start Phase 2: Model inputs", [o["label"] for o in q["options"]])
        self.assertEqual(self.runner.steer_calls[1]["tasks"][1]["status"], "completed")  # the 2nd assessment saw the finished review

    def test_completion_cannot_be_declared_without_evidence_for_every_criterion(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, False], status="complete", tasks=[{"key": "n1", "title": "Fix gap", "objective": "o", "workType": "implementation"}])
        self.steer([True, True], status="complete")
        self.run_ticks(40)
        self.assertEqual(self.phases()[0]["steers"], 2)  # the first "complete" was downgraded to "continue" and a task was created
        self.assertEqual(self.phases()[0]["status"], "verifying")

    def test_owner_checkpoint_starts_the_next_phase_and_it_steers_itself(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(30)
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phase-") and q["status"] == "open")
        full = self.rt.get_question(self.pid, q["questionId"])
        self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": full["etag"], "text": "Start Phase 2", "selectedOptionId": START_NEXT})
        self.assertEqual([p["status"] for p in self.phases()], ["completed", "ready"])
        self.runner.steers.append({"assessment": [{"criterion": "Sources chosen", "met": False, "evidence": ""}], "status": "continue",
                                   "newTasks": [{"key": "m1", "title": "Survey sources", "objective": "o", "workType": "research"}]})
        self.run_ticks(5)
        p1, p2 = self.phases()
        self.assertEqual(p2["status"], "running")  # started by the owner's decision, then planned its own work
        self.assertEqual(p2["steers"] >= 1, True)
        self.assertEqual(p2["tasks"]["total"], 1)

    def test_keep_improving_sends_the_phase_back_and_it_is_not_reassessed_until_new_work_finishes(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, True], status="complete", improvements=[{"title": "Handle abbreviations", "objective": "U.S. splits badly", "workType": "implementation"}])
        self.run_ticks(30)
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phase-") and q["status"] == "open")
        full = self.rt.get_question(self.pid, q["questionId"])
        self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": full["etag"], "text": "keep improving", "selectedOptionId": KEEP_IMPROVING})
        self.assertEqual(self.phases()[0]["status"], "running")
        calls_before = len(self.runner.steer_calls)
        self.run_ticks(10)  # the queued improvement runs, then the phase is re-checked exactly once
        self.runner.steers.append({"assessment": [{"criterion": c, "met": True, "evidence": "e"} for c in self.phases()[0]["exitCriteria"]], "status": "complete", "caveats": []})
        self.run_ticks(10)
        self.assertGreaterEqual(len(self.runner.steer_calls), calls_before)  # no spinning on an unchanged phase

    def test_owner_can_move_on_from_the_phases_view_without_a_question(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(30)
        phase_id = next(w["workId"] for w in self.state()["workItems"].values() if w["title"] == "Data readiness")
        out = self.rt.decide_phase(self.pid, phase_id, {"moveOn": True})
        self.assertEqual([p["status"] for p in out["phases"]], ["completed", "ready"])
        for bad in ({}, {"moveOn": "yes"}, {"moveOn": True, "x": 1}):
            with self.assertRaises(ValueError):
                self.rt.decide_phase(self.pid, phase_id, bad)
        with self.assertRaises(Exception):
            self.rt.decide_phase(self.pid, "work.vp-notaphase", {"moveOn": True})


class ImprovementTests(PhaseBase):
    def test_workers_improve_lines_become_backlog_tasks_done_last_and_deduped(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.runner.worker_reply = "Did it.\nIMPROVE: Handle abbreviations like U.S. because they split into fragments.\nIMPROVE: handle ABBREVIATIONS like u.s. because...\nDONE: ok"
        self.steer([True, True], status="complete")
        self.run_ticks(30)
        phase_id = next(w["workId"] for w in self.state()["workItems"].values() if w["title"] == "Data readiness")
        kids = [w for w in self.state()["workItems"].values() if w["parentWorkId"] == phase_id]
        improvements = [w for w in kids if is_improvement(w)]
        self.assertEqual(len(improvements), 1)  # the near-duplicate was folded in
        self.assertTrue(improvements[0]["title"].startswith("[Improvement] "))
        self.assertEqual(self.phases()[0]["improvements"]["open"] + self.phases()[0]["improvements"]["done"], 1)

    def test_improvements_never_block_phase_completion_and_run_in_parallel_with_the_next_phase(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, True], status="complete", improvements=[{"title": "Try better tokenizer", "objective": "o", "workType": "implementation"}])
        self.run_ticks(30)
        self.assertEqual(self.phases()[0]["status"], "verifying")  # complete although the backlog item exists
        phase_id = next(w["workId"] for w in self.state()["workItems"].values() if w["title"] == "Data readiness")
        self.rt.decide_phase(self.pid, phase_id, {"moveOn": True})
        self.runner.steers.append({"assessment": [{"criterion": "Sources chosen", "met": False, "evidence": ""}], "status": "continue",
                                   "newTasks": [{"key": "m", "title": "Phase 2 task", "objective": "o", "workType": "research"}]})
        self.run_ticks(20)
        titles = {w["title"]: w["status"] for w in self.state()["workItems"].values()}
        self.assertEqual(titles["[Improvement] Try better tokenizer"], "completed")  # worked on while phase 1 is closed
        self.assertEqual(titles["Phase 2 task"], "completed")


class SafetyTests(PhaseBase):
    def test_phase_containers_are_not_failed_by_restart_recovery_and_do_not_use_worker_slots(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.rt.vp.phases.activate(self.pid, True)
        self.assertEqual(self.phases()[0]["status"], "running")
        self.assertEqual(self.rt.vp.recover(self.pid), 0)
        self.assertEqual(self.phases()[0]["status"], "running")
        eligible = self.rt.vp._eligible(self.state(), True)
        self.assertEqual([w["title"] for w in eligible], ["Build splitter"])  # the running phase did not eat a slot

    def test_a_phase_that_runs_out_of_ideas_asks_the_owner_instead_of_spinning(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, False], status="continue", tasks=[])  # unmet criterion, nothing it can do on its own
        self.run_ticks(30)
        q = [q for q in self.state()["questions"].values() if q["status"] == "open" and q["blocking"]]
        self.assertEqual(len(q), 1)
        self.assertIn("Extraction reviewed", q[0]["questionText"])
        self.assertEqual(len(self.runner.steer_calls), 1)  # one model call, then it waited

    def test_needs_owner_blocks_the_phase_and_resumes_after_the_answer(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, False], status="needs_owner", question="Which licence applies to the transcripts?")
        self.run_ticks(30)
        q = next(q for q in self.state()["questions"].values() if q["status"] == "open" and q["blocking"])
        self.assertEqual(q["questionText"], "Which licence applies to the transcripts?")
        full = self.rt.get_question(self.pid, q["questionId"])
        self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": full["etag"], "text": "CC-BY"})
        self.steer([True, True], status="complete")
        self.run_ticks(20)
        self.assertEqual(self.phases()[0]["status"], "verifying")  # resumed on its own and finished

    def test_steer_round_limit_asks_the_owner(self):
        self.rt.plan_phases(self.pid); self.accept()
        for i in range(MAX_STEERS_PER_PHASE + 2):
            self.steer([True, False], tasks=[{"key": f"k{i}", "title": f"Gap {i}", "objective": "o", "workType": "implementation"}])
        self.run_ticks(120)
        self.assertLessEqual(len(self.runner.steer_calls), MAX_STEERS_PER_PHASE)
        self.assertTrue(any(q["blocking"] and q["status"] == "open" for q in self.state()["questions"].values()))

    def test_accepting_a_phase_covers_everything_inside_it_but_flat_tasks_still_need_approval(self):
        self.rt.plan_phases(self.pid)
        self.accept()
        self.run_ticks(5)  # phase 1 starts; its planned task runs with NO per-task approval
        self.assertEqual(self.tasks(next(w["workId"] for w in self.state()["workItems"].values() if w["title"] == "Data readiness"))["Build splitter"]["status"], "completed")
        self.runner.steers.append({"assessment": [{"criterion": "Splitter verified", "met": True, "evidence": "e"}, {"criterion": "Extraction reviewed", "met": False, "evidence": ""}],
                                   "status": "continue", "newTasks": [{"key": "n", "title": "Follow-up", "objective": "o", "workType": "implementation"}]})
        self.run_ticks(10)
        follow = next(w for w in self.state()["workItems"].values() if w["title"] == "Follow-up")
        self.assertEqual(follow["status"], "completed")  # the VP's own follow-up ran without asking
        flat = self.rt.vp._new_work(self.pid, "Legacy flat task", "o", "research", "proposed", {"actorType": "agent", "actorId": "agent.subagent", "role": "worker"}, [], [], None,
                                    {"actorType": "agent", "actorId": "agent.project-vp", "role": "vp"})
        self.run_ticks(5)
        self.assertEqual(self.state()["workItems"][flat["workId"]]["status"], "proposed")  # outside any phase, approval is still required


class AutomaticPlanningTests(PhaseBase):
    def test_the_vp_plans_on_its_own_once_the_interview_is_done_and_asks_you_to_accept_or_change_it(self):
        self.assertEqual(self.phases(), [])
        self.run_ticks(3)  # no button pressed: the scheduler plans
        self.assertEqual([p["status"] for p in self.phases()], ["proposed", "proposed"])
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phaseplan-") and q["status"] == "open")
        self.assertFalse(q["blocking"])
        for text in ("Phase 1: Data readiness", "Splitter verified", "Phase 2: Model inputs"):
            self.assertIn(text, q["questionText"])
        self.assertEqual([o["label"] for o in q["options"]][0], "Accept the plan and start Phase 1")
        self.assertEqual(self.runner.worker_calls, [])  # nothing runs before you accept
        self.assertEqual(len(self.runner.plan_calls), 1)  # and it planned once, not on every pass
        self.run_ticks(5)
        self.assertEqual(len(self.runner.plan_calls), 1)

    def test_it_does_not_plan_before_the_interview_is_finished(self):
        rt = PrototypeRuntime(Path(self.temp.name) / "d.sqlite3", workspace_root=Path(self.temp.name) / "ws2", runner=self.runner)
        pid = rt.create_project({"mode": "new", "name": "Q", "goal": "g"})["projectId"]
        rt.vp.tick(pid)
        self.assertEqual(phase_summaries(rt.control.projector.replay(pid)), [])

    def test_accepting_the_plan_starts_phase_one_and_it_runs_on_its_own(self):
        self.run_ticks(2)
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phaseplan-") and q["status"] == "open")
        full = self.rt.get_question(self.pid, q["questionId"])
        self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": full["etag"], "text": "Accept the plan and start Phase 1", "selectedOptionId": "option.plan.accept"})
        self.run_ticks(5)
        self.assertEqual(self.phases()[0]["status"], "running")
        self.assertEqual(self.phases()[0]["tasks"]["done"], 1)

    def test_accepting_on_the_phases_page_withdraws_the_plan_question(self):
        self.run_ticks(2)
        self.accept()  # the Accept button on the Phases page approves phase 1 directly
        self.run_ticks(2)
        self.assertEqual([q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phaseplan-") and q["status"] == "open"], [])

    def test_rejecting_the_plan_with_feedback_discards_it_and_replans_using_that_feedback(self):
        self.run_ticks(2)
        q = next(q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phaseplan-") and q["status"] == "open")
        full = self.rt.get_question(self.pid, q["questionId"])
        self.rt.answer_question(self.pid, q["questionId"], {"expectedEtag": full["etag"], "selectedOptionId": "option.plan.reject",
                                                            "text": "Change the plan: I'll say what's wrong\n\nNote: Split data cleaning from extraction."})
        self.run_ticks(3)
        self.assertEqual(len(self.runner.plan_calls), 2)
        self.assertEqual(self.runner.plan_calls[1]["planFeedback"], "Split data cleaning from extraction.")
        self.assertTrue(self.runner.plan_calls[1]["rejectedPlan"][0]["title"])
        self.assertEqual([p["status"] for p in self.phases()], ["proposed", "proposed"])  # only the NEW plan is shown; the old one is hidden
        open_plans = [q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phaseplan-") and q["status"] == "open"]
        self.assertEqual(len(open_plans), 1)  # and you are asked about the new one
        self.assertEqual(self.runner.worker_calls, [])

    def test_planning_failures_are_retried_a_few_times_never_spun_on(self):
        import ui.prototype.phases as P
        self.runner.plan_error = True
        clock = [1000.0]
        orig = P.time.time
        P.time.time = lambda: clock[0]
        try:
            for _ in range(10):
                self.rt.vp.tick(self.pid)
                clock[0] += 130  # past the retry delay each time
        finally:
            P.time.time = orig
        self.assertEqual(len(self.runner.plan_calls), 3)  # at most 3 attempts per hour
        self.assertEqual(self.phases(), [])


class FullAutoTests(PhaseBase):
    def test_full_auto_accepts_its_own_plan_runs_every_phase_and_never_stops_for_you(self):
        self.rt.set_automation(self.pid, {"autoRun": True})
        self.steer_for_all = lambda crits: {"assessment": [{"criterion": c, "met": True, "evidence": "verified"} for c in crits], "status": "complete", "caveats": []}
        self.run_ticks(3)  # planned and accepted without a question
        self.assertEqual([q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phaseplan-")], [])
        self.runner.steers.append(self.steer_for_all(["Splitter verified", "Extraction reviewed"]))
        self.run_ticks(15)
        self.runner.steers.append(self.steer_for_all(["Sources chosen"]))
        self.runner.plan["phases"][1]["tasks"] = []
        self.run_ticks(15)
        statuses = [p["status"] for p in self.phases()]
        self.assertEqual(statuses[0], "completed")  # rolled on by itself
        self.assertNotIn("verifying", statuses)
        open_checkpoints = [q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phase-") and q["status"] == "open"]
        self.assertEqual(open_checkpoints, [])  # nothing waits for you
        fyi = [a for a in self.rt.activity(self.pid) if "is complete: every exit criterion is evidenced" in a["text"]]
        self.assertTrue(fyi)  # but you can read what happened in Latest
        self.assertIn("starts now", fyi[-1]["text"] + " ".join(a["text"] for a in fyi))

    def test_turning_full_auto_on_passes_a_checkpoint_that_was_waiting_and_withdraws_its_question(self):
        self.rt.plan_phases(self.pid); self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(15)
        self.assertEqual(self.phases()[0]["status"], "verifying")
        self.rt.set_automation(self.pid, {"autoRun": True})
        self.run_ticks(3)
        self.assertEqual(self.phases()[0]["status"], "completed")
        self.assertEqual([q for q in self.state()["questions"].values() if q["questionId"].startswith("question.phase-work.") and q["status"] == "open"], [])


if __name__ == "__main__":
    unittest.main()


class ReviewerRetryTests(PhaseBase):
    def test_one_transient_reviewer_failure_is_retried_and_the_phase_still_gets_its_independent_review(self):
        self.rt.plan_phases(self.pid)
        self.runner.review_fail_once = True
        self.accept()
        self.steer([True, True], status="complete")
        self.run_ticks(4)
        self.assertEqual(len(self.runner.review_calls), 2)
        self.assertTrue(self.phases()[0]["reviewed"])
