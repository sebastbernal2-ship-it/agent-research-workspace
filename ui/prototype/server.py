"""Start the local synthetic project/inbox prototype on IPv4 loopback only."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .local_server import build_server
from .runtime import PrototypeRuntime


def default_database_path() -> Path:
    configured_state_home = os.environ.get("XDG_STATE_HOME")
    state_home = Path(configured_state_home) if configured_state_home else Path.home() / ".local" / "state"
    return state_home / "northstar-ai-workspace-prototype" / "canonical.sqlite3"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4173, help="loopback TCP port (default: 4173)")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")

    database_path = default_database_path()
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(database_path.parent, 0o700)
        runtime = PrototypeRuntime(database_path)
        if os.name == "posix":
            for path in (database_path, Path(f"{database_path}-wal"), Path(f"{database_path}-shm")):
                if path.exists():
                    os.chmod(path, 0o600)
        server = build_server(runtime, args.port)
    except Exception as exc:
        parser.exit(1, f"Unable to start local prototype: {exc}\n")

    print("Northstar local prototype (synthetic data only)")
    print(f"URL: http://127.0.0.1:{args.port}")
    print(f"Canonical SQLite store: {database_path}")
    print("Bound to 127.0.0.1 only. No real authentication, remote access, model providers, or tools.")
    print("Press Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping local prototype.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
