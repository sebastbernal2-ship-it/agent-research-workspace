"""Offline Temporal durability probe; not a production worker or security boundary."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from shutil import which
from typing import Any

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from harness.control_plane import ProjectControlPlane
from harness.event_store import SQLiteEventStore
from harness.inbox import InboxApplication, InboxReadModel
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project
from workflow_def import CanonicalApprovalWorkflow

RUN_DIR = Path("/tmp/temporal-canonical-probe")
TEMPORAL_DB = RUN_DIR / "temporal.sqlite"
CANONICAL_DB = RUN_DIR / "canonical.sqlite"
TEMPORAL_ADDRESS = "127.0.0.1:17233"
TEMPORAL_CLI = Path(os.environ.get("TEMPORAL_CLI") or which("temporal") or "temporal")
TASK_QUEUE = "canonical-approval-probe"
PROJECT_ID = "project.temporal-probe"
WORK_ID = "work.temporal-probe"
RUN_ID = "run.temporal-probe"
QUESTION_ID = "question.temporal-probe"
NOW = "2025-01-01T00:00:00Z"
AGENT = {"actorType": "agent", "actorId": "agent.temporal-probe", "role": "worker"}
USER = {"actorType": "human", "actorId": "user.synthetic", "role": "user"}


class ProbePrincipal:
    def current_actor(self) -> dict[str, str]:
        return dict(USER)


class ProbeAccess:
    def require_project_access(self, actor: dict[str, str], project_id: str, action: str) -> None:
        if actor != USER or project_id != PROJECT_ID:
            raise PermissionError("probe access denied")


def _store() -> SQLiteEventStore:
    return SQLiteEventStore(CANONICAL_DB)


@activity.defn
async def read_canonical_answer(_input: dict[str, str]) -> str | None:
    state = Projector(_store()).replay(PROJECT_ID)
    question = state["questions"].get(QUESTION_ID)
    if question is None or question["status"] != "answered":
        return None
    return question["answer"]["text"]


@activity.defn
async def finalize_canonical_run(answer: str) -> str:
    store = _store()
    control = ProjectControlPlane(store, clock=lambda: NOW)
    ledger = RunLedger(store, clock=lambda: NOW)
    state = control.projector.replay(PROJECT_ID)
    question = state["questions"][QUESTION_ID]
    if question["status"] != "answered" or question["answer"]["text"] != answer:
        raise RuntimeError("Temporal result does not match canonical human answer")

    message = next((item for item in state["messages"] if item.get("runId") == RUN_ID), None)
    if message is None:
        content = f"Synthetic task resumed after canonical approval: {answer}"
        content_ref, content_hash = store.put_blob(content)
        start_event = next(
            event for event in store.verified_events(PROJECT_ID)
            if event["eventType"] == "run.started" and event["payload"]["runId"] == RUN_ID
        )
        store.append({
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": f"event.output.{RUN_ID}",
            "projectId": PROJECT_ID,
            "eventType": "discussion.message_added",
            "actor": AGENT,
            "occurredAt": NOW,
            "causationId": start_event["eventId"],
            "correlationId": start_event["correlationId"],
            "idempotencyKey": f"idem.output.{RUN_ID}",
            "payload": {
                "workId": WORK_ID,
                "threadId": "thread.temporal-probe",
                "messageType": "evidence",
                "runId": RUN_ID,
                "contentRef": content_ref,
                "contentHash": content_hash,
                "visibleTo": [AGENT["actorId"], USER["actorId"]],
            },
        })
    elif message["content"] != f"Synthetic task resumed after canonical approval: {answer}":
        raise RuntimeError("run already has conflicting canonical output")

    current_work = control.projector.replay(PROJECT_ID)["workItems"][WORK_ID]
    if current_work["status"] == "ready":
        control.transition_work(
            PROJECT_ID, WORK_ID, "running", actor=AGENT, reason="Resumed after canonical approval.",
            occurred_at=NOW, run_id=RUN_ID, expected_revision=current_work["revision"],
        )
    ledger.finish(PROJECT_ID, RUN_ID, terminal_status="succeeded", actor=AGENT, occurred_at=NOW)
    current_work = control.projector.replay(PROJECT_ID)["workItems"][WORK_ID]
    control.transition_work(
        PROJECT_ID, WORK_ID, "completed", actor=AGENT, reason="Canonical approval probe completed.",
        occurred_at=NOW, run_id=RUN_ID, expected_revision=current_work["revision"],
    )
    return "completed"


async def start_server() -> tuple[Client, asyncio.subprocess.Process]:
    proc = await asyncio.create_subprocess_exec(
        str(TEMPORAL_CLI), "server", "start-dev",
        "--ip", "127.0.0.1", "--ui-ip", "127.0.0.1",
        "--port", "17233", "--ui-port", "18233",
        "--db-filename", str(TEMPORAL_DB), "--headless", "--ui-disable-news-fetch",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        cwd=RUN_DIR,
    )
    for _ in range(100):
        if proc.returncode is not None:
            raise RuntimeError(f"Temporal dev server exited early: {proc.returncode}")
        try:
            return await Client.connect(TEMPORAL_ADDRESS, namespace="default"), proc
        except Exception:
            await asyncio.sleep(0.2)
    proc.send_signal(signal.SIGTERM)
    raise TimeoutError("Temporal dev server did not become ready")


async def stop_server(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=20)
        except TimeoutError:
            proc.kill()
            await proc.wait()


async def run_worker(client: Client, result: asyncio.Future[Any]) -> None:
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CanonicalApprovalWorkflow],
        activities=[read_canonical_answer, finalize_canonical_run],
    )
    task = asyncio.create_task(worker.run())
    try:
        await result
    finally:
        await worker.shutdown()
        await task


def prepare_canonical_project() -> tuple[ProjectControlPlane, InboxApplication]:
    CANONICAL_DB.unlink(missing_ok=True)
    store = SQLiteEventStore(CANONICAL_DB)
    control = seed_synthetic_project(store, project_id=PROJECT_ID, work_id=WORK_ID, occurred_at=NOW)
    ledger = RunLedger(store, clock=lambda: NOW)
    ledger.start(
        PROJECT_ID, WORK_ID, run_id=RUN_ID, agent=AGENT,
        adapter={"name": "temporal-probe", "version": "0.1"},
        context_hash="a" * 64, occurred_at=NOW,
    )
    control.open_question({
        "recordType": "human_question",
        "schemaVersion": "0.1",
        "projectId": PROJECT_ID,
        "questionId": QUESTION_ID,
        "questionType": "decision",
        "priority": "high",
        "blocking": True,
        "requestedBy": AGENT,
        "workIds": [WORK_ID],
        "runId": RUN_ID,
        "questionText": "Approve resuming this synthetic task?",
        "impact": "The synthetic task remains paused until approved.",
        "options": [{"optionId": "option.proceed", "label": "Proceed"}],
        "status": "open",
        "createdAt": NOW,
    })
    inbox = InboxApplication(InboxReadModel(store), control, ProbePrincipal(), ProbeAccess())
    return control, inbox


async def main() -> None:
    if not TEMPORAL_CLI.is_file():
        raise FileNotFoundError(f"Set TEMPORAL_CLI to an installed Temporal CLI; missing {TEMPORAL_CLI}")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    TEMPORAL_DB.unlink(missing_ok=True)
    control, inbox = prepare_canonical_project()
    client1, server1 = await start_server()
    worker_done1 = asyncio.get_running_loop().create_future()
    worker_task1 = asyncio.create_task(run_worker(client1, worker_done1))
    workflow_handle = await client1.start_workflow(
        CanonicalApprovalWorkflow.run,
        {"projectId": PROJECT_ID, "workId": WORK_ID, "runId": RUN_ID, "questionId": QUESTION_ID},
        id="canonical-approval-survives-restart",
        task_queue=TASK_QUEUE,
    )
    await asyncio.sleep(2)
    print("before restart:", (await workflow_handle.describe()).status)
    worker_done1.set_result(None)
    await worker_task1
    await stop_server(server1)

    # Commit the human answer only to the canonical project store. Intentionally
    # do not send a Temporal signal; polling/replay must observe canonical state.
    question = inbox.get_question(PROJECT_ID, QUESTION_ID)
    inbox.answer_question(
        PROJECT_ID, QUESTION_ID, expected_etag=question["etag"],
        text="Proceed with the synthetic task.", selected_option_id="option.proceed", occurred_at=NOW,
    )

    client2, server2 = await start_server()
    worker_done2 = asyncio.get_running_loop().create_future()
    worker_task2 = asyncio.create_task(run_worker(client2, worker_done2))
    resumed = client2.get_workflow_handle("canonical-approval-survives-restart")
    result = await asyncio.wait_for(resumed.result(), timeout=30)
    worker_done2.set_result(None)
    await worker_task2
    await stop_server(server2)

    final = control.projector.replay(PROJECT_ID)
    assert result == "completed"
    assert final["questions"][QUESTION_ID]["status"] == "answered"
    assert final["agentRuns"][RUN_ID]["status"] == "succeeded"
    assert final["workItems"][WORK_ID]["status"] == "completed"
    assert sum(message.get("runId") == RUN_ID for message in final["messages"]) == 1
    assert control.store.verify(PROJECT_ID)
    print("after restart: workflow completed from canonical answer; one run-attributed message; hash chain verified")
    print(f"Temporal SQLite bytes: {TEMPORAL_DB.stat().st_size}; canonical SQLite bytes: {CANONICAL_DB.stat().st_size}")


if __name__ == "__main__":
    asyncio.run(main())
