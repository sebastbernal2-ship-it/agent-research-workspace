"""Local-only synthetic runtime for the inbox interaction prototype."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from harness.control_plane import ProjectControlPlane
from harness.event_store import SQLiteEventStore
from harness.inbox import (
    InboxApplication,
    InboxForbidden,
    InboxNotFound,
    InboxReadModel,
    ProjectAccessPolicy,
    PrincipalProvider,
)
from harness.projection import Projector
from harness.project_application import ProjectApplication


PROJECT_ID = "project.northstar"
PROJECT_NAME = "Northstar research"
CREATED_AT = "2026-11-08T09:00:00Z"
DEMO_USER = {"actorType": "human", "actorId": "user.local-demo", "role": "user"}
DEMO_VP = {"actorType": "agent", "actorId": "agent.project-vp", "role": "vp"}
DEMO_RESEARCHER = {"actorType": "agent", "actorId": "agent.research-specialist", "role": "specialist"}


class LocalDemoPrincipal(PrincipalProvider):
    """Fixed synthetic identity. This is explicitly not authentication."""

    def current_actor(self) -> dict[str, str]:
        return dict(DEMO_USER)


class SingleProjectDemoAccess(ProjectAccessPolicy):
    """Demo-only allowlist. It is not suitable as a production access policy."""

    def require_project_access(self, actor: dict[str, str], project_id: str, action: str) -> None:
        if actor != DEMO_USER or project_id != PROJECT_ID:
            raise InboxForbidden("project access denied")
        if action not in {
            "project.read", "inbox.read", "inbox.answer", "inbox.close",
            "run.read", "evidence.read", "artifact.read",
        }:
            raise InboxForbidden("action is not available in the local demo")


def _validated_etag(request: dict[str, Any]) -> str:
    etag = request.get("expectedEtag")
    if not isinstance(etag, str) or re.fullmatch(r"[a-f0-9]{64}", etag) is None:
        raise ValueError("expectedEtag must be a SHA-256 revision token")
    return etag


class PrototypeRuntime:
    """Own one canonical store and the demo-only project/inbox facade."""

    def __init__(self, db_path: str | Path):
        self.store = SQLiteEventStore(db_path)
        self.control = ProjectControlPlane(self.store)
        self._seed_if_empty()
        self.projector = Projector(self.store)
        self.inbox = InboxReadModel(self.store)
        principal = LocalDemoPrincipal()
        access_policy = SingleProjectDemoAccess()
        self.application = InboxApplication(
            self.inbox, self.control, principal, access_policy,
        )
        self.project_application = ProjectApplication(
            self.control, principal, access_policy,
        )

    def _seed_if_empty(self) -> None:
        existing = self.store.verified_events(PROJECT_ID)
        if existing:
            project = self.projector_project()
            if project["name"] != PROJECT_NAME or project.get("createdBy") != DEMO_USER:
                raise RuntimeError("refusing to serve a database that is not the synthetic demo project")
            return

        self.control.create_project({
            "recordType": "project",
            "schemaVersion": "0.1",
            "projectId": PROJECT_ID,
            "revision": 1,
            "name": PROJECT_NAME,
            "status": "active",
            "charter": {
                "revision": 1,
                "goals": [
                    "Coordinate research and other project work across agent roles.",
                    "Keep project decisions, evidence, and permissions transparent.",
                ],
                "nonGoals": [
                    "Use model refusal behavior as the security boundary.",
                    "Mix another project's private context into this project.",
                ],
                "acceptanceCriteria": [
                    "Persist human decisions in the canonical project history.",
                    "Resume only the exact work affected by an answer.",
                ],
            },
            "createdBy": DEMO_USER,
            "createdAt": CREATED_AT,
            "updatedAt": CREATED_AT,
        })

        work_items = [
            ("work.map-foundations", "Map candidate system foundations", "Compare coordination and project-control candidates.", []),
            ("work.prepare-evidence", "Prepare comparison evidence", "Collect primary sources and identify unresolved claims.", ["work.map-foundations"]),
            ("work.ui-foundation", "Compare project-first UI foundations", "Evaluate UI candidates against the project and inbox workflows.", []),
            ("work.model-routing", "Define model-routing policy", "Separate role-based model selection from tool permissions.", []),
        ]
        for work_id, title, objective, dependencies in work_items:
            self.control.create_work({
                "recordType": "work_item",
                "schemaVersion": "0.1",
                "projectId": PROJECT_ID,
                "workId": work_id,
                "revision": 1,
                "parentWorkId": None,
                "title": title,
                "objective": objective,
                "workType": "research" if "ui" not in work_id and "model" not in work_id else "planning",
                "status": "ready",
                "owner": DEMO_RESEARCHER,
                "dependsOn": dependencies,
                "capabilityGrantIds": [],
                "acceptanceCriteria": ["Record the result and its evidence in the project."],
                "createdBy": DEMO_USER,
                "createdAt": CREATED_AT,
                "updatedAt": CREATED_AT,
            })

        self.control.open_question({
            "recordType": "human_question",
            "schemaVersion": "0.1",
            "projectId": PROJECT_ID,
            "questionId": "question.source-policy",
            "questionType": "decision",
            "priority": "high",
            "blocking": True,
            "requestedBy": DEMO_VP,
            "workIds": ["work.map-foundations", "work.prepare-evidence"],
            "runId": None,
            "questionText": "Which source policy should research use?",
            "impact": "This choice changes what evidence the research team can cite. Two downstream work items are paused until you decide.",
            "options": [
                {
                    "optionId": "option.peer-reviewed",
                    "label": "Prioritize peer-reviewed and official sources",
                    "consequence": "Higher confidence; narrower coverage and slower research.",
                },
                {
                    "optionId": "option.technical-reporting",
                    "label": "Include reputable technical reporting",
                    "consequence": "Broader coverage; claims must be labeled and cross-checked.",
                },
            ],
            "status": "open",
            "createdAt": CREATED_AT,
            "dueAt": "2026-11-09T17:00:00Z",
        })
        self.control.open_question({
            "recordType": "human_question",
            "schemaVersion": "0.1",
            "projectId": PROJECT_ID,
            "questionId": "question.ui-global-view",
            "questionType": "tradeoff",
            "priority": "normal",
            "blocking": False,
            "requestedBy": DEMO_VP,
            "workIds": ["work.ui-foundation"],
            "runId": None,
            "questionText": "Should the UI include a global workspace view?",
            "impact": "This affects navigation and information density. No work is paused; the UI evaluation can continue either way.",
            "options": [
                {
                    "optionId": "option.project-first",
                    "label": "Keep the experience project-first",
                    "consequence": "Simpler privacy boundaries; users switch projects to see their work.",
                },
                {
                    "optionId": "option.global-view",
                    "label": "Add a cross-project overview",
                    "consequence": "More convenient; requires careful project access filtering.",
                },
            ],
            "status": "open",
            "createdAt": CREATED_AT,
            "dueAt": "2026-11-11T17:00:00Z",
        })

    def projector_project(self) -> dict[str, Any]:
        return self.control.projector.replay(PROJECT_ID)["project"]

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "local-synthetic-demo",
            "projectId": PROJECT_ID,
            "modelProvidersConnected": False,
        }

    def project_summary(self, project_id: str) -> dict[str, Any]:
        try:
            state, events = self.project_application.get_project_snapshot(project_id)
        except KeyError as exc:
            raise InboxNotFound("project was not found") from exc
        project = state["project"]
        questions = list(state["questions"].values())
        open_count = sum(question["status"] == "open" for question in questions)
        resolved_count = sum(question["status"] in {"answered", "rejected", "cancelled", "expired", "superseded"} for question in questions)
        recent_events = []
        for event in reversed(events[-6:]):
            payload = event["payload"]
            event_type = event["eventType"]
            if event_type == "project.created":
                title, description = "Project created", project["name"]
            elif event_type == "work.created":
                title, description = "Work item added", payload["title"]
            elif event_type == "work.status_changed":
                work = state["workItems"].get(payload["workId"], {})
                title = "Work status changed"
                description = f"{work.get('title', payload['workId'])}: {payload['from']} → {payload['to']}"
            elif event_type == "human.question_opened":
                question = state["questions"].get(payload["questionId"], {})
                title, description = "Question opened", question.get("questionText", "Project question")
            elif event_type == "human.question_answered":
                question = state["questions"].get(payload["questionId"], {})
                title, description = "Question answered", question.get("questionText", "Project question")
            elif event_type == "human.question_closed":
                title, description = f"Question {payload['to']}", payload.get("resolutionReason", "Project question closed")
            else:
                title, description = "Project event", event_type
            role = event["actor"].get("role", "system")
            actor_label = {"user": "You", "vp": "Project VP", "specialist": "Research specialist"}.get(role, role.title())
            recent_events.append({
                "eventId": event["eventId"],
                "title": title,
                "description": description,
                "actorLabel": actor_label,
                "occurredAt": event["occurredAt"],
            })
        return {
            "project": project,
            "workItems": list(state["workItems"].values()),
            "recentEvents": recent_events,
            "counts": {
                "workItems": len(state["workItems"]),
                "runningWorkItems": sum(item["status"] == "running" for item in state["workItems"].values()),
                "blockedWorkItems": sum(item["status"] in {"blocked", "waiting_human"} for item in state["workItems"].values()),
                "agentRuns": len(state["agentRuns"]),
                "openQuestions": open_count,
                "resolvedQuestions": resolved_count,
                "evidence": len(state["evidence"]),
                "artifacts": len(state["artifacts"]),
                "eventSequence": state["_eventSequence"],
            },
        }

    def project_collection(self, project_id: str, collection: str) -> list[dict[str, Any]]:
        readers = {
            "runs": self.project_application.list_agent_runs,
            "evidence": self.project_application.list_evidence,
            "artifacts": self.project_application.list_artifacts,
        }
        reader = readers.get(collection)
        if reader is None:
            raise ValueError("unsupported project collection")
        try:
            return reader(project_id)
        except KeyError as exc:
            raise InboxNotFound("project was not found") from exc

    def list_questions(self, project_id: str, status: str = "open") -> dict[str, Any]:
        if status == "open":
            statuses = ("open",)
        elif status == "resolved":
            statuses = ("answered", "rejected", "cancelled", "expired", "superseded")
        else:
            raise ValueError("status must be open or resolved")
        return {
            "projectId": project_id,
            "status": status,
            "questions": self.application.list_questions(project_id, statuses=statuses),
        }

    def get_question(self, project_id: str, question_id: str) -> dict[str, Any]:
        return self.application.get_question(project_id, question_id)

    def answer_question(self, project_id: str, question_id: str, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) - {"expectedEtag", "text", "selectedOptionId"}:
            raise ValueError("request contains unsupported fields")
        expected_etag = _validated_etag(request)
        text = request.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 10_000:
            raise ValueError("text must contain 1 to 10000 characters")
        option_id = request.get("selectedOptionId")
        if option_id is not None and not isinstance(option_id, str):
            raise ValueError("selectedOptionId must be a string")
        return self.application.answer_question(
            project_id,
            question_id,
            expected_etag=expected_etag,
            text=text,
            selected_option_id=option_id,
        )

    def close_question(self, project_id: str, question_id: str, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) - {"expectedEtag", "newStatus", "reason"}:
            raise ValueError("request contains unsupported fields")
        expected_etag = _validated_etag(request)
        new_status = request.get("newStatus")
        reason = request.get("reason")
        if not isinstance(new_status, str) or new_status not in {"rejected", "cancelled"}:
            raise ValueError("newStatus must be rejected or cancelled")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4_000:
            raise ValueError("reason must contain 1 to 4000 characters")
        return self.application.close_question(
            project_id,
            question_id,
            new_status=new_status,
            expected_etag=expected_etag,
            reason=reason,
        )
