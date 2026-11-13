"""Deterministic whole-project dispatch preflight; no LLM or queue side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Any

from .agent_router import AgentRouteDecision, AgentRouter, AgentRoutingError
from .execution_context import ExecutionContextError, execution_context_hash
from .projection import Projector


MAX_PROJECT_WORK_ITEMS = 2_000


@dataclass(frozen=True)
class WorkDispatchPlan:
    work_id: str
    revision: int
    status: str
    route_state: str
    profile_id: str | None
    alternatives: tuple[str, ...]
    blockers: tuple[str, ...]
    context_hash: str | None

    @property
    def preflight_passed(self) -> bool:
        return self.route_state == "matched" and self.profile_id is not None and not self.blockers


@dataclass(frozen=True)
class ProjectDispatchPlan:
    project_id: str
    project_revision: int
    event_sequence: int
    items: tuple[WorkDispatchPlan, ...]


class DispatchPlannerError(ValueError):
    """Canonical state or trusted route configuration cannot be preflighted."""


class DispatchPlanner:
    """Report route/readiness decisions from one verified canonical snapshot.

    A passed preflight is not permission to run. Callers still need an
    authorized approval, provider/data/budget policy, capability policy, and
    worker isolation before enqueueing or dispatching anything.
    """

    def __init__(
        self,
        projector: Projector,
        router: AgentRouter,
        verification_actor_factory: Callable[[Mapping[str, Any]], dict[str, str]],
        *,
        max_items: int = MAX_PROJECT_WORK_ITEMS,
    ):
        if not callable(verification_actor_factory):
            raise DispatchPlannerError("trusted verification_actor_factory is required")
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= MAX_PROJECT_WORK_ITEMS:
            raise DispatchPlannerError(f"max_items must be between 1 and {MAX_PROJECT_WORK_ITEMS}")
        self.projector = projector
        self.router = router
        self.verification_actor_factory = verification_actor_factory
        self.max_items = max_items

    def plan(self, project_id: str) -> ProjectDispatchPlan:
        if not isinstance(project_id, str) or not project_id.strip():
            raise DispatchPlannerError("project_id must be non-empty")
        state = self.projector.replay(project_id)
        project = state.get("project")
        work_items = state.get("workItems")
        if not isinstance(project, dict) or not isinstance(work_items, dict):
            raise DispatchPlannerError("canonical project state is unavailable")
        if len(work_items) > self.max_items:
            raise DispatchPlannerError("project work-item count exceeds the dispatch preflight limit")

        planned: list[WorkDispatchPlan] = []
        for work_id, work in sorted(work_items.items()):
            try:
                decision: AgentRouteDecision = self.router.route(work)
            except AgentRoutingError as exc:
                raise DispatchPlannerError(f"route preflight failed for {work_id!r}") from exc
            blockers: list[str] = []
            context_hash = None
            profile_id = decision.profile_id
            if decision.state != "matched":
                blockers.append(decision.reason or f"route is {decision.state}")
            else:
                try:
                    profile = self.router.profile_for(decision)
                    verifier = self.verification_actor_factory(work)
                    context_hash = execution_context_hash(state, work_id, profile, verifier)
                except (AgentRoutingError, ExecutionContextError, ValueError, TypeError) as exc:
                    blockers.append(str(exc))
                    profile_id = decision.profile_id
            planned.append(WorkDispatchPlan(
                work_id=work_id,
                revision=work["revision"],
                status=work["status"],
                route_state=decision.state,
                profile_id=profile_id,
                alternatives=decision.alternatives,
                blockers=tuple(blockers),
                context_hash=context_hash,
            ))
        return ProjectDispatchPlan(
            project_id=project_id,
            project_revision=project["revision"],
            event_sequence=state["_eventSequence"],
            items=tuple(planned),
        )
