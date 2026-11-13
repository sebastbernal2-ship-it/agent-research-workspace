"""Bounded, read-only inspection of a user-selected local Git repository.

Only tracked Markdown documentation is read. Ignored/untracked files, code,
bulk data, credentials, symlinks, and generated/vendor trees are never opened.
No network or model calls are made.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any


MAX_GIT_OUTPUT = 2 * 1024 * 1024
MAX_TRACKED_PATHS = 20_000
MAX_DOCUMENTS = 8
MAX_DOCUMENT_BYTES = 24 * 1024
MAX_TOTAL_DOCUMENT_BYTES = 96 * 1024
MAX_EXCERPT_CHARS = 900
SKIP_COMPONENTS = {
    ".git", ".venv", "venv", "node_modules", "vendor", "upstream",
    "data", "dataset", "datasets", "database", "databases", "db",
    "artifacts", "outputs", "models", "fixtures", "__pycache__",
}
SKIP_NAME = re.compile(r"(?:^|/)(?:\.env(?:\..*)?|[^/]*(?:secret|credential|token|private[_-]?key)[^/]*)(?:$|/)", re.I)
SECRET_LINE = re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password|private[_-]?key)\s*[:=]\s*[^\s,;]+")


class RepositoryInspectionError(ValueError):
    """The selected repository cannot be safely inspected."""


def _git(root: Path, *args: str, timeout: float = 5.0) -> bytes:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat",
    }
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryInspectionError("repository inspection timed out") from exc
    except OSError as exc:
        raise RepositoryInspectionError("Git could not inspect the selected directory") from exc
    if len(result.stdout) > MAX_GIT_OUTPUT:
        raise RepositoryInspectionError("repository metadata exceeds the inspection limit")
    if result.returncode != 0:
        raise RepositoryInspectionError("select a readable Git working tree")
    return result.stdout


def _safe_relative_path(raw: bytes) -> str | None:
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        return None
    if any(part.lower() in SKIP_COMPONENTS for part in candidate.parts) or SKIP_NAME.search(path):
        return None
    return path


def _document_excerpt(path: str, content: bytes) -> tuple[str, str]:
    text = content.decode("utf-8", errors="replace")
    title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#") and line.lstrip("# ").strip()), Path(path).stem)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("```") or line.startswith("<!--"):
            continue
        line = SECRET_LINE.sub(r"\1=[REDACTED]", line)
        lines.append(line)
        if sum(len(item) + 1 for item in lines) >= MAX_EXCERPT_CHARS:
            break
    excerpt = " ".join(lines)
    return title[:160], excerpt[:MAX_EXCERPT_CHARS]


def inspect_repository(path: str, *, allowed_root: str | Path | None = None) -> dict[str, Any]:
    if not isinstance(path, str) or not path.strip() or len(path) > 4096:
        raise RepositoryInspectionError("repositoryPath must be a nonempty local path")
    supplied = Path(path).expanduser()
    try:
        root = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RepositoryInspectionError("the selected repository directory does not exist") from exc
    if supplied.is_symlink() or not root.is_dir():
        raise RepositoryInspectionError("select a real directory, not a symlink")
    if allowed_root is not None:
        allowed = Path(allowed_root).expanduser().resolve(strict=True)
        try:
            root.relative_to(allowed)
        except ValueError as exc:
            raise RepositoryInspectionError("the selected repository must be inside the allowed local workspace") from exc

    root_bytes = _git(root, "rev-parse", "--show-toplevel").strip()
    try:
        git_root = Path(os.fsdecode(root_bytes)).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RepositoryInspectionError("the selected directory is not a Git working tree") from exc
    if git_root != root:
        raise RepositoryInspectionError("select the Git repository root, not a nested directory")

    tracked_raw = _git(root, "ls-files", "-z").split(b"\0")
    if len(tracked_raw) > MAX_TRACKED_PATHS + 1:
        raise RepositoryInspectionError(f"repository has more than {MAX_TRACKED_PATHS} tracked paths")
    tracked = [item for raw in tracked_raw if raw and (item := _safe_relative_path(raw))]
    head = _git(root, "rev-parse", "--verify", "HEAD").decode("ascii", errors="replace").strip()
    branch = _git(root, "branch", "--show-current").decode("utf-8", errors="replace").strip() or "(detached HEAD)"
    # Avoid `git status`: repository-local fsmonitor/filter configuration may run helpers.
    # Worktree changes are therefore deliberately not inspected in this bounded scan.

    top_level = sorted({Path(item).parts[0] for item in tracked if len(Path(item).parts) > 1})
    doc_paths = []
    for item in tracked:
        candidate = Path(item)
        name = candidate.name.lower()
        is_root_context = len(candidate.parts) == 1 and name in {"readme.md", "readme.rst", "context.md", "agents.md"}
        is_docs_markdown = candidate.suffix.lower() in {".md", ".rst"} and "docs" in {part.lower() for part in candidate.parts[:-1]}
        if (is_root_context or is_docs_markdown) and candidate.suffix.lower() in {".md", ".rst"}:
            doc_paths.append(item)
    preferred = sorted(doc_paths, key=lambda item: (0 if Path(item).name.lower() in {"readme.md", "context.md"} else 1, item.lower()))[:MAX_DOCUMENTS]

    documents = []
    total_bytes = 0
    for item in preferred:
        file_path = root / item
        try:
            file_stat = file_path.lstat()
            ancestors = [root.joinpath(*Path(item).parts[:index]) for index in range(1, len(Path(item).parts))]
            if any(not ancestor.is_dir() or ancestor.is_symlink() for ancestor in ancestors):
                continue
        except OSError:
            continue
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > MAX_DOCUMENT_BYTES:
            continue
        if total_bytes + file_stat.st_size > MAX_TOTAL_DOCUMENT_BYTES:
            continue
        try:
            content = file_path.read_bytes()
        except OSError:
            continue
        total_bytes += len(content)
        title, excerpt = _document_excerpt(item, content)
        if excerpt:
            documents.append({"path": item, "title": title, "excerpt": excerpt, "sha256": hashlib.sha256(content).hexdigest()})

    readme = next((doc for doc in documents if Path(doc["path"]).name.lower() == "readme.md"), None)
    fingerprint_material = "\n".join([
        head,
        branch,
        "working-tree-state-not-inspected",
        *tracked,
        *(f"{doc['path']}:{doc['sha256']}" for doc in documents),
    ]).encode("utf-8")
    return {
        "repositoryName": root.name,
        "repositoryPath": str(root),
        "head": head,
        "branch": branch,
        "trackedFileCount": len(tracked),
        "trackedChanges": None,
        "topLevelAreas": top_level[:40],
        "documents": documents,
        "goalHint": readme["excerpt"][:500] if readme else "",
        "fingerprint": hashlib.sha256(fingerprint_material).hexdigest(),
        "inspectionPolicy": "Read-only local scan. Only up to 8 tracked Markdown/RST documents (96 KiB total) were opened. Untracked, ignored, data, generated, vendor, code, and credential-like paths were not read. Git working-tree status was not inspected to avoid repository-configured helpers. No network or model call was made.",
    }


def alignment_segments(report: dict[str, Any]) -> list[dict[str, str]]:
    documents = report.get("documents", [])
    evidence = "\n".join(f"- {doc['path']}: {doc['title']} — {doc['excerpt'][:260]}" for doc in documents) or "- No eligible tracked project documentation was found in the bounded scan."
    areas = ", ".join(report.get("topLevelAreas", [])[:20]) or "(no top-level code areas identified)"
    goal = report.get("goalHint") or "The repository does not state a concise project goal in an eligible README."
    facts = (
        f"Repository: {report['repositoryName']}\nBranch: {report['branch']}\n"
        f"HEAD: {report['head']}\nTracked files: {report['trackedFileCount']}\n"
        "Working-tree modifications: not inspected (to avoid repository-configured helpers).\n"
        f"Top-level areas: {areas}\n"
        f"Documentation reviewed:\n{evidence}\n\n{report['inspectionPolicy']}"
    )
    return [
        {"title": "Vision and intended outcome", "prompt": f"I reviewed the repository documents and this is the goal I could infer: {goal}\nWhat is the actual vision and outcome you want? Confirm, correct, or replace that inference.", "evidence": facts},
        {"title": "Current state and maturity", "prompt": "What currently works, what is experimental or incomplete, and what should be treated as obsolete? Correct any assumptions implied by the repository structure or documents.", "evidence": f"Repository inventory: {report['trackedFileCount']} tracked files across {areas}. Working-tree modification state was not inspected; names and contents of untracked/ignored files were not read.\n\n{evidence}"},
        {"title": "Scope, ownership, and boundaries", "prompt": "How should the project's major areas be separated and owned? What is in scope, out of scope, or deliberately independent? Please call out any boundary the repository alone cannot establish.", "evidence": f"Top-level tracked areas: {areas}\n\n{evidence}"},
        {"title": "Open choices and constraints", "prompt": "What research, target, data, annotation, privacy, provider, or operational choices remain unresolved? List anything that must stay a hold rather than be guessed.", "evidence": f"I found these project documents and repository facts, but they do not automatically settle product policy:\n{evidence}\n\n{report['inspectionPolicy']}"},
        {"title": "Success and execution approval", "prompt": "What evidence would convince you the initial outcomes are done? Which kinds of work may be approved one task at a time, and what—if anything—could eventually run under a project-wide automatic mode? Keep external effects and sensitive data explicitly gated.", "evidence": "No execution or provider permission is inferred from repository access or from completing this interview. Project-wide automatic execution remains off until separately configured and authorized."},
    ]
