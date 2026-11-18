"""A built-in MCP server that lets a sandboxed worker ask a model a question without holding any credentials.

Workers run inside a sandbox that cannot write the model CLI's own state (for example Codex writes under ~/.codex), so
a worker cannot call a model itself. This server runs OUTSIDE the worker's sandbox, started by its harness like any other
MCP server, and uses the operator's normal sign-in. It exposes one tool, ``ask_model``, with no file or shell access
for the model it calls (a pure text question), a prompt-size limit, a per-call timeout and a per-session call cap.

Declare it for a project with the integrations API::

    {"name": "model", "spec": {"kind": "mcp-stdio", "command": ["python3", "-m", "ui.prototype.model_mcp"],
     "env": {"PYTHONPATH": "/path/to/agent-research-workspace", "NORTHSTAR_MODEL_KIND": "codex"},
     "description": "ask_model(prompt, system?): ask the operator's model a text question"}}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .vp import CliRunner, VPError

MAX_PROMPT_CHARS = 60_000
MAX_CALLS = int(os.environ.get("NORTHSTAR_MODEL_MAX_CALLS", "200"))
TOOL = {
    "name": "ask_model",
    "description": "Ask the operator's language model a pure text question and get its answer. The model has no tools, no file "
                   "or network access, and cannot see your workspace: include everything it needs in `prompt`. Use it for "
                   "generation or judgement that needs a model, such as drafting candidate ideas from atoms you pass in.",
    "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string", "description": "the question or task, with all needed context"},
                                                       "system": {"type": "string", "description": "optional instructions for how to answer"}},
                    "required": ["prompt"], "additionalProperties": False},
}


class ModelServer:
    def __init__(self, runner: Any):
        self.runner, self.calls = runner, 0

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        mid, method = message.get("id"), message.get("method")
        if method == "initialize":
            return self._ok(mid, {"protocolVersion": (message.get("params") or {}).get("protocolVersion", "2024-11-05"),
                                  "capabilities": {"tools": {}}, "serverInfo": {"name": "northstar-model", "version": "1"}})
        if method == "tools/list":
            return self._ok(mid, {"tools": [TOOL]})
        if method == "tools/call":
            params = message.get("params") or {}
            if params.get("name") != "ask_model":
                return self._error(mid, -32602, "unknown tool")
            return self._ok(mid, self._ask(params.get("arguments") or {}))
        if mid is not None:  # ping and anything else that expects an answer
            return self._ok(mid, {})
        return None  # notifications get no reply

    def _ask(self, args: dict[str, Any]) -> dict[str, Any]:
        prompt, system = args.get("prompt"), args.get("system") or "Answer the request directly and concisely."
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(system, str):
            return self._text("prompt must be a non-empty string (and system a string)", error=True)
        if len(prompt) > MAX_PROMPT_CHARS or len(system) > 8000:
            return self._text(f"prompt is too long (limit {MAX_PROMPT_CHARS} characters)", error=True)
        if self.calls >= MAX_CALLS:
            return self._text(f"this session has used its {MAX_CALLS} model calls", error=True)
        self.calls += 1
        try:
            return self._text(self.runner.think(system, prompt))  # a pure text call: no tools, an empty working directory
        except VPError as exc:
            return self._text(f"the model call failed: {exc}", error=True)

    @staticmethod
    def _text(text: str, error: bool = False) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], **({"isError": True} if error else {})}

    @staticmethod
    def _ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main() -> None:
    kind = os.environ.get("NORTHSTAR_MODEL_KIND", "codex")
    if kind not in {"codex", "claude"}:
        sys.exit("NORTHSTAR_MODEL_KIND must be codex or claude")
    server = ModelServer(CliRunner(kind))
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = server.handle(message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
