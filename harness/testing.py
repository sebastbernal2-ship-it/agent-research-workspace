"""Synthetic, credential-free canonical records for integration tests only."""

from __future__ import annotations

from .control_plane import ProjectControlPlane
from .event_store import SQLiteEventStore


USER = {"actorType": "human", "actorId": "user.synthetic", "role": "user"}
VP = {"actorType": "agent", "actorId": "agent.synthetic-vp", "role": "vp"}


def seed_synthetic_project(
    store: SQLiteEventStore,
    *,
    project_id: str,
    work_id: str,
    occurred_at: str = "2026-04-16T12:00:00Z",
) -> ProjectControlPlane:
    control = ProjectControlPlane(store, clock=lambda: occurred_at)
    control.create_project({
        "recordType": "project",
        "schemaVersion": "0.1",
        "projectId": project_id,
        "revision": 1,
        "name": f"Synthetic {project_id}",
        "status": "active",
        "charter": {
            "revision": 1,
            "goals": ["Exercise adapter and replay behavior"],
            "nonGoals": ["No real model calls or external actions"],
            "acceptanceCriteria": ["Preserve all accepted events and evidence refs"],
        },
        "createdBy": USER,
        "createdAt": occurred_at,
        "updatedAt": occurred_at,
    })
    control.create_work({
        "recordType": "work_item",
        "schemaVersion": "0.1",
        "projectId": project_id,
        "workId": work_id,
        "revision": 1,
        "parentWorkId": None,
        "title": "Synthetic integration work",
        "objective": "Exercise an isolated runtime adapter against the canonical contract.",
        "workType": "research",
        "status": "ready",
        "owner": VP,
        "dependsOn": [],
        "capabilityGrantIds": [],
        "acceptanceCriteria": ["Adapter events replay into canonical state"],
        "createdBy": USER,
        "createdAt": occurred_at,
        "updatedAt": occurred_at,
    })
    return control
