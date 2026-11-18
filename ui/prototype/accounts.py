"""Rotating a provider's accounts when one runs out of quota, through the operator's own account tool.

pi keeps exactly one live Codex credential, so switching accounts is global and exclusive. The operator's
``codex-account`` tool (pi-harness) already does that safely: it snapshots the live account's refreshed tokens back to its
stored copy, backs up, installs the chosen account, and never prints a token. This module only DECIDES when to call
``codex-account use <name>``; it never reads, copies or writes a credential itself.

Blocked accounts are remembered (in memory and in a small state file) so a limited account is not retried, and the
live account is not switched back and forth, until its reset time passes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
ROTATION_DEFAULT_BLOCK = 7200  # seconds, when a limit message does not say when the account resets


class AccountRotation:
    def __init__(self, cli: Path, names: list[str], *, state_path: Path | None = None, clock=time.time):
        if not names or any(not isinstance(n, str) or not NAME.fullmatch(n) for n in names):
            raise ValueError("rotation must be a non-empty list of account names (a-z, 0-9, - and _)")
        if not Path(cli).is_absolute():
            raise ValueError("accountCli must be an absolute path")
        self.cli, self.names, self.state_path, self.clock = Path(cli), list(names), state_path, clock
        self.blocked: dict[str, float] = {}
        self._load()

    # ---- the operator's tool -------------------------------------------------------------------------------------
    def _run(self, *args: str) -> dict:
        try:
            done = subprocess.run([sys.executable, str(self.cli), *args], capture_output=True, text=True, timeout=180,
                                  stdin=subprocess.DEVNULL)
            return json.loads(done.stdout) if done.returncode == 0 else {"ok": False, "error": done.stderr.strip()[-300:]}
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return {"ok": False, "error": type(exc).__name__}

    def live_name(self) -> str | None:
        info = self._run("list")
        name = info.get("live_name") if info.get("ok") else None
        return name if isinstance(name, str) else None

    def switch(self, name: str) -> bool:
        return bool(self._run("use", name).get("ok"))

    # ---- what is blocked, and who is next ------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path else {}
            self.blocked = {k: float(v) for k, v in data.items() if k in self.names and isinstance(v, (int, float))}
        except (OSError, ValueError, AttributeError):
            self.blocked = {}

    def _save(self) -> None:
        if self.state_path:
            try:
                tmp = self.state_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.blocked), encoding="utf-8")
                os.chmod(tmp, 0o600)
                tmp.replace(self.state_path)
            except OSError:
                pass

    def is_blocked(self, name: str) -> bool:
        return self.blocked.get(name, 0.0) > self.clock()

    def block(self, name: str | None, until: float | None = None) -> float:
        until = until if until and until > self.clock() else self.clock() + ROTATION_DEFAULT_BLOCK
        if name in self.names:
            self.blocked[name] = until
            self._save()
        return until

    def next_available(self, exclude: str | None = None) -> str | None:
        return next((n for n in self.names if n != exclude and not self.is_blocked(n)), None)

    def all_blocked(self) -> bool:
        return all(self.is_blocked(n) for n in self.names)

    def earliest_unblock(self) -> float:
        return min((self.blocked.get(n, 0.0) for n in self.names), default=0.0) if self.all_blocked() else 0.0
