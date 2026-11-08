import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness.control_plane import ProjectControlPlane
from harness.event_store import IntegrityFailure, SQLiteEventStore
from harness.projection import Projector, ProjectionError


NOW = "2026-04-16T12:00:00Z"
USER = {"actorType": "human", "actorId": "user.owner", "role": "user"}
VP = {"actorType": "agent", "actorId": "agent.vp", "role": "vp"}


def project_record():
    return {
        "recordType": "project",
        "schemaVersion": "0.1",
        "projectId": "project.alpha",
        "revision": 1,
        "name": "Synthetic project",
        "status": "active",
        "charter": {
            "revision": 1,
            "goals": ["Produce sourced findings"],
            "nonGoals": ["No external side effects"],
            "acceptanceCriteria": ["Citations are traceable"],
        },
        "createdBy": USER,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def work_record(work_id, title):
    return {
        "recordType": "work_item",
        "schemaVersion": "0.1",
        "projectId": "project.alpha",
        "workId": work_id,
        "revision": 1,
        "parentWorkId": None,
        "title": title,
        "objective": f"Complete {title}.",
        "workType": "research",
        "status": "ready",
        "owner": VP,
        "dependsOn": [],
        "capabilityGrantIds": [],
        "acceptanceCriteria": ["Record evidence"],
        "createdBy": USER,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


class ProjectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteEventStore(Path(self.temp.name) / "canonical.sqlite3")
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def event(self, event_type, payload, actor=USER):
        self.sequence += 1
        return self.store.append({
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": f"event.{self.sequence}",
            "projectId": "project.alpha",
            "eventType": event_type,
            "actor": actor,
            "occurredAt": NOW,
            "causationId": None,
            "correlationId": "trace.test",
            "idempotencyKey": f"idem.{self.sequence}",
            "payload": payload,
        }).event

    def create_project(self):
        record = project_record()
        record_hash = self.store.put_record(record)
        charter_bytes = json.dumps(record["charter"], sort_keys=True, separators=(",", ":")).encode()
        charter_hash = hashlib.sha256(charter_bytes).hexdigest()
        self.store.put_blob(charter_bytes)
        self.event("project.created", {
            "name": record["name"],
            "charterHash": charter_hash,
            "recordHash": record_hash,
        })
        return record

    def create_work(self, work_id, title):
        record = work_record(work_id, title)
        record_hash = self.store.put_record(record)
        self.event("work.created", {
            "workId": work_id,
            "title": record["title"],
            "objective": record["objective"],
            "owner": record["owner"],
            "parentWorkId": None,
            "recordHash": record_hash,
        }, actor=VP)
        return record

    def change_status(self, record, new_status):
        updated = dict(record)
        updated["revision"] += 1
        updated["status"] = new_status
        updated["updatedAt"] = "2026-04-16T12:01:00Z"
        record_hash = self.store.put_record(updated)
        self.event("work.status_changed", {
            "workId": record["workId"],
            "from": record["status"],
            "to": new_status,
            "reason": "synthetic question pause/resume test",
            "recordHash": record_hash,
        }, actor=VP)
        return updated

    def test_replay_reports_empty_project_state_without_inventing_history(self):
        state = Projector(self.store).replay("project.missing")
        self.assertIsNone(state["project"])
        self.assertEqual(state["_eventSequence"], 0)

    def test_replay_rebuilds_work_discussion_and_inbox_pause_resume(self):
        project = self.create_project()
        primary = self.create_work("work.primary", "Primary research")
        independent = self.create_work("work.independent", "Independent literature scan")

        # Accepted messages are content-addressed, so event replay rebuilds
        # the exact transcript without consulting AG2/Meldwork storage.
        content_ref, content_hash = self.store.put_blob("A sourced proposal.")
        self.event("discussion.message_added", {
            "workId": primary["workId"],
            "threadId": "thread.primary",
            "messageType": "proposal",
            "contentRef": content_ref,
            "contentHash": content_hash,
            "visibleTo": ["agent.vp"],
        }, actor=VP)

        primary = self.change_status(primary, "waiting_human")
        question = {
            "recordType": "human_question",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "questionId": "question.scope",
            "questionType": "clarification",
            "priority": "normal",
            "blocking": True,
            "requestedBy": VP,
            "workIds": [primary["workId"]],
            "runId": None,
            "questionText": "Should historical evidence be included?",
            "impact": "This changes which sources are in scope.",
            "options": [],
            "status": "open",
            "createdAt": NOW,
        }
        question_hash = self.store.put_record(question)
        self.event("human.question_opened", {
            "questionId": question["questionId"],
            "questionType": question["questionType"],
            "priority": question["priority"],
            "blocking": True,
            "workIds": question["workIds"],
            "questionRef": question["questionId"],
            "recordHash": question_hash,
        }, actor=VP)

        answered = dict(question)
        answered["status"] = "answered"
        answered["answer"] = {
            "text": "Yes, include historical evidence.",
            "answeredBy": USER,
            "answeredAt": "2026-04-16T12:02:00Z",
            "resumedWorkIds": [primary["workId"]],
        }
        answered_hash = self.store.put_record(answered)
        self.event("human.question_answered", {
            "questionId": question["questionId"],
            "answerRef": "answer.scope.1",
            "resumedWorkIds": [primary["workId"]],
            "recordHash": answered_hash,
        }, actor=USER)
        primary = self.change_status(primary, "ready")

        rebuilt = Projector(self.store).replay("project.alpha")
        self.assertEqual(rebuilt["project"], project)
        self.assertEqual(rebuilt["workItems"]["work.primary"], primary)
        self.assertEqual(rebuilt["workItems"]["work.independent"], independent)
        self.assertEqual(rebuilt["workItems"]["work.independent"]["status"], "ready")
        self.assertEqual(rebuilt["questions"]["question.scope"]["status"], "answered")
        self.assertEqual(rebuilt["messages"][0]["content"], "A sourced proposal.")
        self.assertEqual(len(rebuilt["messages"]), 1)

    def test_replay_detects_artifact_content_tampering(self):
        self.create_project()
        work = self.create_work("work.artifact", "Review stored artifact")
        content = b"synthetic artifact body"
        storage_ref, digest = self.store.put_blob(content)
        artifact = {
            "recordType": "artifact", "schemaVersion": "0.1",
            "projectId": "project.alpha", "artifactId": "artifact.synthetic",
            "workId": work["workId"], "name": "synthetic.txt", "mediaType": "text/plain",
            "sha256": digest, "byteSize": len(content), "storageRef": storage_ref,
            "createdBy": VP, "createdAt": NOW, "reviewStatus": "unreviewed",
        }
        record_hash = self.store.put_record(artifact)
        self.event("artifact.registered", {
            "artifactId": artifact["artifactId"], "workId": work["workId"],
            "sha256": digest, "recordHash": record_hash,
        }, actor=VP)
        self.assertIn("artifact.synthetic", Projector(self.store).replay("project.alpha")["artifacts"])

        conn = sqlite3.connect(self.store.path)
        conn.execute("DROP TRIGGER canonical_blobs_no_update")
        conn.execute("UPDATE canonical_blobs SET content=? WHERE content_hash=?", (b"tampered", digest))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(IntegrityFailure, "blob hash mismatch"):
            Projector(self.store).replay("project.alpha")

    def test_replay_rejects_running_work_with_incomplete_dependencies(self):
        self.create_project()
        dependency = self.create_work("work.dependency", "Prerequisite")
        work = self.create_work("work.dependent", "Dependent")
        control = ProjectControlPlane(self.store)
        control.revise_work(
            "project.alpha", work["workId"], {"dependsOn": [dependency["workId"]]},
            actor=VP, occurred_at="2026-04-16T12:01:00Z",
        )
        current = Projector(self.store).replay("project.alpha")["workItems"][work["workId"]]
        running = dict(current)
        running.update({
            "revision": current["revision"] + 1,
            "status": "running",
            "updatedAt": "2026-04-16T12:02:00Z",
        })
        record_hash = self.store.put_record(running)
        self.event("work.status_changed", {
            "workId": work["workId"], "from": "ready", "to": "running",
            "recordHash": record_hash,
        }, actor=VP)
        with self.assertRaisesRegex(ProjectionError, "cannot run before dependencies complete"):
            Projector(self.store).replay("project.alpha")

    def test_replay_rejects_forged_terminal_work_resurrection(self):
        self.create_project()
        work = self.create_work("work.terminal", "Terminal work")
        control = ProjectControlPlane(self.store)
        control.transition_work("project.alpha", work["workId"], "running", actor=VP)
        completed = control.transition_work("project.alpha", work["workId"], "completed", actor=VP)
        resurrected = dict(completed)
        resurrected.update({
            "revision": completed["revision"] + 1,
            "status": "running",
            "updatedAt": "2026-04-16T12:03:00Z",
        })
        record_hash = self.store.put_record(resurrected)
        self.event("work.status_changed", {
            "workId": work["workId"], "from": "completed", "to": "running",
            "recordHash": record_hash,
        }, actor=VP)
        with self.assertRaisesRegex(ProjectionError, "forbidden work status transition"):
            Projector(self.store).replay("project.alpha")

    def test_replay_rejects_forged_work_revision_inside_live_question_closure(self):
        self.create_project()
        work = self.create_work("work.paused", "Research while gated")
        question = {
            "recordType": "human_question", "schemaVersion": "0.1",
            "projectId": "project.alpha", "questionId": "question.freeze",
            "questionType": "clarification", "priority": "normal", "blocking": True,
            "requestedBy": VP, "workIds": [work["workId"]], "runId": None,
            "questionText": "Clarify the scope?", "impact": "It changes the work.",
            "options": [], "status": "open", "createdAt": NOW,
        }
        ProjectControlPlane(self.store).open_question(question)
        paused_work = Projector(self.store).replay("project.alpha")["workItems"][work["workId"]]
        changed = dict(paused_work)
        changed.update({
            "revision": paused_work["revision"] + 1,
            "objective": "Altered while blocked.",
            "updatedAt": "2026-04-16T12:02:00Z",
        })
        record_hash = self.store.put_record(changed)
        self.event("work.revised", {
            "workId": work["workId"], "fromRevision": paused_work["revision"],
            "toRevision": changed["revision"], "recordHash": record_hash,
        }, actor=VP)
        with self.assertRaisesRegex(ProjectionError, "live blocking question dependency closure"):
            Projector(self.store).replay("project.alpha")

    def test_replay_preserves_failed_runs_and_conflicting_evidence(self):
        self.create_project()
        work = self.create_work("work.conflicts", "Compare evidence")
        run = {
            "recordType": "agent_run",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "runId": "run.failed",
            "workId": work["workId"],
            "agent": {"actorType": "agent", "actorId": "agent.researcher", "role": "specialist"},
            "adapter": {"name": "test-adapter", "version": "1"},
            "status": "running",
            "contextHash": "a" * 64,
            "capabilityGrantIds": [],
            "startedAt": NOW,
        }
        run_hash = self.store.put_record(run)
        self.event("run.started", {
            "runId": run["runId"], "workId": work["workId"],
            "adapter": run["adapter"], "contextHash": run["contextHash"],
            "capabilityGrantIds": [], "recordHash": run_hash,
        }, actor=run["agent"])
        failed_run = {**run, "status": "failed", "finishedAt": "2026-04-16T12:01:00Z"}
        failed_hash = self.store.put_record(failed_run)
        self.event("run.finished", {
            "runId": run["runId"], "terminalStatus": "failed", "recordHash": failed_hash,
        }, actor=run["agent"])

        source_hashes = ["b" * 64, "c" * 64]
        stances = ["supports", "contradicts"]
        for index, (source_hash, stance) in enumerate(zip(source_hashes, stances), start=1):
            evidence = {
                "recordType": "evidence",
                "schemaVersion": "0.1",
                "projectId": "project.alpha",
                "evidenceId": f"evidence.claim.{index}",
                "workId": work["workId"],
                "claimText": "The synthetic claim is true.",
                "stance": stance,
                "source": {
                    "uri": f"https://example.test/source-{index}",
                    "title": f"Synthetic source {index}",
                    "retrievedAt": NOW,
                    "contentHash": source_hash,
                },
                "createdBy": run["agent"],
                "createdAt": NOW,
                "reviewStatus": "unreviewed",
            }
            evidence_hash = self.store.put_record(evidence)
            self.event("evidence.registered", {
                "evidenceId": evidence["evidenceId"],
                "workId": work["workId"],
                "stance": stance,
                "sourceHash": source_hash,
                "recordHash": evidence_hash,
            }, actor=run["agent"])

        state = Projector(self.store).replay("project.alpha")
        self.assertEqual(state["agentRuns"]["run.failed"]["status"], "failed")
        self.assertEqual(
            {item["stance"] for item in state["evidence"].values()},
            {"supports", "contradicts"},
        )

    def test_replay_rejects_payload_snapshot_mismatch(self):
        project = project_record()
        record_hash = self.store.put_record(project)
        wrong_charter_hash = "b" * 64
        self.event("project.created", {
            "name": project["name"],
            "charterHash": wrong_charter_hash,
            "recordHash": record_hash,
        })
        with self.assertRaisesRegex(Exception, "does not match"):
            Projector(self.store).replay("project.alpha")


if __name__ == "__main__":
    unittest.main()
