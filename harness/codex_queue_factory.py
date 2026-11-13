"""Resolve a leased durable-queue descriptor into a trusted Codex execution.

This helper is not a daemon or authorization service. Route profiles and all
callbacks are supplied by trusted startup code; unknown/mismatched queue routes
fail closed before Codex can start.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .agent_router import AgentRoutingError
from .codex_cli import CodexInvocation
from .codex_execution import CodexExecutionFactory
from .durable_queue import SQLiteWorkQueue
from .execution_context import ExecutionContextError, execution_context_hash
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
        verification_actor_factory: Callable[[Mapping[str, Any]], dict[str, str]],
        verify: Callable[[ExecutionContext], bool],
    ):
        self.projector = projector
        self.execution_factory = execution_factory
        self.run_id_factory = run_id_factory
        self.invocation_factory = invocation_factory
        self.verification_actor_factory = verification_actor_factory
        self.verify = verify
        if not all(callable(value) for value in (run_id_factory, invocation_factory, verification_actor_factory, verify)):
            raise ValueError("trusted run-ID, invocation, verifier-actor, and verification factories are required")

    def _verification_actor(self, work: Mapping[str, Any]) -> dict[str, str]:
        actor = self.verification_actor_factory(work)
        if (
            not isinstance(actor, dict)
            or set(actor) != {"actorType", "actorId", "role"}
            or any(not isinstance(value, str) or not value.strip() for value in actor.values())
        ):
            raise CodexQueueExecutionFactoryError("trusted verifier factory returned a noncanonical actor")
        return dict(actor)

    def build_descriptor(
        self,
        project_id: str,
        work_id: str,
        *,
        correlation_id: str,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Prepare a versioned queue descriptor from current canonical state.

        This performs deterministic route and readiness checks only. Enqueueing
        remains an explicit caller action; this method cannot authorize a model
        call, start a worker, or grant capabilities.
        """
        if not isinstance(project_id, str) or not project_id or not isinstance(work_id, str) or not work_id:
            raise CodexQueueExecutionFactoryError("project_id and work_id must be non-empty")
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise CodexQueueExecutionFactoryError("correlation_id must be non-empty")
        if parent_run_id is not None and (not isinstance(parent_run_id, str) or not parent_run_id.strip()):
            raise CodexQueueExecutionFactoryError("parent_run_id must be non-empty when supplied")
        state = self.projector.replay(project_id)
        work = state["workItems"].get(work_id)
        if work is None:
            raise CodexQueueExecutionFactoryError("canonical work does not exist")
        try:
            profile = self.execution_factory.router.profile_for(self.execution_factory.router.route(work))
            if profile.adapter_name != "codex-cli" or profile.endpoint_class != "remote":
                raise CodexQueueExecutionFactoryError("matched route is not a remote Codex CLI profile")
            verification_actor = self._verification_actor(work)
            context_hash = execution_context_hash(state, work_id, profile, verification_actor)
        except (AgentRoutingError, ExecutionContextError) as exc:
            raise CodexQueueExecutionFactoryError(str(exc)) from exc
        actor = {"actorType": "agent", "actorId": profile.agent_id, "role": profile.role}
        descriptor: dict[str, Any] = {
            "descriptorVersion": 1,
            "executionKey": profile.profile_id,
            "actor": actor,
            "verificationActor": dict(verification_actor),
            "adapter": {"name": profile.adapter_name, "version": profile.adapter_version},
            "contextHash": context_hash,
            "correlationId": correlation_id,
        }
        if parent_run_id is not None:
            descriptor["parentRunId"] = parent_run_id
        return descriptor

    def enqueue(
        self,
        queue: SQLiteWorkQueue,
        job_id: str,
        project_id: str,
        work_id: str,
        *,
        correlation_id: str,
        now: float,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an exact route descriptor for already-ready canonical work.

        A ready work status is necessary but not sufficient authorization for
        real execution. The caller remains responsible for the trusted project
        authorization, approval, data, budget, and isolation policy.
        """
        descriptor = self.build_descriptor(
            project_id, work_id, correlation_id=correlation_id,
            parent_run_id=parent_run_id,
        )
        return queue.enqueue(job_id, project_id, work_id, descriptor, now=now)

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
        expected_verifier = self._verification_actor(work)
        if descriptor.get("actor") != expected_actor or descriptor.get("adapter") != expected_adapter:
            raise CodexQueueExecutionFactoryError("queued actor/adapter does not match the exact routed profile")
        if descriptor.get("verificationActor") != expected_verifier:
            raise CodexQueueExecutionFactoryError("queued verificationActor does not match trusted verifier configuration")
        try:
            expected_context_hash = execution_context_hash(state, work_id, profile, expected_verifier)
        except ExecutionContextError as exc:
            raise CodexQueueExecutionFactoryError(str(exc)) from exc
        if descriptor.get("contextHash") != expected_context_hash:
            raise CodexQueueExecutionFactoryError("queued contextHash does not match current canonical project/work inputs")

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
