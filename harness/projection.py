"""Deterministic project-state replay over the canonical append-only log."""

from __future__ import annotations

import hashlib
from typing import Any

from .event_store import IntegrityFailure, SQLiteEventStore


class ProjectionError(IntegrityFailure):
    """The canonical event sequence cannot be projected consistently."""


class Projector:
    """Rebuild project/work/run/question/evidence/message state from events."""

    def __init__(self, store: SQLiteEventStore):
        self.store = store

    def _load_record(self, digest: str, record_type: str, project_id: str) -> dict[str, Any]:
        record = self.store.get_record(digest)
        if record.get("recordType") != record_type or record.get("projectId") != project_id:
            raise ProjectionError(f"{record_type} record hash {digest} has wrong type or project scope")
        return record

    @staticmethod
    def _charter_hash(project: dict[str, Any]) -> str:
        encoded = SQLiteEventStore._canonical_json(project["charter"]).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _check_same_identity(old: dict[str, Any], new: dict[str, Any], fields: tuple[str, ...]) -> None:
        for field in fields:
            if old.get(field) != new.get(field):
                raise ProjectionError(f"immutable identity field {field!r} changed")

    def replay(self, project_id: str) -> dict[str, Any]:
        self.store.verify(project_id)
        state: dict[str, Any] = {
            "project": None,
            "workItems": {},
            "agentRuns": {},
            "questions": {},
            "artifacts": {},
            "evidence": {},
            "messages": [],
        }
        events = self.store.events(project_id)
        for event in events:
            event_type = event["eventType"]
            payload = event["payload"]
            if state["project"] is None and event_type != "project.created":
                raise ProjectionError("project.created must be the first event")

            if event_type == "project.created":
                if state["project"] is not None or event["sequence"] != 1:
                    raise ProjectionError("project can be created only once as sequence 1")
                project = self._load_record(payload["recordHash"], "project", project_id)
                if project["name"] != payload["name"] or self._charter_hash(project) != payload["charterHash"]:
                    raise ProjectionError("project.created payload does not match its immutable project record")
                if project.get("createdBy") is not None and project["createdBy"] != event["actor"]:
                    raise ProjectionError("project creator does not match event actor")
                state["project"] = project

            elif event_type == "project.charter_revised":
                old = state["project"]
                new = self._load_record(payload["recordHash"], "project", project_id)
                if payload["charterRevision"] != old["charter"]["revision"] + 1:
                    raise ProjectionError("charter revision is not the next revision")
                if new["charter"]["revision"] != payload["charterRevision"] or self._charter_hash(new) != payload["charterHash"]:
                    raise ProjectionError("charter revision payload does not match its immutable record")
                self._check_same_identity(old, new, ("projectId", "name", "status", "createdAt", "createdBy"))
                if new["revision"] != old["revision"] + 1:
                    raise ProjectionError("project record revision is not monotonic")
                state["project"] = new

            elif event_type == "work.created":
                work_id = payload["workId"]
                if work_id in state["workItems"]:
                    raise ProjectionError(f"work item {work_id!r} already exists")
                work = self._load_record(payload["recordHash"], "work_item", project_id)
                if work["workId"] != work_id or work["title"] != payload["title"] or work["objective"] != payload["objective"] or work["owner"] != payload["owner"]:
                    raise ProjectionError("work.created payload does not match immutable work record")
                parent = work.get("parentWorkId")
                if parent is not None and parent not in state["workItems"]:
                    raise ProjectionError(f"unknown parent work item {parent!r}")
                missing_dependencies = set(work["dependsOn"]) - state["workItems"].keys()
                if missing_dependencies:
                    raise ProjectionError(f"work dependencies must be created first: {sorted(missing_dependencies)}")
                state["workItems"][work_id] = work

            elif event_type == "work.status_changed":
                work_id = payload["workId"]
                old = state["workItems"].get(work_id)
                if old is None or old["status"] != payload["from"]:
                    raise ProjectionError(f"invalid prior work status for {work_id!r}")
                new = self._load_record(payload["recordHash"], "work_item", project_id)
                if new["workId"] != work_id or new["status"] != payload["to"] or new["revision"] != old["revision"] + 1:
                    raise ProjectionError("work status event does not match next work record revision")
                expected = dict(old)
                expected.update(status=payload["to"], revision=new["revision"], updatedAt=new["updatedAt"])
                if expected != new:
                    raise ProjectionError("work.status_changed modified fields other than status/revision/time")
                state["workItems"][work_id] = new

            elif event_type == "discussion.message_added":
                payload_ref = payload["contentRef"]
                if not payload_ref.startswith("blob.") or payload_ref[5:] != payload["contentHash"]:
                    raise ProjectionError("discussion content reference does not match content hash")
                try:
                    content = self.store.get_blob(payload["contentHash"]).decode("utf-8")
                except (KeyError, UnicodeDecodeError) as exc:
                    raise ProjectionError("discussion content blob is missing or not UTF-8") from exc
                if payload["workId"] not in state["workItems"]:
                    raise ProjectionError(f"message references unknown work item {payload['workId']!r}")
                state["messages"].append({
                    "eventId": event["eventId"],
                    "sequence": event["sequence"],
                    "workId": payload["workId"],
                    "threadId": payload["threadId"],
                    "messageType": payload["messageType"],
                    "actor": event["actor"],
                    "content": content,
                    "contentHash": payload["contentHash"],
                    "visibleTo": payload["visibleTo"],
                    "occurredAt": event["occurredAt"],
                })

            elif event_type == "run.started":
                run_id = payload["runId"]
                if run_id in state["agentRuns"] or payload["workId"] not in state["workItems"]:
                    raise ProjectionError("run id is duplicate or references unknown work")
                run = self._load_record(payload["recordHash"], "agent_run", project_id)
                if run["runId"] != run_id or run["workId"] != payload["workId"] or run["status"] != "running":
                    raise ProjectionError("run.started payload does not match run record")
                if run["adapter"] != payload["adapter"] or run["contextHash"] != payload["contextHash"] or run["capabilityGrantIds"] != payload["capabilityGrantIds"]:
                    raise ProjectionError("run.started metadata does not match run record")
                state["agentRuns"][run_id] = run

            elif event_type == "run.finished":
                run_id = payload["runId"]
                old = state["agentRuns"].get(run_id)
                if old is None:
                    raise ProjectionError(f"run {run_id!r} was not started")
                new = self._load_record(payload["recordHash"], "agent_run", project_id)
                if new["runId"] != run_id or new["workId"] != old["workId"] or new["status"] != payload["terminalStatus"]:
                    raise ProjectionError("run.finished payload does not match terminal run record")
                self._check_same_identity(old, new, ("projectId", "runId", "workId", "agent", "adapter", "contextHash", "capabilityGrantIds", "startedAt"))
                state["agentRuns"][run_id] = new

            elif event_type == "human.question_opened":
                question_id = payload["questionId"]
                question = self._load_record(payload["recordHash"], "human_question", project_id)
                if question_id in state["questions"] or question["questionId"] != question_id or question["status"] != "open":
                    raise ProjectionError("question.opened has duplicate id or non-open record")
                if question["questionType"] != payload["questionType"] or question["blocking"] != payload["blocking"] or question["workIds"] != payload["workIds"]:
                    raise ProjectionError("question.opened payload does not match question record")
                if payload["questionRef"] != question_id or any(work_id not in state["workItems"] for work_id in question["workIds"]):
                    raise ProjectionError("question references unknown work or mismatched question id")
                if question["blocking"] and any(state["workItems"][work_id]["status"] != "waiting_human" for work_id in question["workIds"]):
                    raise ProjectionError("blocking question opened without pausing every affected work item")
                state["questions"][question_id] = question

            elif event_type == "human.question_answered":
                question_id = payload["questionId"]
                old = state["questions"].get(question_id)
                if old is None or old["status"] != "open":
                    raise ProjectionError(f"question {question_id!r} is not open")
                new = self._load_record(payload["recordHash"], "human_question", project_id)
                if new["questionId"] != question_id or new["status"] != "answered" or new.get("answer") is None:
                    raise ProjectionError("question answer event requires an answered question snapshot")
                self._check_same_identity(old, new, ("projectId", "questionId", "questionType", "blocking", "requestedBy", "workIds", "questionText", "impact", "options", "createdAt"))
                resumed = payload["resumedWorkIds"]
                if resumed != new["answer"].get("resumedWorkIds", []):
                    raise ProjectionError("resumed work set does not match recorded human answer")
                if old["blocking"] and set(resumed) != set(old["workIds"]):
                    raise ProjectionError("blocking answer must resume exactly its affected work set")
                if not old["blocking"] and resumed:
                    raise ProjectionError("nonblocking question cannot resume paused work")
                if any(work_id not in state["workItems"] or state["workItems"][work_id]["status"] != "waiting_human" for work_id in resumed):
                    raise ProjectionError("answer references work that is not paused")
                state["questions"][question_id] = new

            elif event_type == "artifact.registered":
                artifact_id = payload["artifactId"]
                artifact = self._load_record(payload["recordHash"], "artifact", project_id)
                if artifact_id in state["artifacts"] or artifact["artifactId"] != artifact_id or artifact["workId"] != payload["workId"] or artifact["sha256"] != payload["sha256"]:
                    raise ProjectionError("artifact.registered payload does not match artifact record")
                if artifact["workId"] not in state["workItems"]:
                    raise ProjectionError("artifact references unknown work item")
                state["artifacts"][artifact_id] = artifact

            elif event_type == "evidence.registered":
                evidence_id = payload["evidenceId"]
                evidence = self._load_record(payload["recordHash"], "evidence", project_id)
                if evidence_id in state["evidence"] or evidence["evidenceId"] != evidence_id or evidence["workId"] != payload["workId"] or evidence["stance"] != payload["stance"]:
                    raise ProjectionError("evidence.registered payload does not match evidence record")
                if evidence["source"].get("contentHash") != payload["sourceHash"] or evidence["workId"] not in state["workItems"]:
                    raise ProjectionError("evidence source hash or work reference is inconsistent")
                state["evidence"][evidence_id] = evidence

            else:
                raise ProjectionError(f"unsupported event type {event_type!r}")
        return state
