"""Materialize an exact tracked Git snapshot and explicit path allowlist.

The source checkout is never modified. Uncommitted/ignored data and Git metadata
are not copied. This is a content-scope helper, not an OS sandbox or secret
scanner; the caller must still authorize the selected commit and paths.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from .bounded_process import BoundedProcessError, BoundedProcessOutputLimit, BoundedProcessTimeout, run_bounded_subprocess


_COMMIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_DENIED_BASENAMES = frozenset({
    ".git", ".ssh", ".aws", ".azure", ".gnupg", "id_rsa", "id_ed25519",
    "credentials.json", "secrets.json",
})


class ScopedWorkspaceError(RuntimeError):
    """A requested committed snapshot is invalid or cannot be safely materialized."""


@dataclass(frozen=True)
class ScopedFile:
    path: str
    sha256: str
    size_bytes: int
    executable: bool


@dataclass(frozen=True)
class ScopedWorkspace:
    root: Path
    source_commit: str
    allowed_paths: tuple[str, ...]
    files: tuple[ScopedFile, ...]
    total_bytes: int

    def manifest(self) -> dict[str, object]:
        return {
            "sourceCommit": self.source_commit,
            "allowedPaths": list(self.allowed_paths),
            "fileCount": len(self.files),
            "totalBytes": self.total_bytes,
            "files": [
                {"path": file.path, "sha256": file.sha256, "sizeBytes": file.size_bytes, "executable": file.executable}
                for file in self.files
            ],
        }

    @property
    def manifest_hash(self) -> str:
        encoded = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def cleanup(self) -> None:
        shutil.rmtree(self.root)


def _validate_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ScopedWorkspaceError("allowed paths must be non-empty POSIX relative paths")
    raw_parts = raw.split("/")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
        raise ScopedWorkspaceError("allowed path is absolute or contains traversal components")
    lowered = {part.lower() for part in path.parts}
    if any(part == ".git" or part.startswith(".env") or part in _DENIED_BASENAMES for part in lowered):
        raise ScopedWorkspaceError("allowed path contains a denied credential or Git metadata name")
    return path.as_posix()


def _git(repo: Path, args: Sequence[str], *, timeout: float, stdout_limit: int) -> bytes:
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        result = run_bounded_subprocess(
            ["git", *args], input_bytes=b"", cwd=repo, env=env,
            timeout=timeout, stdout_limit=stdout_limit, stderr_limit=16_384,
        )
    except (BoundedProcessTimeout, BoundedProcessOutputLimit, BoundedProcessError) as exc:
        raise ScopedWorkspaceError("bounded Git snapshot command failed") from exc
    if result.returncode != 0:
        raise ScopedWorkspaceError("Git rejected the requested snapshot")
    return result.stdout


def create_scoped_git_snapshot(
    repo: str | Path,
    *,
    commit: str,
    allowed_paths: Sequence[str],
    output_parent: str | Path,
    max_archive_bytes: int = 64 * 1024 * 1024,
    timeout_seconds: float = 30,
) -> ScopedWorkspace:
    """Extract only allowlisted regular files from one exact committed tree.

    The commit must be a full object ID. Git ignored/untracked files and all
    uncommitted changes are absent by construction. Symlinks, submodules,
    special files, `.env*`, obvious credential paths, and `.git` data are
    rejected. The output is a private non-Git directory suitable for Codex
    with its explicit `--skip-git-repo-check` mode.
    """
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise ScopedWorkspaceError("commit must be a full lowercase Git object ID")
    if isinstance(allowed_paths, (str, bytes)) or not allowed_paths:
        raise ScopedWorkspaceError("a non-empty explicit path allowlist is required")
    if type(max_archive_bytes) is not int or not 1 <= max_archive_bytes <= 512 * 1024 * 1024:
        raise ScopedWorkspaceError("max_archive_bytes must be between 1 byte and 512 MiB")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 120:
        raise ScopedWorkspaceError("timeout_seconds must be between 1 and 120")
    paths = tuple(sorted({_validate_path(path) for path in allowed_paths}))
    if len(paths) != len(allowed_paths):
        raise ScopedWorkspaceError("allowed paths must be unique")

    repository = Path(repo).resolve(strict=True)
    if not repository.is_dir():
        raise ScopedWorkspaceError("repository must be an existing directory")
    root_bytes = _git(repository, ["rev-parse", "--show-toplevel"], timeout=timeout_seconds, stdout_limit=4096)
    try:
        repository_root = Path(root_bytes.decode("utf-8").strip()).resolve(strict=True)
    except (UnicodeDecodeError, OSError) as exc:
        raise ScopedWorkspaceError("Git repository root could not be resolved") from exc
    resolved_commit = _git(
        repository_root, ["rev-parse", "--verify", f"{commit}^{{commit}}"],
        timeout=timeout_seconds, stdout_limit=4096,
    ).decode("ascii", errors="strict").strip()
    if resolved_commit != commit:
        raise ScopedWorkspaceError("requested commit did not resolve to the exact supplied object ID")

    parent = Path(output_parent).resolve(strict=True)
    try:
        parent.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise ScopedWorkspaceError("snapshot output must be outside the source repository")
    if not parent.is_dir():
        raise ScopedWorkspaceError("output_parent must be an existing directory")

    for path in paths:
        try:
            _git(
                repository_root, ["cat-file", "-e", f"{commit}:{path}"],
                timeout=timeout_seconds, stdout_limit=1,
            )
        except ScopedWorkspaceError as exc:
            raise ScopedWorkspaceError(f"allowlisted path {path!r} is absent from the committed tree") from exc
    archive = _git(
        repository_root,
        ["archive", "--format=tar", commit, "--", *(f":(literal){path}" for path in paths)],
        timeout=timeout_seconds,
        stdout_limit=max_archive_bytes,
    )
    destination = Path(tempfile.mkdtemp(prefix="northstar-scoped-", dir=parent))
    files: list[ScopedFile] = []
    total = 0
    selected_members: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle:
                raw_name = member.name.rstrip("/")
                path = PurePosixPath(raw_name)
                if (
                    not raw_name or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or ".git" in {part.lower() for part in path.parts}
                    or any(part.lower().startswith(".env") for part in path.parts)
                ):
                    raise ScopedWorkspaceError("Git archive contains an unsafe path")
                name = path.as_posix()
                allowed = any(name == item or name.startswith(item.rstrip("/") + "/") for item in paths)
                if not allowed:
                    raise ScopedWorkspaceError("Git archive contains a path outside the requested allowlist")
                selected_members.add(name)
                target = destination.joinpath(*path.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ScopedWorkspaceError("archive path escaped the snapshot root")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isfile():
                    raise ScopedWorkspaceError("snapshot contains a symlink, submodule, or special file")
                source = bundle.extractfile(member)
                if source is None:
                    raise ScopedWorkspaceError("archive file has no readable content")
                content = source.read(max_archive_bytes + 1)
                if len(content) != member.size or total + len(content) > max_archive_bytes:
                    raise ScopedWorkspaceError("archive file size is inconsistent or exceeds the configured cap")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                fd = os.open(target, flags, 0o700 if member.mode & 0o111 else 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(content)
                total += len(content)
                files.append(ScopedFile(
                    path=name,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    executable=bool(member.mode & 0o111),
                ))
        for requested in paths:
            if not any(item == requested or item.startswith(requested.rstrip("/") + "/") for item in selected_members):
                raise ScopedWorkspaceError(f"allowlisted path {requested!r} is absent from the committed tree")
        if not files:
            raise ScopedWorkspaceError("allowlist selected no regular files")
        return ScopedWorkspace(
            root=destination,
            source_commit=commit,
            allowed_paths=paths,
            files=tuple(sorted(files, key=lambda file: file.path)),
            total_bytes=total,
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
