"""Offline process-restart probe for AgenticX's SQLite session metadata.

Run only with the pinned spike venv:
  .venv/bin/python restart_probe.py

This tests persistence primitives across fresh processes; it does not start
Studio, test workflow continuation, or exercise external effects.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SESSION_ID = "issue4-cold-restart"


def child(mode: str, database: Path) -> None:
    from agenticx.memory.session_store import SessionStore

    store = SessionStore(database)
    if mode == "write":
        store._save_todos_sync(
            SESSION_ID,
            [{"content": "Review dataset methods", "status": "in_progress", "active_form": "Reviewing dataset methods"}],
        )
        store._save_scratchpad_sync(
            SESSION_ID, {"decision": "compare two deterministic methods"}
        )
        store._save_session_summary_sync(
            SESSION_ID,
            "Synthetic non-coding research task",
            {"status": "running"},
        )
        return
    if mode == "read":
        todos = store._load_todos_sync(SESSION_ID)
        scratchpad = store._load_scratchpad_sync(SESSION_ID)
        metadata = store._load_latest_session_metadata_sync(SESSION_ID)
        assert todos == [{
            "content": "Review dataset methods",
            "status": "in_progress",
            "active_form": "Reviewing dataset methods",
        }], todos
        assert scratchpad == {"decision": "compare two deterministic methods"}, scratchpad
        assert metadata == {"status": "running"}, metadata
        print(json.dumps({"todos": todos, "scratchpad": scratchpad, "metadata": metadata}))
        return
    raise ValueError(f"unknown mode: {mode}")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] in {"write", "read"}:
        child(sys.argv[1], Path(sys.argv[2]))
        return

    with tempfile.TemporaryDirectory(prefix="agenticx-restart-probe-") as temp:
        root = Path(temp)
        home = root / "home"
        home.mkdir(mode=0o700)
        database = root / "sessions.sqlite"
        env = {
            "HOME": str(home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for mode in ("write", "read"):
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), mode, str(database)],
                check=True,
                env=env,
                cwd=root,
                timeout=30,
                capture_output=mode == "write",
                text=True,
            )
        print("PASS: fresh process recovered persisted session metadata")


if __name__ == "__main__":
    main()
