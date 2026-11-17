"""Issues & future directions: the project's ledger of "we can still make this better" and "later".

Three kinds, all logged the moment anything notices them (a worker, the independent reviewer, the VP's own checks,
the owner), each with a priority and a source:

* issue        a gap or defect against the owner's vision ("topics are single keywords like 'right'")
* improvement  a better approach to something that already works
* direction    a future idea; not worked on until the owner or a later phase's plan promotes it

Issues and improvements are worked through by the VP on its own, highest priority first, after the required phase
work; directions wait. Each is a work item (so it is scheduled, run, reviewed and committed like everything else)
whose title carries its kind; priority, source and evidence live in a ``META:`` message on its own thread.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .vp import OWNER_ID, SUBAGENT, VP

PREFIX = {"issue": "[Issue] ", "improvement": "[Improvement] ", "direction": "[Direction] "}
ISSUE_ID = "work.issue-"
PRIORITIES = ("high", "medium", "low")
MAX_OPEN_PER_PHASE = 25
_DEFAULT_CRITERIA = {
    "issue": ["The problem is fixed and verified by running it against real inputs, with evidence."],
    "improvement": ["The improvement is in place and shown to be better than before, with evidence."],
    "direction": ["A concrete plan or prototype exists, with evidence of whether it is worth doing."],
}


def issue_kind(work: dict[str, Any]) -> str | None:
    for kind, prefix in PREFIX.items():
        if work["title"].startswith(prefix):
            return kind
    return None


def is_issue(work: dict[str, Any]) -> bool:
    return issue_kind(work) is not None


def strip_prefix(title: str) -> str:
    for prefix in PREFIX.values():
        if title.startswith(prefix):
            return title[len(prefix):]
    return title


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_prefix(title).lower()).strip()


def issue_meta(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """priority/source/evidence/deferred per issue; later META messages override earlier ones."""
    meta: dict[str, dict[str, Any]] = {}
    for m in sorted(state["messages"], key=lambda m: m["sequence"]):
        if m["threadId"].startswith("thread.issue.") and m["content"].startswith("META: "):
            try:
                meta.setdefault(m["workId"], {}).update(json.loads(m["content"][6:]))
            except ValueError:
                continue
    return meta


def active_issues(state: dict[str, Any]) -> list[dict[str, Any]]:
    from .phases import phase_of
    items, meta = state["workItems"], issue_meta(state)
    out = []
    for w in items.values():
        kind = issue_kind(w)
        if kind is None:
            continue
        info = meta.get(w["workId"], {})
        out.append({
            "id": w["workId"], "kind": kind, "title": strip_prefix(w["title"]), "detail": w["objective"],
            "priority": info.get("priority", "medium"), "source": info.get("source", "vp"), "evidence": info.get("evidence", ""),
            "deferred": bool(info.get("deferred")), "phaseId": phase_of(items, w["workId"]), "status": w["status"],
            "revision": w["revision"], "createdAt": w["createdAt"],
        })
    return sorted(out, key=lambda i: (PRIORITIES.index(i["priority"]) if i["priority"] in PRIORITIES else 1, i["createdAt"]))


class IssueBoard:
    def __init__(self, vp: Any):
        self.vp = vp

    def _meta(self, project_id: str, work_id: str, data: dict[str, Any], actor: dict[str, str] = VP) -> None:
        self.vp.rt.control.add_discussion_message(
            project_id, work_id, thread_id=f"thread.issue.{work_id}", message_type="evidence",
            content="META: " + json.dumps(data, ensure_ascii=False), actor=actor, visible_to=[OWNER_ID, VP["actorId"]])

    def file(self, project_id: str, phase_id: str | None, kind: str, title: str, detail: str, *, priority: str = "medium",
             source: str = "vp", evidence: str = "", work_type: str | None = None) -> dict[str, Any] | None:
        """Log one item. Returns None for duplicates, items with no phase to belong to, or a phase with too many open."""
        from .vp import WORK_TYPES, _clip
        title, detail = _clip(title, 140), _clip(detail, 2000) or _clip(title, 140)
        if kind not in PREFIX or not title or phase_id is None:
            return None
        state = self.vp._state(project_id)
        items = state["workItems"]
        if any(is_issue(w) and w["status"] != "cancelled" and norm(w["title"]) == norm(title) for w in items.values()):
            return None  # already logged (open or done): not a new item
        from .phases import phase_of
        open_here = sum(1 for w in items.values() if is_issue(w) and phase_of(items, w["workId"]) == phase_id
                        and w["status"] in {"proposed", "ready", "running"})
        if open_here >= MAX_OPEN_PER_PHASE:
            return None
        work = self.vp._new_work(
            project_id, PREFIX[kind] + title, detail, work_type if work_type in WORK_TYPES else ("research" if kind == "direction" else "implementation"),
            "proposed", SUBAGENT, [], list(_DEFAULT_CRITERIA[kind]), phase_id, VP, work_id=ISSUE_ID + os.urandom(6).hex())
        self._meta(project_id, work["workId"], {"kind": kind, "priority": priority if priority in PRIORITIES else "medium",
                                               "source": source, "evidence": _clip(evidence, 1000), "deferred": False})
        return work

    def act(self, project_id: str, issue_id: str, action: str) -> dict[str, Any]:
        """The owner's controls: work on it now, defer or un-defer it, call it won't-do, retry a failed one."""
        state = self.vp._state(project_id)
        work = state["workItems"].get(issue_id)
        if work is None or not is_issue(work):
            raise KeyError("issue not found")
        if action == "do_now":
            if work["status"] == "proposed":
                self.vp._transition(project_id, issue_id, "ready", VP, "The owner asked for this to be worked on now.")
            self._meta(project_id, issue_id, {"deferred": False}, actor=VP)
        elif action == "defer":
            self._meta(project_id, issue_id, {"deferred": True})
        elif action == "undefer":
            self._meta(project_id, issue_id, {"deferred": False})
        elif action == "wont_do":
            if work["status"] in {"proposed", "ready", "failed", "blocked", "waiting_human"}:
                self.vp._transition(project_id, issue_id, "cancelled", VP, "The owner decided not to do this.")
        elif action == "retry":
            if work["status"] == "failed":
                self.vp._transition(project_id, issue_id, "ready", VP, "The owner asked to retry.")
        else:
            raise ValueError("unknown action")
        return next(i for i in active_issues(self.vp._state(project_id)) if i["id"] == issue_id)
