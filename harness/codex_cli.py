"""Opt-in Codex CLI worker boundary; offline tests use a fake executable only.

Codex CLI is an agent runtime, not a normalized text-completion provider. This
adapter deliberately requires a trusted policy at every invocation and does
not itself establish identity, provider terms, spend enforcement, isolation,
or canonical run/receipt durability.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .bounded_process import (
    BoundedProcessError,
    BoundedProcessOutputLimit,
    BoundedProcessTimeout,
    run_bounded_subprocess,
)


_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_ALLOWED_SANDBOXES = frozenset({"read-only", "workspace-write"})


class CodexInvocationError(RuntimeError):
    """Codex could not complete within the configured local process contract."""


class CodexInvocationPolicy(Protocol):
    """Trusted host policy; production identity/data/budget policy is not supplied here."""

    def authorize(self, invocation: "CodexInvocation", *, codex_home: Path, workspace: Path) -> None: ...


@dataclass(frozen=True)
class CodexInvocation:
    project_id: str
    work_id: str
    run_id: str
    agent_id: str
    model_id: str
    workspace: Path
    prompt: str
    approval_ref: str
    data_scope_ref: str
    provider_terms_ref: str
    budget_ref: str
    sandbox_mode: str = "read-only"
    timeout_seconds: float = 120
    max_prompt_bytes: int = 100_000
    max_response_bytes: int = 200_000
    max_stdout_bytes: int = 1_000_000
    max_stderr_bytes: int = 64_000

    def __post_init__(self) -> None:
        for name in (
            "project_id", "work_id", "run_id", "agent_id", "approval_ref",
            "data_scope_ref", "provider_terms_ref", "budget_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty reference")
        if not isinstance(self.model_id, str) or _MODEL_ID.fullmatch(self.model_id) is None:
            raise ValueError("model_id must be explicit and contain only supported identifier characters")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be non-empty text")
        if not isinstance(self.workspace, Path) or not self.workspace.is_absolute():
            raise ValueError("workspace must be an explicit absolute Path")
        if self.sandbox_mode not in _ALLOWED_SANDBOXES:
            raise ValueError("sandbox_mode must be read-only or workspace-write")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not 1 <= self.timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 1 and 600")
        for name in ("max_prompt_bytes", "max_response_bytes", "max_stdout_bytes", "max_stderr_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1 or value > 10_000_000:
                raise ValueError(f"{name} must be an integer between 1 and 10000000")
        if len(self.prompt.encode("utf-8")) > self.max_prompt_bytes:
            raise ValueError("prompt exceeds max_prompt_bytes")


@dataclass(frozen=True)
class CodexInvocationResult:
    text: str
    model_id: str
    sandbox_mode: str
    stdout_bytes: int
    stderr_bytes: int

    def audit_metadata(self) -> dict[str, object]:
        """Non-prompt metadata only; this result is not yet persisted canonically."""
        return {
            "adapter": "codex-cli",
            "modelId": self.model_id,
            "sandboxMode": self.sandbox_mode,
            "stdoutBytes": self.stdout_bytes,
            "stderrBytes": self.stderr_bytes,
        }


class CodexCliWorker:
    """Invoke a fixed Codex CLI command after an injected trusted policy check.

    This is an adapter candidate, not a hardened sandbox. The caller must
    provide a dedicated CODEX_HOME and a project-scoped isolated workspace.
    The command uses no shell, no extra writable directories, no user config,
    no persistent Codex session, and no dangerous sandbox bypass option.
    """

    def __init__(
        self,
        *,
        executable: Path,
        codex_home: Path,
        policy: CodexInvocationPolicy,
    ):
        if not isinstance(executable, Path) or not executable.is_absolute():
            raise ValueError("executable must be an explicit absolute Path")
        if not isinstance(codex_home, Path) or not codex_home.is_absolute():
            raise ValueError("codex_home must be an explicit absolute Path")
        if policy is None or not callable(getattr(policy, "authorize", None)):
            raise ValueError("a trusted invocation policy is required")
        self.executable = executable
        self.codex_home = codex_home
        self.policy = policy

    def invoke(self, invocation: CodexInvocation) -> CodexInvocationResult:
        if not isinstance(invocation, CodexInvocation):
            raise TypeError("invocation must be a CodexInvocation")
        executable = self.executable.resolve(strict=True)
        codex_home = self.codex_home.resolve(strict=True)
        workspace = invocation.workspace.resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise CodexInvocationError("configured Codex executable is not runnable")
        if not codex_home.is_dir():
            raise CodexInvocationError("configured CODEX_HOME is not a directory")
        if not workspace.is_dir():
            raise CodexInvocationError("authorized workspace is not a directory")

        # No process starts until policy has checked the explicit project/work,
        # workspace, model, authorization, data, terms, and budget references.
        self.policy.authorize(invocation, codex_home=codex_home, workspace=workspace)
        with tempfile.TemporaryDirectory(prefix="northstar-codex-") as temp_dir:
            output_path = Path(temp_dir) / "last-message.txt"
            args = [
                str(executable), "exec", "--json", "--ephemeral", "--ignore-user-config",
                "--sandbox", invocation.sandbox_mode,
                "--model", invocation.model_id,
                "--cd", str(workspace),
                "--output-last-message", str(output_path), "-",
            ]
            # Never inherit provider API keys, proxy credentials, or arbitrary
            # project env. Codex auth is read only from the explicitly selected
            # CODEX_HOME; official account/terms review is still a prerequisite.
            env = {
                "PATH": os.environ.get("PATH", os.defpath),
                "HOME": str(Path.home()),
                "CODEX_HOME": str(codex_home),
                "LANG": "C.UTF-8",
            }
            try:
                completed = run_bounded_subprocess(
                    args,
                    input_bytes=invocation.prompt.encode("utf-8"),
                    cwd=workspace,
                    env=env,
                    timeout=float(invocation.timeout_seconds),
                    stdout_limit=invocation.max_stdout_bytes,
                    stderr_limit=invocation.max_stderr_bytes,
                )
            except BoundedProcessTimeout as exc:
                raise CodexInvocationError("Codex CLI exceeded its wall-clock limit") from exc
            except BoundedProcessOutputLimit as exc:
                raise CodexInvocationError("Codex CLI exceeded a captured-output limit") from exc
            except BoundedProcessError as exc:
                raise CodexInvocationError("Codex CLI process could not be safely collected") from exc
            if completed.returncode != 0:
                # Do not surface arbitrary child stderr: it may contain prompts,
                # workspace data, or provider diagnostics.
                raise CodexInvocationError(f"Codex CLI exited with status {completed.returncode}")
            try:
                output_stat = output_path.lstat()
            except FileNotFoundError as exc:
                raise CodexInvocationError("Codex CLI produced no final-message file") from exc
            if not output_path.is_file() or output_path.is_symlink() or output_stat.st_size > invocation.max_response_bytes:
                raise CodexInvocationError("Codex CLI final-message file is invalid or oversized")
            try:
                text = output_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise CodexInvocationError("Codex CLI final-message file is unreadable") from exc
            if not text.strip():
                raise CodexInvocationError("Codex CLI returned an empty final message")
            return CodexInvocationResult(
                text=text,
                model_id=invocation.model_id,
                sandbox_mode=invocation.sandbox_mode,
                stdout_bytes=len(completed.stdout),
                stderr_bytes=len(completed.stderr),
            )
