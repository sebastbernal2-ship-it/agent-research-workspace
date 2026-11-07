"""Minimal project/work/inbox commands over the canonical event store."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from .event_store import EventStoreError, SQLiteEventStore
from .projection import Projector


class ControlPlaneError(EventStoreError):
    """A project/work/question command violates the local control contract."""


_ALLOWED_WORK_TRANSITIONS = {
    "proposed": {"ready", "cancelled"},
    "ready": {"running", "blocked", "waiting_human", "cancelled"},
    "running": {"blocked", "waiting_human", "verifying", "completed", "failed", "cancelled"},
    "blocked": {"ready", "failed", "cancelled"},
    "waiting_human": {"ready", "cancelled"},
    "verifying": {"running", "completed", "failed", "blocked", "cancelled"},
    "failed": {"ready", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


class ProjectControlPlane:
    """Authoritative local commands; candidate runtimes never own project state."""

    def __init__(self, store: SQLiteEventStore, *, clock: Callable[[], str] | None = None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.projector = Projector(store)

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
        return {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": event_id,
            "projectId": project_id,
            "eventType": event_type,
            "actor": actor,
            "occurredAt": occurred_at or self.clock(),
            "causationId": causation_id,
            "correlationId": f"project.{project_id}",
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
        self.store.append(event)
        return self.projector.replay(project_id)["project"]

    def create_work(self, work: dict[str, Any], *, actor: dict[str, str] | None = None) -> dict[str, Any]:
        self.store.validate_record(work)
        if work.get("recordType") != "work_item":
            raise ControlPlaneError("create_work requires a work_item record")
        project_id = work["projectId"]
        state = self.projector.replay(project_id)
        work_id = work["workId"]
        if work_id in state["workItems"]:
            if state["workItems"][work_id] == work:
                return work
            raise ControlPlaneError(f"work item {work_id!r} already exists")
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
        self.store.append(event)
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
        self.store.append(event)
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
            for work_id in work_ids:
                status = state["workItems"][work_id]["status"]
                if status not in {"ready", "running", "blocked"}:
                    raise ControlPlaneError(f"cannot block work {work_id!r} from status {status!r}")

        events = []
        if question["blocking"]:
            for work_id in work_ids:
                work = state["workItems"][work_id]
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
                "blocking": question["blocking"],
                "workIds": work_ids,
                "questionRef": question_id,
                "recordHash": question_hash,
            },
            event_id=f"event.question.opened.{question_id}",
            occurred_at=question["createdAt"],
        ))
        self.store.append_many(events)
        return self.projector.replay(project_id)["questions"][question_id]

    def answer_question(
        self,
        project_id: str,
        question_id: str,
        answer_text: str,
        *,
        actor: dict[str, str],
        selected_option_id: str | None = None,
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
        events = [self._event(
            project_id,
            "human.question_answered",
            actor,
            {
                "questionId": question_id,
                "answerRef": f"answer.{question_id}.{answered['answer']['answeredAt'].replace(':', '-')}",
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
        self.store.append_many(events)
        return self.projector.replay(project_id)["questions"][question_id]
