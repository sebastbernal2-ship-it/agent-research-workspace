"""Resolve a leased durable-queue descriptor into a trusted Codex execution.

This helper is not a daemon or authorization service. Route profiles and all
callbacks are supplied by trusted startup code; unknown/mismatched queue routes
fail closed before Codex can start.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .codex_cli import CodexInvocation
from .codex_execution import CodexExecutionFactory
from .organization_runtime import ExecutionContext, WorkExecution
from .projection import Projector


class CodexQueueExecutionFactoryError(RuntimeError):
    """A queued descriptor cannot be resolved to its exact trusted Codex route."""


class CodexQueueExecutionFactory:
    def __init__(
        self,
        projector: Projector,
        execution_factory: CodexExecutionFactory,
        *,
        run_id_factory: Callable[[Mapping[str, Any]], str],
        invocation_factory: Callable[[ExecutionContext], CodexInvocation],
        verify: Callable[[ExecutionContext], bool],
    ):
        self.projector = projector
        self.execution_factory = execution_factory
        self.run_id_factory = run_id_factory
        self.invocation_factory = invocation_factory
        self.verify = verify
        if not all(callable(value) for value in (run_id_factory, invocation_factory, verify)):
            raise ValueError("trusted run-ID, invocation, and verification factories are required")

    def __call__(self, job: Mapping[str, Any]) -> WorkExecution:
        if not isinstance(job, Mapping) or not isinstance(job.get("payload"), dict):
            raise CodexQueueExecutionFactoryError("claimed queue job has no trusted descriptor")
        project_id, work_id = job.get("projectId"), job.get("workId")
        generation = job.get("leaseGeneration")
        descriptor = job["payload"]
        if (
            not isinstance(project_id, str) or not project_id
            or not isinstance(work_id, str) or not work_id
            or type(generation) is not int or generation < 1
        ):
            raise CodexQueueExecutionFactoryError("claimed queue job identity/generation is invalid")
        state = self.projector.replay(project_id)
        work = state["workItems"].get(work_id)
        if work is None:
            raise CodexQueueExecutionFactoryError("queued canonical work does not exist")

        router = self.execution_factory.router
        decision = router.route(work)
        profile = router.profile_for(decision)
        if descriptor.get("executionKey") != profile.profile_id:
            raise CodexQueueExecutionFactoryError("queue executionKey does not match the exact routed profile")
        expected_actor = {
            "actorType": "agent", "actorId": profile.agent_id, "role": profile.role,
        }
        expected_adapter = {
            "name": profile.adapter_name, "version": profile.adapter_version,
        }
        if descriptor.get("actor") != expected_actor or descriptor.get("adapter") != expected_adapter:
            raise CodexQueueExecutionFactoryError("queued actor/adapter does not match the exact routed profile")

        run_id = self.run_id_factory(job)
        try:
            execution = self.execution_factory.build(
                work,
                run_id=run_id,
                correlation_id=descriptor["correlationId"],
                context_hash=descriptor["contextHash"],
                verification_actor=descriptor["verificationActor"],
                invocation_factory=self.invocation_factory,
                verify=self.verify,
                parent_run_id=descriptor.get("parentRunId"),
            )
        except Exception as exc:
            raise CodexQueueExecutionFactoryError("trusted Codex execution could not be built") from exc
        if (
            execution.project_id != project_id or execution.work_id != work_id
            or execution.actor != descriptor["actor"]
            or execution.adapter != descriptor["adapter"]
            or execution.context_hash != descriptor["contextHash"]
            or execution.correlation_id != descriptor["correlationId"]
            or execution.parent_run_id != descriptor.get("parentRunId")
        ):
            raise CodexQueueExecutionFactoryError("resolved Codex execution differs from its durable queue descriptor")
        return execution
