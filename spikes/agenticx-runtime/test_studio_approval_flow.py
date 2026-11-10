"""Studio/AgentRuntime approval integration with a deterministic mock LLM.

The command runner is replaced by an in-memory counter. A separate Python
reader process checks checkpoint recovery; no command, provider, server socket,
or external service is used.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from safe_studio import build_app

TOKEN = "issue4-studio-approval-test-token-32chars-min"


class FakeResponse:
    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class DeterministicApprovalLLM:
    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    def invoke(self, prompt, **kwargs):
        self.calls += 1
        prompt_text = str(prompt)
        self.prompts.append(prompt_text)
        if "[ACTION_CONFIRMED] 用户已确认执行。" in prompt_text:
            return FakeResponse("Approval received; the synthetic action is permitted but not executed.")
        if "[ACTION_REJECTED] 用户已取消执行。不得继续该动作。" in prompt_text:
            return FakeResponse("Approval denied; the synthetic action must not be executed.")
        if self.calls == 1:
            return FakeResponse(
                "I need explicit human approval before the synthetic action.",
                [{
                    "id": "issue4-confirm-call",
                    "type": "function",
                    "function": {
                        "name": "request_action_confirmation",
                        "arguments": {
                            "title": "Approve synthetic research publication?",
                            "summary": [{"label": "target", "value": "offline-test-only"}],
                            "approve_label": "Approve",
                            "reject_label": "Reject",
                            "expires_in_seconds": 30,
                        },
                    },
                }],
            )
        return FakeResponse("The synthetic workflow resumed after the user's decision.")

    def stream(self, prompt, **kwargs):
        response = self.invoke(prompt, **kwargs)
        yield response.content


class DeterministicCommandLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, prompt, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return FakeResponse(
                "This synthetic command needs human approval.",
                [{
                    "id": "issue4-command-call",
                    "type": "function",
                    "function": {
                        "name": "bash_exec",
                        "arguments": {"command": "python -c \"print('offline-only')\""},
                    },
                }],
            )
        return FakeResponse("The command workflow completed after the human decision.")

    def stream(self, prompt, **kwargs):
        response = self.invoke(prompt, **kwargs)
        yield response.content


def extract_sse(line: str):
    if not line.startswith("data: "):
        return None
    try:
        return json.loads(line[6:])
    except json.JSONDecodeError:
        return None


def create_client(monkeypatch, llm):
    from agenticx.studio.storage.factory import reset_storage_backend_for_testing
    from agenticx.studio import server as server_module

    reset_storage_backend_for_testing()

    async def skip_title_suggestion(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server_module, "_llm_suggest_session_title_job", skip_title_suggestion)
    monkeypatch.setattr(server_module.ProviderResolver, "resolve", lambda **_kwargs: llm)
    client = TestClient(build_app(TOKEN))
    headers = {"x-agx-desktop-token": TOKEN}
    session_id = client.get("/api/session", headers=headers).json()["session_id"]
    managed = client.app.app.state.session_manager.get(session_id)
    assert managed is not None
    managed.session_name = "Offline approval probe"
    return client, headers, session_id, managed


def run_chat(client, headers, session_id):
    events = []
    with client.stream(
        "POST",
        "/api/chat",
        headers=headers,
        json={"session_id": session_id, "user_input": "Prepare a synthetic report."},
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            event = extract_sse(line)
            if event is not None:
                events.append(event)
    return events


@pytest.mark.parametrize(
    ("selected", "expected_marker", "expected_final"),
    [
        ("Approve", "[ACTION_CONFIRMED]", "permitted but not executed"),
        ("Reject", "[ACTION_REJECTED]", "must not be executed"),
    ],
)
def test_chat_waits_for_clarify_api_decision_then_resumes(monkeypatch, selected, expected_marker, expected_final):
    with tempfile.TemporaryDirectory(prefix="issue4-studio-flow-") as temp_home:
        env = patch.dict(
            os.environ,
            {"HOME": temp_home, "AGX_DESKTOP_TOKEN": TOKEN},
            clear=False,
        )
        env.start()
        try:
            llm = DeterministicApprovalLLM()
            client, headers, session_id, managed = create_client(monkeypatch, llm)
            clarify_gate = managed.get_clarify_gate("meta")

            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_chat = pool.submit(run_chat, client, headers, session_id)
                deadline = time.monotonic() + 10
                while not clarify_gate.has_pending():
                    if pending_chat.done():
                        pending_chat.result()
                        raise AssertionError("chat finished without pausing for clarification")
                    if time.monotonic() >= deadline:
                        raise AssertionError("chat did not reach the confirmation gate")
                    time.sleep(0.01)
                pending_ids = [request_id for request_id, future in clarify_gate._pending.items() if not future.done()]
                assert len(pending_ids) == 1
                confirmation_id = pending_ids[0]
                from agenticx.runtime.checkpoint import CheckpointStore
                from agenticx.runtime.clarify import AsyncClarifyGate

                checkpoint = CheckpointStore().load(session_id)
                assert checkpoint is not None
                assert checkpoint.confirm_state["pending"] == []
                assert not hasattr(checkpoint, "clarify_state")
                fresh_clarify_gate = AsyncClarifyGate(timeout_seconds=1)
                assert not fresh_clarify_gate.resolve(
                    confirmation_id, {"selected_options": [selected], "answer_text": ""},
                )
                clarify = client.post(
                    "/api/clarify",
                    headers=headers,
                    json={
                        "session_id": session_id,
                        "request_id": confirmation_id,
                        "selected_options": [selected],
                    },
                )
                assert clarify.status_code == 200, clarify.text
                events = pending_chat.result(timeout=15)

            types = [event.get("type") for event in events]
            assert "clarification_required" in types
            assert "clarification_response" in types
            assert "final" in types
            assert not any(kind == "error" for kind in types)
            prompts = "\n".join(llm.prompts)
            assert expected_marker in prompts
            final_payloads = [str(event.get("data")) for event in events if event.get("type") == "final"]
            assert any(expected_final in payload for payload in final_payloads)
        finally:
            env.stop()


@pytest.mark.parametrize("approved", [True, False])
def test_chat_tool_execution_waits_for_confirm_api_and_runs_only_if_approved(monkeypatch, approved):
    with tempfile.TemporaryDirectory(prefix="issue4-studio-command-") as temp_home:
        env = patch.dict(
            os.environ,
            {
                "HOME": temp_home,
                "AGX_DESKTOP_TOKEN": TOKEN,
                "AGX_CONFIRM_TIMEOUT_SEC": "10",
            },
            clear=False,
        )
        env.start()
        try:
            import agenticx.cli.agent_tools as agent_tools

            llm = DeterministicCommandLLM()
            client, headers, session_id, managed = create_client(monkeypatch, llm)
            gate = managed.get_confirm_gate("meta")
            launches = []

            async def fake_sandbox(argv, _session, cwd, **_kwargs):
                return argv, None, "synthetic-test-sandbox"

            class FakeProcess:
                returncode = 0

                def __init__(self):
                    self.stdout = asyncio.StreamReader()
                    self.stdout.feed_data(b"synthetic command accepted\n")
                    self.stdout.feed_eof()
                    self.stderr = asyncio.StreamReader()
                    self.stderr.feed_eof()

                async def wait(self):
                    return 0

            async def fake_process(*argv, **_kwargs):
                launches.append(argv)
                return FakeProcess()

            monkeypatch.setattr(agent_tools, "_apply_command_sandbox", fake_sandbox)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_chat = pool.submit(run_chat, client, headers, session_id)
                deadline = time.monotonic() + 10
                while not gate._pending:
                    if pending_chat.done():
                        pending_chat.result()
                        raise AssertionError("tool action finished without waiting for confirmation")
                    if time.monotonic() >= deadline:
                        raise AssertionError("chat did not reach the confirm gate")
                    time.sleep(0.01)
                request_ids = [request_id for request_id, future in gate._pending.items() if not future.done()]
                assert len(request_ids) == 1
                assert launches == [], "command dispatch occurred before human approval"
                from agenticx.runtime.checkpoint import CheckpointStore

                checkpoint = CheckpointStore().load(session_id)
                assert checkpoint is not None, "expected an AgenticX turn checkpoint before tool dispatch"
                assert checkpoint.pending_tool_calls
                # Current upstream saves the turn checkpoint before dispatch, but
                # its gate snapshot has no pending request after the gate is opened.
                assert checkpoint.status == "in_progress"
                assert checkpoint.confirm_state["pending"] == []
                from agenticx.runtime.confirm import AsyncConfirmGate

                async def restore_and_resolve() -> bool:
                    fresh_gate = AsyncConfirmGate(timeout_seconds=1)
                    fresh_gate.restore_state(checkpoint.confirm_state)
                    return fresh_gate.resolve(request_ids[0], approved)

                assert asyncio.run(restore_and_resolve()) is False
                # Patch only after the real confirmation gate is pending, so no
                # test infrastructure can accidentally start a subprocess early.
                monkeypatch.setattr(agent_tools.asyncio, "create_subprocess_exec", fake_process)
                confirm = client.post(
                    "/api/confirm",
                    headers=headers,
                    json={
                        "session_id": session_id,
                        "request_id": request_ids[0],
                        "approved": approved,
                    },
                )
                assert confirm.status_code == 200, confirm.text
                events = pending_chat.result(timeout=15)

            assert len(launches) == (1 if approved else 0)
            types = [event.get("type") for event in events]
            assert "confirm_required" in types
            assert "confirm_response" in types
            assert "final" in types
            assert not any(kind == "error" for kind in types)
        finally:
            env.stop()


def test_pending_confirm_checkpoint_cannot_be_resolved_by_fresh_manager(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="issue4-studio-restart-") as temp_home:
        env = patch.dict(
            os.environ,
            {
                "HOME": temp_home,
                "AGX_DESKTOP_TOKEN": TOKEN,
                "AGX_CONFIRM_TIMEOUT_SEC": "5",
                "AGX_RESUME_INTERRUPTED": "1",
            },
            clear=False,
        )
        env.start()
        try:
            import agenticx.cli.agent_tools as agent_tools
            from agenticx.runtime.checkpoint import CheckpointStore
            from agenticx.studio.server import create_studio_app
            from agenticx.studio.storage.factory import reset_storage_backend_for_testing

            async def fake_sandbox(argv, _session, cwd, **_kwargs):
                return argv, None, "synthetic-test-sandbox"

            launches = []

            async def forbidden_process(*argv, **_kwargs):
                launches.append(argv)
                raise AssertionError("restart probe must never launch a process")

            monkeypatch.setattr(agent_tools, "_apply_command_sandbox", fake_sandbox)
            monkeypatch.setattr(agent_tools.asyncio, "create_subprocess_exec", forbidden_process)
            llm = DeterministicCommandLLM()
            client, headers, session_id, managed = create_client(monkeypatch, llm)
            gate = managed.get_confirm_gate("meta")

            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_chat = pool.submit(run_chat, client, headers, session_id)
                deadline = time.monotonic() + 10
                while not gate._pending:
                    if pending_chat.done():
                        pending_chat.result()
                        raise AssertionError("chat finished without a pending confirmation")
                    if time.monotonic() >= deadline:
                        raise AssertionError("chat did not reach the confirmation gate")
                    time.sleep(0.01)
                request_id = next(iter(gate._pending))
                checkpoint = CheckpointStore().load(session_id)
                assert checkpoint is not None
                assert checkpoint.confirm_state["pending"] == []
                child = r'''
import asyncio, json, sys
from agenticx.runtime.checkpoint import CheckpointStore
from agenticx.runtime.confirm import AsyncConfirmGate
checkpoint = CheckpointStore().load(sys.argv[1])
async def probe():
    gate = AsyncConfirmGate(timeout_seconds=1)
    gate.restore_state(checkpoint.confirm_state or {})
    return gate.resolve(sys.argv[2], True)
print(json.dumps({
    "status": checkpoint.status,
    "pendingToolCount": len(checkpoint.pending_tool_calls),
    "pendingConfirmCount": len((checkpoint.confirm_state or {}).get("pending", [])),
    "canResolveOldRequest": asyncio.run(probe()),
}))
'''
                child_env = {
                    "HOME": temp_home,
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "AGX_STORAGE_BACKEND": "local",
                }
                child_result = subprocess.run(
                    [sys.executable, "-c", child, session_id, request_id],
                    cwd=Path(__file__).resolve().parents[2],
                    env=child_env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
                restored_probe = json.loads(child_result.stdout)
                assert restored_probe == {
                    "status": "in_progress",
                    "pendingToolCount": 1,
                    "pendingConfirmCount": 0,
                    "canResolveOldRequest": False,
                }

                # Drop process-scoped storage and build a fresh Studio manager
                # over the same isolated HOME, as a restart would.
                reset_storage_backend_for_testing()
                fresh_app = create_studio_app()
                fresh_manager = fresh_app.state.session_manager
                fresh_store = CheckpointStore()
                restored_checkpoint = fresh_store.load(session_id)
                assert restored_checkpoint is not None
                from agenticx.llms.provider_resolver import ProviderResolver

                class ResumeLLM:
                    def invoke(self, _prompt, **_kwargs):
                        return FakeResponse("Recovered without replaying the interrupted action.")

                monkeypatch.setattr(
                    ProviderResolver,
                    "resolve",
                    lambda **_kwargs: ResumeLLM(),
                )
                fresh_managed = fresh_manager.get(session_id, touch=False)
                assert fresh_managed is not None
                resumed = asyncio.run(fresh_manager._resume_single_session(
                    session_id,
                    restored_checkpoint,
                    fresh_store,
                    runtime_runner=None,
                ))
                assert resumed is True
                assert fresh_managed.execution_state == "idle"
                assert fresh_store.load(session_id) is None
                assert fresh_managed.get_confirm_gate("meta").resolve(request_id, True) is False
                fresh_client = TestClient(build_app(TOKEN, fresh_app))
                lost_response = fresh_client.post(
                    "/api/confirm",
                    headers=headers,
                    json={"session_id": session_id, "request_id": request_id, "approved": True},
                )
                assert lost_response.status_code == 404
                assert lost_response.json()["detail"] == "confirm request not found"
                # The original request eventually times out fail-closed; no command
                # is launched during this restart simulation.
                assert pending_chat.result(timeout=10)
                assert launches == []
        finally:
            env.stop()
