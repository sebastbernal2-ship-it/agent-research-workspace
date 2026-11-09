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
from .projection import Projector


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
        parent_run_id: str | None = None,
        occurred_at: str | None = None,
        queue_lease: dict[str, Any] | None = None,
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
        lease_identity = {} if queue_lease is None else {
            "queueJobId": queue_lease.get("jobId"),
            "leaseGeneration": queue_lease.get("leaseGeneration"),
        }
        identity = {
            "projectId": project_id,
            "runId": run_id,
            "workId": work_id,
            "agent": agent,
            "adapter": adapter,
            "contextHash": context_hash,
            "capabilityGrantIds": grants,
            **lease_identity,
        }
        if existing is not None:
            if existing.get("parentRunId") != parent_run_id:
                raise ControlPlaneError(f"run id {run_id!r} already exists with a different parent")
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
        if parent_run_id is not None:
            parent = state["agentRuns"].get(parent_run_id)
            if parent is None or parent["status"] != "running":
                raise ControlPlaneError("parent run must exist and be active before its child starts")
            parent_event = next((
                event for event in reversed(self.store.verified_events(project_id))
                if event["eventType"] == "run.started" and event["payload"]["runId"] == parent_run_id
            ), None)
            if parent_event is None or parent_event["correlationId"] != (correlation_id or run_id):
                raise ControlPlaneError("parent and child runs must share the same correlation ID")
        if work["status"] not in {"ready", "running"}:
            raise ControlPlaneError("agent runs require ready or running work")
        active_work_runs = [
            active_id for active_id, active_run in state["agentRuns"].items()
            if active_run["workId"] == work_id and active_run["status"] == "running"
        ]
        if work["status"] == "running" and not active_work_runs:
            raise ControlPlaneError("running work has no active canonical run; recover or explicitly requeue it")
        run_root_id = (
            run_id if parent_run_id is None
            else Projector._run_root_id(parent_run_id, state["agentRuns"])
        )
        conflicting_roots = [
            active_id for active_id in active_work_runs
            if Projector._run_root_id(active_id, state["agentRuns"]) != run_root_id
        ]
        if conflicting_roots:
            raise ControlPlaneError(f"work already belongs to another active run tree: {conflicting_roots}")
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
        if parent_run_id is not None:
            record["parentRunId"] = parent_run_id
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
                **({"parentRunId": parent_run_id} if parent_run_id is not None else {}),
                **lease_identity,
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
        self.store.append_many(
            events, expected_sequence=state["_eventSequence"], queue_lease=queue_lease,
        )
        return self.control.projector.replay(project_id)["agentRuns"][run_id]

    def finish(
        self,
        project_id: str,
        run_id: str,
        *,
        terminal_status: str,
        actor: dict[str, str],
        output_artifact_ids: Iterable[str] | None = None,
        occurred_at: str | None = None,
        reconcile_receipts: bool = True,
    ) -> dict[str, Any]:
        if terminal_status not in _TERMINAL_STATUSES:
            raise ValueError("terminal_status must be succeeded, failed, cancelled, or interrupted")
        supplied_output_ids = None if output_artifact_ids is None else list(output_artifact_ids)
        if reconcile_receipts:
            self.store.reconcile_staged_adapter_receipts(project_id)
        state = self.control.projector.replay(project_id)
        old = state["agentRuns"].get(run_id)
        if old is None:
            raise ControlPlaneError(f"unknown run {run_id!r}")
        if actor != old["agent"]:
            raise ControlPlaneError("only the run's assigned agent can finish it")
        output_event_ids = [
            message["eventId"] for message in state["messages"]
            if message.get("runId") == run_id
        ]
        output_ids = [
            artifact_id for artifact_id, artifact in state["artifacts"].items()
            if artifact.get("runId") == run_id
        ]
        output_evidence_ids = [
            evidence_id for evidence_id, evidence in state["evidence"].items()
            if evidence.get("runId") == run_id
        ]
        if supplied_output_ids is not None and supplied_output_ids != output_ids:
            raise ControlPlaneError("run output artifact IDs must match artifacts registered for this run")
        if old["status"] != "running":
            if (
                old["status"] == terminal_status
                and old.get("outputArtifactIds", []) == output_ids
                and old.get("outputEvidenceIds", []) == output_evidence_ids
                and old.get("outputEventIds", []) == output_event_ids
            ):
                return old
            raise ControlPlaneError(f"run {run_id!r} is already terminal")

        active_children = [
            child_id for child_id, child in state["agentRuns"].items()
            if child.get("parentRunId") == run_id and child["status"] == "running"
        ]
        if active_children:
            raise ControlPlaneError(f"parent run cannot finish while child runs are active: {active_children}")
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
            outputEvidenceIds=output_evidence_ids,
            outputEventIds=output_event_ids,
        )
        self.store.validate_record(finished)
        record_hash = self.store.put_record(finished)
        event = self._event(
            project_id,
            "run.finished",
            actor,
            {
                "runId": run_id,
                "terminalStatus": terminal_status,
                "artifactIds": output_ids,
                "evidenceIds": output_evidence_ids,
                "eventIds": output_event_ids,
                "recordHash": record_hash,
            },
            event_id=f"event.run.finished.{run_id}",
            occurred_at=when,
            correlation_id=start_event["correlationId"],
            causation_id=start_event["eventId"],
        )
        self.store.append(event, expected_sequence=state["_eventSequence"])
        return self.control.projector.replay(project_id)["agentRuns"][run_id]

    def interrupt_active_descendants(
        self, project_id: str, parent_run_id: str, *, occurred_at: str | None = None,
    ) -> list[str]:
        """Close active descendants as interrupted before a cancelled parent is finalized."""
        state = self.control.projector.replay(project_id)
        if parent_run_id not in state["agentRuns"]:
            raise ControlPlaneError(f"unknown parent run {parent_run_id!r}")
        descendants: set[str] = set()
        changed = True
        while changed:
            changed = False
            for run_id, run in state["agentRuns"].items():
                if run.get("parentRunId") == parent_run_id or run.get("parentRunId") in descendants:
                    if run_id not in descendants:
                        descendants.add(run_id)
                        changed = True
        active_ids = [
            run_id for run_id, run in reversed(list(state["agentRuns"].items()))
            if run_id in descendants and run["status"] == "running"
        ]
        for run_id in active_ids:
            self.finish(
                project_id, run_id, terminal_status="interrupted",
                actor=state["agentRuns"][run_id]["agent"], occurred_at=occurred_at,
            )
        return active_ids

    def recover_interrupted(
        self,
        project_id: str,
        *,
        occurred_at: str | None = None,
        recovery_job_ids: Iterable[str] = (),
        attest_no_live_worker: bool = False,
    ) -> list[str]:
        """Import durable receipts and close active runs under explicit queue recovery authorization."""
        authorized_jobs = set(recovery_job_ids)
        self.store.reconcile_staged_adapter_receipts(
            project_id, recovery_job_ids=authorized_jobs,
            attest_no_live_worker=attest_no_live_worker,
        )
        if not authorized_jobs:
            self.store.mark_ambiguous_adapter_invocations(project_id)
        state = self.control.projector.replay(project_id)
        active_ids = [
            run_id for run_id, run in reversed(list(state["agentRuns"].items()))
            if run["status"] == "running"
            and (not authorized_jobs or run.get("queueJobId") in authorized_jobs)
        ]
        recovered: list[str] = []
        for run_id in active_ids:
            self.finish(
                project_id,
                run_id,
                terminal_status="interrupted",
                actor=state["agentRuns"][run_id]["agent"],
                occurred_at=occurred_at,
                reconcile_receipts=False,
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
