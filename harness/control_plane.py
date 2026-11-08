"""Minimal project/work/inbox commands over the canonical event store."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from datetime import datetime, timezone
from typing import Any, Callable

from .event_store import EventStoreError, SQLiteEventStore
from .inbox import InboxReadModel
from .projection import Projector, ProjectionError, WORK_STATUS_TRANSITIONS


class ControlPlaneError(EventStoreError):
    """A project/work/question command violates the local control contract."""


class StaleQuestionRevision(ControlPlaneError):
    """The inbox question or affected work changed since the client read it."""


_ALLOWED_WORK_TRANSITIONS = WORK_STATUS_TRANSITIONS


class ProjectControlPlane:
    """Authoritative local commands; candidate runtimes never own project state."""

    def __init__(self, store: SQLiteEventStore, *, clock: Callable[[], str] | None = None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.projector = Projector(store)

    @staticmethod
    def _check_question_etag(
        question: dict[str, Any],
        state: dict[str, Any],
        expected_etag: str | None,
    ) -> None:
        if expected_etag is None:
            return
        actual = InboxReadModel.etag_for(question, state["workItems"])
        if actual != expected_etag:
            raise StaleQuestionRevision("question or affected work changed; refresh the inbox item")

    def _event(
        self,
        project_id: str,
        event_type: str,
        actor: dict[str, str],
        payload: dict[str, Any],
        *,
        event_id: str,
        occurred_at: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        if len(event_id) > 120:
            event_id = f"event.{sha256(event_id.encode('utf-8')).hexdigest()}"
        return {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": event_id,
            "projectId": project_id,
            "eventType": event_type,
            "actor": actor,
            "occurredAt": occurred_at or self.clock(),
            "causationId": causation_id,
            "correlationId": project_id,
            "idempotencyKey": f"idem.{event_id}",
            "payload": payload,
        }

    def create_project(self, project: dict[str, Any]) -> dict[str, Any]:
        self.store.validate_record(project)
        if project.get("recordType") != "project":
            raise ControlPlaneError("create_project requires a project record")
        project_id = project["projectId"]
        existing_events = self.store.events(project_id)
        if existing_events:
            current = self.projector.replay(project_id)["project"]
            if current == project:
                return current
            raise ControlPlaneError(f"project {project_id!r} already has canonical history")
        actor = project.get("createdBy")
        if actor is None:
            raise ControlPlaneError("project.createdBy is required for a canonical project")

        record_hash = self.store.put_record(project)
        charter_json = self.store._canonical_json(project["charter"])
        _, charter_hash = self.store.put_blob(charter_json)
        event = self._event(
            project_id,
            "project.created",
            actor,
            {"name": project["name"], "charterHash": charter_hash, "recordHash": record_hash},
            event_id=f"event.project.created.{project_id}.r{project['revision']}",
            occurred_at=project["createdAt"],
        )
        self.store.append(event, expected_sequence=0)
        return self.projector.replay(project_id)["project"]

    def create_work(self, work: dict[str, Any], *, actor: dict[str, str] | None = None) -> dict[str, Any]:
        self.store.validate_record(work)
        if work.get("recordType") != "work_item":
            raise ControlPlaneError("create_work requires a work_item record")
        project_id = work["projectId"]
        state = self.projector.replay(project_id)
        if state["project"] is None:
            raise ControlPlaneError(f"unknown project {project_id!r}")
        work_id = work["workId"]
        if work_id in state["workItems"]:
            if state["workItems"][work_id] == work:
                return work
            raise ControlPlaneError(f"work item {work_id!r} already exists")
        try:
            Projector._validate_work_graph({**state["workItems"], work_id: work})
        except ProjectionError as exc:
            raise ControlPlaneError(str(exc)) from exc
        if any(
            grant_id not in state["capabilityGrants"]
            or work_id not in state["capabilityGrants"][grant_id]["workIds"]
            for grant_id in work["capabilityGrantIds"]
        ):
            raise ControlPlaneError("work item references a missing or out-of-scope capability grant")
        creator = actor or work.get("createdBy") or work["owner"]
        record_hash = self.store.put_record(work)
        payload = {
            "workId": work_id,
            "title": work["title"],
            "objective": work["objective"],
            "owner": work["owner"],
            "recordHash": record_hash,
        }
        if "parentWorkId" in work:
            payload["parentWorkId"] = work["parentWorkId"]
        event = self._event(
            project_id,
            "work.created",
            creator,
            payload,
            event_id=f"event.work.created.{work_id}.r{work['revision']}",
            occurred_at=work["createdAt"],
        )
        self.store.append(event, expected_sequence=state["_eventSequence"])
        return self.projector.replay(project_id)["workItems"][work_id]

    def revise_project_charter(
        self,
        project_id: str,
        charter: dict[str, Any],
        *,
        actor: dict[str, str],
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        state = self.projector.replay(project_id)
        old = state["project"]
        if old is None:
            raise ControlPlaneError(f"unknown project {project_id!r}")
        when = occurred_at or self.clock()
        updated = deepcopy(old)
        updated["revision"] += 1
        updated["charter"] = deepcopy(charter)
        updated["charter"]["revision"] = old["charter"]["revision"] + 1
        updated["updatedAt"] = when
        self.store.validate_record(updated)
        record_hash = self.store.put_record(updated)
        charter_json = self.store._canonical_json(updated["charter"])
        _, charter_hash = self.store.put_blob(charter_json)
        event = self._event(
            project_id,
            "project.charter_revised",
            actor,
            {"charterRevision": updated["charter"]["revision"], "charterHash": charter_hash, "recordHash": record_hash},
            event_id=f"event.project.charter.{project_id}.r{updated['charter']['revision']}",
            occurred_at=when,
        )
        self.store.append(event, expected_sequence=state["_eventSequence"])
        return self.projector.replay(project_id)["project"]

    def revise_work(
        self,
        project_id: str,
        work_id: str,
        changes: dict[str, Any],
        *,
        actor: dict[str, str],
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "title", "objective", "workType", "parentWorkId", "owner",
            "dependsOn", "capabilityGrantIds", "acceptanceCriteria", "budget",
        }
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ControlPlaneError("work revision has no changes or contains unsupported fields")
        state = self.projector.replay(project_id)
        old = state["workItems"].get(work_id)
        if old is None:
            raise ControlPlaneError(f"unknown work item {work_id!r}")
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
            raise ControlPlaneError("cannot revise work scope in an open blocking question dependency closure")
        updated = deepcopy(old)
        updated.update(deepcopy(changes))
        if updated == old:
            return old
        when = occurred_at or self.clock()
        updated["revision"] += 1
        updated["updatedAt"] = when
        self.store.validate_record(updated)
        if any(
            grant_id not in state["capabilityGrants"]
            or work_id not in state["capabilityGrants"][grant_id]["workIds"]
            for grant_id in updated["capabilityGrantIds"]
        ):
            raise ControlPlaneError("work item references a missing or out-of-scope capability grant")
        try:
            Projector._validate_work_graph({**state["workItems"], work_id: updated})
        except ProjectionError as exc:
            raise ControlPlaneError(str(exc)) from exc
        record_hash = self.store.put_record(updated)
        event = self._event(
            project_id,
            "work.revised",
            actor,
            {"workId": work_id, "fromRevision": old["revision"], "toRevision": updated["revision"], "recordHash": record_hash},
            event_id=f"event.work.revised.{work_id}.r{updated['revision']}",
            occurred_at=when,
        )
        self.store.append(event, expected_sequence=state["_eventSequence"])
        return self.projector.replay(project_id)["workItems"][work_id]

    def transition_work(
        self,
        project_id: str,
        work_id: str,
        new_status: str,
        *,
        actor: dict[str, str],
        reason: str = "",
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        state = self.projector.replay(project_id)
        old = state["workItems"].get(work_id)
        if old is None:
            raise ControlPlaneError(f"unknown work item {work_id!r}")
        if old["status"] == new_status:
            return old
        if new_status not in _ALLOWED_WORK_TRANSITIONS.get(old["status"], set()):
            raise ControlPlaneError(f"work transition {old['status']!r} -> {new_status!r} is not allowed")
        if new_status == "running":
            incomplete = [
                dependency for dependency in old["dependsOn"]
                if state["workItems"][dependency]["status"] != "completed"
            ]
            if incomplete:
                raise ControlPlaneError(f"work cannot start before dependencies complete: {incomplete}")
        if new_status in {"completed", "failed", "cancelled"} and any(
            run["workId"] == work_id and run["status"] == "running"
            for run in state["agentRuns"].values()
        ):
            raise ControlPlaneError("work cannot enter a terminal state while agent runs are active")
        when = occurred_at or self.clock()
        updated = deepcopy(old)
        updated["revision"] += 1
        updated["status"] = new_status
        updated["updatedAt"] = when
        record_hash = self.store.put_record(updated)
        event = self._event(
            project_id,
            "work.status_changed",
            actor,
            {"workId": work_id, "from": old["status"], "to": new_status, "recordHash": record_hash, "reason": reason},
            event_id=f"event.work.status.{work_id}.r{updated['revision']}",
            occurred_at=when,
        )
        self.store.append(event, expected_sequence=state["_eventSequence"])
        return self.projector.replay(project_id)["workItems"][work_id]

    def open_question(self, question: dict[str, Any]) -> dict[str, Any]:
        self.store.validate_record(question)
        if question.get("recordType") != "human_question" or question.get("status") != "open":
            raise ControlPlaneError("open_question requires a human_question in open state")
        project_id = question["projectId"]
        state = self.projector.replay(project_id)
        question_id = question["questionId"]
        existing = state["questions"].get(question_id)
        if existing is not None:
            if existing == question:
                return existing
            raise ControlPlaneError(f"question {question_id!r} already exists")
        work_ids = question["workIds"]
        if not work_ids or any(work_id not in state["workItems"] for work_id in work_ids):
            raise ControlPlaneError("question must reference existing affected work items")
        if question["blocking"]:
            if any(
                existing.get("blocking") and existing.get("status") == "open"
                and set(existing.get("workIds", [])) & set(work_ids)
                for existing in state["questions"].values()
            ):
                raise ControlPlaneError("an open blocking question already covers affected work")
            for work_id in work_ids:
                status = state["workItems"][work_id]["status"]
                if status not in {"ready", "running", "blocked", "waiting_human"}:
                    raise ControlPlaneError(f"cannot block work {work_id!r} from status {status!r}")

        events = []
        if question["blocking"]:
            for work_id in work_ids:
                work = state["workItems"][work_id]
                if work["status"] == "waiting_human":
                    continue
                updated = deepcopy(work)
                updated["revision"] += 1
                updated["status"] = "waiting_human"
                updated["updatedAt"] = question["createdAt"]
                record_hash = self.store.put_record(updated)
                events.append(self._event(
                    project_id,
                    "work.status_changed",
                    question["requestedBy"],
                    {"workId": work_id, "from": work["status"], "to": "waiting_human", "recordHash": record_hash, "reason": f"blocked by {question_id}"},
                    event_id=f"event.work.status.{work_id}.r{updated['revision']}",
                    occurred_at=question["createdAt"],
                ))

        question_hash = self.store.put_record(question)
        events.append(self._event(
            project_id,
            "human.question_opened",
            question["requestedBy"],
            {
                "questionId": question_id,
                "questionType": question["questionType"],
                "priority": question["priority"],
                "blocking": question["blocking"],
                "workIds": work_ids,
                "questionRef": question_id,
                "recordHash": question_hash,
            },
            event_id=f"event.question.opened.{question_id}",
            occurred_at=question["createdAt"],
        ))
        self.store.append_many(events, expected_sequence=state["_eventSequence"])
        return self.projector.replay(project_id)["questions"][question_id]

    def close_question(
        self,
        project_id: str,
        question_id: str,
        new_status: str,
        *,
        actor: dict[str, str],
        reason: str,
        successor_question: dict[str, Any] | None = None,
        expected_etag: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if new_status not in {"rejected", "cancelled", "expired", "superseded"}:
            raise ControlPlaneError("question closure status is not a supported terminal state")
        if not isinstance(reason, str) or not reason.strip():
            raise ControlPlaneError("question closure requires an explicit reason")
        identity = (actor.get("actorType"), actor.get("role"))
        if new_status in {"rejected", "cancelled"} and identity != ("human", "user"):
            raise ControlPlaneError("only the user may reject or cancel an inbox question")
        if new_status == "expired" and identity != ("system", "system"):
            raise ControlPlaneError("only the system may expire an inbox question")
        if new_status == "superseded" and identity not in {("human", "user"), ("agent", "vp")}:
            raise ControlPlaneError("only the user or project VP may supersede a question")
        if (new_status == "superseded") != (successor_question is not None):
            raise ControlPlaneError("superseding requires exactly one successor question")

        when = occurred_at or self.clock()
        state = self.projector.replay(project_id)
        question = state["questions"].get(question_id)
        if question is None:
            raise ControlPlaneError(f"unknown question {question_id!r}")
        successor_id = successor_question.get("questionId") if successor_question else None
        if question["status"] != "open":
            if (
                question["status"] == new_status
                and question.get("closedBy") == actor
                and question.get("resolutionReason") == reason.strip()
                and question.get("supersededByQuestionId") == successor_id
            ):
                if successor_question is not None and state["questions"].get(successor_id) != successor_question:
                    raise ControlPlaneError("question retry changed its successor record")
                return question
            raise ControlPlaneError(f"question {question_id!r} is not open")
        self._check_question_etag(question, state, expected_etag)

        if new_status == "expired":
            due_at = question.get("dueAt")
            if due_at is None:
                raise ControlPlaneError("question has no expiry deadline")
            try:
                deadline = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                now = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except (AttributeError, ValueError) as exc:
                raise ControlPlaneError("question expiry timestamp is invalid") from exc
            if now < deadline:
                raise ControlPlaneError("question expiry deadline has not passed")

        affected_work_ids = list(question["workIds"]) if question["blocking"] else []
        if question["blocking"] and any(
            state["workItems"][work_id]["status"] != "waiting_human"
            for work_id in affected_work_ids
        ):
            raise ControlPlaneError("affected work is no longer paused by this question")

        successor_record_hash = None
        if successor_question is not None:
            self.store.validate_record(successor_question)
            if (
                successor_question.get("recordType") != "human_question"
                or successor_question.get("status") != "open"
                or successor_question.get("projectId") != project_id
                or successor_question.get("questionId") == question_id
                or successor_question.get("workIds") != question["workIds"]
                or successor_question.get("blocking") != question["blocking"]
                or successor_question.get("createdAt") != when
                or successor_question.get("requestedBy") != actor
                or successor_question.get("questionId") in state["questions"]
            ):
                raise ControlPlaneError("successor must be a new question for the same exact affected work set")
            successor_record_hash = self.store.put_record(successor_question)

        closed = deepcopy(question)
        closed["status"] = new_status
        closed["closedBy"] = actor
        closed["closedAt"] = when
        closed["resolutionReason"] = reason.strip()
        if successor_id:
            closed["supersededByQuestionId"] = successor_id
        record_hash = self.store.put_record(closed)
        events = []
        if question["blocking"] and new_status != "superseded":
            for work_id in affected_work_ids:
                old_work = state["workItems"][work_id]
                updated_work = deepcopy(old_work)
                updated_work["revision"] += 1
                updated_work["status"] = "blocked"
                updated_work["updatedAt"] = when
                work_hash = self.store.put_record(updated_work)
                events.append(self._event(
                    project_id,
                    "work.status_changed",
                    actor,
                    {
                        "workId": work_id, "from": "waiting_human", "to": "blocked",
                        "recordHash": work_hash, "reason": f"{new_status} question {question_id}: {reason.strip()}",
                    },
                    event_id=f"event.work.status.{work_id}.r{updated_work['revision']}",
                    occurred_at=when,
                ))
        events.append(self._event(
            project_id,
            "human.question_closed",
            actor,
            {
                "questionId": question_id,
                "from": "open",
                "to": new_status,
                "affectedWorkIds": affected_work_ids,
                "resolutionReason": reason.strip(),
                **({"supersededByQuestionId": successor_id} if successor_id else {}),
                "recordHash": record_hash,
            },
            event_id=f"event.question.closed.{question_id}.{new_status}",
            occurred_at=when,
        ))
        if successor_question is not None:
            events.append(self._event(
                project_id,
                "human.question_opened",
                actor,
                {
                    "questionId": successor_question["questionId"],
                    "questionType": successor_question["questionType"],
                    "priority": successor_question["priority"],
                    "blocking": successor_question["blocking"],
                    "workIds": successor_question["workIds"],
                    "questionRef": successor_question["questionId"],
                    "recordHash": successor_record_hash,
                },
                event_id=f"event.question.opened.{successor_question['questionId']}",
                occurred_at=when,
            ))
        self.store.append_many(events, expected_sequence=state["_eventSequence"])
        return self.projector.replay(project_id)["questions"][question_id]

    def answer_question(
        self,
        project_id: str,
        question_id: str,
        answer_text: str,
        *,
        actor: dict[str, str],
        selected_option_id: str | None = None,
        expected_etag: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if actor.get("actorType") != "human" or actor.get("role") != "user":
            raise ControlPlaneError("only the user may answer a human inbox question")
        if not isinstance(answer_text, str) or not answer_text.strip():
            raise ControlPlaneError("answer text must be nonempty")
        when = occurred_at or self.clock()
        state = self.projector.replay(project_id)
        question = state["questions"].get(question_id)
        if question is None:
            raise ControlPlaneError(f"unknown question {question_id!r}")
        if question["status"] == "answered":
            answer = question["answer"]
            if answer["text"] == answer_text and answer["answeredBy"] == actor and answer.get("selectedOptionId") == selected_option_id:
                return question
            raise ControlPlaneError(f"question {question_id!r} already has a different answer")
        self._check_question_etag(question, state, expected_etag)
        if question["status"] != "open":
            raise ControlPlaneError(f"question {question_id!r} is not open")
        if selected_option_id is not None and selected_option_id not in {option["optionId"] for option in question["options"]}:
            raise ControlPlaneError("selected option does not belong to this question")

        resumed_work_ids = list(question["workIds"]) if question["blocking"] else []
        if question["blocking"]:
            for work_id in resumed_work_ids:
                work = state["workItems"][work_id]
                if work["status"] != "waiting_human":
                    raise ControlPlaneError(f"blocking question work {work_id!r} is not paused")

        answered = deepcopy(question)
        answered["status"] = "answered"
        answered["answer"] = {
            "text": answer_text,
            "answeredBy": actor,
            "answeredAt": when,
            "resumedWorkIds": resumed_work_ids,
            **({"selectedOptionId": selected_option_id} if selected_option_id else {}),
        }
        record_hash = self.store.put_record(answered)
        answer_ref_hash = sha256(f"{question_id}\0{when}".encode("utf-8")).hexdigest()
        events = [self._event(
            project_id,
            "human.question_answered",
            actor,
            {
                "questionId": question_id,
                "answerRef": f"answer.{answer_ref_hash}",
                "resumedWorkIds": resumed_work_ids,
                "recordHash": record_hash,
            },
            event_id=f"event.question.answered.{question_id}",
            occurred_at=when,
        )]
        for work_id in resumed_work_ids:
            work = state["workItems"][work_id]
            updated = deepcopy(work)
            updated["revision"] += 1
            updated["status"] = "ready"
            updated["updatedAt"] = when
            work_hash = self.store.put_record(updated)
            events.append(self._event(
                project_id,
                "work.status_changed",
                actor,
                {"workId": work_id, "from": "waiting_human", "to": "ready", "recordHash": work_hash, "reason": f"resumed after {question_id}"},
                event_id=f"event.work.status.{work_id}.r{updated['revision']}",
                occurred_at=when,
            ))
        self.store.append_many(events, expected_sequence=state["_eventSequence"])
        return self.projector.replay(project_id)["questions"][question_id]
