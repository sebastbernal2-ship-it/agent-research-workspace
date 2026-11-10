from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from safe_studio import FailClosedDesktopAuth, _private_environment, build_app

TOKEN = "unit-test-token-with-at-least-32-characters"


def concrete_path(path: str) -> str:
    return re.sub(r"\{[^}:]+(?::[^}]+)?\}", "synthetic", path)


class FailClosedStudioWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agx-auth-test-")
        cls.home = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {"HOME": str(cls.home), "AGX_DESKTOP_TOKEN": TOKEN}, clear=False)
        cls.env.start()
        from agenticx.studio.server import create_studio_app

        cls.upstream_app = create_studio_app()
        cls.wrapper = build_app(TOKEN, cls.upstream_app)
        cls.client = TestClient(cls.wrapper)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.temp.cleanup()

    def test_token_is_mandatory_and_fail_closed(self):
        self.assertEqual(self.client.get("/api/session").status_code, 401)
        self.assertEqual(
            self.client.get("/api/session", headers={"x-agx-desktop-token": "wrong-token"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/session",
                headers=[("x-agx-desktop-token", TOKEN), ("x-agx-desktop-token", "wrong-token")],
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/session", headers={"x-agx-desktop-token": TOKEN}).status_code,
            200,
        )

    def test_health_and_readiness_are_only_http_exemptions(self):
        for path in ("/api/health", "/api/ready"):
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path).status_code, 401)
                self.assertNotEqual(
                    self.client.get(path, headers={"x-agx-desktop-token": "wrong-token"}).status_code,
                    401,
                )

    def test_correct_token_passes_all_discovered_api_methods_to_a_safe_sink(self):
        async def sink(scope, receive, send):
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
            if scope["type"] == "websocket":
                await send({"type": "websocket.accept"})
                while (await receive())["type"] != "websocket.disconnect":
                    pass

        client = TestClient(FailClosedDesktopAuth(sink, TOKEN))
        tested = 0
        for route in self.upstream_app.routes:
            if not getattr(route, "path", "").startswith("/api/"):
                continue
            for method in sorted(getattr(route, "methods", set()) or set()):
                with self.subTest(method=method, path=route.path):
                    response = client.request(
                        method, concrete_path(route.path),
                        headers={"x-agx-desktop-token": TOKEN},
                    )
                    self.assertEqual(response.status_code, 204)
                    tested += 1
        self.assertEqual(tested, 262)
        for route in (item for item in self.upstream_app.routes if getattr(item, "path", "").startswith("/ws/")):
            with client.websocket_connect(
                concrete_path(route.path), headers={"x-agx-desktop-token": TOKEN},
            ):
                pass

    def test_all_discovered_api_methods_are_rejected_without_or_with_wrong_token(self):
        routes = [
            route for route in self.upstream_app.routes
            if getattr(route, "path", "").startswith("/api/")
        ]
        expected = sum(
            len(getattr(route, "methods", set()) or set())
            for route in routes
            if route.path not in {"/api/health", "/api/ready"}
        )
        tested = 0
        for route in routes:
            path = concrete_path(route.path)
            for method in sorted(getattr(route, "methods", set()) or set()):
                if path in {"/api/health", "/api/ready"}:
                    continue
                with self.subTest(method=method, path=route.path):
                    self.assertEqual(self.client.request(method, path).status_code, 401)
                    self.assertEqual(
                        self.client.request(method, path, headers={"x-agx-desktop-token": "wrong-token"}).status_code,
                        401,
                    )
                    tested += 1
        self.assertEqual(tested, expected)
        self.assertEqual(expected, 260)

    def test_unauthenticated_websockets_are_closed_before_route_execution(self):
        routes = [route for route in self.upstream_app.routes if getattr(route, "path", "").startswith("/ws/")]
        self.assertTrue(routes)
        for route in routes:
            for headers in ({}, {"x-agx-desktop-token": "wrong-token"}):
                with self.subTest(path=route.path, headers=headers), self.assertRaises(WebSocketDisconnect) as raised:
                    with self.client.websocket_connect(concrete_path(route.path), headers=headers):
                        self.fail("unauthenticated websocket unexpectedly connected")
                self.assertEqual(raised.exception.code, 4401)

    def test_correct_token_reaches_representative_unprotected_upstream_routers(self):
        for path in (
            "/api/kb/config",
            "/api/brains",
            "/api/code-index/config",
            "/api/web-search/config",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"x-agx-desktop-token": TOKEN})
                self.assertNotEqual(response.status_code, 401, response.text)

    def test_docs_and_openapi_are_not_public(self):
        for path in ("/docs", "/openapi.json", "/redoc"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                self.assertNotEqual(
                    self.client.get(path, headers={"x-agx-desktop-token": TOKEN}).status_code,
                    401,
                )

    def test_wrapper_requires_long_token(self):
        with self.assertRaisesRegex(ValueError, "at least 32"):
            FailClosedDesktopAuth(self.upstream_app, "short")

    def test_launcher_environment_drops_inherited_secrets_and_disables_gateway(self):
        home = self.home / "isolated"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak", "AGX_GATEWAY_ENABLED": "1"}):
            env = _private_environment(home, TOKEN)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["AGX_GATEWAY_ENABLED"], "0")
        self.assertEqual(env["HOME"], str(home))
        self.assertEqual(env["AGX_DESKTOP_TOKEN"], TOKEN)

    def test_launcher_rejects_non_loopback_before_starting_server(self):
        completed = subprocess.run(
            [sys.executable, "safe_studio.py", "--host", "0.0.0.0"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("permits loopback 127.0.0.1 only", completed.stderr)


if __name__ == "__main__":
    unittest.main()
