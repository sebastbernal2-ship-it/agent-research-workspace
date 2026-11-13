"""Canonical, deterministic inputs for an explicitly routed work execution.

The digest is an integrity binding for queue preparation, not a prompt builder,
model call, authorization grant, or evidence of output correctness.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


MAX_EXECUTION_CONTEXT_BYTES = 1_000_000
MAX_CONTEXT_RELATION_COUNT = 512
_CANONICAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ACTOR_TYPES = frozenset({"human", "agent", "system"})
_ACTOR_ROLES = frozenset({"user", "vp", "orchestrator", "specialist", "reviewer", "worker", "system"})


class ExecutionContextError(ValueError):
    """Canonical project state cannot safely form an execution context."""


def execution_context_record(
    state: Mapping[str, Any], work_id: str, profile: Any,
    verification_actor: Mapping[str, str],
) -> dict[str, Any]:
    """Build a bounded-by-project canonical context snapshot for ready work.

    Includes the project charter, selected work, its parent lineage, completed
    dependency closure, and exact trusted route identity. It excludes unrelated
    project work, chat, repository contents, and provider-selected fields.
    """
    project = state.get("project")
    work_items = state.get("workItems")
    if not isinstance(project, dict) or not isinstance(work_items, dict):
        raise ExecutionContextError("canonical project and work state are required")
    work = work_items.get(work_id)
    if not isinstance(work, dict):
        raise ExecutionContextError("canonical work item does not exist")
    if project.get("status") != "active":
        raise ExecutionContextError("project is not active")
    if work.get("status") != "ready":
        raise ExecutionContextError("work is not canonically ready")

    for dependency_id in work.get("dependsOn", []):
        if work_items.get(dependency_id, {}).get("status") != "completed":
            raise ExecutionContextError("work has an incomplete dependency")
    pending = list(work.get("dependsOn", []))
    dependency_ids: set[str] = set()
    while pending:
        dependency_id = pending.pop()
        if dependency_id in dependency_ids:
            continue
        dependency = work_items.get(dependency_id)
        if not isinstance(dependency, dict):
            raise ExecutionContextError("work dependency is missing from canonical state")
        dependency_ids.add(dependency_id)
        if len(dependency_ids) > MAX_CONTEXT_RELATION_COUNT:
            raise ExecutionContextError("dependency closure exceeds the execution-context limit")
        pending.extend(dependency.get("dependsOn", []))
    if any(work_items[item_id].get("status") != "completed" for item_id in dependency_ids):
        raise ExecutionContextError("work has an incomplete transitive dependency")

    for question in state.get("questions", {}).values():
        if (
            question.get("status") == "open" and question.get("blocking")
            and work_id in question.get("workIds", [])
        ):
            raise ExecutionContextError("work is paused by an open blocking question")
    if any(
        run.get("workId") == work_id and run.get("status") == "running"
        for run in state.get("agentRuns", {}).values()
    ):
        raise ExecutionContextError("work already has an active agent run")

    lineage: list[dict[str, Any]] = []
    parent_id = work.get("parentWorkId")
    seen_parents: set[str] = set()
    while parent_id is not None:
        if parent_id in seen_parents:
            raise ExecutionContextError("work parent lineage contains a cycle")
        seen_parents.add(parent_id)
        parent = work_items.get(parent_id)
        if not isinstance(parent, dict):
            raise ExecutionContextError("work parent is missing from canonical state")
        lineage.append(parent)
        if len(lineage) > MAX_CONTEXT_RELATION_COUNT:
            raise ExecutionContextError("parent lineage exceeds the execution-context limit")
        parent_id = parent.get("parentWorkId")

    if (
        not isinstance(verification_actor, Mapping)
        or set(verification_actor) != {"actorType", "actorId", "role"}
        or not isinstance(verification_actor.get("actorType"), str)
        or verification_actor["actorType"] not in _ACTOR_TYPES
        or not isinstance(verification_actor.get("actorId"), str)
        or _CANONICAL_ID.fullmatch(verification_actor["actorId"]) is None
        or not isinstance(verification_actor.get("role"), str)
        or verification_actor["role"] not in _ACTOR_ROLES
    ):
        raise ExecutionContextError("verification actor is not a canonical contract actor")

    route_fields = ("profile_id", "agent_id", "role", "adapter_name", "adapter_version", "endpoint_class")
    route = {field: getattr(profile, field, None) for field in route_fields}
    if any(not isinstance(value, str) or not value for value in route.values()):
        raise ExecutionContextError("trusted route profile is incomplete")

    return {
        "contextVersion": 1,
        "project": {
            "projectId": project.get("projectId"),
            "name": project.get("name"),
            "charter": project.get("charter"),
        },
        "work": work,
        "parentLineage": lineage,
        "completedDependencies": [work_items[item_id] for item_id in sorted(dependency_ids)],
        "route": {
            "profileId": route["profile_id"],
            "agentId": route["agent_id"],
            "role": route["role"],
            "adapter": {"name": route["adapter_name"], "version": route["adapter_version"]},
            "endpointClass": route["endpoint_class"],
        },
        "verificationActor": dict(verification_actor),
    }


def execution_context_hash(
    state: Mapping[str, Any], work_id: str, profile: Any,
    verification_actor: Mapping[str, str],
) -> str:
    """Hash the canonical JSON execution-context record with SHA-256."""
    try:
        encoded = json.dumps(
            execution_context_record(state, work_id, profile, verification_actor),
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_EXECUTION_CONTEXT_BYTES:
            raise ExecutionContextError("execution context exceeds the 1 MiB limit")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        if isinstance(exc, ExecutionContextError):
            raise
        raise ExecutionContextError("execution context is not finite canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()
