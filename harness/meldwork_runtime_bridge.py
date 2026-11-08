"""Run the pinned Meldwork V4 pipeline with deterministic AgenticX workers.

This is an offline integration adapter. It uses the real LocalWorkspace V4
orchestrator and AgenticX AgentExecutor, but every provider response is fixed
and the repository's receipt parser/sanitizer remains the canonical ingress.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .bounded_process import BoundedProcessOutputLimit, BoundedProcessTimeout, run_bounded_subprocess
from .event_store import AppendResult, SQLiteEventStore
from .projection import Projector
from .meldwork_bridge import MeldworkBridgeError, MeldworkReceiptBridge


RUNTIME_SCRIPT = Path(__file__).with_name("meldwork_runtime_worker.cjs")


class MeldworkRuntimeError(RuntimeError):
    pass


class MeldworkAgenticXRuntimeBridge:
    """Compose Meldwork V4 orchestration, AgenticX mock workers, and canonical storage."""

    def __init__(
        self,
        event_store: SQLiteEventStore,
        *,
        node: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.event_store = event_store
        self.node = node or shutil.which("node") or ""
        self.timeout = timeout
        if not self.node:
            raise MeldworkRuntimeError("Node.js executable not found")

    def run(
        self,
        *,
        project_id: str,
        work_id: str,
        correlation_id: str | None = None,
        run_ids_by_agent: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="meldwork-runtime-bridge-") as home:
            env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
            try:
                result = run_bounded_subprocess(
                    [self.node, str(RUNTIME_SCRIPT)],
                    input_bytes=b"",
                    cwd=home,
                    env=env,
                    timeout=self.timeout,
                    stdout_limit=2_000_000,
                    stderr_limit=16_000,
                )
            except BoundedProcessTimeout as exc:
                raise MeldworkRuntimeError("Meldwork/AgenticX runtime timed out") from exc
            except BoundedProcessOutputLimit as exc:
                raise MeldworkRuntimeError("Meldwork/AgenticX runtime exceeded its output limit") from exc
        if result.returncode != 0:
            stderr = result.stderr[-4000:].decode("utf-8", errors="replace").strip()
            raise MeldworkRuntimeError(
                f"Meldwork/AgenticX runtime failed: {stderr}"
            )
        try:
            response = json.loads(result.stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MeldworkRuntimeError("Meldwork/AgenticX runtime returned malformed JSON") from exc
        if response.get("orchestrationPhase") != "completed":
            raise MeldworkRuntimeError("Meldwork V4 orchestration did not complete")
        invocations = response.get("invocations")
        if not isinstance(invocations, list) or not 1 <= len(invocations) <= 64:
            raise MeldworkRuntimeError("Meldwork/AgenticX runtime returned an invalid invocation list")

        receipt_bridge = MeldworkReceiptBridge(
            self.event_store,
            node=self.node,
            timeout=min(self.timeout, 10.0),
        )
        if run_ids_by_agent is not None and not isinstance(run_ids_by_agent, dict):
            raise MeldworkRuntimeError("run_ids_by_agent must be a worker-to-run mapping")
        run_metadata: dict[str, dict[str, Any]] = {}
        effective_correlation_id = correlation_id
        if run_ids_by_agent is not None:
            if (
                not run_ids_by_agent
                or set(run_ids_by_agent) - {"codex", "hermes"}
                or any(not isinstance(run_id, str) or not run_id for run_id in run_ids_by_agent.values())
                or len(set(run_ids_by_agent.values())) != len(run_ids_by_agent)
            ):
                raise MeldworkRuntimeError("run_ids_by_agent must map known workers to unique non-empty run IDs")
            state = Projector(self.event_store).replay(project_id)
            start_events = {
                event["payload"]["runId"]: event
                for event in self.event_store.verified_events(project_id)
                if event["eventType"] == "run.started"
            }
            correlations = set()
            for worker_kind, run_id in run_ids_by_agent.items():
                run = state["agentRuns"].get(run_id)
                start_event = start_events.get(run_id)
                if (
                    run is None or run["status"] != "running" or run["workId"] != work_id
                    or run["agent"].get("actorId") != f"agent.{worker_kind}" or start_event is None
                ):
                    raise MeldworkRuntimeError(f"worker {worker_kind!r} is not assigned an active canonical run")
                run_metadata[worker_kind] = {
                    "runId": run_id,
                    "startedAt": run["startedAt"],
                    "startEventId": start_event["eventId"],
                }
                correlations.add(start_event["correlationId"])
            if len(correlations) != 1:
                raise MeldworkRuntimeError("Meldwork worker runs must share a correlation ID")
            run_correlation_id = next(iter(correlations))
            if correlation_id is not None and correlation_id != run_correlation_id:
                raise MeldworkRuntimeError("Meldwork correlation ID does not match canonical worker runs")
            effective_correlation_id = run_correlation_id
        invocation_counts: dict[tuple[str, str], int] = {}
        events: list[dict[str, Any]] = []
        for invocation in invocations:
            try:
                phase = invocation["phase"]
                actor_kind = invocation["agentKind"]
                receipt_id = invocation["sourceReceiptId"]
                text = invocation["text"]
                collaboration = invocation["collaboration"]
                occurred_at = invocation["occurredAt"]
                operation_id = invocation["operationId"]
            except (KeyError, TypeError) as exc:
                raise MeldworkRuntimeError("Meldwork runtime invocation is incomplete") from exc
            if actor_kind not in {"codex", "hermes"}:
                raise MeldworkRuntimeError("Meldwork runtime returned an unknown AgenticX worker")
            if run_ids_by_agent is not None and actor_kind not in run_ids_by_agent:
                raise MeldworkRuntimeError(f"Meldwork worker {actor_kind!r} has no canonical run assignment")
            # A worker's project role is stable across phases; phase/message
            # type carries whether that contribution critiques or verifies.
            role = "orchestrator" if actor_kind == "hermes" else "specialist"
            if run_ids_by_agent is None:
                canonical_receipt_id = receipt_id
                canonical_occurred_at = occurred_at
                canonical_causation_id = operation_id
                canonical_run_id = None
            else:
                run_info = run_metadata[actor_kind]
                count_key = (actor_kind, phase)
                invocation_counts[count_key] = invocation_counts.get(count_key, 0) + 1
                canonical_run_id = run_info["runId"]
                canonical_receipt_id = (
                    f"{canonical_run_id}.{actor_kind}.{phase}.{invocation_counts[count_key]}"
                )
                canonical_occurred_at = run_info["startedAt"]
                canonical_causation_id = run_info["startEventId"]
            request = {
                "projectId": project_id,
                "workId": work_id,
                "sourceReceiptId": canonical_receipt_id,
                "occurredAt": canonical_occurred_at,
                "causationId": canonical_causation_id,
                "phase": phase,
                "text": text,
                "actor": {
                    "actorType": "agent",
                    "actorId": f"agent.{actor_kind}",
                    "role": role,
                },
                "visibleTo": ["agent.vp", "agent.codex", "agent.hermes"],
                "collaboration": collaboration,
                **({"runId": canonical_run_id} if canonical_run_id is not None else {}),
            }
            try:
                event, blob_content = receipt_bridge.canonicalize(
                    request, correlation_id=effective_correlation_id,
                )
                content_ref, content_hash = self.event_store.put_blob(blob_content)
                if (content_ref, content_hash) != (
                    event["payload"]["contentRef"], event["payload"]["contentHash"]
                ):
                    raise MeldworkRuntimeError("V4 adapter blob digest did not match its event")
                events.append(event)
            except MeldworkBridgeError as exc:
                raise MeldworkRuntimeError(
                    f"V4 receipt rejected for {phase}/{actor_kind}: {exc}"
                ) from exc
        results: list[AppendResult] = self.event_store.append_many(events)
        return {
            "runId": response.get("runId"),
            "taskId": response.get("taskId"),
            "orchestrationPhase": response["orchestrationPhase"],
            "agentMessages": response.get("messages", []),
            "invocationCount": len(invocations),
            "eventSequences": [result.sequence for result in results],
            "eventIds": [result.event["eventId"] for result in results],
            "duplicateEvents": [result.duplicate for result in results],
        }
