"""Fail-closed, loopback-only launcher wrapper for the pinned Studio spike.

This is an evaluation boundary, not a production server or hardened sandbox.
The launcher is untested; the test suite exercises the ASGI wrapper in-process.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable


class FailClosedDesktopAuth:
    """Require the desktop header for all routes except public health/readiness."""

    def __init__(self, app: Any, token: str):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("desktop token must contain at least 32 characters")
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        kind = scope.get("type")
        if kind not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = str(scope.get("method", "GET")).upper()
        if kind == "http" and method in {"GET", "HEAD"} and path in {"/api/health", "/api/ready"}:
            await self.app(scope, receive, send)
            return
        presented_values = [
            value for key, value in scope.get("headers", [])
            if key.lower() == b"x-agx-desktop-token"
        ]
        presented = presented_values[0] if len(presented_values) == 1 else b""
        if not hmac.compare_digest(self._token, presented):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 4401, "reason": "unauthorized"})
                return
            body = json.dumps({"detail": "desktop token required"}).encode("utf-8")
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_app(token: str, app: Any | None = None) -> FailClosedDesktopAuth:
    if app is None:
        from agenticx.studio.server import create_studio_app

        app = create_studio_app()
    return FailClosedDesktopAuth(app, token)


def _private_environment(home: Path, token: str) -> dict[str, str]:
    """Drop inherited provider, gateway, and integration credentials/config."""
    return {
        "HOME": str(home),
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "AGX_DESKTOP_TOKEN": token,
        "AGX_GATEWAY_ENABLED": "0",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("this evaluation wrapper permits loopback 127.0.0.1 only")
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")

    expected_venv = Path(__file__).resolve().parent / ".venv"
    if Path(sys.prefix).resolve() != expected_venv.resolve():
        parser.error(f"use the pinned spike interpreter: {expected_venv / 'bin/python'}")

    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="agenticx-studio-local-") as temp_home:
        home = Path(temp_home)
        os.chmod(home, 0o700)
        private_env = _private_environment(home, token)
        os.environ.clear()
        os.environ.update(private_env)
        token_path = home / ".agenticx" / "desktop-token"
        token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        token_path.write_text(token + "\n", encoding="utf-8")
        os.chmod(token_path, 0o600)
        print(f"AgenticX Studio isolated HOME: {home}", file=sys.stderr)
        print(f"Desktop token file (mode 0600): {token_path}", file=sys.stderr)
        print("Loopback only; inherited credentials removed; gateway disabled.", file=sys.stderr)

        import uvicorn

        uvicorn.run(build_app(token), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
