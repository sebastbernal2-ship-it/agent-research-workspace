"""Ideas: a global list of things the owner wants to build someday, that can be escalated into a real project.

Deliberately simple and not part of any project's history: an idea is not a commitment. Starting one creates a
project (with its alignment interview) and carries the owner's original words into it.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_TITLE, MAX_NOTE, MAX_IDEAS = 140, 4000, 500


class IdeaStore:
    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [i for i in data if isinstance(i, dict) and isinstance(i.get("id"), str)]
        except (OSError, ValueError, TypeError):
            return []

    def _write(self, ideas: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ideas, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    @staticmethod
    def _clean(title: Any, note: Any) -> tuple[str, str]:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE:
            raise ValueError(f"title must contain 1 to {MAX_TITLE} characters")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
            raise ValueError(f"note must be at most {MAX_NOTE} characters")
        return title.strip(), (note or "").strip()

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._read(), key=lambda i: i["createdAt"], reverse=True)

    def get(self, idea_id: str) -> dict[str, Any]:
        for idea in self._read():
            if idea["id"] == idea_id:
                return idea
        raise KeyError("idea not found")

    def add(self, title: Any, note: Any = "") -> dict[str, Any]:
        title, note = self._clean(title, note)
        now = datetime.now(timezone.utc).isoformat()
        idea = {"id": "idea-" + os.urandom(5).hex(), "title": title, "note": note, "status": "idea", "projectId": None,
                "createdAt": now, "updatedAt": now}
        with self._lock:
            ideas = self._read()
            if len(ideas) >= MAX_IDEAS:
                raise ValueError("too many ideas; archive or delete some first")
            self._write([*ideas, idea])
        return idea

    def update(self, idea_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes or set(changes) - {"title", "note", "action"}:
            raise ValueError("an idea update accepts title, note or action")
        if changes.get("action") not in {None, "archive", "unarchive"}:
            raise ValueError("action must be archive or unarchive")
        with self._lock:
            ideas = self._read()
            idea = next((i for i in ideas if i["id"] == idea_id), None)
            if idea is None:
                raise KeyError("idea not found")
            if "title" in changes or "note" in changes:
                idea["title"], idea["note"] = self._clean(changes.get("title", idea["title"]), changes.get("note", idea["note"]))
            if changes.get("action") == "archive" and idea["status"] == "idea":
                idea["status"] = "archived"
            elif changes.get("action") == "unarchive" and idea["status"] == "archived":
                idea["status"] = "idea"
            idea["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self._write(ideas)
            return idea

    def mark_started(self, idea_id: str, project_id: str) -> dict[str, Any]:
        with self._lock:
            ideas = self._read()
            idea = next((i for i in ideas if i["id"] == idea_id), None)
            if idea is None:
                raise KeyError("idea not found")
            idea["status"], idea["projectId"], idea["updatedAt"] = "started", project_id, datetime.now(timezone.utc).isoformat()
            self._write(ideas)
            return idea
