"""Disposable, scripted NAC-to-canonical-work adapter probe.

This is an evaluation spike, not a production adapter. It starts a loopback-only
NAC process with a separate workspace/store, sends one deterministic scripted
Responses request, and imports the returned text as a canonical run-attributed
discussion message. The model fixture never requests a tool call.

Run against a separately built complete NAC checkout:
  NAC_WEB_BIN=/path/to/nac-web python3 spikes/nac-harness/adapter_probe.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from harness.event_store import SQLiteEventStore
from harness.organization_runtime import (
    ExecutionContext,
    ExecutionSignal,
    OrganizationCoordinator,
    WorkExecution,
    WorkflowStatus,
)
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project

PROJECT_ID = "project.nac-adapter-probe"
WORK_ID = "work.nac-adapter-probe"
RUN_ID = "run.nac-adapter-probe"
CORRELATION_ID = "correlation.nac-adapter-probe"
ACTOR = {"actorType": "agent", "actorId": "agent.nac-worker", "role": "specialist"}
VERIFIER = {"actorType": "agent", "actorId": "agent.nac-verifier", "role": "reviewer"}
ADAPTER = {"name": "nac-http-probe", "version": "0.1"}
TOKEN = "NAC_ADAPTER_PROBE_SYNTHETIC_ONLY"
ANSWER = "Deterministic NAC adapter probe completed; no native tool was invoked."


class ScriptedResponses(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[dict[str, Any]] = []
    lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/models-dev":
            self.send_response(304)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/models":
            self._json(200, {"object": "list", "data": [{"id": "gpt-5.6-sol", "object": "model"}]})
            return
        self._json(404, {"error": "unexpected fixture route"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/responses":
            self._json(404, {"error": "unexpected fixture route"})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "malformed JSON"})
            return
        with self.lock:
            self.requests.append({"headers": dict(self.headers), "body": body})
        item = {
            "id": "msg-nac-adapter-probe",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": ANSWER, "annotations": []}],
        }
        envelope = {
            "id": "response-nac-adapter-probe",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.6-sol",
            "output": [item],
            "usage": {"input_tokens": 7, "output_tokens": 8, "total_tokens": 15},
        }
        if isinstance(body, dict) and body.get("stream") is True:
            events = [
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": envelope},
            ]
            payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._json(200, envelope)

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def request_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def wait_for_server(process: subprocess.Popen[str], lines: queue.Queue[str]) -> str:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"nac-web exited before readiness (status {process.returncode})")
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            continue
        match = re.search(r"nac-web listening on (http://\S+)", line)
        if match:
            base_url = match.group(1).rstrip("/")
            for _ in range(40):
                try:
                    status, _ = request_json(f"{base_url}/health")
                    if status == 200:
                        return base_url
                except (OSError, TimeoutError, urllib.error.URLError):
                    time.sleep(0.05)
            raise RuntimeError("nac-web announced readiness but health did not become available")
    raise TimeoutError("nac-web did not announce readiness within 15 seconds")


def read_messages(base_url: str, session_id: str) -> list[dict[str, Any]]:
    status, page = request_json(f"{base_url}/sessions/{session_id}/messages?limit=100")
    if status != 200 or not isinstance(page, dict) or not isinstance(page.get("messages"), list):
        raise RuntimeError("NAC transcript endpoint returned an invalid response")
    return page["messages"]


async def run_probe(nac_binary: Path) -> dict[str, Any]:
    ScriptedResponses.requests = []
    provider = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedResponses)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    provider_url = f"http://127.0.0.1:{provider.server_port}"
    process: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="nac-adapter-probe-") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        for path in (workspace, root / "home", root / "nac-home", root / "xdg", root / "tmp"):
            path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(workspace)], check=True)
        subprocess.run(
            ["git", "-C", str(workspace), "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
             "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "baseline"],
            check=True,
        )
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root / "home"),
            "NAC_HOME": str(root / "nac-home"),
            "XDG_CONFIG_HOME": str(root / "xdg"),
            "TMPDIR": str(root / "tmp"),
            "LANG": "C.UTF-8",
            "BROWSER": "none",
            "MODELS_DEV_URL": f"{provider_url}/models-dev",
            "NAC_ADAPTER_API_KEY": "nac-adapter-dummy-only",
        }
        lines: queue.Queue[str] = queue.Queue()
        process = subprocess.Popen(
            [str(nac_binary), "--bind", "127.0.0.1:0", "--directory", str(workspace),
             "--store-path", str(root / "nac.sqlite3"), "--no-open"],
            cwd=workspace,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
        reader.start()
        try:
            base_url = wait_for_server(process, lines)
            status, created = request_json(
                f"{base_url}/sessions",
                method="POST",
                body={
                    "behavior": "direct",
                    "cwd": str(workspace),
                    "backend": "openai-responses",
                    "model": "gpt-5.6-sol",
                    "base_url": provider_url,
                    "reasoning_effort": "high",
                    "api_key_env": "NAC_ADAPTER_API_KEY",
                    "extra_headers": {},
                    "orchestrator_compaction_threshold": 0,
                },
            )
            if status != 201 or not isinstance(created, dict):
                raise RuntimeError(f"NAC session creation failed with HTTP {status}")
            session_id = created.get("metadata", {}).get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("NAC did not return a session ID")

            store = SQLiteEventStore(root / "canonical.sqlite3")
            control = seed_synthetic_project(store, project_id=PROJECT_ID, work_id=WORK_ID)
            ledger = RunLedger(store)
            coordinator = OrganizationCoordinator(store, control=control, ledger=ledger)
            prompt_context = {
                "projectId": PROJECT_ID,
                "workId": WORK_ID,
                "runId": RUN_ID,
                "correlationId": CORRELATION_ID,
                "contextHash": hashlib.sha256(b"nac-adapter-probe-context-v1").hexdigest(),
            }

            async def execute(context: ExecutionContext) -> ExecutionSignal:
                if (context.project_id, context.work_id, context.run_id) != (PROJECT_ID, WORK_ID, RUN_ID):
                    raise RuntimeError("coordinator passed unexpected canonical work/run identity")
                prompt = TOKEN + "\n" + json.dumps(prompt_context, sort_keys=True)
                run_status, _ = request_json(
                    f"{base_url}/sessions/{session_id}/runs",
                    method="POST",
                    body={"prompt": prompt},
                )
                if run_status != 202:
                    raise RuntimeError(f"NAC run admission failed with HTTP {run_status}")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    read_status, session = request_json(f"{base_url}/sessions/{session_id}")
                    if read_status != 200 or not isinstance(session, dict):
                        raise RuntimeError("NAC session status read failed")
                    if session.get("active_run") is None:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise TimeoutError("NAC run did not settle within 30 seconds")

                messages = read_messages(base_url, session_id)
                outputs = [
                    message for message in messages
                    if message.get("role") == "assistant" and TOKEN not in json.dumps(message)
                ]
                if not outputs or ANSWER not in json.dumps(outputs[-1]):
                    raise RuntimeError("NAC transcript did not contain the scripted worker response")
                if not ScriptedResponses.requests:
                    raise RuntimeError("NAC did not call the local scripted provider")
                provider_body = ScriptedResponses.requests[0]["body"]
                if TOKEN not in json.dumps(provider_body):
                    raise RuntimeError("canonical work identity was not forwarded into the NAC model request")

                content = ANSWER.encode()
                content_ref, content_hash = store.put_blob(content)
                start_event = next(
                    event for event in store.verified_events(PROJECT_ID)
                    if event["eventType"] == "run.started" and event["payload"]["runId"] == RUN_ID
                )
                event_id = "event.nac-probe." + hashlib.sha256(RUN_ID.encode()).hexdigest()[:32]
                store.append({
                    "recordType": "event",
                    "schemaVersion": "0.1",
                    "eventId": event_id,
                    "projectId": PROJECT_ID,
                    "eventType": "discussion.message_added",
                    "actor": ACTOR,
                    "occurredAt": start_event["occurredAt"],
                    "causationId": start_event["eventId"],
                    "correlationId": CORRELATION_ID,
                    "idempotencyKey": "idem." + event_id,
                    "payload": {
                        "workId": WORK_ID,
                        "threadId": "thread.nac-adapter-probe",
                        "messageType": "status",
                        "contentRef": content_ref,
                        "contentHash": content_hash,
                        "runId": RUN_ID,
                        "visibleTo": ["agent.synthetic-vp", "agent.nac-worker"],
                    },
                })
                return ExecutionSignal.COMPLETED

            def verify(context: ExecutionContext) -> bool:
                state = Projector(store).replay(context.project_id)
                messages = [message for message in state["messages"] if message.get("runId") == context.run_id]
                return len(messages) == 1 and messages[0]["content"] == ANSWER

            task = WorkExecution(
                project_id=PROJECT_ID,
                work_id=WORK_ID,
                run_id=RUN_ID,
                correlation_id=CORRELATION_ID,
                actor=ACTOR,
                adapter=ADAPTER,
                context_hash=prompt_context["contextHash"],
                verification_actor=VERIFIER,
                execute=execute,
                verify=verify,
            )
            outcomes = await coordinator.run_plan((task,))
            state = Projector(store).replay(PROJECT_ID)
            if len(outcomes) != 1 or outcomes[0].status != WorkflowStatus.COMPLETED:
                detail = "missing outcome" if not outcomes else f"{outcomes[0].status}: {outcomes[0].detail}"
                raise RuntimeError(
                    f"NAC worker did not complete canonical work ({detail}); "
                    f"scripted provider requests={len(ScriptedResponses.requests)}"
                )
            if state["workItems"][WORK_ID]["status"] != "completed" or not store.verify(PROJECT_ID):
                raise RuntimeError("canonical result failed completion or hash-chain verification")
            if len(ScriptedResponses.requests) != 1:
                raise RuntimeError("expected exactly one local scripted provider request")
            return {
                "result": "passed",
                "projectId": PROJECT_ID,
                "workId": WORK_ID,
                "runId": RUN_ID,
                "workStatus": state["workItems"][WORK_ID]["status"],
                "canonicalMessageCount": len(state["messages"]),
                "providerRequests": len(ScriptedResponses.requests),
                "providerHost": f"127.0.0.1:{provider.server_port}",
                "nacStoreSeparate": True,
                "canonicalStoreVerified": True,
                "limitations": [
                    "prototype only; no NAC session-to-canonical receipt mapping survives host restart",
                    "no queue lease, capability snapshot, cancellation, or budget enforcement in NAC",
                    "scripted model made no tool call; no worker file/effect path was exercised",
                ],
            }
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=2)


def main() -> int:
    binary = os.environ.get("NAC_WEB_BIN")
    if not binary:
        raise SystemExit("set NAC_WEB_BIN to a separately built NAC nac-web binary")
    nac_binary = Path(binary).resolve()
    if not nac_binary.is_file() or not os.access(nac_binary, os.X_OK):
        raise SystemExit("NAC_WEB_BIN must name an executable nac-web binary")
    report = asyncio.run(run_probe(nac_binary))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
