"""Executable exit criteria: "done" is proven by running something, not by a worker's report.

Workers register runnable checks in ``.northstar/checks.json`` inside the work directory::

    {"checks": [{"id": "unit-tests", "criterion": "All tests pass", "command": ["python3", "-m", "unittest", "-q"]}]}

``command`` is an argv list (never a shell string) that exits 0 only when the criterion really holds. The file
accumulates across phases, so every phase assessment also re-runs earlier checks: that is the regression suite.
Checks run in the work directory with a timeout and a scrubbed environment (no API keys or tokens).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

CHECKS_FILE = Path(".northstar") / "checks.json"
MAX_CHECKS, MAX_ARGS, TIMEOUT_SECONDS, OUTPUT_TAIL = 40, 16, 180, 1500
_SECRET = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)


def load_checks(directory: Path) -> list[dict[str, Any]]:
    """Valid checks only; a malformed entry is skipped, a malformed file yields none."""
    try:
        data = json.loads((directory / CHECKS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for raw in (data.get("checks") if isinstance(data, dict) else None) or []:
        if not isinstance(raw, dict):
            continue
        command = raw.get("command")
        if (not isinstance(raw.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", raw["id"])
                or not isinstance(raw.get("criterion"), str) or not raw["criterion"].strip()
                or not isinstance(command, list) or not 1 <= len(command) <= MAX_ARGS
                or any(not isinstance(a, str) or not a for a in command)):
            continue
        out.append({"id": raw["id"], "criterion": raw["criterion"].strip()[:300], "command": command})
    return out[:MAX_CHECKS]


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not _SECRET.search(k)}


def run_checks(directory: Path, checks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Run every registered check; each result says whether it passed and shows the tail of its output."""
    results = []
    for check in load_checks(directory) if checks is None else checks:
        try:
            done = subprocess.run(check["command"], cwd=directory, env=_clean_env(), capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
            passed, output = done.returncode == 0, (done.stdout + done.stderr)
        except subprocess.TimeoutExpired:
            passed, output = False, f"timed out after {TIMEOUT_SECONDS}s"
        except (OSError, ValueError) as exc:
            passed, output = False, f"could not run: {exc}"
        results.append({"id": check["id"], "criterion": check["criterion"], "passed": passed, "output": output[-OUTPUT_TAIL:]})
    return results
