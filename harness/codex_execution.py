"""Wire one matched Codex route into the canonical organization coordinator.

This is a trusted execution-factory helper, not a public API or authorization
service. The caller still supplies an invocation policy, isolated workspace,
context builder, and independent canonical verifier.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from .agent_router import AgentRouteDecision, AgentRouter
from .codex_cli import CodexInvocation
from .codex_runtime_bridge import CodexRuntimeBridge
from .organization_runtime import ExecutionContext, ExecutionSignal, WorkExecution


class CodexExecutionFactoryError(ValueError):
    """The routed work cannot be represented as a trusted Codex execution."""


class CodexExecutionFactory:
    def __init__(self, router: AgentRouter, bridge: CodexRuntimeBridge):
        self.router = router
        self.bridge = bridge

    def build(
        self,
        work: Mapping[str, Any],
        *,
        run_id: str,
        correlation_id: str,
        context_hash: str,
        verification_actor: dict[str, str],
        invocation_factory: Callable[[ExecutionContext], CodexInvocation],
        verify: Callable[[ExecutionContext], bool],
        parent_run_id: str | None = None,
    ) -> WorkExecution:
        decision = self.router.route(work)
        profile = self.router.profile_for(decision)
        if profile.adapter_name != "codex-cli":
            raise CodexExecutionFactoryError("matched agent route is not the Codex CLI adapter")
        if profile.endpoint_class != "remote":
            raise CodexExecutionFactoryError("Codex CLI must be declared as a remote endpoint")
        if not isinstance(run_id, str) or not run_id.strip():
            raise CodexExecutionFactoryError("run_id must be non-empty")
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise CodexExecutionFactoryError("correlation_id must be non-empty")
        if not isinstance(context_hash, str) or re.fullmatch(r"[a-f0-9]{64}", context_hash) is None:
            raise CodexExecutionFactoryError("context_hash must be a SHA-256 digest")
        if not callable(invocation_factory) or not callable(verify):
            raise CodexExecutionFactoryError("trusted invocation factory and independent verifier are required")
        if not isinstance(verification_actor, dict) or set(verification_actor) != {"actorType", "actorId", "role"}:
            raise CodexExecutionFactoryError("verification_actor must be a canonical actor")

        project_id, work_id = work.get("projectId"), work.get("workId")
        if not isinstance(project_id, str) or not project_id or not isinstance(work_id, str) or not work_id:
            raise CodexExecutionFactoryError("canonical work requires projectId and workId")
        actor = {
            "actorType": "agent",
            "actorId": profile.agent_id,
            "role": profile.role,
        }
        adapter = {"name": profile.adapter_name, "version": profile.adapter_version}

        async def execute(context: ExecutionContext) -> ExecutionSignal:
            invocation = invocation_factory(context)
            if not isinstance(invocation, CodexInvocation):
                raise CodexExecutionFactoryError("invocation_factory must return a CodexInvocation")
            if (
                invocation.project_id != context.project_id
                or invocation.work_id != context.work_id
                or invocation.run_id != context.run_id
                or invocation.agent_id != profile.agent_id
            ):
                raise CodexExecutionFactoryError("Codex invocation does not match its routed canonical run")
            await self.bridge.run_async(invocation)
            return ExecutionSignal.COMPLETED

        return WorkExecution(
            project_id=project_id,
            work_id=work_id,
            run_id=run_id,
            correlation_id=correlation_id,
            actor=actor,
            adapter=adapter,
            context_hash=context_hash,
            verification_actor=verification_actor,
            execute=execute,
            verify=verify,
            parent_run_id=parent_run_id,
        )
