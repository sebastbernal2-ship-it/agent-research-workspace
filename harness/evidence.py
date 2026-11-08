"""Canonical evidence registration for already-retrieved source snapshots.

This module does not fetch URLs. It hashes and stores caller-supplied source
bytes, records source metadata, and appends evidence to the project event log.
A content hash proves byte integrity, not source authenticity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable
from urllib.parse import urlsplit

from .event_store import AppendResult, EventStoreError, SQLiteEventStore
from .projection import Projector


MAX_SOURCE_SNAPSHOT_BYTES = 5 * 1024 * 1024
_ALLOWED_URI_SCHEMES = {"http", "https"}


class EvidenceRegistrationError(EventStoreError):
    """Evidence cannot be registered with the requested provenance."""


@dataclass(frozen=True)
class EvidenceReceipt:
    record: dict[str, Any]
    event: dict[str, Any]
    duplicate: bool


class EvidenceRegistrar:
    """Store immutable source bytes and register a claim-linked evidence record."""

    def __init__(
        self,
        store: SQLiteEventStore,
        *,
        clock: Callable[[], str] | None = None,
        max_source_bytes: int = MAX_SOURCE_SNAPSHOT_BYTES,
    ) -> None:
        if not isinstance(max_source_bytes, int) or isinstance(max_source_bytes, bool) or max_source_bytes < 1:
            raise ValueError("max_source_bytes must be a positive integer")
        self.store = store
        self.projector = Projector(store)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.max_source_bytes = max_source_bytes

    @staticmethod
    def _validate_source_uri(uri: str) -> None:
        if not isinstance(uri, str) or not uri or len(uri) > 2048:
            raise EvidenceRegistrationError("source_uri must be a non-empty URI no longer than 2048 characters")
        if any(character.isspace() or ord(character) < 0x20 for character in uri):
            raise EvidenceRegistrationError("source_uri must percent-encode whitespace and control characters")
        try:
            parsed = urlsplit(uri)
            host = parsed.hostname
        except ValueError as exc:
            raise EvidenceRegistrationError("source_uri is malformed") from exc
        if (
            parsed.scheme.lower() not in _ALLOWED_URI_SCHEMES or not host
            or parsed.username is not None or parsed.password is not None or parsed.fragment
        ):
            raise EvidenceRegistrationError("source_uri must be HTTP(S), include a host, and omit credentials/fragments")

    def register(
        self,
        project_id: str,
        work_id: str,
        *,
        evidence_id: str,
        claim_text: str,
        stance: str,
        source_uri: str,
        source_title: str,
        source_content: bytes | str,
        actor: dict[str, str],
        retrieved_at: str | None = None,
        source_publisher: str | None = None,
        published_at: str | None = None,
        locator: str | None = None,
        content_media_type: str | None = None,
        run_id: str | None = None,
        review_status: str = "unreviewed",
        occurred_at: str | None = None,
    ) -> EvidenceReceipt:
        """Register supplied bytes without network access or URL dereferencing."""
        for label, value in (("project_id", project_id), ("work_id", work_id), ("evidence_id", evidence_id)):
            if not isinstance(value, str) or not value:
                raise EvidenceRegistrationError(f"{label} must be a non-empty string")
        if run_id is not None and (not isinstance(run_id, str) or not run_id):
            raise EvidenceRegistrationError("run_id must be a non-empty string when supplied")
        self._validate_source_uri(source_uri)
        if isinstance(source_content, str):
            raw_content = source_content.encode("utf-8")
        elif isinstance(source_content, bytes):
            raw_content = source_content
        else:
            raise EvidenceRegistrationError("source_content must be bytes or UTF-8 text")
        if not raw_content or len(raw_content) > self.max_source_bytes:
            raise EvidenceRegistrationError(
                f"source snapshot must contain 1..{self.max_source_bytes} bytes"
            )

        state, events = self.projector.replay_snapshot(project_id)
        if state["project"] is None or work_id not in state["workItems"]:
            raise EvidenceRegistrationError("evidence must reference an existing project and work item")
        existing = state["evidence"].get(evidence_id)
        when = occurred_at or (existing["createdAt"] if existing is not None else self.clock())
        retrieved = retrieved_at or (
            existing["source"]["retrievedAt"] if existing is not None else when
        )
        content_hash = sha256(raw_content).hexdigest()
        source: dict[str, Any] = {
            "uri": source_uri,
            "title": source_title,
            "retrievedAt": retrieved,
            "contentHash": content_hash,
        }
        for field, value in (
            ("publisher", source_publisher),
            ("publishedAt", published_at),
            ("locator", locator),
            ("contentMediaType", content_media_type),
        ):
            if value is not None:
                source[field] = value

        record: dict[str, Any] = {
            "recordType": "evidence",
            "schemaVersion": "0.1",
            "projectId": project_id,
            "evidenceId": evidence_id,
            "workId": work_id,
            "claimText": claim_text,
            "stance": stance,
            "source": source,
            "createdBy": actor,
            "createdAt": when,
            "reviewStatus": review_status,
        }
        if run_id is not None:
            record["runId"] = run_id
        self.store.validate_record(record)
        if existing is not None and existing != record:
            raise EvidenceRegistrationError(f"evidence ID {evidence_id!r} already has different immutable content")
        if existing is None and run_id is not None:
            run = state["agentRuns"].get(run_id)
            if (
                run is None or run["status"] != "running" or run["workId"] != work_id
                or run["agent"] != actor
            ):
                raise EvidenceRegistrationError("evidence run attribution must match an active assigned run")
        _, stored_hash = self.store.put_blob(raw_content)
        if stored_hash != content_hash:
            raise EvidenceRegistrationError("source snapshot digest changed while storing")
        record_hash = self.store.put_record(record)
        event_id = f"event.evidence.registered.{evidence_id}"
        if len(event_id) > 120:
            event_id = f"event.evidence.{sha256(event_id.encode('utf-8')).hexdigest()}"
        start_event = next((
            event for event in reversed(events)
            if event["eventType"] == "run.started" and event["payload"]["runId"] == run_id
        ), None) if run_id is not None else None
        event = {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": event_id,
            "projectId": project_id,
            "eventType": "evidence.registered",
            "actor": actor,
            "occurredAt": when,
            "causationId": None if start_event is None else start_event["eventId"],
            "correlationId": project_id if start_event is None else start_event["correlationId"],
            "idempotencyKey": f"idem.{event_id}",
            "payload": {
                "evidenceId": evidence_id,
                "workId": work_id,
                "stance": stance,
                "sourceHash": content_hash,
                "recordHash": record_hash,
            },
        }
        result: AppendResult = self.store.append(event, expected_sequence=state["_eventSequence"])
        return EvidenceReceipt(record=record, event=result.event, duplicate=result.duplicate)
