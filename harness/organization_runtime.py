"""Reusable, adapter-agnostic coordinator for one canonical work item.

The coordinator owns work/run transitions and retry decisions. An adapter owns
its runtime protocol and must commit outputs to the canonical store before its
callback returns. Callback registrations are process-local and must be rebuilt
on restart; the event log remains the source of truth.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .control_plane import ControlPlaneError, ProjectControlPlane
from .durable_queue import QueueError, SQLiteWorkQueue
from .event_store import SQLiteEventStore
from .projection import Projector
from .run_ledger import RunLedger


class WorkflowStatus(str, Enum):
    COMPLETED = "completed"
    WAITING_HUMAN = "waiting_human"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERY_REQUIRED = "recovery_required"
    CONTINUATION_REQUIRED = "continuation_required"


class ExecutionSignal(str, Enum):
    COMPLETED = "completed"
    WAITING_HUMAN = "waiting_human"


@dataclass(frozen=True)
class ExecutionContext:
    project_id: str
    work_id: str
    run_id: str
    correlation_id: str
    context_hash: str


@dataclass(frozen=True)
class WorkExecution:
    project_id: str
    work_id: str
    run_id: str
    correlation_id: str
    actor: dict[str, str]
    adapter: dict[str, str]
    context_hash: str
    verification_actor: dict[str, str]
    execute: Callable[[ExecutionContext], Any]
    verify: Callable[[ExecutionContext], Any]
    parent_run_id: str | None = None


@dataclass(frozen=True)
class WorkOutcome:
    status: WorkflowStatus
    work_id: str
    run_id: str
    question_ids: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    detail: str = ""


class OrganizationCoordinator:
    """Run one adapter stage with canonical preflight, lifecycle, and recovery gates."""

    def __init__(
        self,
        store: SQLiteEventStore,
        *,
        control: ProjectControlPlane | None = None,
        ledger: RunLedger | None = None,
    ):
        self.store = store
        self.projector = Projector(store)
        self.control = control or ProjectControlPlane(store)
        self.ledger = ledger or RunLedger(store)

    def recover_project(self, project_id: str, *, occurred_at: str | None = None) -> tuple[str, ...]:
        """Mark abandoned active runs interrupted; never replay adapter work automatically."""
        return tuple(self.ledger.recover_interrupted(project_id, occurred_at=occurred_at))

    async def serve_queue(
        self,
        queue: SQLiteWorkQueue,
        worker_id: str,
        execution_factory: Callable[[dict[str, Any]], WorkExecution],
        stop_event: asyncio.Event,
        *,
        poll_interval: float = 1,
        lease_seconds: float = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Run a supervised local worker loop until asked to stop or an error occurs.

        A stop request prevents another claim but drains any job already claimed;
        callers that need forced termination must cancel the task explicitly and
        then follow the interrupted-run recovery procedure.
        """
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)) or not 0.05 <= poll_interval <= 60:
            raise ValueError("poll_interval must be between 0.05 and 60 seconds")
        while not stop_event.is_set():
            outcome = await self.run_queued(
                queue, worker_id, execution_factory,
                lease_seconds=lease_seconds, clock=clock,
            )
            if outcome is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    pass

    async def run_queued(
        self,
        queue: SQLiteWorkQueue,
        worker_id: str,
        execution_factory: Callable[[dict[str, Any]], WorkExecution],
        *,
        lease_seconds: float = 60,
        clock: Callable[[], float] = time.time,
    ) -> WorkOutcome | None:
        """Claim and execute one durable job using only trusted, re-registered callbacks.

        The factory receives the persisted job (including its lease generation) and
        must create a fresh run ID for this attempt. Callback code itself is never
        deserialized from the queue.
        """
        job = queue.claim_next(worker_id, now=clock(), lease_seconds=lease_seconds)
        if job is None:
            return None
        token = job["leaseToken"]
        generation = job["leaseGeneration"]
        try:
            task = execution_factory(job)
            if not isinstance(task, WorkExecution):
                raise QueueError("execution factory must return a trusted WorkExecution")
            if task.project_id != job["projectId"] or task.work_id != job["workId"]:
                raise QueueError("resolved execution does not match its queued project/work assignment")
            descriptor = job["payload"]
            if (
                task.actor != descriptor["actor"]
                or task.verification_actor != descriptor["verificationActor"]
                or task.adapter != descriptor["adapter"]
                or task.context_hash != descriptor["contextHash"]
                or task.correlation_id != descriptor["correlationId"]
                or task.parent_run_id != descriptor.get("parentRunId")
            ):
                raise QueueError("resolved execution does not match its persisted run descriptor")
            state = self.projector.replay(task.project_id)
            if task.run_id in state["agentRuns"]:
                raise QueueError("queued execution factory must supply a fresh run ID for each lease generation")
        except Exception:
            queue.abandon(
                job["jobId"], worker_id, token, generation, now=clock(),
            )
            raise

        stop_heartbeat = asyncio.Event()
        interval = min(30.0, lease_seconds / 3)

        async def heartbeat() -> None:
            while True:
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
                    return
                except asyncio.TimeoutError:
                    try:
                        queue.renew(
                            job["jobId"], worker_id, token, generation,
                            now=clock(), lease_seconds=lease_seconds,
                        )
                    except Exception:
                        return

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            lease_context = {
                "jobId": job["jobId"], "workerId": worker_id,
                "leaseToken": token, "leaseGeneration": generation,
            }
            with self.store.queued_write_context(lease_context, clock):
                outcome = await self.run_work(
                    task, queue_lease=lease_context, lease_clock=clock,
                )
        except asyncio.CancelledError:
            try:
                queue.fail(job["jobId"], worker_id, token, generation, now=clock())
            except QueueError:
                # Lease loss or partial cancellation requires explicit recovery.
                try:
                    queue.abandon(job["jobId"], worker_id, token, generation, now=clock())
                except QueueError:
                    pass
            raise
        except Exception:
            queue.abandon(job["jobId"], worker_id, token, generation, now=clock())
            raise
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        if outcome.status == WorkflowStatus.COMPLETED:
            queue.complete(job["jobId"], worker_id, token, generation, now=clock())
        elif outcome.status == WorkflowStatus.RECOVERY_REQUIRED:
            queue.abandon(job["jobId"], worker_id, token, generation, now=clock())
        else:
            queue.release(job["jobId"], worker_id, token, generation, now=clock())
        return outcome

    def retry_work(
        self,
        project_id: str,
        work_id: str,
        *,
        actor: dict[str, str],
        reason: str,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly requeue failed/interrupted work after all its runs are terminal."""
        if actor.get("actorType") != "human" or actor.get("role") != "user":
            raise ControlPlaneError("only the user may explicitly requeue work")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError("retry reason must contain 1 to 2000 characters")
        state = self.projector.replay(project_id)
        work = state["workItems"].get(work_id)
        if work is None:
            raise ControlPlaneError(f"unknown work item {work_id!r}")
        open_questions = [
            question["questionId"] for question in state["questions"].values()
            if question["status"] == "open" and question["blocking"] and work_id in question["workIds"]
        ]
        if open_questions:
            raise ControlPlaneError("work cannot be requeued while an inbox question is open")
        active_runs = [
            run_id for run_id, run in state["agentRuns"].items()
            if run["workId"] == work_id and run["status"] == "running"
        ]
        if active_runs:
            raise ControlPlaneError("work cannot be requeued while agent runs are active")
        status = work["status"]
        if status == "ready":
            return work
        if status not in {"failed", "running"}:
            raise ControlPlaneError(f"work in {status!r} state is not eligible for retry")
        if status == "running":
            interrupted_or_failed = any(
                run["workId"] == work_id and run["status"] in {"interrupted", "failed", "cancelled"}
                for run in state["agentRuns"].values()
            )
            if not interrupted_or_failed:
                raise ControlPlaneError("running work has no recorded interrupted/failed run to retry")
            self.control.transition_work(
                project_id, work_id, "failed", actor=actor,
                reason="User authorized recovery of an interrupted work item.", occurred_at=occurred_at,
            )
        return self.control.transition_work(
            project_id, work_id, "ready", actor=actor, reason=reason.strip(), occurred_at=occurred_at,
        )

    async def run_plan(self, tasks: tuple[WorkExecution, ...] | list[WorkExecution]) -> tuple[WorkOutcome, ...]:
        """Run a caller-declared DAG in deterministic ready order; stop at the first gate/error."""
        task_list = tuple(tasks)
        if not task_list:
            return ()
        project_ids = {task.project_id for task in task_list}
        work_ids = [task.work_id for task in task_list]
        if len(project_ids) != 1 or len(set(work_ids)) != len(work_ids):
            raise ValueError("a plan must contain unique work items from exactly one project")
        outcomes: list[WorkOutcome] = []
        while True:
            state = self.projector.replay(task_list[0].project_id)
            pending = [
                task for task in task_list
                if state["workItems"].get(task.work_id, {}).get("status") != "completed"
            ]
            if not pending:
                return tuple(outcomes)
            ready = []
            for task in pending:
                work = state["workItems"].get(task.work_id)
                if work is None:
                    raise ControlPlaneError(f"unknown work item {task.work_id!r}")
                if (
                    work["status"] in {"ready", "running", "verifying"}
                    and all(state["workItems"][dependency]["status"] == "completed" for dependency in work["dependsOn"])
                ):
                    ready.append(task)
            task = ready[0] if ready else pending[0]
            outcome = await self.run_work(task)
            outcomes.append(outcome)
            if outcome.status != WorkflowStatus.COMPLETED:
                return tuple(outcomes)

    async def run_work(
        self,
        task: WorkExecution,
        *,
        queue_lease: dict[str, Any] | None = None,
        lease_clock: Callable[[], float] = time.time,
    ) -> WorkOutcome:
        state = self.projector.replay(task.project_id)
        work = state["workItems"].get(task.work_id)
        if work is None:
            raise ControlPlaneError(f"unknown work item {task.work_id!r}")
        project = state["project"]
        if project is None or project["status"] != "active":
            return WorkOutcome(WorkflowStatus.BLOCKED, task.work_id, task.run_id, detail="Project is not active.")
        questions = [
            question["questionId"] for question in state["questions"].values()
            if question["status"] == "open" and question["blocking"] and task.work_id in question["workIds"]
        ]
        if questions:
            return WorkOutcome(WorkflowStatus.WAITING_HUMAN, task.work_id, task.run_id, tuple(questions))
        if work["status"] == "waiting_human":
            return WorkOutcome(WorkflowStatus.WAITING_HUMAN, task.work_id, task.run_id)
        if work["status"] == "completed":
            return WorkOutcome(WorkflowStatus.COMPLETED, task.work_id, task.run_id, detail="Already complete.")
        if work["status"] == "cancelled":
            return WorkOutcome(WorkflowStatus.CANCELLED, task.work_id, task.run_id)
        if work["status"] == "failed":
            return WorkOutcome(WorkflowStatus.FAILED, task.work_id, task.run_id, detail="Explicit user retry is required.")
        if work["status"] not in {"ready", "running", "verifying"}:
            return WorkOutcome(WorkflowStatus.BLOCKED, task.work_id, task.run_id, detail=f"Work is {work['status']}.")

        incomplete = tuple(
            dependency for dependency in work["dependsOn"]
            if state["workItems"][dependency]["status"] != "completed"
        )
        if incomplete:
            return WorkOutcome(
                WorkflowStatus.BLOCKED, task.work_id, task.run_id,
                blocked_by=incomplete, detail="Dependencies must complete before execution.",
            )

        existing = state["agentRuns"].get(task.run_id)
        should_execute = existing is None
        reconciling_interrupted = False
        if existing is not None:
            if (
                existing["workId"] != task.work_id
                or existing["agent"] != task.actor
                or existing["adapter"] != task.adapter
                or existing["contextHash"] != task.context_hash
            ):
                raise ControlPlaneError("run ID is already assigned to different work, agent, adapter, or context")
            self.ledger.start(
                task.project_id,
                task.work_id,
                run_id=task.run_id,
                agent=task.actor,
                adapter=task.adapter,
                context_hash=task.context_hash,
                correlation_id=task.correlation_id,
                parent_run_id=task.parent_run_id,
            )
            if existing["status"] == "running":
                return WorkOutcome(
                    WorkflowStatus.RECOVERY_REQUIRED, task.work_id, task.run_id,
                    detail="Run is still marked active; recover it before deciding whether to retry.",
                )
            if existing["status"] == "interrupted":
                reconciling_interrupted = True
            elif existing["status"] != "succeeded":
                return WorkOutcome(
                    WorkflowStatus.RECOVERY_REQUIRED, task.work_id, task.run_id,
                    detail=f"Run ended as {existing['status']}; explicit requeue and a fresh run ID are required.",
                )
            if work["status"] == "ready":
                return WorkOutcome(
                    WorkflowStatus.CONTINUATION_REQUIRED, task.work_id, task.run_id,
                    detail="The prior run ended before the human gate; continue under a fresh run ID.",
                )
            questions_from_run = [
                question["questionId"] for question in state["questions"].values()
                if question.get("runId") == task.run_id
            ]
            open_run_questions = tuple(
                question_id for question_id in questions_from_run
                if state["questions"][question_id]["status"] == "open"
            )
            if open_run_questions:
                return WorkOutcome(
                    WorkflowStatus.WAITING_HUMAN, task.work_id, task.run_id, open_run_questions,
                )
            if any(state["questions"][question_id]["status"] != "open" for question_id in questions_from_run):
                return WorkOutcome(
                    WorkflowStatus.CONTINUATION_REQUIRED, task.work_id, task.run_id,
                    detail="The human question was resolved; continue under a fresh run ID.",
                )

        if work["status"] == "running" and existing is None:
            return WorkOutcome(
                WorkflowStatus.RECOVERY_REQUIRED, task.work_id, task.run_id,
                detail="Work is running without this canonical run; recover or explicitly requeue it first.",
            )
        other_active_runs = [
            run_id for run_id, run in state["agentRuns"].items()
            if run["workId"] == task.work_id and run["status"] == "running" and run_id != task.run_id
        ]
        if other_active_runs:
            return WorkOutcome(
                WorkflowStatus.RECOVERY_REQUIRED, task.work_id, task.run_id,
                detail="Another run is active for this work item.",
            )

        context = ExecutionContext(
            task.project_id, task.work_id, task.run_id, task.correlation_id, task.context_hash,
        )
        if should_execute:
            self.ledger.start(
                task.project_id,
                task.work_id,
                run_id=task.run_id,
                agent=task.actor,
                adapter=task.adapter,
                context_hash=task.context_hash,
                correlation_id=task.correlation_id,
                parent_run_id=task.parent_run_id,
                queue_lease=None if queue_lease is None else {
                    **queue_lease, "now": lease_clock(),
                },
            )
            try:
                signal = await self._invoke(task.execute, context)
                if not isinstance(signal, ExecutionSignal):
                    raise TypeError("adapter must return an ExecutionSignal")
            except asyncio.CancelledError:
                self.ledger.interrupt_active_descendants(task.project_id, task.run_id)
                self.ledger.finish(
                    task.project_id, task.run_id, terminal_status="cancelled",
                    actor=task.actor,
                )
                current = self.projector.replay(task.project_id)["workItems"][task.work_id]
                if current["status"] == "running":
                    self.control.transition_work(
                        task.project_id, task.work_id, "cancelled", actor=task.actor,
                        reason="Adapter execution was cancelled.", run_id=task.run_id,
                    )
                raise
            except Exception as exc:
                self.ledger.interrupt_active_descendants(task.project_id, task.run_id)
                self.ledger.finish(
                    task.project_id, task.run_id, terminal_status="failed",
                    actor=task.actor,
                )
                self._fail_running_work(task, "Adapter execution failed.")
                return WorkOutcome(
                    WorkflowStatus.FAILED, task.work_id, task.run_id,
                    detail=f"Adapter failure: {type(exc).__name__}.",
                )
            if signal == ExecutionSignal.WAITING_HUMAN:
                fresh = self.projector.replay(task.project_id)
                linked_questions = tuple(
                    question["questionId"] for question in fresh["questions"].values()
                    if question["status"] == "open" and task.work_id in question["workIds"]
                )
                if not linked_questions:
                    self.ledger.interrupt_active_descendants(task.project_id, task.run_id)
                    self.ledger.finish(
                        task.project_id, task.run_id, terminal_status="failed", actor=task.actor,
                    )
                    self._fail_running_work(task, "Adapter requested input without opening a canonical question.")
                    return WorkOutcome(
                        WorkflowStatus.FAILED, task.work_id, task.run_id,
                        detail="Adapter requested human input without a canonical inbox question.",
                    )
                self.ledger.interrupt_active_descendants(task.project_id, task.run_id)
                self.ledger.finish(
                    task.project_id, task.run_id, terminal_status="succeeded", actor=task.actor,
                )
                return WorkOutcome(
                    WorkflowStatus.WAITING_HUMAN, task.work_id, task.run_id, linked_questions,
                )
            unfinished_children = self.ledger.interrupt_active_descendants(
                task.project_id, task.run_id,
            )
            if unfinished_children:
                self.ledger.finish(
                    task.project_id, task.run_id, terminal_status="failed", actor=task.actor,
                )
                self._fail_running_work(task, "Adapter returned with active delegated runs.")
                return WorkOutcome(
                    WorkflowStatus.FAILED, task.work_id, task.run_id,
                    detail="Adapter returned while delegated runs were still active; they were interrupted.",
                )
            self.ledger.finish(
                task.project_id, task.run_id, terminal_status="succeeded", actor=task.actor,
            )

        current_state = self.projector.replay(task.project_id)
        current_work = current_state["workItems"][task.work_id]
        open_questions = tuple(
            question["questionId"] for question in current_state["questions"].values()
            if question["status"] == "open" and question["blocking"] and task.work_id in question["workIds"]
        )
        if current_work["status"] == "waiting_human" or open_questions:
            return WorkOutcome(WorkflowStatus.WAITING_HUMAN, task.work_id, task.run_id, open_questions)
        if current_work["status"] == "completed":
            return WorkOutcome(WorkflowStatus.COMPLETED, task.work_id, task.run_id, detail="Already complete.")
        if current_work["status"] == "running":
            self.control.transition_work(
                task.project_id, task.work_id, "verifying", actor=task.verification_actor,
                reason="Adapter output is canonical; begin the configured verification gate.",
                run_id=task.run_id,
            )
        elif current_work["status"] != "verifying":
            return WorkOutcome(
                WorkflowStatus.BLOCKED, task.work_id, task.run_id,
                detail=f"Work changed to {current_work['status']} during execution.",
            )

        try:
            verified = await self._invoke(task.verify, context)
            if not isinstance(verified, bool):
                raise TypeError("verification callback must return bool")
        except asyncio.CancelledError:
            current = self.projector.replay(task.project_id)["workItems"][task.work_id]
            if current["status"] == "verifying":
                self.control.transition_work(
                    task.project_id, task.work_id, "cancelled", actor=task.verification_actor,
                    reason="Verification was cancelled after adapter output was persisted.",
                    run_id=task.run_id,
                )
            raise
        except Exception as exc:
            self._fail_verifying_work(task, "Verification callback failed.")
            return WorkOutcome(
                WorkflowStatus.FAILED, task.work_id, task.run_id,
                detail=f"Verification failure: {type(exc).__name__}.",
            )
        if not verified:
            self._fail_verifying_work(task, "Configured verification gate did not pass.")
            return WorkOutcome(
                WorkflowStatus.FAILED, task.work_id, task.run_id,
                detail="Configured verification gate did not pass.",
            )
        self.control.transition_work(
            task.project_id, task.work_id, "completed", actor=task.verification_actor,
            reason="Configured verification gate passed.", run_id=task.run_id,
        )
        return WorkOutcome(
            WorkflowStatus.COMPLETED, task.work_id, task.run_id,
            detail="Interrupted run reconciled from canonical outputs." if reconciling_interrupted else "",
        )

    def _fail_running_work(self, task: WorkExecution, reason: str) -> None:
        work = self.projector.replay(task.project_id)["workItems"][task.work_id]
        if work["status"] == "running":
            self.control.transition_work(
                task.project_id, task.work_id, "failed", actor=task.verification_actor,
                reason=reason, run_id=task.run_id,
            )

    def _fail_verifying_work(self, task: WorkExecution, reason: str) -> None:
        work = self.projector.replay(task.project_id)["workItems"][task.work_id]
        if work["status"] == "verifying":
            self.control.transition_work(
                task.project_id, task.work_id, "failed", actor=task.verification_actor,
                reason=reason, run_id=task.run_id,
            )

    @staticmethod
    async def _invoke(callback: Callable[[ExecutionContext], Any], context: ExecutionContext) -> Any:
        result = callback(context)
        if inspect.isawaitable(result):
            return await result
        return result
