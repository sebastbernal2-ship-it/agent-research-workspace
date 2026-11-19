"""'What changed since you last looked': the owner steers by seeing the product change, so every project can answer
that question in product terms (finished work, commits, issues, decisions needed, executable checks), not as a log.

The owner's "last looked" marker is a local UI setting (like automation), not canonical project history.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .issues import active_issues, is_issue
from .phases import is_phase, phase_of, phase_summaries
from .vp import SKIP_WORK

MAX_ITEMS, SUMMARY_CHARS = 12, 280


class LastSeen:
    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            return {}

    def get(self, project_id: str) -> str | None:
        return self._read().get(project_id)

    def mark(self, project_id: str, at: str) -> None:
        with self._lock:
            data = self._read()
            data[project_id] = at
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS - 1].rstrip() + "…"


def build_digest(state: dict[str, Any], activity: list[dict[str, Any]], since: str | None, now: str | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc).isoformat()
    since = since or state["project"].get("createdAt") or "0"
    items = state["workItems"]
    newer = lambda stamp: bool(stamp) and stamp > since  # ISO-8601 UTC strings order correctly as text
    by_work: dict[str, str] = {}
    for entry in sorted(activity, key=lambda e: e["at"]):  # the latest report per task is its summary
        if entry["text"].strip() and not entry["text"].startswith(("Committed ", "WAITING ON QUESTION", "META:")):
            by_work[entry["workId"]] = entry["text"]
    finished, resolved = [], []
    for w in sorted(items.values(), key=lambda w: w["updatedAt"], reverse=True):
        if is_phase(w) or w["workId"] in SKIP_WORK or w["workId"].startswith("work.alignment") or w["status"] != "completed" or not newer(w["updatedAt"]):
            continue
        phase = items.get(phase_of(items, w["workId"]) or "")
        row = {"title": w["title"], "phase": phase["title"] if phase else "", "at": w["updatedAt"], "summary": _excerpt(by_work.get(w["workId"], ""))}
        (resolved if is_issue(w) else finished).append(row)
    phases = phase_summaries(state)
    checks = next((p["checks"] for p in reversed(phases) if p.get("checks")), [])
    terminal = {"completed", "failed", "cancelled"}
    questions = [q for q in state["questions"].values() if q["status"] == "open" and not q["questionId"].startswith("question.memory")
                 and not (q["blocking"] and q["workIds"] and all(items.get(w, {}).get("status") in terminal for w in q["workIds"]))]
    digest = {
        "since": since, "until": now,
        "finished": finished[:MAX_ITEMS], "issuesResolved": resolved[:MAX_ITEMS],
        "commits": [{"text": _excerpt(a["text"]), "at": a["at"]} for a in sorted(activity, key=lambda e: e["at"], reverse=True)
                    if a["text"].startswith("Committed ") and newer(a["at"])][:MAX_ITEMS],
        "newIssues": [{"title": i["title"], "kind": i["kind"], "priority": i["priority"]} for i in active_issues(state) if newer(i["createdAt"])][:MAX_ITEMS],
        "needsYou": [{"questionId": q["questionId"], "text": _excerpt(q["questionText"])} for q in questions][:MAX_ITEMS],
        "answeredSince": sum(1 for q in state["questions"].values() if q["status"] == "answered" and newer((q.get("answer") or {}).get("answeredAt"))),
        "phases": [{"index": p["index"], "title": p["title"], "status": p["status"], "done": p["tasks"]["done"], "total": p["tasks"]["total"]} for p in phases],
        "checks": {"passing": sum(1 for c in checks if c["passed"]), "failing": [c["criterion"] for c in checks if not c["passed"]]},
    }
    parts = [f"{len(finished)} task{'s' if len(finished) != 1 else ''} finished"] if finished else []
    parts += [f"{len(digest['newIssues'])} new issue{'s' if len(digest['newIssues']) != 1 else ''} logged"] if digest["newIssues"] else []
    parts += [f"{len(questions)} decision{'s' if len(questions) != 1 else ''} need you"] if questions else []
    parts += [f"{len(digest['checks']['failing'])} check{'s' if len(digest['checks']['failing']) != 1 else ''} failing"] if digest["checks"]["failing"] else []
    digest["headline"] = ", ".join(parts) or "Nothing has changed since you last looked."
    digest["empty"] = not (finished or resolved or digest["commits"] or digest["newIssues"] or questions)
    return digest
