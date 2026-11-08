import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "v0.1" / "schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
NOW = "2026-04-16T12:00:00Z"
HASH = "a" * 64
ACTOR = {"actorType": "agent", "actorId": "agent.researcher", "role": "specialist"}


def valid_records():
    return [
        {
            "recordType": "project",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "revision": 1,
            "name": "Synthetic research project",
            "status": "active",
            "charter": {
                "revision": 1,
                "goals": ["Compare public evidence"],
                "nonGoals": ["No external side effects"],
                "acceptanceCriteria": ["Cite every material claim"],
            },
            "createdBy": {"actorType": "human", "actorId": "user.owner", "role": "user"},
            "createdAt": NOW,
            "updatedAt": NOW,
        },
        {
            "recordType": "work_item",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "workId": "work.research",
            "revision": 1,
            "parentWorkId": None,
            "title": "Review public evidence",
            "objective": "Produce a sourced comparison.",
            "workType": "research",
            "status": "ready",
            "owner": ACTOR,
            "dependsOn": [],
            "capabilityGrantIds": [],
            "acceptanceCriteria": ["Every factual claim has a source"],
            "createdAt": NOW,
            "updatedAt": NOW,
        },
        {
            "recordType": "agent_run",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "runId": "run.first",
            "workId": "work.research",
            "agent": ACTOR,
            "adapter": {"name": "mock", "version": "0.1"},
            "status": "succeeded",
            "contextHash": HASH,
            "capabilityGrantIds": [],
            "startedAt": NOW,
            "finishedAt": NOW,
            "outputArtifactIds": [],
        },
        {
            "recordType": "human_question",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "questionId": "question.scope",
            "questionType": "clarification",
            "priority": "normal",
            "blocking": True,
            "requestedBy": ACTOR,
            "workIds": ["work.research"],
            "runId": None,
            "questionText": "Should the comparison include historical sources?",
            "impact": "This changes source selection.",
            "options": [],
            "status": "open",
            "createdAt": NOW,
        },
        {
            "recordType": "human_question",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "questionId": "question.answered",
            "questionType": "approval",
            "priority": "high",
            "blocking": True,
            "requestedBy": ACTOR,
            "workIds": ["work.research"],
            "runId": "run.first",
            "questionText": "May this public source be used?",
            "impact": "It affects one evidence citation.",
            "options": [],
            "status": "answered",
            "answer": {
                "text": "Yes, use the source.",
                "answeredBy": {"actorType": "human", "actorId": "user.owner", "role": "user"},
                "answeredAt": NOW,
                "resumedWorkIds": ["work.research"],
            },
            "createdAt": NOW,
        },
        {
            "recordType": "artifact",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "artifactId": "artifact.report",
            "workId": "work.research",
            "name": "comparison.md",
            "mediaType": "text/markdown",
            "sha256": HASH,
            "byteSize": 0,
            "storageRef": "blob.report",
            "createdBy": ACTOR,
            "runId": "run.first",
            "createdAt": NOW,
            "reviewStatus": "unreviewed",
        },
        {
            "recordType": "capability_grant",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "grantId": "grant.research.read",
            "revision": 1,
            "subject": ACTOR,
            "workIds": ["work.research"],
            "capability": "filesystem.read",
            "resourceIds": ["resource.public-corpus"],
            "grantedBy": {"actorType": "human", "actorId": "user.owner", "role": "user"},
            "createdAt": NOW,
            "expiresAt": "2026-04-17T12:00:00Z",
            "status": "active",
        },
        {
            "recordType": "evidence",
            "schemaVersion": "0.1",
            "projectId": "project.alpha",
            "evidenceId": "evidence.claim1",
            "workId": "work.research",
            "claimText": "A synthetic example claim.",
            "stance": "supports",
            "source": {
                "uri": "https://example.org/source",
                "title": "Example source",
                "retrievedAt": NOW,
                "contentHash": HASH,
                "excerptRef": "blob.excerpt",
            },
            "createdBy": ACTOR,
            "runId": "run.first",
            "createdAt": NOW,
            "reviewStatus": "unreviewed",
        },
        {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": "event.create-project",
            "projectId": "project.alpha",
            "sequence": 1,
            "eventType": "project.created",
            "actor": {"actorType": "human", "actorId": "user.owner", "role": "user"},
            "occurredAt": NOW,
            "causationId": None,
            "correlationId": "trace.initial",
            "idempotencyKey": "idem.project-create",
            "payload": {"name": "Synthetic research project", "charterHash": HASH, "recordHash": HASH},
        },
    ]


class ContractSchemaTests(unittest.TestCase):
    def test_schema_is_valid_draft_2020_12(self):
        Draft202012Validator.check_schema(SCHEMA)

    def test_synthetic_records_validate(self):
        for record in valid_records():
            with self.subTest(recordType=record["recordType"], id=record.get("projectId", record.get("eventId"))):
                self.assertEqual([], list(VALIDATOR.iter_errors(record)))

    def test_evidence_requires_a_content_addressed_source_snapshot(self):
        record = next(item for item in valid_records() if item["recordType"] == "evidence")
        del record["source"]["contentHash"]
        self.assertTrue(list(VALIDATOR.iter_errors(record)))

    def test_unknown_sensitive_fields_fail_closed(self):
        record = valid_records()[0]
        record["apiKey"] = "never-allowed"
        self.assertTrue(list(VALIDATOR.iter_errors(record)))

    def test_question_answer_state_is_consistent(self):
        answered = valid_records()[4]
        answered.pop("answer")
        self.assertTrue(list(VALIDATOR.iter_errors(answered)))

        pending = valid_records()[3]
        pending["answer"] = valid_records()[4]["answer"]
        self.assertTrue(list(VALIDATOR.iter_errors(pending)))

    def test_event_type_and_payload_must_match(self):
        event = valid_records()[-1]
        event["eventType"] = "work.status_changed"
        self.assertTrue(list(VALIDATOR.iter_errors(event)))

    def test_question_terminal_states_require_actor_time_and_reason(self):
        cancelled = valid_records()[3]
        cancelled.update({
            "status": "cancelled",
            "closedBy": {"actorType": "human", "actorId": "user.owner", "role": "user"},
            "closedAt": NOW,
            "resolutionReason": "The user withdrew the question.",
        })
        self.assertEqual([], list(VALIDATOR.iter_errors(cancelled)))
        cancelled.pop("closedBy")
        self.assertTrue(list(VALIDATOR.iter_errors(cancelled)))

    def test_capability_grants_are_scoped_and_revocation_is_attributed(self):
        grant = valid_records()[6]
        grant["resourceIds"] = ["*"]
        self.assertTrue(list(VALIDATOR.iter_errors(grant)))

        grant = valid_records()[6]
        revoked = {
            **grant,
            "revision": 2,
            "status": "revoked",
            "revokedBy": {"actorType": "human", "actorId": "user.owner", "role": "user"},
            "revokedAt": NOW,
            "revokeReason": "Scope no longer required.",
        }
        self.assertEqual([], list(VALIDATOR.iter_errors(revoked)))
        revoked.pop("revokeReason")
        self.assertTrue(list(VALIDATOR.iter_errors(revoked)))

    def test_hashes_and_actor_roles_are_constrained(self):
        artifact = valid_records()[5]
        artifact["sha256"] = "not-a-hash"
        self.assertTrue(list(VALIDATOR.iter_errors(artifact)))

        project = valid_records()[0]
        project["createdBy"]["role"] = "root-admin"
        self.assertTrue(list(VALIDATOR.iter_errors(project)))


if __name__ == "__main__":
    unittest.main()
