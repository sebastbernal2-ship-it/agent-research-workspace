"""Authenticated-principal application boundary for project commands.

Identity providers and membership policies are injected by the hosting service;
this module does not authenticate sessions or provide a production policy.
"""

from __future__ import annotations

from typing import Any

from .capabilities import CapabilityAuthority
from .control_plane import ProjectControlPlane
from .event_store import ConcurrentWriteConflict
from .inbox import (
    InboxForbidden, PrincipalProvider, ProjectAccessPolicy, is_authenticated_human_actor,
)
from .projection import Projector


class ProjectApplication:
    """Expose project reads/commands without accepting caller-supplied actors."""

    def __init__(
        self,
        control_plane: ProjectControlPlane,
        principal_provider: PrincipalProvider,
        access_policy: ProjectAccessPolicy,
        capability_authority: CapabilityAuthority | None = None,
    ):
        self.control = control_plane
        self.projector = Projector(control_plane.store)
        self.principal_provider = principal_provider
        self.access_policy = access_policy
        self.capabilities = capability_authority or CapabilityAuthority(control_plane.store)

    def _authorize(self, project_id: str, action: str) -> dict[str, str]:
        actor = self.principal_provider.current_actor()
        if not is_authenticated_human_actor(actor):
            raise InboxForbidden("an authenticated human user is required")
        self.access_policy.require_project_access(actor, project_id, action)
        return actor

    def get_project(self, project_id: str) -> dict[str, Any]:
        self._authorize(project_id, "project.read")
        project = self.projector.replay(project_id)["project"]
        if project is None:
            raise KeyError(f"unknown project {project_id!r}")
        return project

    def list_work(self, project_id: str) -> list[dict[str, Any]]:
        self._authorize(project_id, "work.read")
        state = self.projector.replay(project_id)
        return list(state["workItems"].values())

    def list_agent_runs(self, project_id: str) -> list[dict[str, Any]]:
        self._authorize(project_id, "run.read")
        state = self.projector.replay(project_id)
        return list(state["agentRuns"].values())

    def list_evidence(self, project_id: str) -> list[dict[str, Any]]:
        self._authorize(project_id, "evidence.read")
        state = self.projector.replay(project_id)
        return list(state["evidence"].values())

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        self._authorize(project_id, "artifact.read")
        state = self.projector.replay(project_id)
        return list(state["artifacts"].values())

    def list_messages(self, project_id: str) -> list[dict[str, Any]]:
        self._authorize(project_id, "discussion.read")
        state = self.projector.replay(project_id)
        return list(state["messages"])

    def list_capability_grants(self, project_id: str) -> list[dict[str, Any]]:
        self._authorize(project_id, "capability.read")
        state = self.projector.replay(project_id)
        return list(state["capabilityGrants"].values())

    def create_work(self, project_id: str, record: dict[str, Any]) -> dict[str, Any]:
        actor = self._authorize(project_id, "work.create")
        if not isinstance(record, dict) or record.get("projectId") != project_id:
            raise ValueError("work item must target the authorized project")
        work = dict(record)
        work["createdBy"] = actor
        return self.control.create_work(work, actor=actor)

    def revise_project_charter(
        self, project_id: str, charter: dict[str, Any], *, occurred_at: str | None = None,
    ) -> dict[str, Any]:
        actor = self._authorize(project_id, "project.charter.revise")
        return self.control.revise_project_charter(
            project_id, charter, actor=actor, occurred_at=occurred_at,
        )

    def revise_work(
        self,
        project_id: str,
        work_id: str,
        changes: dict[str, Any],
        *,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        actor = self._authorize(project_id, "work.revise")
        return self.control.revise_work(
            project_id, work_id, changes, actor=actor, occurred_at=occurred_at,
        )

    def grant_capability(
        self, project_id: str, record: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._authorize(project_id, "capability.grant")
        if not isinstance(record, dict) or record.get("projectId") != project_id:
            raise ValueError("capability grant must target the authorized project")
        approved = dict(record)
        approved["grantedBy"] = actor
        return self.capabilities.grant(approved, actor=actor)

    def revoke_capability(
        self,
        project_id: str,
        grant_id: str,
        *,
        reason: str,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        actor = self._authorize(project_id, "capability.revoke")
        return self.capabilities.revoke(
            project_id, grant_id, actor=actor, reason=reason, occurred_at=occurred_at,
        )

    def transition_work(
        self,
        project_id: str,
        work_id: str,
        new_status: str,
        *,
        reason: str,
        expected_revision: int,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        actions = {"ready": "work.approve", "cancelled": "work.cancel"}
        action = actions.get(new_status)
        if action is None:
            raise ValueError("users may only approve ready work or cancel work through this boundary")
        actor = self._authorize(project_id, action)
        work = self.projector.replay(project_id)["workItems"].get(work_id)
        if work is None:
            raise KeyError(f"unknown work item {work_id!r}")
        if work["revision"] != expected_revision:
            raise ConcurrentWriteConflict("work approval/cancellation targets a stale revision")
        return self.control.transition_work(
            project_id, work_id, new_status, actor=actor,
            reason=reason, occurred_at=occurred_at, expected_revision=expected_revision,
        )
