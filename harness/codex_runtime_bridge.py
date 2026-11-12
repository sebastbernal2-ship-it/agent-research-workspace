"""Canonical receipt bridge for one already-started Codex agent run.

The caller owns approval, routing, worktree creation, run lifecycle, and
verification. This bridge binds the CLI invocation to an active canonical run,
uses the store's durable receipt journal, and never replays an ambiguous call.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .codex_cli import CodexCliWorker, CodexInvocation, CodexInvocationError
from .event_store import SQLiteEventStore
from .projection import Projector


class CodexRuntimeBridgeError(RuntimeError):
    """Codex request does not match canonical run state or has an ambiguous result."""


class CodexRuntimeBridge:
    def __init__(self, event_store: SQLiteEventStore, worker: CodexCliWorker):
        self.event_store = event_store
        self.worker = worker

    def run(self, invocation: CodexInvocation) -> dict[str, Any]:
        if not isinstance(invocation, CodexInvocation):
            raise TypeError("invocation must be a CodexInvocation")
        state = Projector(self.event_store).replay(invocation.project_id)
        run = state["agentRuns"].get(invocation.run_id)
        if (
            run is None or run["status"] != "running"
            or run["workId"] != invocation.work_id
            or run["agent"].get("actorId") != invocation.agent_id
            or run["adapter"].get("name") != "codex-cli"
        ):
            raise CodexRuntimeBridgeError("request does not match an active canonical Codex run")
        start_event = next((
            event for event in reversed(self.event_store.verified_events(invocation.project_id))
            if event["eventType"] == "run.started"
            and event["payload"].get("runId") == invocation.run_id
        ), None)
        if start_event is None:
            raise CodexRuntimeBridgeError("canonical run has no verified start event")

        # Reject denied or invalid invocations before making a durable dispatch
        # marker. The policy is checked again immediately before subprocess start.
        _executable, _codex_home, workspace = self.worker.authorize(invocation)
        descriptor = {
            "projectId": invocation.project_id,
            "workId": invocation.work_id,
            "runId": invocation.run_id,
            "agentId": invocation.agent_id,
            "modelId": invocation.model_id,
            "sandboxMode": invocation.sandbox_mode,
            "workspacePathHash": hashlib.sha256(str(workspace).encode("utf-8")).hexdigest(),
            "promptHash": hashlib.sha256(invocation.prompt.encode("utf-8")).hexdigest(),
            "approvalRef": invocation.approval_ref,
            "dataScopeRef": invocation.data_scope_ref,
            "providerTermsRef": invocation.provider_terms_ref,
            "budgetRef": invocation.budget_ref,
        }
        request_hash = hashlib.sha256(
            json.dumps(descriptor, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        invocation_id = f"inv.codex.{request_hash}"
        durable = self.event_store.prepare_adapter_invocation(
            invocation_id, invocation.project_id, request_hash,
        )
        if durable["state"] in {"receipt_staged", "imported"}:
            results = self.event_store.commit_adapter_receipt(invocation_id)
            return self._report(invocation_id, results)
        if durable["state"] in {"executing", "ambiguous"}:
            raise CodexRuntimeBridgeError(
                f"Codex invocation {invocation_id} is ambiguous; it will not be replayed automatically"
            )
        dispatch = self.event_store.start_adapter_invocation(invocation_id)
        if not dispatch["started"]:
            raise CodexRuntimeBridgeError(
                f"Codex invocation {invocation_id} is already claimed; inspect its receipt"
            )

        try:
            result = self.worker.invoke(invocation)
        except CodexInvocationError:
            # A CLI failure after dispatch is conservatively ambiguous: the
            # remote request may have reached the provider before local failure.
            raise

        content_ref, content_hash = self.event_store.put_blob(result.text)
        actor = run["agent"]
        visible_to = sorted({"agent.vp", actor["actorId"]})
        work_digest = hashlib.sha256(
            f"{invocation.project_id}\0{invocation.work_id}".encode("utf-8")
        ).hexdigest()[:32]
        suffix = request_hash[:32]
        event = {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": f"event.codex.output.{suffix}",
            "projectId": invocation.project_id,
            "eventType": "discussion.message_added",
            "actor": actor,
            "occurredAt": run["startedAt"],
            "causationId": start_event["eventId"],
            "correlationId": start_event["correlationId"],
            "idempotencyKey": f"idem.codex.output.{suffix}",
            "payload": {
                "workId": invocation.work_id,
                "threadId": f"thread.codex.{work_digest}",
                "messageType": "status",
                "runId": invocation.run_id,
                "contentRef": content_ref,
                "contentHash": content_hash,
                "visibleTo": visible_to,
            },
        }
        self.event_store.stage_adapter_receipt(invocation_id, [event])
        results = self.event_store.commit_adapter_receipt(invocation_id)
        return self._report(invocation_id, results, result.audit_metadata())

    @staticmethod
    def _report(invocation_id: str, results: list[Any], metadata: dict[str, object] | None = None) -> dict[str, Any]:
        return {
            "invocationId": invocation_id,
            "eventIds": [item.event["eventId"] for item in results],
            "auditMetadata": metadata or {},
        }
