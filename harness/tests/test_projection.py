import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness.control_plane import ProjectControlPlane
from harness.event_store import IntegrityFailure, SQLiteEventStore
from harness.projection import Projector, ProjectionError
from harness.run_ledger import RunLedger


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

    def test_run_outputs_preserve_artifact_evidence_and_message_provenance(self):
        self.create_project()
        work = self.create_work("work.outputs", "Persist attributable outputs")
        ledger = RunLedger(self.store, clock=lambda: NOW)
        run = ledger.start(
            "project.alpha", work["workId"], run_id="run.outputs", agent=VP,
            adapter={"name": "test-adapter", "version": "1"},
            context_hash="a" * 64, correlation_id="trace.test", occurred_at=NOW,
        )

        artifact_content = b"artifact from this run"
        storage_ref, artifact_hash = self.store.put_blob(artifact_content)
        artifact = {
            "recordType": "artifact", "schemaVersion": "0.1",
            "projectId": "project.alpha", "artifactId": "artifact.run-output",
            "workId": work["workId"], "name": "result.txt", "mediaType": "text/plain",
            "sha256": artifact_hash, "byteSize": len(artifact_content), "storageRef": storage_ref,
            "createdBy": VP, "runId": run["runId"], "createdAt": NOW, "reviewStatus": "unreviewed",
        }
        self.event("artifact.registered", {
            "artifactId": artifact["artifactId"], "workId": work["workId"],
            "sha256": artifact_hash, "recordHash": self.store.put_record(artifact),
        }, actor=VP)

        source_content = b"synthetic source excerpt"
        source_hash = hashlib.sha256(source_content).hexdigest()
        self.store.put_blob(source_content)
        evidence = {
            "recordType": "evidence", "schemaVersion": "0.1",
            "projectId": "project.alpha", "evidenceId": "evidence.run-output",
            "workId": work["workId"], "claimText": "The reviewed synthetic claim is supported.",
            "stance": "supports",
            "source": {"uri": "https://example.test/source", "title": "Synthetic source", "retrievedAt": NOW, "contentHash": source_hash},
            "createdBy": VP, "runId": run["runId"], "createdAt": NOW, "reviewStatus": "unreviewed",
        }
        self.event("evidence.registered", {
            "evidenceId": evidence["evidenceId"], "workId": work["workId"],
            "stance": evidence["stance"], "sourceHash": source_hash,
            "recordHash": self.store.put_record(evidence),
        }, actor=VP)

        message_content = "The artifact and evidence support the result."
        message_ref, message_hash = self.store.put_blob(message_content)
        message = self.event("discussion.message_added", {
            "workId": work["workId"], "threadId": "thread.outputs", "messageType": "evidence",
            "runId": run["runId"], "contentRef": message_ref, "contentHash": message_hash,
            "artifactIds": [artifact["artifactId"]], "evidenceIds": [evidence["evidenceId"]],
            "visibleTo": [VP["actorId"]],
        }, actor=VP)
        finished = ledger.finish(
            "project.alpha", run["runId"], terminal_status="succeeded", actor=VP, occurred_at=NOW,
        )
        self.assertEqual(finished["outputArtifactIds"], [artifact["artifactId"]])
        self.assertEqual(finished["outputEvidenceIds"], [evidence["evidenceId"]])
        self.assertEqual(finished["outputEventIds"], [message["eventId"]])
        replayed = Projector(self.store).replay("project.alpha")
        self.assertEqual(replayed["messages"][0]["runId"], run["runId"])
        self.assertEqual(replayed["agentRuns"][run["runId"]]["outputEventIds"], [message["eventId"]])
        self.assertTrue(self.store.verify("project.alpha"))

    def test_artifact_derivation_records_registered_inputs_and_evidence(self):
        self.create_project()
        work = self.create_work("work.derivation", "Derive an output")
        input_content = b"source artifact bytes"
        input_ref, input_hash = self.store.put_blob(input_content)
        input_record = {
            "recordType": "artifact", "schemaVersion": "0.1",
            "projectId": "project.alpha", "artifactId": "artifact.input",
            "workId": work["workId"], "name": "input.txt", "mediaType": "text/plain",
            "sha256": input_hash, "byteSize": len(input_content), "storageRef": input_ref,
            "createdBy": VP, "createdAt": NOW,
        }
        self.event("artifact.registered", {
            "artifactId": input_record["artifactId"], "workId": work["workId"],
            "sha256": input_hash, "recordHash": self.store.put_record(input_record),
        }, actor=VP)

        source_content = b"evidence source bytes"
        source_hash = hashlib.sha256(source_content).hexdigest()
        self.store.put_blob(source_content)
        evidence = {
            "recordType": "evidence", "schemaVersion": "0.1",
            "projectId": "project.alpha", "evidenceId": "evidence.input",
            "workId": work["workId"], "claimText": "The source supports the derived result.",
            "stance": "supports",
            "source": {"uri": "https://example.test/evidence", "title": "Synthetic evidence", "retrievedAt": NOW, "contentHash": source_hash},
            "createdBy": VP, "createdAt": NOW,
        }
        self.event("evidence.registered", {
            "evidenceId": evidence["evidenceId"], "workId": work["workId"],
            "stance": evidence["stance"], "sourceHash": source_hash,
            "recordHash": self.store.put_record(evidence),
        }, actor=VP)

        output_content = b"derived output bytes"
        output_ref, output_hash = self.store.put_blob(output_content)
        output = {
            "recordType": "artifact", "schemaVersion": "0.1",
            "projectId": "project.alpha", "artifactId": "artifact.derived",
            "workId": work["workId"], "name": "output.txt", "mediaType": "text/plain",
            "sha256": output_hash, "byteSize": len(output_content), "storageRef": output_ref,
            "createdBy": VP, "createdAt": NOW,
            "inputArtifactIds": [input_record["artifactId"]],
            "evidenceIds": [evidence["evidenceId"]],
        }
        self.event("artifact.registered", {
            "artifactId": output["artifactId"], "workId": work["workId"],
            "sha256": output_hash, "inputArtifactIds": output["inputArtifactIds"],
            "evidenceIds": output["evidenceIds"], "recordHash": self.store.put_record(output),
        }, actor=VP)
        replayed = Projector(self.store).replay("project.alpha")
        self.assertEqual(replayed["artifacts"]["artifact.derived"]["inputArtifactIds"], ["artifact.input"])
        self.assertEqual(replayed["artifacts"]["artifact.derived"]["evidenceIds"], ["evidence.input"])

    def test_artifact_derivation_rejects_missing_input_reference(self):
        self.create_project()
        work = self.create_work("work.missing-input", "Reject missing provenance")
        content = b"derived without a registered input"
        storage_ref, digest = self.store.put_blob(content)
        artifact = {
            "recordType": "artifact", "schemaVersion": "0.1",
            "projectId": "project.alpha", "artifactId": "artifact.invalid-derived",
            "workId": work["workId"], "name": "output.txt", "mediaType": "text/plain",
            "sha256": digest, "byteSize": len(content), "storageRef": storage_ref,
            "createdBy": VP, "createdAt": NOW,
            "inputArtifactIds": ["artifact.not-registered"],
        }
        self.event("artifact.registered", {
            "artifactId": artifact["artifactId"], "workId": work["workId"],
            "sha256": digest, "inputArtifactIds": artifact["inputArtifactIds"],
            "recordHash": self.store.put_record(artifact),
        }, actor=VP)
        with self.assertRaisesRegex(ProjectionError, "missing or unrelated input"):
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

    def test_replay_rejects_two_active_root_runs_for_one_work_item(self):
        self.create_project()
        work = self.create_work("work.root-owner", "Enforce one work owner")
        ProjectControlPlane(self.store).transition_work(
            "project.alpha", work["workId"], "running", actor=VP, reason="Begin work.",
        )
        agent = {"actorType": "agent", "actorId": "agent.owner", "role": "specialist"}
        for run_id in ("run.root-one", "run.root-two"):
            run = {
                "recordType": "agent_run", "schemaVersion": "0.1", "projectId": "project.alpha",
                "runId": run_id, "workId": work["workId"], "agent": agent,
                "adapter": {"name": "test-adapter", "version": "1"}, "status": "running",
                "contextHash": "d" * 64, "capabilityGrantIds": [], "startedAt": NOW,
            }
            record_hash = self.store.put_record(run)
            self.event("run.started", {
                "runId": run_id, "workId": work["workId"], "adapter": run["adapter"],
                "contextHash": run["contextHash"], "capabilityGrantIds": [], "recordHash": record_hash,
            }, actor=agent)
        with self.assertRaisesRegex(ProjectionError, "concurrent active root-run ownership"):
            Projector(self.store).replay("project.alpha")

    def test_run_started_actor_must_match_assigned_agent(self):
        self.create_project()
        work = self.create_work("work.run-actor", "Check run attribution")
        agent = {"actorType": "agent", "actorId": "agent.specialist", "role": "specialist"}
        run = {
            "recordType": "agent_run", "schemaVersion": "0.1", "projectId": "project.alpha",
            "runId": "run.actor-mismatch", "workId": work["workId"], "agent": agent,
            "adapter": {"name": "test-adapter", "version": "1"}, "status": "running",
            "contextHash": "a" * 64, "capabilityGrantIds": [], "startedAt": NOW,
        }
        record_hash = self.store.put_record(run)
        self.event("run.started", {
            "runId": run["runId"], "workId": work["workId"], "adapter": run["adapter"],
            "contextHash": run["contextHash"], "capabilityGrantIds": [], "recordHash": record_hash,
        }, actor=VP)
        with self.assertRaisesRegex(ProjectionError, "assigned agent"):
            Projector(self.store).replay("project.alpha")

    def test_run_finished_actor_must_match_assigned_agent(self):
        self.create_project()
        work = self.create_work("work.run-finish-actor", "Check terminal run attribution")
        agent = {"actorType": "agent", "actorId": "agent.specialist", "role": "specialist"}
        run = {
            "recordType": "agent_run", "schemaVersion": "0.1", "projectId": "project.alpha",
            "runId": "run.finish-actor-mismatch", "workId": work["workId"], "agent": agent,
            "adapter": {"name": "test-adapter", "version": "1"}, "status": "running",
            "contextHash": "b" * 64, "capabilityGrantIds": [], "startedAt": NOW,
        }
        started_hash = self.store.put_record(run)
        self.event("run.started", {
            "runId": run["runId"], "workId": work["workId"], "adapter": run["adapter"],
            "contextHash": run["contextHash"], "capabilityGrantIds": [], "recordHash": started_hash,
        }, actor=agent)
        finished = {**run, "status": "succeeded", "finishedAt": "2026-04-16T12:01:00Z"}
        finished_hash = self.store.put_record(finished)
        self.event("run.finished", {
            "runId": run["runId"], "terminalStatus": "succeeded", "recordHash": finished_hash,
        }, actor=VP)
        with self.assertRaisesRegex(ProjectionError, "assigned agent"):
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

        source_contents = [b"synthetic source one", b"synthetic source two"]
        source_hashes = [hashlib.sha256(content).hexdigest() for content in source_contents]
        for content in source_contents:
            self.store.put_blob(content)
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
