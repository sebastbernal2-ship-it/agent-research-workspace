"""Canonical agent-run lifecycle commands for the local organization runtime.

This records which agent/adapter ran on which work item. It does not grant
capabilities, execute tools, or treat an adapter's private state as canonical.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, Iterable

from .control_plane import ControlPlaneError, ProjectControlPlane
from .event_store import SQLiteEventStore


_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


class RunLedger:
    """Append-only start/finish records for one project-scoped agent run."""

    def __init__(
        self,
        store: SQLiteEventStore,
        *,
        clock: Callable[[], str] | None = None,
    ):
        self.store = store
        self.control = ProjectControlPlane(store, clock=clock)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def start(
        self,
        project_id: str,
        work_id: str,
        *,
        run_id: str,
        agent: dict[str, str],
        adapter: dict[str, str],
        context_hash: str,
        capability_grant_ids: Iterable[str] = (),
        correlation_id: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(agent, dict) or agent.get("actorType") != "agent":
            raise ValueError("agent run assignment must name an agent actor")
        grants = list(capability_grant_ids)
        if grants:
            raise ControlPlaneError("run ledger does not authorize capability-bearing execution")
        if not isinstance(context_hash, str) or re.fullmatch(r"[a-f0-9]{64}", context_hash) is None:
            raise ValueError("context_hash must be a SHA-256 digest")

        state = self.control.projector.replay(project_id)
        work = state["workItems"].get(work_id)
        if work is None:
            raise ControlPlaneError(f"unknown work item {work_id!r}")
        existing = state["agentRuns"].get(run_id)
        identity = {
            "projectId": project_id,
            "runId": run_id,
            "workId": work_id,
            "agent": agent,
            "adapter": adapter,
            "contextHash": context_hash,
            "capabilityGrantIds": grants,
        }
        if existing is not None:
            start_event = next((
                event for event in reversed(self.store.verified_events(project_id))
                if event["eventType"] == "run.started" and event["payload"]["runId"] == run_id
            ), None)
            correlation_matches = start_event is not None and (
                correlation_id is None or start_event["correlationId"] == correlation_id
            )
            if correlation_matches and all(existing.get(key) == value for key, value in identity.items()):
                return existing
            raise ControlPlaneError(f"run id {run_id!r} already exists with different state")
        if work["status"] not in {"ready", "running"}:
            raise ControlPlaneError("agent runs require ready or running work")
        if work["status"] == "ready":
            incomplete = [
                dependency for dependency in work["dependsOn"]
                if state["workItems"][dependency]["status"] != "completed"
            ]
            if incomplete:
                raise ControlPlaneError(f"work cannot start before dependencies complete: {incomplete}")

        when = occurred_at or self.clock()
        record = {
            "recordType": "agent_run",
            "schemaVersion": "0.1",
            **identity,
            "status": "running",
            "startedAt": when,
        }
        self.store.validate_record(record)
        events: list[dict[str, Any]] = []
        effective_correlation_id = correlation_id or run_id
        if work["status"] == "ready":
            updated_work = deepcopy(work)
            updated_work.update(revision=work["revision"] + 1, status="running", updatedAt=when)
            work_hash = self.store.put_record(updated_work)
            events.append(self._event(
                project_id,
                "work.status_changed",
                agent,
                {
                    "workId": work_id,
                    "from": "ready",
                    "to": "running",
                    "recordHash": work_hash,
                    "reason": f"Started by agent run {run_id}.",
                },
                event_id=f"event.work.status.{work_id}.r{updated_work['revision']}",
                occurred_at=when,
                correlation_id=effective_correlation_id,
            ))
        record_hash = self.store.put_record(record)
        event = self._event(
            project_id,
            "run.started",
            agent,
            {
                "runId": run_id,
                "workId": work_id,
                "adapter": adapter,
                "contextHash": context_hash,
                "capabilityGrantIds": grants,
                "recordHash": record_hash,
            },
            event_id=f"event.run.started.{run_id}",
            occurred_at=when,
            correlation_id=effective_correlation_id,
        )
        events.append(event)
        self.store.append_many(events, expected_sequence=state["_eventSequence"])
        return self.control.projector.replay(project_id)["agentRuns"][run_id]

    def finish(
        self,
        project_id: str,
        run_id: str,
        *,
        terminal_status: str,
        actor: dict[str, str],
        output_artifact_ids: Iterable[str] = (),
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if terminal_status not in _TERMINAL_STATUSES:
            raise ValueError("terminal_status must be succeeded, failed, cancelled, or interrupted")
        output_ids = list(output_artifact_ids)
        state = self.control.projector.replay(project_id)
        old = state["agentRuns"].get(run_id)
        if old is None:
            raise ControlPlaneError(f"unknown run {run_id!r}")
        if actor != old["agent"]:
            raise ControlPlaneError("only the run's assigned agent can finish it")
        if old["status"] != "running":
            if old["status"] == terminal_status and old.get("outputArtifactIds", []) == output_ids:
                return old
            raise ControlPlaneError(f"run {run_id!r} is already terminal")

        start_event = next((
            event for event in reversed(self.store.verified_events(project_id))
            if event["eventType"] == "run.started" and event["payload"]["runId"] == run_id
        ), None)
        if start_event is None:
            raise ControlPlaneError(f"run {run_id!r} has no canonical start event")
        when = occurred_at or self.clock()
        finished = deepcopy(old)
        finished.update(
            status=terminal_status,
            finishedAt=when,
            outputArtifactIds=output_ids,
        )
        self.store.validate_record(finished)
        record_hash = self.store.put_record(finished)
        event = self._event(
            project_id,
            "run.finished",
            actor,
            {"runId": run_id, "terminalStatus": terminal_status, "recordHash": record_hash},
            event_id=f"event.run.finished.{run_id}",
            occurred_at=when,
            correlation_id=start_event["correlationId"],
            causation_id=start_event["eventId"],
        )
        self.store.append(event, expected_sequence=state["_eventSequence"])
        return self.control.projector.replay(project_id)["agentRuns"][run_id]

    def recover_interrupted(self, project_id: str, *, occurred_at: str | None = None) -> list[str]:
        """Close runs left active by a process restart; never retry work implicitly."""
        state = self.control.projector.replay(project_id)
        active_ids = [
            run_id for run_id, run in state["agentRuns"].items()
            if run["status"] == "running"
        ]
        recovered: list[str] = []
        for run_id in active_ids:
            self.finish(
                project_id,
                run_id,
                terminal_status="interrupted",
                actor=state["agentRuns"][run_id]["agent"],
                occurred_at=occurred_at,
            )
            recovered.append(run_id)
        return recovered

    def _event(
        self,
        project_id: str,
        event_type: str,
        actor: dict[str, str],
        payload: dict[str, Any],
        *,
        event_id: str,
        occurred_at: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        if len(event_id) > 120:
            # Event IDs are schema-bounded; a digest retains deterministic identity.
            event_id = f"event.run.{sha256(event_id.encode('utf-8')).hexdigest()}"
        return {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": event_id,
            "projectId": project_id,
            "eventType": event_type,
            "actor": actor,
            "occurredAt": occurred_at,
            "causationId": causation_id,
            "correlationId": correlation_id,
            "idempotencyKey": f"idem.{event_id}",
            "payload": payload,
        }
