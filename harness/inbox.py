"""Project-scoped inbox read model for the local prototype.

This is not an HTTP API and does not authenticate or authorize a caller.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Iterable, Protocol

if TYPE_CHECKING:
    from .control_plane import ProjectControlPlane

from .event_store import SQLiteEventStore
from .projection import Projector


QUESTION_STATUSES = {"open", "answered", "rejected", "cancelled", "expired", "superseded"}
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


class InboxNotFound(LookupError):
    """Question is not present in the requested project's canonical state."""


class InboxForbidden(PermissionError):
    """Caller lacks the authenticated user principal or project access."""


class PrincipalProvider(Protocol):
    """Trusted application adapter that returns the authenticated actor."""

    def current_actor(self) -> dict[str, str]: ...


class ProjectAccessPolicy(Protocol):
    """Application adapter that enforces project membership/permissions."""

    def require_project_access(self, actor: dict[str, str], project_id: str, action: str) -> None: ...


class InboxReadModel:
    """Build minimal question summaries from canonical project replay."""

    def __init__(self, store: SQLiteEventStore):
        self.store = store
        self.projector = Projector(store)

    def list_questions(
        self,
        project_id: str,
        *,
        statuses: Iterable[str] = ("open",),
        priority: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_statuses = set(statuses)
        if not selected_statuses or selected_statuses - QUESTION_STATUSES:
            raise ValueError("statuses must be a nonempty subset of known question states")
        if priority is not None and priority not in PRIORITY_ORDER:
            raise ValueError("priority must be high, normal, or low")
        state = self.projector.replay(project_id)
        questions = [
            question for question in state["questions"].values()
            if question["status"] in selected_statuses
            and (priority is None or question["priority"] == priority)
        ]
        questions.sort(key=lambda question: (
            PRIORITY_ORDER[question["priority"]],
            question.get("dueAt") or "9999-12-31T23:59:59Z",
            question["createdAt"],
            question["questionId"],
        ))
        return [self._summary(question, state["workItems"]) for question in questions]

    def get_question(self, project_id: str, question_id: str) -> dict[str, Any]:
        state, events = self.projector.replay_snapshot(project_id)
        question = state["questions"].get(question_id)
        if question is None:
            raise InboxNotFound(f"question {question_id!r} is not in project {project_id!r}")
        summary = self._summary(question, state["workItems"])
        history = []
        for event in events:
            payload = event["payload"]
            if payload.get("questionId") == question_id or payload.get("supersededByQuestionId") == question_id:
                history.append({
                    "eventId": event["eventId"],
                    "eventType": event["eventType"],
                    "actor": event["actor"],
                    "occurredAt": event["occurredAt"],
                })
        return {**summary, "record": question, "history": history}

    @staticmethod
    def etag_for(question: dict[str, Any], work_items: dict[str, dict[str, Any]]) -> str:
        affected = [
            {
                "workId": work_id,
                "title": work_items[work_id]["title"],
                "status": work_items[work_id]["status"],
                "revision": work_items[work_id]["revision"],
            }
            for work_id in question["workIds"]
        ]
        version_material = {"question": question, "affectedWork": affected}
        return hashlib.sha256(
            SQLiteEventStore._canonical_json(version_material).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _summary(question: dict[str, Any], work_items: dict[str, dict[str, Any]]) -> dict[str, Any]:
        affected = [
            {
                "workId": work_id,
                "title": work_items[work_id]["title"],
                "status": work_items[work_id]["status"],
                "revision": work_items[work_id]["revision"],
            }
            for work_id in question["workIds"]
        ]
        etag = InboxReadModel.etag_for(question, work_items)
        return {
            "questionId": question["questionId"],
            "questionType": question["questionType"],
            "priority": question["priority"],
            "status": question["status"],
            "blocking": question["blocking"],
            "questionText": question["questionText"],
            "impact": question["impact"],
            "options": question["options"],
            "dueAt": question.get("dueAt"),
            "requestedBy": question["requestedBy"],
            "runId": question["runId"],
            "workIds": question["workIds"],
            "affectedWork": affected,
            "createdAt": question["createdAt"],
            "closedAt": question.get("closedAt"),
            "resolutionReason": question.get("resolutionReason"),
            "supersededByQuestionId": question.get("supersededByQuestionId"),
            "etag": etag,
        }


class InboxApplication:
    """Framework-neutral inbox application boundary.

    The HTTP/UI adapter must inject a genuinely authenticated principal
    provider and project-access policy. Fake implementations are suitable for
    offline tests only; this class does not authenticate by itself.
    """

    def __init__(
        self,
        read_model: InboxReadModel,
        control_plane: ProjectControlPlane,
        principal_provider: PrincipalProvider,
        access_policy: ProjectAccessPolicy,
    ):
        self.read_model = read_model
        self.control_plane = control_plane
        self.principal_provider = principal_provider
        self.access_policy = access_policy

    def _user(self, project_id: str, action: str) -> dict[str, str]:
        actor = self.principal_provider.current_actor()
        if actor.get("actorType") != "human" or actor.get("role") != "user":
            raise InboxForbidden("an authenticated human user is required")
        self.access_policy.require_project_access(actor, project_id, action)
        return actor

    @staticmethod
    def _require_etag(expected_etag: str) -> None:
        if not isinstance(expected_etag, str) or not expected_etag:
            raise ValueError("an inbox ETag precondition is required")

    def list_questions(
        self,
        project_id: str,
        *,
        statuses: Iterable[str] = ("open",),
        priority: str | None = None,
    ) -> list[dict[str, Any]]:
        self._user(project_id, "inbox.read")
        return self.read_model.list_questions(project_id, statuses=statuses, priority=priority)

    def get_question(self, project_id: str, question_id: str) -> dict[str, Any]:
        self._user(project_id, "inbox.read")
        return self.read_model.get_question(project_id, question_id)

    def answer_question(
        self,
        project_id: str,
        question_id: str,
        *,
        expected_etag: str,
        text: str,
        selected_option_id: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        self._require_etag(expected_etag)
        actor = self._user(project_id, "inbox.answer")
        return self.control_plane.answer_question(
            project_id,
            question_id,
            text,
            actor=actor,
            selected_option_id=selected_option_id,
            expected_etag=expected_etag,
            occurred_at=occurred_at,
        )

    def close_question(
        self,
        project_id: str,
        question_id: str,
        *,
        new_status: str,
        expected_etag: str,
        reason: str,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if new_status not in {"rejected", "cancelled"}:
            raise ValueError("user inbox closure supports reject or cancel only")
        self._require_etag(expected_etag)
        actor = self._user(project_id, "inbox.close")
        return self.control_plane.close_question(
            project_id,
            question_id,
            new_status,
            actor=actor,
            reason=reason,
            expected_etag=expected_etag,
            occurred_at=occurred_at,
        )
