"""Restricted subprocess bridge to the isolated AgenticX runtime spike."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .bounded_process import (
    BoundedProcessOutputLimit,
    BoundedProcessTimeout,
    run_bounded_subprocess,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = REPO_ROOT / "spikes" / "agenticx-runtime" / ".venv" / "bin" / "python"
WORKER_SCRIPT = Path(__file__).with_name("agenticx_worker.py")
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 16 * 1024


class AgenticXWorkerError(RuntimeError):
    pass


class AgenticXSubprocessWorker:
    """Run exactly one bounded AgenticX task in its own pinned environment.

    The bridge uses JSON over stdin/stdout, a temporary HOME/cwd, no tool
    registrations, a fixed test provider, and a small explicit environment.
    It is not a production sandbox; it is a process/dependency boundary test.
    """

    def __init__(self, python: str | Path = DEFAULT_PYTHON, *, timeout: float = 20.0):
        self.python = str(python)
        self.timeout = timeout
        if not Path(self.python).is_file():
            raise AgenticXWorkerError(f"AgenticX interpreter not found: {self.python}")

    def run_task(self, *, task_id: str, description: str, scripted_result: str) -> dict[str, Any]:
        request = {
            "taskId": task_id,
            "description": description,
            "scriptedResult": scripted_result,
        }
        encoded = json.dumps(request, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 30000:
            raise AgenticXWorkerError("request exceeds 30 KB bridge limit")

        with tempfile.TemporaryDirectory(prefix="agenticx-worker-") as isolated_home:
            env = {
                "HOME": isolated_home,
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
            try:
                completed = run_bounded_subprocess(
                    [self.python, str(WORKER_SCRIPT)],
                    input_bytes=(encoded + "\n").encode("utf-8"),
                    cwd=isolated_home,
                    env=env,
                    timeout=self.timeout,
                    stdout_limit=MAX_STDOUT_BYTES,
                    stderr_limit=MAX_STDERR_BYTES,
                )
            except BoundedProcessTimeout as exc:
                raise AgenticXWorkerError(f"AgenticX worker exceeded {self.timeout:g}s timeout") from exc
            except BoundedProcessOutputLimit as exc:
                raise AgenticXWorkerError("AgenticX worker exceeded its stdout/stderr byte limit") from exc
            if completed.returncode != 0:
                stderr_text = completed.stderr[-4000:].decode("utf-8", errors="replace")
                raise AgenticXWorkerError(
                    f"AgenticX worker exited {completed.returncode}; stderr tail: {stderr_text}"
                )
            try:
                stdout_text = completed.stdout.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AgenticXWorkerError("AgenticX worker returned non-UTF-8 stdout") from exc
            lines = stdout_text.splitlines()
            if len(lines) != 1:
                raise AgenticXWorkerError(f"expected one JSON response line, received {len(lines)}")
            try:
                response = json.loads(lines[0])
            except json.JSONDecodeError as exc:
                raise AgenticXWorkerError("AgenticX worker returned invalid JSON") from exc
            if not isinstance(response, dict) or response.get("success") is not True or not isinstance(response.get("result"), str):
                raise AgenticXWorkerError(f"AgenticX worker returned invalid result: {response!r}")
            return response
