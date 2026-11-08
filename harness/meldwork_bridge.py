"""Call the pinned Meldwork V4 receipt parser and persist its canonical event."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .bounded_process import BoundedProcessOutputLimit, BoundedProcessTimeout, run_bounded_subprocess
from .event_store import AppendResult, SQLiteEventStore


ADAPTER_SCRIPT = Path(__file__).with_name("meldwork_adapter.cjs")


class MeldworkBridgeError(RuntimeError):
    pass


class MeldworkReceiptBridge:
    """Node subprocess adapter for the pinned Meldwork V4 receipt parser."""

    def __init__(self, event_store: SQLiteEventStore, *, node: str | None = None, timeout: float = 10.0):
        self.event_store = event_store
        self.node = node or shutil.which("node") or ""
        self.timeout = timeout
        if not self.node:
            raise MeldworkBridgeError("Node.js executable not found")

    def canonicalize(self, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
        encoded = json.dumps(request, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 60000:
            raise MeldworkBridgeError("Meldwork adapter request exceeds 60 KB")
        with tempfile.TemporaryDirectory(prefix="meldwork-adapter-") as home:
            env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
            try:
                result = run_bounded_subprocess(
                    [self.node, str(ADAPTER_SCRIPT)],
                    input_bytes=encoded.encode("utf-8"),
                    cwd=home,
                    env=env,
                    timeout=self.timeout,
                    stdout_limit=2_000_000,
                    stderr_limit=16_000,
                )
            except BoundedProcessTimeout as exc:
                raise MeldworkBridgeError("Meldwork receipt adapter timed out") from exc
            except BoundedProcessOutputLimit as exc:
                raise MeldworkBridgeError("Meldwork receipt adapter exceeded its output limit") from exc
        if result.returncode != 0:
            stderr = result.stderr[-2000:].decode("utf-8", errors="replace").strip()
            raise MeldworkBridgeError(f"Meldwork receipt rejected: {stderr}")
        try:
            response = json.loads(result.stdout.decode("utf-8"))
            event = response["event"]
            blob_content = response["blobContent"]
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
            raise MeldworkBridgeError("Meldwork adapter returned malformed JSON") from exc
        if not isinstance(event, dict) or not isinstance(blob_content, str):
            raise MeldworkBridgeError("Meldwork adapter response has invalid types")
        return event, blob_content

    def persist(self, request: dict[str, Any]) -> AppendResult:
        event, blob_content = self.canonicalize(request)
        content_ref, content_hash = self.event_store.put_blob(blob_content)
        if (content_ref, content_hash) != (event["payload"]["contentRef"], event["payload"]["contentHash"]):
            raise MeldworkBridgeError("adapter blob digest did not match canonical event reference")
        return self.event_store.append(event)
