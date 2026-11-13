"""Local-only synthetic runtime for the inbox interaction prototype."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.control_plane import ControlPlaneError, ProjectControlPlane
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
from .repository_inspection import RepositoryInspectionError, alignment_segments, inspect_repository


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


class LocalDemoProjectAccess(ProjectAccessPolicy):
    """Single-user demo access to existing local projects; not production authorization."""

    def __init__(self, store: SQLiteEventStore):
        self.store = store

    def require_project_access(self, actor: dict[str, str], project_id: str, action: str) -> None:
        if actor != DEMO_USER or not self.store.verified_events(project_id):
            raise InboxForbidden("project access denied")
        if action not in {
            "project.read", "project.charter.revise", "work.read", "work.create", "inbox.read", "inbox.answer", "inbox.close",
            "run.read", "evidence.read", "artifact.read", "work.approve", "discussion.read",
        }:
            raise InboxForbidden("action is not available in the local demo")


def _validated_etag(request: dict[str, Any]) -> str:
    etag = request.get("expectedEtag")
    if not isinstance(etag, str) or re.fullmatch(r"[a-f0-9]{64}", etag) is None:
        raise ValueError("expectedEtag must be a SHA-256 revision token")
    return etag


class PrototypeRuntime:
    """Own one canonical store and the demo-only project/inbox facade."""

    def __init__(self, db_path: str | Path, *, workspace_root: str | Path | None = None):
        self.workspace_root = Path(workspace_root or Path.home() / "AI-Projects").expanduser()
        self.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.workspace_root.is_symlink():
            raise RuntimeError("project workspace root must not be a symlink")
        self.workspace_root = self.workspace_root.resolve()
        self.store = SQLiteEventStore(db_path)
        self.control = ProjectControlPlane(self.store)
        self._seed_if_empty()
        self.projector = Projector(self.store)
        self.inbox = InboxReadModel(self.store)
        principal = LocalDemoPrincipal()
        access_policy = LocalDemoProjectAccess(self.store)
        self.application = InboxApplication(
            self.inbox, self.control, principal, access_policy,
        )
        self.project_application = ProjectApplication(
            self.control, principal, access_policy,
        )
        self._ensure_workspace_directories()

    def _ensure_workspace_directories(self) -> None:
        for project_id in self.store._project_ids():
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", project_id) is None:
                raise RuntimeError("canonical project ID cannot safely map to a workspace directory")
            directory = self.workspace_root / project_id
            if directory.is_symlink():
                raise RuntimeError("project workspace directory must not be a symlink")
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.resolve().parent != self.workspace_root:
                raise RuntimeError("project workspace escaped its configured root")

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

    def list_projects(self) -> list[dict[str, Any]]:
        projects = []
        for project_id in sorted(self.store._project_ids()):
            try:
                projects.append(self.project_application.get_project(project_id))
            except (InboxForbidden, KeyError):
                continue
        return projects

    def create_project(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("project creation body must be an object")
        mode = request.get("mode", "new")
        allowed_fields = {"name", "goal"} if mode == "new" else {"name", "goal", "repositoryPath", "inspectionFingerprint"}
        if mode not in {"new", "existing"} or set(request) - allowed_fields - {"mode"} or not {"name", "goal"}.issubset(request):
            raise ValueError("project creation fields do not match the selected project type")
        name, goal = request["name"], request["goal"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("name must contain 1 to 100 characters")
        if not isinstance(goal, str) or not goal.strip() or len(goal.strip()) > 1000:
            raise ValueError("goal must contain 1 to 1000 characters")
        report = None
        if mode == "existing":
            if not isinstance(request.get("repositoryPath"), str) or not isinstance(request.get("inspectionFingerprint"), str):
                raise ValueError("existing projects require an inspected local Git repository")
            report = self.inspect_repository(request["repositoryPath"])
            if report["fingerprint"] != request["inspectionFingerprint"]:
                raise ControlPlaneError("the repository changed since inspection; inspect it again before creating the project")
        project_id = f"project.local-{secrets.token_hex(8)}"
        project_directory = self.workspace_root / project_id
        project_directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        now = datetime.now(timezone.utc).isoformat()
        try:
            project = self.control.create_project({
                "recordType": "project", "schemaVersion": "0.1", "projectId": project_id,
                "revision": 1, "name": name.strip(), "status": "active",
                "charter": {
                    "revision": 1, "goals": [goal.strip()],
                    "nonGoals": [],
                    "acceptanceCriteria": [],
                },
                "createdBy": DEMO_USER, "createdAt": now, "updatedAt": now,
            })
            if report is not None:
                self._start_alignment_interview(project_id, report)
        except Exception:
            if self.control.projector.replay(project_id)["project"] is None:
                project_directory.rmdir()
            raise
        return project

    def inspect_repository(self, repository_path: str) -> dict[str, Any]:
        try:
            allowed_root = self.workspace_root.parent
            return inspect_repository(repository_path, allowed_root=allowed_root)
        except RepositoryInspectionError as exc:
            raise ValueError(str(exc)) from exc

    def _start_alignment_interview(self, project_id: str, report: dict[str, Any]) -> None:
        work_id = "work.alignment-intake"
        now = datetime.now(timezone.utc).isoformat()
        self.control.create_work({
            "recordType": "work_item", "schemaVersion": "0.1", "projectId": project_id,
            "workId": work_id, "revision": 1, "parentWorkId": None,
            "title": "Guided repository alignment", "objective": "Review repository-derived hypotheses with the project owner before proposing delivery work.",
            "workType": "planning", "status": "ready", "owner": DEMO_VP,
            "dependsOn": [], "capabilityGrantIds": [],
            "acceptanceCriteria": ["Record the owner's vision, current-state corrections, scope boundaries, unresolved choices, and approval preferences."],
            "createdBy": DEMO_USER, "createdAt": now, "updatedAt": now,
        }, actor=DEMO_USER)
        segments = alignment_segments(report)
        plan = {"kind": "repo_alignment_plan.v1", "repository": report, "segments": segments}
        self.control.add_discussion_message(
            project_id, work_id, thread_id="thread.alignment-intake", message_type="proposal",
            content=json.dumps(plan, ensure_ascii=False, separators=(",", ":")),
            actor=DEMO_VP, visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]],
        )
        self._open_alignment_question(project_id, 0, segments[0], work_id, now)

    def _open_alignment_question(self, project_id: str, index: int, segment: dict[str, str], work_id: str, now: str | None = None) -> None:
        created_at = now or datetime.now(timezone.utc).isoformat()
        question_id = f"question.alignment.{index + 1:02d}"
        self.control.open_question({
            "recordType": "human_question", "schemaVersion": "0.1", "projectId": project_id,
            "questionId": question_id, "questionType": "clarification", "priority": "high",
            "blocking": True, "requestedBy": DEMO_VP, "workIds": [work_id], "runId": None,
            "questionText": f"{index + 1} of 5 · {segment['title']}\n\n{segment['prompt']}",
            "impact": segment["evidence"][:3500],
            "options": ([
                {"optionId": "option.alignment.keep-goal", "label": "Keep the initial goal text", "consequence": "The goal you reviewed before creating the project remains canonical."},
                {"optionId": "option.alignment.replace-goal", "label": "Replace it with my response", "consequence": "Enter the complete replacement goal in the response box."},
            ] if index == 0 else []),
            "status": "open", "createdAt": created_at,
        })

    def _advance_alignment_interview(self, project_id: str, question_id: str) -> None:
        match = re.fullmatch(r"question\.alignment\.(\d{2})", question_id)
        if not match:
            return
        state = self.control.projector.replay(project_id)
        plan_message = next((message for message in state["messages"] if message["threadId"] == "thread.alignment-intake"), None)
        if plan_message is None:
            raise RuntimeError("canonical alignment plan is missing")
        plan = json.loads(plan_message["content"])
        index = int(match.group(1))
        segments = plan["segments"]
        work_id = "work.alignment-intake"
        if index < len(segments):
            next_id = f"question.alignment.{index + 1:02d}"
            if next_id not in state["questions"]:
                self._open_alignment_question(project_id, index, segments[index], work_id)
            return
        work = state["workItems"][work_id]
        if work["status"] == "ready":
            self.control.transition_work(
                project_id, work_id, "completed", actor=DEMO_USER,
                reason="All repository-alignment interview segments were answered by the project owner.",
                expected_revision=work["revision"],
            )
        completion_text = "Repository alignment interview completed. The recorded answers are project input; no delivery task was automatically approved or started."
        state = self.control.projector.replay(project_id)
        if not any(message["content"] == completion_text for message in state["messages"]):
            self.control.add_discussion_message(
                project_id, work_id, thread_id="thread.alignment-intake", message_type="decision",
                content=completion_text, actor=DEMO_USER,
                visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]],
            )

    def resume_alignment(self, project_id: str) -> None:
        self.project_application._authorize(project_id, "inbox.read")
        state = self.control.projector.replay(project_id)
        plan_message = next((message for message in state["messages"] if message["threadId"] == "thread.alignment-intake"), None)
        if plan_message is None:
            return
        if any(question["status"] == "open" for question in state["questions"].values() if question["questionId"].startswith("question.alignment.")):
            return
        plan = json.loads(plan_message["content"])
        answered = [
            question for question in state["questions"].values()
            if question["questionId"].startswith("question.alignment.") and question["status"] == "answered"
        ]
        if answered:
            latest = max(answered, key=lambda question: question["questionId"])
            self._advance_alignment_interview(project_id, latest["questionId"])
        elif plan.get("segments"):
            self._open_alignment_question(project_id, 0, plan["segments"][0], "work.alignment-intake")

    def approve_work(self, project_id: str, work_id: str, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) != {"expectedRevision", "decision"} or type(request["expectedRevision"]) is not int:
            raise ValueError("approval requires expectedRevision and decision")
        if request["decision"] not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        actor = self.project_application._authorize(project_id, "work.approve")
        state = self.control.projector.replay(project_id)
        work = state["workItems"].get(work_id)
        if work is None or work["status"] != "proposed":
            raise ControlPlaneError("only a proposed work item may be approved or declined")
        next_status = "ready" if request["decision"] == "approve" else "cancelled"
        reason = "Human approved this proposed item; approval makes it ready but does not start execution." if next_status == "ready" else "Human declined this proposed item."
        return self.control.transition_work(
            project_id, work_id, next_status, actor=actor, reason=reason,
            expected_revision=request["expectedRevision"],
        )

    def create_work(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.project_application._authorize(project_id, "work.create")
        if set(request) - {"title", "objective", "workType", "parentWorkId", "acceptanceCriteria"}:
            raise ValueError("unsupported work fields")
        title, objective = request.get("title"), request.get("objective")
        work_type = request.get("workType", "research")
        parent_work_id = request.get("parentWorkId")
        acceptance_criteria = request.get("acceptanceCriteria", [])
        if parent_work_id is not None and (
            not isinstance(parent_work_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", parent_work_id) is None
        ):
            raise ValueError("parentWorkId must be a project work ID or null")
        if (
            not isinstance(acceptance_criteria, list)
            or len(acceptance_criteria) > 128
            or any(not isinstance(item, str) or not item.strip() or len(item.strip()) > 1000 for item in acceptance_criteria)
        ):
            raise ValueError("acceptanceCriteria must be a list of nonempty strings up to 1000 characters")
        acceptance_criteria = [item.strip() for item in acceptance_criteria]
        if len(set(acceptance_criteria)) != len(acceptance_criteria):
            raise ValueError("acceptanceCriteria must not contain duplicates")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValueError("title must contain 1 to 160 characters")
        if not isinstance(objective, str) or not objective.strip() or len(objective.strip()) > 2000:
            raise ValueError("objective must contain 1 to 2000 characters")
        if work_type not in {"research", "analysis", "planning", "writing", "review", "implementation", "operations", "other"}:
            raise ValueError("unsupported work type")
        work_id = f"work.local-{secrets.token_hex(8)}"
        now = datetime.now(timezone.utc).isoformat()
        return self.control.create_work({
            "recordType": "work_item", "schemaVersion": "0.1", "projectId": project_id,
            "workId": work_id, "revision": 1, "parentWorkId": parent_work_id,
            "title": title.strip(), "objective": objective.strip(), "workType": work_type,
            "status": "proposed", "owner": DEMO_RESEARCHER, "dependsOn": [],
            "capabilityGrantIds": [], "acceptanceCriteria": acceptance_criteria,
            "createdBy": DEMO_USER, "createdAt": now, "updatedAt": now,
        }, actor=DEMO_USER)

    def projector_project(self) -> dict[str, Any]:
        return self.control.projector.replay(PROJECT_ID)["project"]

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "local-project-prototype",
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
        alignment = None
        plan_message = next((message for message in state["messages"] if message["threadId"] == "thread.alignment-intake"), None)
        if plan_message is not None:
            plan = json.loads(plan_message["content"])
            segments = []
            for index, segment in enumerate(plan.get("segments", []), start=1):
                question = state["questions"].get(f"question.alignment.{index:02d}")
                segments.append({
                    "index": index, "title": segment["title"], "prompt": segment["prompt"],
                    "evidence": segment["evidence"],
                    "status": question["status"] if question else "pending",
                    "answer": question.get("answer") if question else None,
                })
            repository = plan.get("repository", {})
            intake = state["workItems"].get("work.alignment-intake", {})
            alignment = {
                "status": "completed" if intake.get("status") == "completed" else "in_progress",
                "repositoryName": repository.get("repositoryName"),
                "branch": repository.get("branch"),
                "head": repository.get("head"),
                "segments": segments,
            }
        return {
            "project": project,
            "workItems": list(state["workItems"].values()),
            "recentEvents": recent_events,
            "alignment": alignment,
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
        goal_replacement = None
        if question_id == "question.alignment.01" and option_id == "option.alignment.replace-goal":
            marker = "\n\nNote: "
            goal_replacement = text.split(marker, 1)[1].strip() if marker in text else ""
            if not goal_replacement or len(goal_replacement) > 1000:
                raise ValueError("enter a complete replacement goal of at most 1000 characters")
        answered = self.application.answer_question(
            project_id,
            question_id,
            expected_etag=expected_etag,
            text=text,
            selected_option_id=option_id,
        )
        if goal_replacement is not None:
            project = self.control.projector.replay(project_id)["project"]
            if project["charter"]["goals"] != [goal_replacement]:
                charter = {**project["charter"], "goals": [goal_replacement]}
                self.project_application.revise_project_charter(project_id, charter)
        self._advance_alignment_interview(project_id, question_id)
        return answered

    def close_question(self, project_id: str, question_id: str, request: dict[str, Any]) -> dict[str, Any]:
        if question_id.startswith("question.alignment."):
            raise ValueError("alignment interview segments must be answered, not rejected")
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
