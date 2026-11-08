"""Restricted subprocess bridge to the isolated AgenticX runtime spike."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any


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
            process = subprocess.Popen(
                [self.python, str(WORKER_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=isolated_home,
                env=env,
            )
            stdout = bytearray()
            stderr = bytearray()
            overflow = threading.Event()

            def drain(stream, buffer, limit):
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    if len(buffer) + len(chunk) > limit:
                        overflow.set()
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        return
                    buffer.extend(chunk)

            readers = [
                threading.Thread(target=drain, args=(process.stdout, stdout, MAX_STDOUT_BYTES), daemon=True),
                threading.Thread(target=drain, args=(process.stderr, stderr, MAX_STDERR_BYTES), daemon=True),
            ]
            for reader in readers:
                reader.start()
            try:
                try:
                    process.stdin.write((encoded + "\n").encode("utf-8"))
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise AgenticXWorkerError(f"AgenticX worker exceeded {self.timeout:g}s timeout") from exc
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
                for reader in readers:
                    reader.join(timeout=2)
                process.stdout.close()
                process.stderr.close()
            if any(reader.is_alive() for reader in readers):
                raise AgenticXWorkerError("AgenticX output reader did not stop")
            if overflow.is_set():
                raise AgenticXWorkerError("AgenticX worker exceeded its stdout/stderr byte limit")
            if process.returncode != 0:
                stderr_text = bytes(stderr[-4000:]).decode("utf-8", errors="replace")
                raise AgenticXWorkerError(
                    f"AgenticX worker exited {process.returncode}; stderr tail: {stderr_text}"
                )
            try:
                stdout_text = bytes(stdout).decode("utf-8")
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
