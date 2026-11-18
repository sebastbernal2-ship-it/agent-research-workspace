"""Local-only synthetic runtime for the inbox interaction prototype."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
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
from .pi_chat import LocalModelChat, PiChatError, PiCodexChat
from .digest import LastSeen, build_digest
from .ideas import IdeaStore
from .integrations import IntegrationStore, usable, validate
from .issues import PREFIX as ISSUE_KINDS, active_issues
from .phases import is_phase, phase_summaries
from .vp import DataRoots, LOG_KINDS, active_logs, CHAT_WORK, CONVERSE_SYSTEM, LEARN_SYSTEM, REFRESH_SYSTEM, SUMMARY_SYSTEM, AutomationSettings, _json_object, project_memory, uncurated_notes, RepoChat, ProjectVP, VPError, default_runner, new_project_segments
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
            "board.draft", "automation.manage",
        }:
            raise InboxForbidden("action is not available in the local demo")


def _validated_etag(request: dict[str, Any]) -> str:
    etag = request.get("expectedEtag")
    if not isinstance(etag, str) or re.fullmatch(r"[a-f0-9]{64}", etag) is None:
        raise ValueError("expectedEtag must be a SHA-256 revision token")
    return etag


class _SerializedControl:
    """Every control-plane write runs under one lock so parallel agents never race on event sequence numbers.

    Each call replays and appends atomically; slow model calls happen outside it.
    """

    def __init__(self, inner: ProjectControlPlane):
        self._inner, self._lock = inner, threading.RLock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def locked(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return attr(*args, **kwargs)
        return locked


class PrototypeRuntime:
    """Own one canonical store and the demo-only project/inbox facade."""

    def __init__(
        self, db_path: str | Path, *, workspace_root: str | Path | None = None,
        chat_provider: Any | None = None, runner: Any | None = None,
    ):
        self.workspace_root = Path(workspace_root or Path.home() / "AI-Projects").expanduser()
        self.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.workspace_root.is_symlink():
            raise RuntimeError("project workspace root must not be a symlink")
        self.workspace_root = self.workspace_root.resolve()
        self.store = SQLiteEventStore(db_path)
        self.control = _SerializedControl(ProjectControlPlane(self.store))
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
        self._plain_chat = chat_provider or PiCodexChat()
        self.chat_provider = chat_provider
        self.red_provider = LocalModelChat()
        self._chat_lock = threading.Lock()
        self._chat_timestamps: list[float] = []
        self.settings = AutomationSettings(Path(db_path).parent / "automation.json")
        self.last_seen = LastSeen(Path(db_path).parent / "last_seen.json")
        self.integrations = IntegrationStore(Path(db_path).parent / "integrations.json")
        self.ideas = IdeaStore(Path(db_path).parent / "ideas.json")
        self.data_roots = DataRoots(Path(db_path).parent / "data_roots.json", self.workspace_root)
        self.vp = ProjectVP(self, runner or default_runner(), self.settings)
        if self.chat_provider is None:
            self.chat_provider = RepoChat(self.vp, self._plain_chat)
        self._autopilot_stop = threading.Event()
        self._autopilot: threading.Thread | None = None
        self._drafting: set[str] = set()
        self.sync_memory = False  # tests run curation inline

    # ---- autopilot -----------------------------------------------------
    def start_autopilot(self, interval: float = 5.0) -> None:
        """Run approved work in the background. Never touches the synthetic demo project."""
        if self._autopilot is not None:
            return

        for project_id in sorted(self.store._project_ids()):
            if project_id != PROJECT_ID:
                try:
                    self.vp.recover(project_id)
                except Exception:
                    pass

        def loop() -> None:
            while not self._autopilot_stop.wait(interval):
                for project_id in sorted(self.store._project_ids()):
                    if project_id == PROJECT_ID:
                        continue
                    try:
                        self.vp.tick(project_id)
                    except Exception:
                        pass  # one project's failure must not stop the others

        self._autopilot = threading.Thread(target=loop, name="northstar-autopilot", daemon=True)
        self._autopilot.start()

    def stop_autopilot(self) -> None:
        self._autopilot_stop.set()

    def _draft_in_background(self, project_id: str) -> None:
        if self._autopilot is None or project_id in self._drafting:
            return
        self._drafting.add(project_id)

        def draft() -> None:
            try:
                self.vp.phases.plan(project_id)  # the whole project as phases; the VP steers each one itself
            except Exception:
                try:
                    self.vp.draft_board(project_id)  # fall back to a flat board
                except Exception:
                    pass  # the owner can retry with "Plan phases" / "Draft board"
            finally:
                self._drafting.discard(project_id)

        threading.Thread(target=draft, name="northstar-draft", daemon=True).start()

    def plan_phases(self, project_id: str) -> dict[str, Any]:
        self.project_application._authorize(project_id, "board.draft")
        if project_id == PROJECT_ID:
            raise ValueError("the synthetic demo project does not use the VP")
        try:
            return {"phases": self.vp.phases.plan(project_id)}
        except VPError as exc:
            raise ControlPlaneError(str(exc)) from exc

    def paused_until(self) -> str | None:
        """When the model account's usage limit lifts, if every usable model is currently limited."""
        runner = self.vp.runner
        until = runner.limit_until() if hasattr(runner, "limit_until") else (getattr(runner, "limited_until", 0.0) if getattr(runner, "limited", None) and runner.limited() else 0.0)
        return datetime.fromtimestamp(until, timezone.utc).isoformat() if until and until > time.time() else None

    def project_digest(self, project_id: str) -> dict[str, Any]:
        """What changed since the owner last looked (their own marker; first look = since the project began)."""
        state, _events = self.project_application.get_project_snapshot(project_id)
        return build_digest(state, self.activity(project_id, 400), self.last_seen.get(project_id))

    def mark_seen(self, project_id: str) -> dict[str, Any]:
        state, _events = self.project_application.get_project_snapshot(project_id)  # raises for an unknown project
        self.last_seen.mark(project_id, datetime.now(timezone.utc).isoformat())
        return {"seen": True}

    def list_integrations(self, project_id: str) -> dict[str, Any]:
        self.project_application.get_project_snapshot(project_id)  # raises for an unknown project
        declared = self.integrations.get(project_id)
        _ok, blocked = usable(declared, dict(os.environ))
        return {"integrations": [{"name": n, **{k: v for k, v in sp.items() if k != "env"}, "envNames": sorted(sp.get("env") or {}),
                                  "ready": n not in blocked, "missingSecrets": blocked.get(n, [])} for n, sp in declared.items()]}

    def set_integration(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.project_application.get_project_snapshot(project_id)
        if not isinstance(request, dict) or "name" not in request or set(request) - {"name", "spec", "remove"}:
            raise ValueError("send name and spec to declare an integration, or name and remove: true")
        if request.get("remove") is True:
            self.integrations.remove(project_id, request["name"])
        else:
            self.integrations.set(project_id, request["name"], request.get("spec"))
        return self.list_integrations(project_id)

    # ---- ideas: things to build someday, escalated into projects on demand --------------------------
    def list_ideas(self) -> dict[str, Any]:
        return {"ideas": self.ideas.list()}

    def add_idea(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or not {"title"} <= set(request) <= {"title", "note"}:
            raise ValueError("an idea needs a title and optionally a note")
        return {"idea": self.ideas.add(request["title"], request.get("note", ""))}

    def update_idea(self, idea_id: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"idea": self.ideas.update(idea_id, request)}
        except KeyError as exc:
            raise InboxNotFound("idea was not found") from exc

    def start_idea(self, idea_id: str) -> dict[str, Any]:
        """Escalate an idea into a project: the owner's own words become the goal and the first logged detail."""
        try:
            idea = self.ideas.get(idea_id)
        except KeyError as exc:
            raise InboxNotFound("idea was not found") from exc
        if idea["status"] != "idea":
            raise ControlPlaneError("only an idea that has not been started or archived can become a project")
        goal = (idea["note"].split("\n")[0].strip() if idea["note"] else "") or idea["title"]
        project = self.create_project({"mode": "new", "name": idea["title"][:100], "goal": goal[:1000]})
        if idea["note"]:  # keep everything they wrote, in full, so the interview and every agent start from it
            work_id, thread_id = self._chat_target(project["projectId"], None)
            self._save_logs(project["projectId"], work_id, thread_id, [{
                "kind": "insight", "title": "The original idea: " + idea["title"][:90], "detail": idea["note"][:1500], "crucial": False}])
        return {"project": project, "idea": self.ideas.mark_started(idea_id, project["projectId"])}

    def add_issue(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """The owner logs an issue, improvement or future direction themselves; it goes on the current (or last) phase."""
        self.project_application._authorize(project_id, "work.create")
        if not isinstance(request, dict) or not {"kind", "title"} <= set(request) <= {"kind", "title", "detail", "priority"}:
            raise ValueError("an issue needs kind and title, and optionally detail and priority")
        if request["kind"] not in ISSUE_KINDS:
            raise ValueError("kind must be issue, improvement or direction")
        for key, limit in (("title", 140), ("detail", 2000)):
            if key in request and (not isinstance(request[key], str) or len(request[key].strip()) > limit):
                raise ValueError(f"{key} must be at most {limit} characters")
        if not isinstance(request["title"], str) or not request["title"].strip():
            raise ValueError("title is required")
        state = self.control.projector.replay(project_id)
        phases = sorted((w for w in state["workItems"].values() if is_phase(w) and w["status"] != "cancelled"), key=lambda w: w["createdAt"])
        current = (next((w for w in phases if w["status"] in {"running", "verifying"}), None)
                   or next((w for w in phases if w["status"] in {"proposed", "ready"}), None) or (phases[-1] if phases else None))
        if current is None:
            raise ControlPlaneError("plan phases first: issues belong to a phase")
        made = self.vp.phases.board.file(project_id, current["workId"], request["kind"], request["title"].strip(),
                                         (request.get("detail") or request["title"]).strip(), priority=str(request.get("priority") or "medium").lower(), source="owner")
        if made is None:
            raise ControlPlaneError("that is already on the list, or this phase has too many open items")
        return {"issue": next(i for i in active_issues(self.control.projector.replay(project_id)) if i["id"] == made["workId"])}

    def act_on_issue(self, project_id: str, issue_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.project_application._authorize(project_id, "work.approve")
        if not isinstance(request, dict) or set(request) != {"action"} or not isinstance(request["action"], str):
            raise ValueError("an issue action requires only an action")
        try:
            return {"issue": self.vp.phases.board.act(project_id, issue_id, request["action"])}
        except KeyError as exc:
            raise InboxNotFound("issue was not found") from exc

    def decide_phase(self, project_id: str, phase_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """The owner moves on from a phase (starting the next) or sends it back for more work, outside a checkpoint question."""
        self.project_application._authorize(project_id, "work.approve")
        if not isinstance(request, dict) or set(request) != {"moveOn"} or type(request["moveOn"]) is not bool:
            raise ValueError("a phase decision requires only a boolean moveOn")
        state = self.control.projector.replay(project_id)
        if not phase_id.startswith("work.phase-") or phase_id not in state["workItems"]:
            raise InboxNotFound("phase was not found")
        self.vp.phases.decide(project_id, phase_id, request["moveOn"])
        return {"phases": phase_summaries(self.control.projector.replay(project_id))}

    def draft_board(self, project_id: str) -> dict[str, Any]:
        self.project_application._authorize(project_id, "board.draft")
        if project_id == PROJECT_ID:
            raise ValueError("the synthetic demo project does not use the VP")
        try:
            return {"workItems": self.vp.draft_board(project_id)}
        except VPError as exc:
            raise ControlPlaneError(str(exc)) from exc

    def set_data_roots(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.project_application._authorize(project_id, "automation.manage")
        if not isinstance(request, dict) or set(request) != {"paths"}:
            raise ValueError("data folders require only paths")
        if project_id == PROJECT_ID:
            raise ValueError("the synthetic demo project does not use local data")
        return {"dataRoots": self.data_roots.set(project_id, request["paths"])}

    def automation(self, project_id: str) -> dict[str, Any]:
        self.project_application._authorize(project_id, "project.read")
        return {
            "autoRun": self.settings.auto_run(project_id),
            "workerAvailable": self.vp.runner.available() and project_id != PROJECT_ID,
            "demoProject": project_id == PROJECT_ID,
        }

    def set_automation(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.project_application._authorize(project_id, "automation.manage")
        if set(request) != {"autoRun"} or type(request["autoRun"]) is not bool:
            raise ValueError("automation requires only a boolean autoRun")
        if project_id == PROJECT_ID:
            raise ValueError("the synthetic demo project cannot run agents")
        self.settings.set_auto_run(project_id, request["autoRun"])
        return self.automation(project_id)

    def activity(self, project_id: str, limit: int = 40) -> list[dict[str, Any]]:
        state, _events = self.project_application.get_project_snapshot(project_id)
        titles = {w["workId"]: w["title"] for w in state["workItems"].values()}
        labels = {"vp": "Project VP", "orchestrator": "Orchestrator", "worker": "Subagent", "reviewer": "Reviewer", "user": "You"}
        feed = [
            {"id": m["eventId"], "kind": "report", "workId": m["workId"], "workTitle": titles.get(m["workId"], m["workId"]),
             "actor": labels.get(m["actor"].get("role", ""), "Agent"), "text": m["content"][:4000], "at": m["occurredAt"]}
            for m in state["messages"] if m["threadId"].startswith("thread.work.")
        ]
        return sorted(feed, key=lambda e: e["at"], reverse=True)[:limit]

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
            self._start_alignment_interview(project_id, report, goal.strip())
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

    def _start_alignment_interview(self, project_id: str, report: dict[str, Any] | None, goal: str = "") -> None:
        work_id = "work.alignment-intake"
        now = datetime.now(timezone.utc).isoformat()
        self.control.create_work({
            "recordType": "work_item", "schemaVersion": "0.1", "projectId": project_id,
            "workId": work_id, "revision": 1, "parentWorkId": None,
            "title": "Guided repository alignment", "objective": "Interview the project owner to align on vision, scope, and constraints before any delivery work is proposed.",
            "workType": "planning", "status": "ready", "owner": DEMO_VP,
            "dependsOn": [], "capabilityGrantIds": [],
            "acceptanceCriteria": ["Record the owner's vision, current-state corrections, scope boundaries, unresolved choices, and approval preferences."],
            "createdBy": DEMO_USER, "createdAt": now, "updatedAt": now,
        }, actor=DEMO_USER)
        segments = alignment_segments(report) if report is not None else new_project_segments(goal)
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
            "questionText": (f"{index + 1} of 5 · " if index < 5 else "Follow-up · ") + f"{segment['title']}\n\n{segment['prompt']}",
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
        extra = sum(1 for qid in state["questions"] if qid.startswith("question.alignment.") and int(qid.rsplit(".", 1)[1]) > len(segments))
        if index >= len(segments) and extra < 3:
            # One blocking question per work item: ask a single follow-up, then re-evaluate after the answer.
            followups = self.vp.followup_questions(project_id)
            if followups:
                self._open_alignment_question(project_id, len(segments) + extra, {**followups[0], "evidence": "Asked by the VP because earlier answers left this unclear."}, work_id)
                return
        if any(q["status"] == "open" for q in self.control.projector.replay(project_id)["questions"].values()
               if q["questionId"].startswith("question.alignment.")):
            return
        work = state["workItems"][work_id]
        if work["status"] == "ready":
            self.control.transition_work(
                project_id, work_id, "completed", actor=DEMO_USER,
                reason="All repository-alignment interview segments were answered by the project owner.",
                expected_revision=work["revision"],
            )
        completion_text = "Alignment interview completed. The VP will draft a task board from your answers; nothing starts until you approve a task or turn on auto-run."
        state = self.control.projector.replay(project_id)
        if not any(message["content"] == completion_text for message in state["messages"]):
            self.control.add_discussion_message(
                project_id, work_id, thread_id="thread.alignment-intake", message_type="decision",
                content=completion_text, actor=DEMO_USER,
                visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]],
            )
            self._draft_in_background(project_id)

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
        if request["decision"] not in {"approve", "reject", "retry"}:
            raise ValueError("decision must be approve, reject or retry")
        actor = self.project_application._authorize(project_id, "work.approve")
        state = self.control.projector.replay(project_id)
        work = state["workItems"].get(work_id)
        if request["decision"] == "retry":
            if work is None or work["status"] != "failed":
                raise ControlPlaneError("only a failed work item can be retried")
            return self.control.transition_work(
                project_id, work_id, "ready", actor=actor, reason="Owner retried this failed task.",
                expected_revision=request["expectedRevision"],
            )
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

    def chat_status(self, project_id: str) -> dict[str, Any]:
        self.project_application._authorize(project_id, "project.read")
        return {**self.chat_provider.status(), "red": self.red_provider.status()}

    def send_project_chat(self, project_id: str, request: dict[str, Any]) -> dict[str, str]:
        if (
            not isinstance(request, dict)
            or set(request) - {"message", "history", "questionId", "mode"}
            or not {"message", "history"}.issubset(request)
        ):
            raise ValueError("chat requires message, history, and optionally questionId")
        mode = request.get("mode", "blue")
        if mode not in {"blue", "red"}:
            raise ValueError("mode must be blue or red")
        question_id = request.get("questionId")
        if question_id is not None and (
            not isinstance(question_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", question_id) is None
        ):
            raise ValueError("questionId must identify an inbox question")
        PiCodexChat._conversation(request["message"], request["history"])
        state, _events = self.project_application.get_project_snapshot(project_id)
        project = state["project"]
        work_items = [w for w in state["workItems"].values() if w["workId"] != CHAT_WORK]
        open_questions = [question for question in state["questions"].values() if question["status"] == "open"]
        recorded_decisions = [question for question in state["questions"].values() if question["status"] == "answered"]
        project_context = {
            "project": {
                "name": project["name"],
                "goals": project["charter"].get("goals", [])[:4],
                "nonGoals": project["charter"].get("nonGoals", [])[:6],
                "acceptanceCriteria": project["charter"].get("acceptanceCriteria", [])[:6],
            },
            "workItems": [
                {
                    "workId": item["workId"], "parentWorkId": item.get("parentWorkId"),
                    "title": item["title"][:160], "objective": item["objective"][:600],
                    "workType": item["workType"], "status": item["status"],
                    "dependsOn": item.get("dependsOn", [])[:10],
                    "acceptanceCriteria": [criterion[:200] for criterion in item.get("acceptanceCriteria", [])[:3]],
                }
                for item in work_items[:20]
            ],
            "workItemCount": len(work_items),
            "openQuestions": [
                {
                    "questionId": question["questionId"],
                    "question": question["questionText"][:500],
                    "blocking": question["blocking"],
                    "workIds": question.get("workIds", [])[:10],
                }
                for question in open_questions[:10]
            ],
            "openQuestionCount": len(open_questions),
            "recordedDecisions": [
                {
                    "question": question["questionText"][:400],
                    "answer": question.get("answer", {}).get("text", "")[:600],
                }
                for question in recorded_decisions[-20:]
            ],
            "recordedDecisionCount": len(recorded_decisions),
            "automation": {
                "projectAutoRun": "on" if self.settings.auto_run(project_id) else "off",
                "chatCanExecuteWork": False,
                "note": "Approved tasks are run by background agents; chat only discusses and cannot start or change work.",
            },
            "recentAgentReports": [
                {"task": entry["workTitle"][:120], "by": entry["actor"], "report": entry["text"][:500]}
                for entry in self.activity(project_id, 6)
            ],
            "repositoryFilesIncluded": False,
            "omittedWorkItems": max(0, len(work_items) - 20),
            "omittedOpenQuestions": max(0, len(open_questions) - 10),
        }
        question_context = None
        if question_id is not None:
            question = state["questions"].get(question_id)
            if question is None:
                raise InboxNotFound("question was not found")
            if question["status"] != "open":
                raise ControlPlaneError("only open questions can be discussed in context")
            question_context = {
                "questionId": question["questionId"],
                "question": question["questionText"][:1000],
                "questionType": question["questionType"],
                "priority": question["priority"],
                "blocking": question["blocking"],
                **({"impact": question.get("impact", "")[:600]} if not question_id.startswith("question.alignment.") else {}),
                "options": [
                    {"label": option["label"][:300], "consequence": option.get("consequence", "")[:500]}
                    for option in question.get("options", [])[:8]
                ],
                "affectedWork": [
                    {
                        "title": state["workItems"][work_id]["title"][:160],
                        "objective": state["workItems"][work_id]["objective"][:500],
                        "status": state["workItems"][work_id]["status"],
                    }
                    for work_id in question.get("workIds", [])[:8]
                    if work_id in state["workItems"]
                ],
            }
        if not self._chat_lock.acquire(blocking=False):
            raise PiChatError("A Codex chat request is already in progress.")
        try:
            now = time.monotonic()
            self._chat_timestamps = [stamp for stamp in self._chat_timestamps if now - stamp < 3600]
            if len(self._chat_timestamps) >= 20:
                raise PiChatError("The local chat limit is 20 requests per hour. Try again later.")
            self._chat_timestamps.append(now)
            # The project record is the single source of truth for the conversation: the browser's copy is ignored.
            work_id, thread_id = self._chat_target(project_id, question_id)
            summary, history = self._chat_context(project_id, thread_id)
            project_context["earlierConversationSummary"] = summary
            project_context["ownerMemory"] = project_memory(state)
            self.control.add_discussion_message(
                project_id, work_id, thread_id=thread_id, message_type="question", content=request["message"].strip(),
                actor=DEMO_USER, visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]])
            request = {**request, "history": history}
            provider = self.red_provider if mode == "red" else self.chat_provider
            extra = {"project_id": project_id} if getattr(provider, "needs_project_id", False) else {}
            reply = provider.send(request["message"], request["history"], project_context, question_context, **extra)
            self.control.add_discussion_message(
                project_id, work_id, thread_id=thread_id, message_type="status", content=reply,
                actor=DEMO_VP, visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]])
            self._learn_soon(project_id, work_id, thread_id, request["message"].strip(), history[-4:] + [{"role": "assistant", "content": reply[:1500]}])
            return {"response": reply}
        finally:
            self._chat_lock.release()

    # ---- curated memory --------------------------------------------------
    def _curate_soon(self, project_id: str) -> None:
        def run() -> None:
            try:
                self.vp.curate_memory(project_id)
            except Exception:
                pass  # memory curation is best effort; notes and answers remain in the record

        if self.sync_memory:
            run()
        else:
            threading.Thread(target=run, name="northstar-memory", daemon=True).start()

    def _learn_soon(self, project_id: str, work_id: str, thread_id: str, message: str, recent: list[dict[str, str]]) -> None:
        """Project chat feeds memory too: corrections and decisions typed anywhere are saved as notes."""
        def run() -> None:
            try:
                state = self.control.projector.replay(project_id)
                reply = _json_object(self.vp.runner.think(LEARN_SYSTEM, json.dumps({
                    "currentMemory": project_memory(state), "recentTurns": recent, "ownerMessage": message[:4000]})))
                self._save_logs(project_id, work_id, thread_id, self._clean_logs(reply.get("logs")))
                notes = [n.strip()[:500] for n in (reply.get("learned") or []) if isinstance(n, str) and n.strip()][:8]
                for note in notes:
                    self.control.add_discussion_message(
                        project_id, work_id, thread_id=thread_id, message_type="evidence", content="NOTE: " + note,
                        actor=DEMO_VP, visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]])
                if len(uncurated_notes(self.control.projector.replay(project_id))) >= 6:
                    self.vp.curate_memory(project_id)
            except Exception:
                pass  # learning is best effort; the chat itself is already saved

        if not self.vp.runner.available():
            return
        if self.sync_memory:
            run()
        else:
            threading.Thread(target=run, name="northstar-learn", daemon=True).start()

    # ---- durable chat ----------------------------------------------------
    def _chat_target(self, project_id: str, question_id: str | None) -> tuple[str, str]:
        state = self.control.projector.replay(project_id)
        work_id = None
        if question_id is not None:
            question = state["questions"].get(question_id)
            work_id = next((w for w in (question or {}).get("workIds", []) if w in state["workItems"]), None)
        if work_id is None:
            work_id = CHAT_WORK
            if CHAT_WORK not in state["workItems"]:
                now = datetime.now(timezone.utc).isoformat()
                self.control.create_work({
                    "recordType": "work_item", "schemaVersion": "0.1", "projectId": project_id, "workId": CHAT_WORK,
                    "revision": 1, "parentWorkId": None, "title": "Project chat log",
                    "objective": "Holds the durable owner-and-VP conversation; not a task.", "workType": "other",
                    "status": "ready", "owner": DEMO_VP, "dependsOn": [], "capabilityGrantIds": [],
                    "acceptanceCriteria": [], "createdBy": DEMO_USER, "createdAt": now, "updatedAt": now,
                }, actor=DEMO_USER)
        return work_id, f"thread.chat.{question_id}" if question_id else "thread.chat.project"

    def _thread(self, project_id: str, thread_id: str) -> list[dict[str, Any]]:
        messages = [m for m in self.control.projector.replay(project_id)["messages"] if m["threadId"] == thread_id]
        return sorted(messages, key=lambda m: m["sequence"])

    @staticmethod
    def _is_summary(message: dict[str, Any]) -> bool:
        return message["messageType"] == "revision" and message["content"].startswith("SUMMARY: ")

    @staticmethod
    def _is_note(message: dict[str, Any]) -> bool:
        """Notes and drafts are memory/state stored in the thread, not chat bubbles."""
        content = message["content"]
        return (message["messageType"] == "evidence" and content.startswith(("NOTE: ", "LOG: "))) or \
            (message["messageType"] == "proposal" and content.startswith("DRAFT: ")) or \
            (message["messageType"] == "revision" and content.startswith(("LOGEDIT: ", "LOGDEL: ")))

    def _clean_logs(self, raw: Any) -> list[dict[str, Any]]:
        out = []
        for item in (raw if isinstance(raw, list) else [])[:15]:
            if not isinstance(item, dict):
                continue
            title, detail = str(item.get("title") or "").strip()[:120], str(item.get("detail") or "").strip()[:1500]
            if not title or not detail:
                continue
            kind = item.get("kind") if item.get("kind") in LOG_KINDS else "insight"
            out.append({"kind": kind, "title": title, "detail": detail, "crucial": bool(item.get("crucial"))})
        return out

    def _save_logs(self, project_id: str, work_id: str, thread_id: str, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        saved = []
        for log in logs:
            entry = {"id": "log-" + secrets.token_hex(5), **log}
            self.control.add_discussion_message(
                project_id, work_id, thread_id=thread_id, message_type="evidence", content="LOG: " + json.dumps(entry, ensure_ascii=False),
                actor=DEMO_VP, visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]])
            saved.append(entry)
        return saved

    def edit_log(self, project_id: str, log_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """The owner edits or removes one of the logs the VP took. Both are immutable events; nothing is erased."""
        self.project_application._authorize(project_id, "inbox.answer")
        if not isinstance(request, dict) or not request or set(request) - {"kind", "title", "detail", "crucial", "remove"}:
            raise ValueError("log edit accepts kind, title, detail, crucial or remove")
        state = self.control.projector.replay(project_id)
        log = next((l for l in active_logs(state) if l["id"] == log_id), None)
        if log is None:
            raise InboxNotFound("log was not found")
        both = [DEMO_USER["actorId"], DEMO_VP["actorId"]]
        work_id = next(m["workId"] for m in state["messages"] if m["threadId"] == log["threadId"])
        if request.get("remove") is True:
            self.control.add_discussion_message(project_id, work_id, thread_id=log["threadId"], message_type="revision",
                                                content="LOGDEL: " + log_id, actor=DEMO_USER, visible_to=both)
            return {"removed": log_id}
        changes = {}
        if "kind" in request:
            if request["kind"] not in LOG_KINDS:
                raise ValueError("unknown log kind")
            changes["kind"] = request["kind"]
        for key, limit in (("title", 120), ("detail", 1500)):
            if key in request:
                if not isinstance(request[key], str) or not request[key].strip() or len(request[key].strip()) > limit:
                    raise ValueError(f"{key} must contain 1 to {limit} characters")
                changes[key] = request[key].strip()
        if "crucial" in request:
            if type(request["crucial"]) is not bool:
                raise ValueError("crucial must be a boolean")
            changes["crucial"] = request["crucial"]
        if not changes:
            raise ValueError("nothing to change")
        self.control.add_discussion_message(project_id, work_id, thread_id=log["threadId"], message_type="revision",
                                            content="LOGEDIT: " + json.dumps({"id": log_id, **changes}, ensure_ascii=False),
                                            actor=DEMO_USER, visible_to=both)
        return {"log": {**{k: log[k] for k in ("id", "kind", "title", "detail", "crucial")}, **changes, "edited": True}}

    def _proposal(self, answer: Any, options: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(answer, dict):
            return None
        option_ids = {o["optionId"] for o in options}
        option_id = answer.get("selectedOptionId") if answer.get("selectedOptionId") in option_ids else None
        label = next((o["label"] for o in options if o["optionId"] == option_id), None)
        note = answer.get("text").strip()[:9000] if isinstance(answer.get("text"), str) else ""
        text = "\n\nNote: ".join(part for part in (label, note) if part)
        return {"text": text, "selectedOptionId": option_id} if text else None

    def _latest_draft(self, project_id: str, thread_id: str) -> dict[str, Any] | None:
        drafts = [m for m in self._thread(project_id, thread_id) if m["messageType"] == "proposal" and m["content"].startswith("DRAFT: ")]
        try:
            return json.loads(drafts[-1]["content"][len("DRAFT: "):]) if drafts else None
        except ValueError:
            return None

    def _save_draft(self, project_id: str, work_id: str, thread_id: str, proposal: dict[str, Any], settled: bool,
                    edited_by_owner: bool = False) -> None:
        self.control.add_discussion_message(
            project_id, work_id, thread_id=thread_id, message_type="proposal",
            content="DRAFT: " + json.dumps({**proposal, "settled": settled, **({"editedByOwner": True} if edited_by_owner else {})}, ensure_ascii=False),
            actor=DEMO_USER if edited_by_owner else DEMO_VP, visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]])

    def set_draft(self, project_id: str, question_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """The owner edits the draft themselves. It becomes the draft to record, and the VP must keep their wording."""
        self.project_application._authorize(project_id, "inbox.answer")
        if not isinstance(request, dict) or not {"text"} <= set(request) <= {"text", "selectedOptionId"}:
            raise ValueError("editing requires text and optionally selectedOptionId")
        text = request["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 9_000:
            raise ValueError("text must contain 1 to 9000 characters")
        state = self.control.projector.replay(project_id)
        question = state["questions"].get(question_id)
        if question is None or question["status"] != "open":
            raise ControlPlaneError("only an open question has a draft")
        option_id = request.get("selectedOptionId")
        if option_id is not None and option_id not in {o["optionId"] for o in question.get("options", [])}:
            raise ValueError("selectedOptionId is not an option of this question")
        work_id, thread_id = self._chat_target(project_id, question_id)
        draft = {"text": text.strip(), "selectedOptionId": option_id}
        self._save_draft(project_id, work_id, thread_id, draft, True, edited_by_owner=True)
        return {"draft": {**draft, "settled": True, "editedByOwner": True}}

    def _chat_context(self, project_id: str, thread_id: str) -> tuple[str, list[dict[str, str]]]:
        """Bounded model context from the durable thread: latest saved summary plus the most recent turns."""
        messages = self._thread(project_id, thread_id)
        last = max((i for i, m in enumerate(messages) if self._is_summary(m)), default=-1)
        summary = messages[last]["content"][len("SUMMARY: "):] if last >= 0 else ""
        turns = [m for m in messages[last + 1:] if not self._is_summary(m) and not self._is_note(m)]
        if len(turns) > 16 and self.vp.runner.available():
            older = turns[:-6]
            try:
                folded = self.vp.runner.think(SUMMARY_SYSTEM, json.dumps({
                    "previousSummary": summary,
                    "turns": [{"by": "owner" if t["actor"]["role"] == "user" else "vp", "text": t["content"][:2000]} for t in older]}))
                self.control.add_discussion_message(
                    project_id, messages[0]["workId"], thread_id=thread_id, message_type="revision",
                    content="SUMMARY: " + folded.strip()[:6000], actor=DEMO_VP,
                    visible_to=[DEMO_USER["actorId"], DEMO_VP["actorId"]])
                summary, turns = folded.strip()[:6000], turns[-6:]
            except Exception:
                pass  # summarizing is an optimization; the full thread stays safe in the record
        recent = turns[-14:]
        return summary, [
            {"role": "user" if t["actor"]["role"] == "user" else "assistant", "content": t["content"][:2000]}
            for t in recent if t["content"].strip()]

    def refresh_draft(self, project_id: str, question_id: str) -> dict[str, Any]:
        """Rebuild the running draft from the saved discussion (e.g. when none was shown yet)."""
        self.project_application._authorize(project_id, "inbox.answer")
        state = self.control.projector.replay(project_id)
        question = state["questions"].get(question_id)
        if question is None or question["status"] != "open":
            raise ControlPlaneError("only an open question has a draft")
        if not self.vp.runner.available():
            raise PiChatError("No model runner is available.")
        work_id, thread_id = self._chat_target(project_id, question_id)
        summary, turns = self._chat_context(project_id, thread_id)
        if not any(t["role"] == "user" for t in turns):
            return {"draft": None}
        options = [{"optionId": o["optionId"], "label": o["label"]} for o in question.get("options", [])]
        payload = json.dumps({"openQuestion": {"text": question["questionText"][:1500], "options": options},
                              "earlierSummary": summary, "conversation": turns,
                              "currentDraft": (self._latest_draft(project_id, thread_id) or {}).get("text"),
                "draftWasEditedByOwner": bool((self._latest_draft(project_id, thread_id) or {}).get("editedByOwner")),
                              "ownerMemory": project_memory(state)})
        try:
            data = _json_object(self.vp.runner.think(REFRESH_SYSTEM, payload))
        except VPError:
            return {"draft": None}
        proposal = self._proposal(data.get("answer"), options)
        if proposal is None:
            return {"draft": None}
        settled = bool(data.get("settled"))
        self._save_draft(project_id, work_id, thread_id, proposal, settled)
        return {"draft": {**proposal, "settled": settled}}

    def chat_history(self, project_id: str, question_id: str | None = None) -> dict[str, Any]:
        self.project_application._authorize(project_id, "project.read")
        thread_id = f"thread.chat.{question_id}" if question_id else "thread.chat.project"
        draft = None
        if question_id:
            question = self.control.projector.replay(project_id)["questions"].get(question_id)
            drafts = [m for m in self._thread(project_id, thread_id) if m["messageType"] == "proposal" and m["content"].startswith("DRAFT: ")]
            if question and question["status"] == "open" and drafts:
                try:
                    draft = json.loads(drafts[-1]["content"][len("DRAFT: "):])
                except ValueError:
                    draft = None
        logs = [{k: l.get(k) for k in ("id", "kind", "title", "detail", "crucial", "edited")}
                for l in active_logs(self.control.projector.replay(project_id)) if l["threadId"] == thread_id]
        return {"draft": draft, "logs": logs, "turns": [
            {"role": "user" if m["actor"]["role"] == "user" else "assistant", "content": m["content"], "at": m["occurredAt"]}
            for m in self._thread(project_id, thread_id) if not self._is_summary(m) and not self._is_note(m)]}

    def converse(self, project_id: str, question_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """One owner message on an open question, handled like a chat turn.

        The VP decides whether it answers the question, is a side question (answered in place), or is partial info.
        Useful things the owner says are saved as notes that inform all later work. A settling message only yields a
        *proposal*; the answer is recorded when the owner confirms it.
        """
        self.project_application._authorize(project_id, "inbox.answer")
        if not isinstance(request, dict) or set(request) != {"message"}:
            raise ValueError("conversation requires only message")
        message = request["message"]
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("message must contain 1 to 4000 characters")
        state = self.control.projector.replay(project_id)
        question = state["questions"].get(question_id)
        if question is None:
            raise InboxNotFound("question was not found")
        if question["status"] != "open":
            raise ControlPlaneError("only an open question can be discussed")
        if not self.vp.runner.available():
            raise PiChatError("No model runner is available.")
        if not self._chat_lock.acquire(blocking=False):
            raise PiChatError("A VP request is already in progress.")
        try:
            now = time.monotonic()
            self._chat_timestamps = [stamp for stamp in self._chat_timestamps if now - stamp < 3600]
            if len(self._chat_timestamps) >= 40:
                raise PiChatError("The local chat limit is 40 requests per hour. Try again later.")
            self._chat_timestamps.append(now)
            work_id, thread_id = self._chat_target(project_id, question_id)
            summary, turns = self._chat_context(project_id, thread_id)
            both = [DEMO_USER["actorId"], DEMO_VP["actorId"]]
            self.control.add_discussion_message(project_id, work_id, thread_id=thread_id, message_type="question",
                                                content=message.strip(), actor=DEMO_USER, visible_to=both)
            options = [{"optionId": o["optionId"], "label": o["label"], "consequence": o.get("consequence", "")[:300]}
                       for o in question.get("options", [])]
            payload = {
                "openQuestion": {"text": question["questionText"][:1500], "impact": question.get("impact", "")[:1500], "options": options},
                "ownerMemory": project_memory(state), "earlierSummary": summary, "conversation": turns,
                "currentDraft": (self._latest_draft(project_id, thread_id) or {}).get("text"),
                "draftWasEditedByOwner": bool((self._latest_draft(project_id, thread_id) or {}).get("editedByOwner")),
                "ownerDecisions": [{"q": q["questionText"][:300], "a": q.get("answer", {}).get("text", "")[:600]}
                                   for q in state["questions"].values() if q["status"] == "answered"][-12:],
                "ownerMessage": message.strip(),
            }
            runner = self.vp.runner
            try:
                raw = runner.explore(CONVERSE_SYSTEM, json.dumps(payload), self.vp.read_directory(project_id)) \
                    if hasattr(runner, "explore") else runner.think(CONVERSE_SYSTEM, json.dumps(payload))
            except VPError as exc:
                raise PiChatError(str(exc)) from exc
            try:
                data = _json_object(raw)
            except VPError:
                data = {"intent": "btw", "reply": raw.strip()[:4000]}
            reply = data.get("reply") if isinstance(data.get("reply"), str) and data["reply"].strip() else "Understood."
            self.control.add_discussion_message(project_id, work_id, thread_id=thread_id, message_type="status",
                                                content=reply[:8000], actor=DEMO_VP, visible_to=both)
            new_logs = self._save_logs(project_id, work_id, thread_id, self._clean_logs(data.get("logs")))
            learned = [n.strip()[:500] for n in (data.get("learned") or []) if isinstance(n, str) and n.strip()][:8]
            for note in learned:  # route anything useful back into project memory
                self.control.add_discussion_message(project_id, work_id, thread_id=thread_id, message_type="evidence",
                                                    content="NOTE: " + note, actor=DEMO_VP, visible_to=both)
            settled = data.get("intent") == "answer"
            proposal = self._proposal(data.get("answer"), options)
            if proposal is not None:  # the running draft is always visible and survives a reload
                self._save_draft(project_id, work_id, thread_id, proposal, settled)
            if len(uncurated_notes(self.control.projector.replay(project_id))) >= 6:
                self._curate_soon(project_id)
            return {"reply": reply, "proposedAnswer": proposal, "settled": settled and proposal is not None,
                    "notesSaved": len(learned), "logs": new_logs}
        finally:
            self._chat_lock.release()

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
            repository = plan.get("repository") or {}
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
            "workItems": [w for w in state["workItems"].values() if w["workId"] != CHAT_WORK],
            "recentEvents": recent_events,
            "alignment": alignment,
            "automation": self.automation(project_id),
            "phases": phase_summaries(state),
            "issues": [{k: v for k, v in i.items() if k != "revision"} for i in active_issues(state)][:200],
            "dataRoots": self.data_roots.get(project_id),
            "memory": project_memory(state),
            "activity": self.activity(project_id, 15),
            "pausedUntil": self.paused_until(),
            "drafting": project_id in self._drafting,
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
        if question_id.startswith("question.vp-"):
            self.vp.resume_waiting_on(project_id, question_id)
        if question_id.startswith("question.phase-"):  # a phase checkpoint: start the next phase or reopen this one
            self.vp.phases.on_checkpoint_answer(project_id, question_id, option_id)
        if question_id.startswith("question.phaseplan-"):  # the plan: accept it, or reject it with what to change
            self.vp.phases.on_plan_answer(project_id, question_id, option_id, text)
        self._curate_soon(project_id)
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
