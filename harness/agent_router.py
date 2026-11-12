"""Deterministic work-to-agent routing over canonical work records.

Routing is deliberately separate from prompt text, model selection, capabilities,
and execution. A matched route is a proposal for a trusted worker factory; it is
not permission to run that worker or grant it tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence


WORK_TYPES = frozenset({
    "research", "analysis", "planning", "writing", "review",
    "implementation", "operations", "other",
})
ROUTE_STATES = frozenset({"matched", "ambiguous", "unknown"})
ENDPOINT_CLASSES = frozenset({"local", "remote"})
AGENT_ROLES = frozenset({"vp", "orchestrator", "specialist", "reviewer", "worker"})
_CANONICAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class AgentRoutingError(ValueError):
    """Invalid trusted route configuration or canonical work input."""


@dataclass(frozen=True)
class AgentRouteProfile:
    profile_id: str
    agent_id: str
    role: str
    adapter_name: str
    adapter_version: str
    endpoint_class: str
    work_types: frozenset[str]
    enabled: bool = True

    def __post_init__(self) -> None:
        for name in ("profile_id", "agent_id", "role", "adapter_name", "adapter_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise AgentRoutingError(f"{name} must be a non-empty string")
        for name in ("profile_id", "agent_id"):
            if _CANONICAL_ID.fullmatch(getattr(self, name)) is None:
                raise AgentRoutingError(f"{name} must be a canonical identifier")
        if len(self.adapter_name) > 100 or len(self.adapter_version) > 100:
            raise AgentRoutingError("adapter name/version must be at most 100 characters")
        if self.role not in AGENT_ROLES:
            raise AgentRoutingError("role must be a canonical agent role")
        if not isinstance(self.endpoint_class, str) or self.endpoint_class not in ENDPOINT_CLASSES:
            raise AgentRoutingError("endpoint_class must be local or remote")
        if not isinstance(self.work_types, frozenset) or not self.work_types or not self.work_types <= WORK_TYPES:
            raise AgentRoutingError("profile work_types must be a nonempty set of canonical work types")
        if type(self.enabled) is not bool:
            raise AgentRoutingError("enabled must be a boolean")


@dataclass(frozen=True)
class AgentRouteDecision:
    state: str
    project_id: str
    work_id: str
    work_type: str | None
    profile_id: str | None = None
    alternatives: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in ROUTE_STATES:
            raise AgentRoutingError("invalid route state")
        if self.state == "matched" and (self.profile_id is None or self.alternatives):
            raise AgentRoutingError("matched routes require one profile and no alternatives")
        if self.state == "ambiguous" and (self.profile_id is not None or len(self.alternatives) < 2):
            raise AgentRoutingError("ambiguous routes require at least two alternatives")
        if self.state == "unknown" and (self.profile_id is not None or self.alternatives):
            raise AgentRoutingError("unknown routes cannot select a profile")


class AgentRouter:
    """Resolve ready work using trusted, exact work-type configuration.

    The router never classifies titles, objectives, prompts, or repository text.
    Global candidate lists may intentionally yield ``ambiguous``; a validated
    project/work-type assignment can select one configured candidate. Missing
    or disabled routes yield ``unknown`` and must be handled by a human or an
    explicit trusted configuration change.
    """

    def __init__(
        self,
        *,
        profiles: Sequence[AgentRouteProfile],
        candidates_by_work_type: Mapping[str, Sequence[str]],
        project_work_type_assignments: Mapping[tuple[str, str], str] | None = None,
    ):
        profile_map = {profile.profile_id: profile for profile in profiles}
        if len(profile_map) != len(profiles):
            raise AgentRoutingError("profile IDs must be unique")
        if not profile_map:
            raise AgentRoutingError("at least one route profile is required")

        candidates: dict[str, tuple[str, ...]] = {}
        for work_type, profile_ids in candidates_by_work_type.items():
            if not isinstance(work_type, str) or work_type not in WORK_TYPES:
                raise AgentRoutingError(f"unsupported work type {work_type!r}")
            if isinstance(profile_ids, (str, bytes)):
                raise AgentRoutingError("route candidates must be a sequence of profile IDs")
            ids = tuple(profile_ids)
            if any(not isinstance(profile_id, str) or not profile_id for profile_id in ids):
                raise AgentRoutingError("route candidate IDs must be non-empty strings")
            if len(ids) != len(set(ids)):
                raise AgentRoutingError(f"duplicate route candidates for {work_type!r}")
            for profile_id in ids:
                profile = profile_map.get(profile_id)
                if profile is None:
                    raise AgentRoutingError(f"unknown route profile {profile_id!r}")
                if work_type not in profile.work_types:
                    raise AgentRoutingError(f"profile {profile_id!r} does not support {work_type!r}")
            candidates[work_type] = ids

        assignments = dict(project_work_type_assignments or {})
        for key, profile_id in assignments.items():
            if (
                not isinstance(key, tuple) or len(key) != 2
                or not isinstance(key[0], str) or not key[0].strip()
                or not isinstance(key[1], str) or key[1] not in WORK_TYPES
            ):
                raise AgentRoutingError("project assignments require (project_id, canonical_work_type) keys")
            profile = profile_map.get(profile_id)
            if profile is None or key[1] not in profile.work_types:
                raise AgentRoutingError("project assignment must name a profile supporting that work type")
            if profile_id not in candidates.get(key[1], ()):
                raise AgentRoutingError("project assignment must select a configured candidate")

        self.profiles = MappingProxyType(profile_map)
        self.candidates_by_work_type = MappingProxyType(candidates)
        self.project_work_type_assignments = MappingProxyType(assignments)

    def route(self, work: Mapping[str, object]) -> AgentRouteDecision:
        if not isinstance(work, Mapping):
            raise AgentRoutingError("work must be a canonical work-item mapping")
        project_id, work_id = work.get("projectId"), work.get("workId")
        if not isinstance(project_id, str) or not project_id or not isinstance(work_id, str) or not work_id:
            raise AgentRoutingError("work requires projectId and workId")
        work_type = work.get("workType")
        if not isinstance(work_type, str) or work_type not in WORK_TYPES:
            return AgentRouteDecision("unknown", project_id, work_id, None, reason="missing or unsupported canonical workType")
        if work.get("status") != "ready":
            return AgentRouteDecision("unknown", project_id, work_id, work_type, reason="work is not canonically ready")

        selected = self.project_work_type_assignments.get((project_id, work_type))
        if selected is not None:
            profile = self.profiles[selected]
            if not profile.enabled:
                return AgentRouteDecision("unknown", project_id, work_id, work_type, reason="project-assigned route is disabled")
            return AgentRouteDecision("matched", project_id, work_id, work_type, profile_id=selected, reason="explicit project/work-type assignment")

        available = tuple(
            profile_id for profile_id in self.candidates_by_work_type.get(work_type, ())
            if self.profiles[profile_id].enabled
        )
        if not available:
            return AgentRouteDecision("unknown", project_id, work_id, work_type, reason="no enabled route is configured")
        if len(available) > 1:
            return AgentRouteDecision("ambiguous", project_id, work_id, work_type, alternatives=available, reason="multiple enabled routes require an explicit assignment")
        return AgentRouteDecision("matched", project_id, work_id, work_type, profile_id=available[0], reason="single configured work-type route")

    def profile_for(self, decision: AgentRouteDecision) -> AgentRouteProfile:
        """Resolve a matched profile; never turn an unresolved decision into a default."""
        if decision.state != "matched" or decision.profile_id is None:
            raise AgentRoutingError(f"route is {decision.state}: {decision.reason}")
        expected = self.route({
            "projectId": decision.project_id,
            "workId": decision.work_id,
            "workType": decision.work_type,
            "status": "ready",
        })
        if expected != decision:
            raise AgentRoutingError("route decision is stale or not produced by this router")
        profile = self.profiles.get(decision.profile_id)
        if profile is None or not profile.enabled:
            raise AgentRoutingError("matched route profile is no longer enabled")
        return profile
