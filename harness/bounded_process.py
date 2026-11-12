"""Bounded sync/async stdin/stdout/stderr runners for local adapter subprocesses.

The async runner preserves coordinator heartbeats and kills its POSIX process
group on cancellation; neither runner is an OS sandbox or effect fence.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class BoundedProcessError(RuntimeError):
    """Base failure from a bounded child process."""


class BoundedProcessTimeout(BoundedProcessError):
    """Child exceeded its wall-clock deadline."""


class BoundedProcessOutputLimit(BoundedProcessError):
    """Child exceeded a configured stdout or stderr byte limit."""


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


async def run_bounded_subprocess_async(
    args: Sequence[str],
    *,
    input_bytes: bytes,
    cwd: str | Path,
    env: Mapping[str, str],
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedProcessResult:
    """Async bounded runner that keeps queue heartbeats responsive and kills on cancel."""
    if stdout_limit <= 0 or stderr_limit <= 0:
        raise ValueError("stdout and stderr limits must be positive")
    process = await asyncio.create_subprocess_exec(
        *list(args),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=dict(env),
        start_new_session=(os.name == "posix"),
    )

    def kill_process_group() -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass

    stdout = bytearray()
    stderr = bytearray()
    overflowed: list[str] = []

    async def drain(stream, buffer: bytearray, limit: int, name: str) -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            if len(buffer) + len(chunk) > limit:
                overflowed.append(name)
                kill_process_group()
                return
            buffer.extend(chunk)

    readers = [
        asyncio.create_task(drain(process.stdout, stdout, stdout_limit, "stdout")),
        asyncio.create_task(drain(process.stderr, stderr, stderr_limit, "stderr")),
    ]
    try:
        try:
            process.stdin.write(input_bytes)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            kill_process_group()
            await process.wait()
            await asyncio.gather(*readers, return_exceptions=True)
            raise BoundedProcessTimeout(f"child exceeded {timeout:g}s timeout") from exc
        try:
            await asyncio.wait_for(asyncio.gather(*readers), timeout=2)
        except asyncio.TimeoutError as exc:
            kill_process_group()
            await asyncio.gather(*readers, return_exceptions=True)
            raise BoundedProcessError("child output pipes remained open after process exit") from exc
    except asyncio.CancelledError:
        kill_process_group()
        await process.wait()
        await asyncio.gather(*readers, return_exceptions=True)
        raise
    finally:
        if process.returncode is None:
            kill_process_group()
            await process.wait()
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
    if overflowed:
        raise BoundedProcessOutputLimit(
            f"child exceeded its {'/'.join(sorted(set(overflowed)))} byte limit"
        )
    return BoundedProcessResult(process.returncode, bytes(stdout), bytes(stderr))


def run_bounded_subprocess(
    args: Sequence[str],
    *,
    input_bytes: bytes,
    cwd: str | Path,
    env: Mapping[str, str],
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedProcessResult:
    """Run a direct child with bounded captured output and a wall-clock limit.

    On POSIX, overflow/timeout kills the new child process group; elsewhere it
    kills the direct child. This is not OS sandboxing or effect containment.
    """
    if stdout_limit <= 0 or stderr_limit <= 0:
        raise ValueError("stdout and stderr limits must be positive")
    process = subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=dict(env),
        start_new_session=(os.name == "posix"),
    )

    def kill_process_group() -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass

    stdout = bytearray()
    stderr = bytearray()
    overflowed: list[str] = []
    overflow_lock = threading.Lock()

    def drain(stream, buffer: bytearray, limit: int, name: str) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            if len(buffer) + len(chunk) > limit:
                with overflow_lock:
                    overflowed.append(name)
                kill_process_group()
                return
            buffer.extend(chunk)

    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout, stdout_limit, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr, stderr_limit, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        try:
            process.stdin.write(input_bytes)
            process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            kill_process_group()
            process.wait()
            raise BoundedProcessTimeout(f"child exceeded {timeout:g}s timeout") from exc
    finally:
        if process.poll() is None:
            kill_process_group()
            process.wait()
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
    if any(reader.is_alive() for reader in readers):
        raise BoundedProcessError("child output reader did not stop")
    if overflowed:
        raise BoundedProcessOutputLimit(
            f"child exceeded its {'/'.join(sorted(set(overflowed)))} byte limit"
        )
    return BoundedProcessResult(process.returncode, bytes(stdout), bytes(stderr))
