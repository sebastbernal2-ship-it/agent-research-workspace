import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.event_store import SQLiteEventStore
from harness.projection import Projector


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
