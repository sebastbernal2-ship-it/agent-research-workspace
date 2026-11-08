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
            # A worker's project role is stable across phases; phase/message
            # type carries whether that contribution critiques or verifies.
            role = "orchestrator" if actor_kind == "hermes" else "specialist"
            request = {
                "projectId": project_id,
                "workId": work_id,
                "sourceReceiptId": receipt_id,
                "occurredAt": occurred_at,
                "causationId": operation_id,
                "phase": phase,
                "text": text,
                "actor": {
                    "actorType": "agent",
                    "actorId": f"agent.{actor_kind}",
                    "role": role,
                },
                "visibleTo": ["agent.vp", "agent.codex", "agent.hermes"],
                "collaboration": collaboration,
            }
            try:
                event, blob_content = receipt_bridge.canonicalize(
                    request, correlation_id=correlation_id,
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
        }
