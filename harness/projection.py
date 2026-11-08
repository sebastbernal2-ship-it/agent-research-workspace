"""Deterministic project-state replay over the canonical append-only log."""

from __future__ import annotations

import hashlib
from typing import Any

from .event_store import IntegrityFailure, SQLiteEventStore


class ProjectionError(IntegrityFailure):
    """The canonical event sequence cannot be projected consistently."""


WORK_STATUS_TRANSITIONS = {
    "proposed": {"ready", "cancelled"},
    "ready": {"running", "blocked", "waiting_human", "cancelled"},
    "running": {"blocked", "waiting_human", "verifying", "completed", "failed", "cancelled"},
    "blocked": {"ready", "waiting_human", "failed", "cancelled"},
    "waiting_human": {"ready", "blocked", "cancelled"},
    "verifying": {"running", "completed", "failed", "blocked", "cancelled"},
    "failed": {"ready", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


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

    @staticmethod
    def _run_root_id(run_id: str, agent_runs: dict[str, dict[str, Any]]) -> str:
        current = run_id
        while agent_runs[current].get("parentRunId") is not None:
            current = agent_runs[current]["parentRunId"]
        return current

    @staticmethod
    def _work_dependency_scope(work_id: str, work_items: dict[str, dict[str, Any]]) -> set[str]:
        scope = {work_id}
        pending = list(work_items[work_id].get("dependsOn", []))
        while pending:
            dependency_id = pending.pop()
            if dependency_id in scope:
                continue
            scope.add(dependency_id)
            pending.extend(work_items[dependency_id].get("dependsOn", []))
        return scope

    @staticmethod
    def _validate_work_graph(work_items: dict[str, dict[str, Any]]) -> None:
        graph = {work_id: set(work.get("dependsOn", [])) for work_id, work in work_items.items()}
        for work_id, dependencies in graph.items():
            if work_id in dependencies:
                raise ProjectionError(f"work item {work_id!r} cannot depend on itself")
            missing = dependencies - graph.keys()
            if missing:
                raise ProjectionError(f"work item {work_id!r} has unknown dependencies: {sorted(missing)}")
            parent = work_items[work_id].get("parentWorkId")
            if parent == work_id or (parent is not None and parent not in graph):
                raise ProjectionError(f"work item {work_id!r} has an invalid parent")

        dependents: dict[str, set[str]] = {work_id: set() for work_id in graph}
        indegree = {work_id: len(dependencies) for work_id, dependencies in graph.items()}
        for work_id, dependencies in graph.items():
            for dependency in dependencies:
                dependents[dependency].add(work_id)
        ready = [work_id for work_id, degree in indegree.items() if degree == 0]
        visited_count = 0
        while ready:
            work_id = ready.pop()
            visited_count += 1
            for dependent in dependents[work_id]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
        if visited_count != len(graph):
            raise ProjectionError("work dependency graph contains a cycle")

        finished: set[str] = set()
        for start in graph:
            chain: set[str] = set()
            current: str | None = start
            while current is not None and current not in finished:
                if current in chain:
                    raise ProjectionError("work parent graph contains a cycle")
                chain.add(current)
                current = work_items[current].get("parentWorkId")
            finished.update(chain)

    def replay(self, project_id: str) -> dict[str, Any]:
        state, _ = self.replay_snapshot(project_id)
        return state

    def replay_snapshot(self, project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return projected state and the exact verified events used to build it."""
        events = self.store.verified_events(project_id)
        run_start_events: dict[str, dict[str, Any]] = {}
        state: dict[str, Any] = {
            "project": None,
            "workItems": {},
            "agentRuns": {},
            "questions": {},
            "artifacts": {},
            "evidence": {},
            "capabilityGrants": {},
            "messages": [],
        }
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
                self._validate_work_graph(state["workItems"])
                if any(
                    grant_id not in state["capabilityGrants"]
                    or work_id not in state["capabilityGrants"][grant_id]["workIds"]
                    for grant_id in work["capabilityGrantIds"]
                ):
                    raise ProjectionError("work.created references a missing or out-of-scope capability grant")

            elif event_type == "work.revised":
                work_id = payload["workId"]
                old = state["workItems"].get(work_id)
                if old is None or payload["fromRevision"] != old["revision"]:
                    raise ProjectionError(f"work item {work_id!r} revision is stale")
                protected_work_ids: set[str] = set()
                for question in state["questions"].values():
                    if question.get("status") != "open" or not question.get("blocking"):
                        continue
                    pending = list(question.get("workIds", []))
                    while pending:
                        protected_id = pending.pop()
                        if protected_id in protected_work_ids:
                            continue
                        protected_work_ids.add(protected_id)
                        pending.extend(state["workItems"][protected_id].get("dependsOn", []))
                if work_id in protected_work_ids:
                    raise ProjectionError("work revision changed a live blocking question dependency closure")
                new = self._load_record(payload["recordHash"], "work_item", project_id)
                if (
                    new["workId"] != work_id or new["revision"] != old["revision"] + 1
                    or payload["toRevision"] != new["revision"] or new["status"] != old["status"]
                ):
                    raise ProjectionError("work.revised payload does not match the next record revision")
                self._check_same_identity(old, new, ("projectId", "workId", "createdAt", "createdBy"))
                mutable_fields = (
                    "title", "objective", "workType", "parentWorkId", "owner",
                    "dependsOn", "capabilityGrantIds", "acceptanceCriteria", "budget",
                )
                expected = dict(old)
                for field in mutable_fields:
                    if field in new:
                        expected[field] = new[field]
                    else:
                        expected.pop(field, None)
                expected.update(revision=new["revision"], updatedAt=new["updatedAt"])
                if expected != new:
                    raise ProjectionError("work.revised changed an unsupported or immutable field")
                updated_work_items = dict(state["workItems"])
                updated_work_items[work_id] = new
                self._validate_work_graph(updated_work_items)
                if any(
                    grant_id not in state["capabilityGrants"]
                    or work_id not in state["capabilityGrants"][grant_id]["workIds"]
                    for grant_id in new["capabilityGrantIds"]
                ):
                    raise ProjectionError("work.revised references a missing or out-of-scope capability grant")
                state["workItems"] = updated_work_items

            elif event_type == "work.status_changed":
                work_id = payload["workId"]
                old = state["workItems"].get(work_id)
                if old is None or old["status"] != payload["from"]:
                    raise ProjectionError(f"invalid prior work status for {work_id!r}")
                if payload["to"] not in WORK_STATUS_TRANSITIONS.get(old["status"], set()):
                    raise ProjectionError(f"forbidden work status transition {old['status']!r} -> {payload['to']!r}")
                if payload["to"] == "running":
                    incomplete = [
                        dependency for dependency in old["dependsOn"]
                        if state["workItems"][dependency]["status"] != "completed"
                    ]
                    if incomplete:
                        raise ProjectionError(f"work item {work_id!r} cannot run before dependencies complete: {incomplete}")
                if payload["to"] in {"completed", "failed", "cancelled"} and any(
                    run["workId"] == work_id and run["status"] == "running"
                    for run in state["agentRuns"].values()
                ):
                    raise ProjectionError("work cannot enter a terminal state while agent runs are active")
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
                run_id = payload.get("runId")
                if run_id is not None:
                    run = state["agentRuns"].get(run_id)
                    if (
                        run is None or run["status"] != "running"
                        or run["workId"] != payload["workId"] or run["agent"] != event["actor"]
                    ):
                        raise ProjectionError("message run attribution does not match an active assigned run")
                if any(artifact_id not in state["artifacts"] for artifact_id in payload.get("artifactIds", [])):
                    raise ProjectionError("message references an artifact not yet registered")
                if any(evidence_id not in state["evidence"] for evidence_id in payload.get("evidenceIds", [])):
                    raise ProjectionError("message references evidence not yet registered")
                state["messages"].append({
                    "eventId": event["eventId"],
                    "sequence": event["sequence"],
                    "workId": payload["workId"],
                    "threadId": payload["threadId"],
                    "messageType": payload["messageType"],
                    "actor": event["actor"],
                    "content": content,
                    "contentHash": payload["contentHash"],
                    **({"runId": run_id} if run_id is not None else {}),
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
                if run["agent"] != event["actor"]:
                    raise ProjectionError("run.started actor does not match the assigned agent")
                if (
                    run["adapter"] != payload["adapter"]
                    or run["contextHash"] != payload["contextHash"]
                    or run["capabilityGrantIds"] != payload["capabilityGrantIds"]
                    or run.get("parentRunId") != payload.get("parentRunId")
                ):
                    raise ProjectionError("run.started metadata does not match run record")
                parent_run_id = run.get("parentRunId")
                if parent_run_id is not None:
                    parent = state["agentRuns"].get(parent_run_id)
                    parent_event = run_start_events.get(parent_run_id)
                    if parent is None or parent["status"] != "running":
                        raise ProjectionError("child run requires a previously started active parent")
                    if parent_event is None or parent_event["correlationId"] != event["correlationId"]:
                        raise ProjectionError("parent and child runs must share a correlation ID")
                    root_run_id = self._run_root_id(parent_run_id, state["agentRuns"])
                else:
                    root_run_id = run_id
                for active_id, active_run in state["agentRuns"].items():
                    if active_run["workId"] != run["workId"] or active_run["status"] != "running":
                        continue
                    active_root_id = self._run_root_id(active_id, state["agentRuns"])
                    if active_root_id != root_run_id:
                        raise ProjectionError("work item cannot have concurrent active root-run ownership")
                state["agentRuns"][run_id] = run
                run_start_events[run_id] = event

            elif event_type == "run.finished":
                run_id = payload["runId"]
                old = state["agentRuns"].get(run_id)
                if old is None:
                    raise ProjectionError(f"run {run_id!r} was not started")
                active_children = [
                    child_id for child_id, child in state["agentRuns"].items()
                    if child.get("parentRunId") == run_id and child["status"] == "running"
                ]
                if active_children:
                    raise ProjectionError(f"parent run finished before its child runs: {active_children}")
                new = self._load_record(payload["recordHash"], "agent_run", project_id)
                if new["runId"] != run_id or new["workId"] != old["workId"] or new["status"] != payload["terminalStatus"]:
                    raise ProjectionError("run.finished payload does not match terminal run record")
                if event["actor"] != old["agent"]:
                    raise ProjectionError("run.finished actor does not match the assigned agent")
                expected_event_ids = [
                    message["eventId"] for message in state["messages"]
                    if message.get("runId") == run_id
                ]
                expected_artifact_ids = [
                    artifact_id for artifact_id, artifact in state["artifacts"].items()
                    if artifact.get("runId") == run_id
                ]
                expected_evidence_ids = [
                    evidence_id for evidence_id, evidence in state["evidence"].items()
                    if evidence.get("runId") == run_id
                ]
                if (
                    new.get("outputEventIds", []) != expected_event_ids
                    or payload.get("eventIds", []) != expected_event_ids
                    or new.get("outputArtifactIds", []) != expected_artifact_ids
                    or payload.get("artifactIds", []) != expected_artifact_ids
                    or new.get("outputEvidenceIds", []) != expected_evidence_ids
                    or payload.get("evidenceIds", []) != expected_evidence_ids
                ):
                    raise ProjectionError("run.finished output references do not match registered run outputs")
                self._check_same_identity(old, new, ("projectId", "runId", "workId", "parentRunId", "agent", "adapter", "contextHash", "capabilityGrantIds", "startedAt"))
                state["agentRuns"][run_id] = new

            elif event_type == "human.question_opened":
                question_id = payload["questionId"]
                question = self._load_record(payload["recordHash"], "human_question", project_id)
                if question_id in state["questions"] or question["questionId"] != question_id or question["status"] != "open":
                    raise ProjectionError("question.opened has duplicate id or non-open record")
                if question["questionType"] != payload["questionType"] or question["priority"] != payload["priority"] or question["blocking"] != payload["blocking"] or question["workIds"] != payload["workIds"]:
                    raise ProjectionError("question.opened payload does not match question record")
                if question["requestedBy"] != event["actor"]:
                    raise ProjectionError("question requester does not match event actor")
                if payload["questionRef"] != question_id or any(work_id not in state["workItems"] for work_id in question["workIds"]):
                    raise ProjectionError("question references unknown work or mismatched question id")
                if question["blocking"] and any(state["workItems"][work_id]["status"] != "waiting_human" for work_id in question["workIds"]):
                    raise ProjectionError("blocking question opened without pausing every affected work item")
                if question["blocking"] and any(
                    other.get("blocking") and other.get("status") == "open"
                    and set(other.get("workIds", [])) & set(question["workIds"])
                    for other in state["questions"].values()
                ):
                    raise ProjectionError("blocking questions cannot overlap on the same work item")
                state["questions"][question_id] = question

            elif event_type == "human.question_answered":
                question_id = payload["questionId"]
                old = state["questions"].get(question_id)
                if old is None or old["status"] != "open":
                    raise ProjectionError(f"question {question_id!r} is not open")
                new = self._load_record(payload["recordHash"], "human_question", project_id)
                if new["questionId"] != question_id or new["status"] != "answered" or new.get("answer") is None:
                    raise ProjectionError("question answer event requires an answered question snapshot")
                if event["actor"].get("actorType") != "human" or event["actor"].get("role") != "user" or new["answer"]["answeredBy"] != event["actor"]:
                    raise ProjectionError("question answer must be attributed to the user who authored it")
                self._check_same_identity(old, new, ("projectId", "questionId", "questionType", "priority", "blocking", "requestedBy", "workIds", "questionText", "impact", "options", "createdAt"))
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

            elif event_type == "human.question_closed":
                question_id = payload["questionId"]
                old = state["questions"].get(question_id)
                if old is None or old["status"] != "open" or payload["from"] != "open":
                    raise ProjectionError(f"question {question_id!r} is not open")
                new = self._load_record(payload["recordHash"], "human_question", project_id)
                if new["questionId"] != question_id or new["status"] != payload["to"] or new.get("answer") is not None:
                    raise ProjectionError("question close event does not match its terminal record")
                self._check_same_identity(
                    old, new,
                    ("projectId", "questionId", "questionType", "priority", "blocking", "requestedBy", "workIds", "runId", "questionText", "impact", "options", "createdAt", "dueAt"),
                )
                if new.get("closedBy") != event["actor"] or new.get("closedAt") != event["occurredAt"]:
                    raise ProjectionError("question closure actor/time does not match its event")
                actor_identity = (event["actor"].get("actorType"), event["actor"].get("role"))
                allowed_closers = {
                    "rejected": {("human", "user")},
                    "cancelled": {("human", "user")},
                    "expired": {("system", "system")},
                    "superseded": {("human", "user"), ("agent", "vp")},
                }
                if actor_identity not in allowed_closers[payload["to"]]:
                    raise ProjectionError("question closure actor is not authorized for this terminal state")
                if new.get("resolutionReason") != payload.get("resolutionReason"):
                    raise ProjectionError("question closure reason does not match its immutable record")
                affected = old["workIds"] if old["blocking"] else []
                if payload["affectedWorkIds"] != affected:
                    raise ProjectionError("question closure affected-work set is incorrect")
                if old["blocking"]:
                    expected_status = "waiting_human" if payload["to"] == "superseded" else "blocked"
                    if any(state["workItems"][work_id]["status"] != expected_status for work_id in affected):
                        raise ProjectionError("question closure did not preserve a safe paused/blocked work state")
                if payload["to"] == "superseded":
                    successor_id = payload.get("supersededByQuestionId")
                    if new.get("supersededByQuestionId") != successor_id or not successor_id or successor_id == question_id:
                        raise ProjectionError("superseded question has no valid successor reference")
                state["questions"][question_id] = new

            elif event_type == "capability.granted":
                grant_id = payload["grantId"]
                grant = self._load_record(payload["recordHash"], "capability_grant", project_id)
                if grant_id in state["capabilityGrants"]:
                    raise ProjectionError(f"capability grant {grant_id!r} already exists")
                if (
                    grant["grantId"] != grant_id or grant["status"] != "active"
                    or grant["revision"] != 1 or grant["workIds"] != payload["workIds"]
                    or grant["capability"] != payload["capability"]
                    or grant["grantedBy"] != event["actor"]
                    or event["actor"].get("actorType") != "human"
                    or event["actor"].get("role") != "user"
                    or grant["subject"].get("actorType") != "agent"
                ):
                    raise ProjectionError("capability.granted payload or authority does not match its record")
                if any(work_id not in state["workItems"] for work_id in grant["workIds"]):
                    raise ProjectionError("capability grant references unknown work")
                state["capabilityGrants"][grant_id] = grant

            elif event_type == "capability.revoked":
                grant_id = payload["grantId"]
                old = state["capabilityGrants"].get(grant_id)
                if old is None or old["status"] != "active":
                    raise ProjectionError(f"capability grant {grant_id!r} is not active")
                new = self._load_record(payload["recordHash"], "capability_grant", project_id)
                if (
                    new["grantId"] != grant_id or new["revision"] != old["revision"] + 1
                    or new["status"] != "revoked" or new["revokedBy"] != event["actor"]
                    or event["actor"].get("actorType") != "human" or event["actor"].get("role") != "user"
                    or new["revokedAt"] != event["occurredAt"]
                    or new["revokeReason"] != payload["revokeReason"]
                ):
                    raise ProjectionError("capability.revoked payload does not match its record")
                self._check_same_identity(
                    old, new,
                    ("projectId", "grantId", "subject", "workIds", "capability", "resourceIds", "grantedBy", "createdAt", "expiresAt"),
                )
                state["capabilityGrants"][grant_id] = new

            elif event_type == "artifact.registered":
                artifact_id = payload["artifactId"]
                artifact = self._load_record(payload["recordHash"], "artifact", project_id)
                if artifact_id in state["artifacts"] or artifact["artifactId"] != artifact_id or artifact["workId"] != payload["workId"] or artifact["sha256"] != payload["sha256"]:
                    raise ProjectionError("artifact.registered payload does not match artifact record")
                if artifact["workId"] not in state["workItems"]:
                    raise ProjectionError("artifact references unknown work item")
                if artifact["createdBy"] != event["actor"]:
                    raise ProjectionError("artifact creator does not match registration actor")
                run_id = artifact.get("runId")
                if run_id is not None:
                    run = state["agentRuns"].get(run_id)
                    if (
                        run is None or run["status"] != "running"
                        or run["workId"] != artifact["workId"] or run["agent"] != artifact["createdBy"]
                    ):
                        raise ProjectionError("artifact run provenance does not match an active assigned run")
                input_artifact_ids = artifact.get("inputArtifactIds", [])
                evidence_ids = artifact.get("evidenceIds", [])
                if (
                    payload.get("inputArtifactIds", []) != input_artifact_ids
                    or payload.get("evidenceIds", []) != evidence_ids
                ):
                    raise ProjectionError("artifact lineage payload does not match immutable artifact record")
                allowed_work_ids = self._work_dependency_scope(artifact["workId"], state["workItems"])
                if any(
                    input_id not in state["artifacts"]
                    or state["artifacts"][input_id]["workId"] not in allowed_work_ids
                    for input_id in input_artifact_ids
                ):
                    raise ProjectionError("artifact derivation references missing or unrelated input artifacts")
                if any(
                    evidence_id not in state["evidence"]
                    or state["evidence"][evidence_id]["workId"] not in allowed_work_ids
                    for evidence_id in evidence_ids
                ):
                    raise ProjectionError("artifact derivation references missing or unrelated evidence")
                if not artifact["storageRef"].startswith("blob.") or artifact["storageRef"][5:] != artifact["sha256"]:
                    raise ProjectionError("artifact storage reference does not match its content hash")
                try:
                    artifact_bytes = self.store.get_blob(artifact["sha256"])
                except KeyError as exc:
                    raise ProjectionError("artifact content blob is missing") from exc
                if len(artifact_bytes) != artifact["byteSize"]:
                    raise ProjectionError("artifact byte size does not match its immutable metadata")
                state["artifacts"][artifact_id] = artifact

            elif event_type == "evidence.registered":
                evidence_id = payload["evidenceId"]
                evidence = self._load_record(payload["recordHash"], "evidence", project_id)
                if evidence_id in state["evidence"] or evidence["evidenceId"] != evidence_id or evidence["workId"] != payload["workId"] or evidence["stance"] != payload["stance"]:
                    raise ProjectionError("evidence.registered payload does not match evidence record")
                source_hash = evidence["source"].get("contentHash")
                if source_hash != payload["sourceHash"] or evidence["workId"] not in state["workItems"]:
                    raise ProjectionError("evidence source hash or work reference is inconsistent")
                try:
                    source_content = self.store.get_blob(source_hash)
                except KeyError as exc:
                    raise ProjectionError("evidence source content is missing from canonical storage") from exc
                if hashlib.sha256(source_content).hexdigest() != source_hash:
                    raise ProjectionError("evidence source content does not match its hash")
                if evidence["createdBy"] != event["actor"]:
                    raise ProjectionError("evidence creator does not match registration actor")
                run_id = evidence.get("runId")
                if run_id is not None:
                    run = state["agentRuns"].get(run_id)
                    if (
                        run is None or run["status"] != "running"
                        or run["workId"] != evidence["workId"] or run["agent"] != evidence["createdBy"]
                    ):
                        raise ProjectionError("evidence run provenance does not match an active assigned run")
                state["evidence"][evidence_id] = evidence

            else:
                raise ProjectionError(f"unsupported event type {event_type!r}")

        for question in state["questions"].values():
            if question["status"] != "superseded":
                continue
            successor = state["questions"].get(question.get("supersededByQuestionId"))
            if successor is None:
                raise ProjectionError("superseded question does not reference a successor question")
            if successor["blocking"] != question["blocking"] or successor["workIds"] != question["workIds"]:
                raise ProjectionError("successor question does not preserve the exact work set and blocking semantics")
        for work_id, work in state["workItems"].items():
            for grant_id in work["capabilityGrantIds"]:
                grant = state["capabilityGrants"].get(grant_id)
                if grant is None or work_id not in grant["workIds"]:
                    raise ProjectionError(f"work item {work_id!r} references a missing or out-of-scope capability grant")
        for grant_id, grant in state["capabilityGrants"].items():
            for work_id in grant["workIds"]:
                if grant_id not in state["workItems"][work_id]["capabilityGrantIds"]:
                    raise ProjectionError(f"capability grant {grant_id!r} is missing its work-item reference")
        state["_eventSequence"] = events[-1]["sequence"] if events else 0
        return state, events
