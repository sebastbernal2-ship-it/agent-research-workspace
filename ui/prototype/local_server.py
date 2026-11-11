"""Loopback-only HTTP adapter for the synthetic inbox demo.

This is a development prototype, not an authenticated service. It serves one
synthetic project and never binds to a non-loopback interface.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from harness.control_plane import ControlPlaneError, StaleQuestionRevision
from harness.event_store import EventStoreError
from harness.inbox import InboxForbidden, InboxNotFound

from .runtime import PROJECT_ID, PrototypeRuntime


STATIC_ROOT = Path(__file__).resolve().parent
MAX_JSON_BODY_BYTES = 16 * 1024
PROJECT_PATH = re.compile(r"^/api/v1/projects/([^/]+)$")
WORK_COLLECTION_PATH = re.compile(r"^/api/v1/projects/([^/]+)/work$")
PROJECT_COLLECTION_PATH = re.compile(r"^/api/v1/projects/([^/]+)/(runs|evidence|artifacts)$")
INBOX_LIST_PATH = re.compile(r"^/api/v1/projects/([^/]+)/inbox$")
INBOX_DETAIL_PATH = re.compile(r"^/api/v1/projects/([^/]+)/inbox/([^/]+)$")
INBOX_COMMAND_PATH = re.compile(r"^/api/v1/projects/([^/]+)/inbox/([^/]+)/(answers|closure)$")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class LoopbackHTTPServer(ThreadingHTTPServer):
    """Always bind to IPv4 loopback; no CLI option permits another interface."""

    daemon_threads = True
    allow_reuse_address = True


class LocalDemoHandler(BaseHTTPRequestHandler):
    runtime: PrototypeRuntime
    server_version = "NorthstarLocalDemo/0.1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format: str, *args: Any) -> None:
        # Avoid logging request bodies, query values, paths, or user-controlled data.
        print(f"[local-demo] {self.client_address[0]} {self.command}")

    def _headers_are_safe(self, *, write: bool = False) -> bool:
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in allowed_hosts:
            self._send_json(403, {"error": "host_not_allowed", "message": "Use the loopback demo URL."})
            return False
        if write:
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin is not None and origin not in allowed_origins:
                self._send_json(403, {"error": "origin_not_allowed", "message": "Cross-origin writes are disabled."})
                return False
            if self.headers.get("X-Local-Prototype") != "1":
                self._send_json(403, {"error": "prototype_header_required", "message": "This local demo accepts only same-origin prototype requests."})
                return False
        return True

    def _send_headers(
        self,
        status: int,
        content_type: str,
        body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(
        self,
        status: int,
        value: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", body, extra_headers)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": code, "message": message})

    def _read_json(self) -> dict[str, Any] | None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._error(400, "invalid_request", "Transfer-encoded request bodies are not supported.")
            return None
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(415, "unsupported_media_type", "Use application/json.")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(400, "invalid_request", "A valid Content-Length is required.")
            return None
        if length < 0:
            self._error(400, "invalid_request", "Invalid request length.")
            return None
        if length > MAX_JSON_BODY_BYTES:
            self._error(413, "request_too_large", "The request body exceeds the local demo limit.")
            return None
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(400, "invalid_json", "The request body must be valid UTF-8 JSON.")
            return None
        if not isinstance(value, dict):
            self._error(400, "invalid_json", "The JSON request body must be an object.")
            return None
        return value

    def _run(self, operation):
        try:
            operation()
        except InboxForbidden:
            self._error(403, "project_access_denied", "This project is not available in the local demo.")
        except InboxNotFound:
            self._error(404, "not_found", "The requested project or inbox item was not found.")
        except StaleQuestionRevision:
            self._error(409, "stale_revision", "This inbox item changed. Refresh it before deciding.")
        except ControlPlaneError:
            self._error(409, "command_conflict", "The command conflicts with the current project state.")
        except ValueError:
            self._error(422, "invalid_request", "The command is invalid or missing required fields.")
        except EventStoreError:
            self._error(500, "canonical_store_error", "The local project store rejected the operation.")
        except Exception:
            self.log_error("Unhandled local demo error", exc_info=True)
            self._error(500, "internal_error", "The local demo could not complete the request.")

    def do_GET(self) -> None:
        if not self._headers_are_safe():
            return
        parsed = urlsplit(self.path)
        path = parsed.path

        if path == "/api/v1/health":
            self._run(lambda: self._send_json(200, self.runtime.health()))
            return
        if path == "/api/v1/projects":
            self._run(lambda: self._send_json(200, {"items": self.runtime.list_projects()}))
            return

        project_match = PROJECT_PATH.fullmatch(path)
        if project_match:
            project_id = unquote(project_match.group(1))
            self._run(lambda: self._send_json(200, self.runtime.project_summary(project_id)))
            return

        collection_match = PROJECT_COLLECTION_PATH.fullmatch(path)
        if collection_match:
            project_id, collection = map(unquote, collection_match.groups())
            self._run(lambda: self._send_json(
                200, {"projectId": project_id, "items": self.runtime.project_collection(project_id, collection)},
            ))
            return

        list_match = INBOX_LIST_PATH.fullmatch(path)
        if list_match:
            project_id = unquote(list_match.group(1))
            query = parse_qs(parsed.query, keep_blank_values=True)
            if set(query) - {"status"} or len(query.get("status", [])) > 1:
                self._error(400, "invalid_query", "Only one status=open|resolved filter is supported.")
                return
            status = query.get("status", ["open"])[0]
            self._run(lambda: self._send_json(200, self.runtime.list_questions(project_id, status)))
            return

        detail_match = INBOX_DETAIL_PATH.fullmatch(path)
        if detail_match:
            project_id, question_id = map(unquote, detail_match.groups())
            self._run(lambda: self._send_question_detail(project_id, question_id))
            return

        asset = STATIC_FILES.get(path)
        if asset is not None:
            file_name, content_type = asset
            try:
                body = (STATIC_ROOT / file_name).read_bytes()
            except OSError:
                self._error(500, "asset_unavailable", "A local UI asset could not be read.")
                return
            self._send_headers(200, content_type, body)
            return
        self._error(404, "not_found", "The requested local demo resource was not found.")

    def _send_question_detail(self, project_id: str, question_id: str) -> None:
        detail = self.runtime.get_question(project_id, question_id)
        self._send_json(200, detail, {"ETag": f'"{detail["etag"]}"'})

    def do_POST(self) -> None:
        if not self._headers_are_safe(write=True):
            return
        body = self._read_json()
        if body is None:
            return
        path = urlsplit(self.path).path
        if path == "/api/v1/projects":
            self._run(lambda: self._send_json(201, {"project": self.runtime.create_project(body)}))
            return
        work_match = WORK_COLLECTION_PATH.fullmatch(path)
        if work_match:
            project_id = unquote(work_match.group(1))
            self._run(lambda: self._send_json(
                201, {"workItem": self.runtime.create_work(project_id, body)},
            ))
            return
        match = INBOX_COMMAND_PATH.fullmatch(path)
        if not match:
            self._error(404, "not_found", "The requested local demo command was not found.")
            return
        project_id, question_id, command = map(unquote, match.groups())
        if command == "answers":
            def answer() -> None:
                self.runtime.answer_question(project_id, question_id, body)
                self._send_question_detail(project_id, question_id)
            self._run(answer)
            return
        if command == "closure":
            def close() -> None:
                self.runtime.close_question(project_id, question_id, body)
                self._send_question_detail(project_id, question_id)
            self._run(close)
            return
        self._error(404, "not_found", "The requested local demo command was not found.")

    def do_OPTIONS(self) -> None:
        # No CORS/preflight support: cross-origin browser writes fail closed.
        self._error(405, "method_not_allowed", "Cross-origin requests are not supported.")


def build_server(runtime: PrototypeRuntime, port: int = 4173) -> LoopbackHTTPServer:
    handler = type("BoundLocalDemoHandler", (LocalDemoHandler,), {"runtime": runtime})
    return LoopbackHTTPServer(("127.0.0.1", port), handler)
