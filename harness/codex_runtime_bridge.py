"""Canonical receipt bridge for one already-started Codex agent run.

The caller owns approval, routing, workspace creation, run lifecycle, and
verification. Both sync and async entry points bind the CLI invocation to an
active canonical run and never replay an ambiguous dispatch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .codex_cli import CodexCliWorker, CodexInvocation
from .event_store import SQLiteEventStore
from .projection import Projector


class CodexRuntimeBridgeError(RuntimeError):
    """Codex request does not match an active run or has an ambiguous result."""


@dataclass(frozen=True)
class _PreparedInvocation:
    invocation: CodexInvocation
    run: dict[str, Any]
    start_event: dict[str, Any]
    request_hash: str
    invocation_id: str


class CodexRuntimeBridge:
    def __init__(self, event_store: SQLiteEventStore, worker: CodexCliWorker):
        self.event_store = event_store
        self.worker = worker

    def _prepare(self, invocation: CodexInvocation) -> _PreparedInvocation:
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

        # Reject denied/invalid invocations before writing the durable dispatch
        # marker. The policy is checked again immediately before process start.
        _executable, _codex_home, workspace = self.worker.authorize(invocation)
        descriptor = {
            "projectId": invocation.project_id,
            "workId": invocation.work_id,
            "runId": invocation.run_id,
            "agentId": invocation.agent_id,
            "modelId": invocation.model_id,
            "sandboxMode": invocation.sandbox_mode,
            "skipGitRepoCheck": invocation.skip_git_repo_check,
            "workspaceManifestHash": invocation.workspace_manifest_hash,
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
        return _PreparedInvocation(
            invocation=invocation,
            run=run,
            start_event=start_event,
            request_hash=request_hash,
            invocation_id=f"inv.codex.{request_hash}",
        )

    def _claim_or_reuse(self, prepared: _PreparedInvocation) -> dict[str, Any] | None:
        durable = self.event_store.prepare_adapter_invocation(
            prepared.invocation_id, prepared.invocation.project_id, prepared.request_hash,
        )
        if durable["state"] in {"receipt_staged", "imported"}:
            results = self.event_store.commit_adapter_receipt(prepared.invocation_id)
            return self._report(prepared.invocation_id, results)
        if durable["state"] in {"executing", "ambiguous"}:
            raise CodexRuntimeBridgeError(
                f"Codex invocation {prepared.invocation_id} is ambiguous; it will not be replayed automatically"
            )
        dispatch = self.event_store.start_adapter_invocation(prepared.invocation_id)
        if not dispatch["started"]:
            raise CodexRuntimeBridgeError(
                f"Codex invocation {prepared.invocation_id} is already claimed; inspect its receipt"
            )
        return None

    def _commit_result(self, prepared: _PreparedInvocation, result: Any) -> dict[str, Any]:
        invocation = prepared.invocation
        content_ref, content_hash = self.event_store.put_blob(result.text)
        actor = prepared.run["agent"]
        visible_to = sorted({"agent.vp", actor["actorId"]})
        work_digest = hashlib.sha256(
            f"{invocation.project_id}\0{invocation.work_id}".encode("utf-8")
        ).hexdigest()[:32]
        suffix = prepared.request_hash[:32]
        event = {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": f"event.codex.output.{suffix}",
            "projectId": invocation.project_id,
            "eventType": "discussion.message_added",
            "actor": actor,
            "occurredAt": prepared.run["startedAt"],
            "causationId": prepared.start_event["eventId"],
            "correlationId": prepared.start_event["correlationId"],
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
        self.event_store.stage_adapter_receipt(prepared.invocation_id, [event])
        results = self.event_store.commit_adapter_receipt(prepared.invocation_id)
        return self._report(prepared.invocation_id, results, result.audit_metadata())

    def run(self, invocation: CodexInvocation) -> dict[str, Any]:
        prepared = self._prepare(invocation)
        reused = self._claim_or_reuse(prepared)
        if reused is not None:
            return reused
        result = self.worker.invoke(invocation)
        return self._commit_result(prepared, result)

    async def run_async(self, invocation: CodexInvocation) -> dict[str, Any]:
        prepared = self._prepare(invocation)
        reused = self._claim_or_reuse(prepared)
        if reused is not None:
            return reused
        result = await self.worker.invoke_async(invocation)
        return self._commit_result(prepared, result)

    @staticmethod
    def _report(invocation_id: str, results: list[Any], metadata: dict[str, object] | None = None) -> dict[str, Any]:
        return {
            "invocationId": invocation_id,
            "eventIds": [item.event["eventId"] for item in results],
            "auditMetadata": metadata or {},
        }
