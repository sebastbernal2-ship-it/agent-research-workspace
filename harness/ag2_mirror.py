"""Mirror accepted AG2 discussion text into the canonical event store.

AG2's own Hub/WAL remains transport-local. The mirror uses deterministic
idempotency keys so a WAL-replay reconciliation can safely retry events.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ag2.network import EV_TEXT, BaseHubListener

from .event_store import SQLiteEventStore


MESSAGE_TYPES = {"proposal", "question", "challenge", "evidence", "revision", "decision", "status"}


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}.{digest}"


class CanonicalAG2Mirror(BaseHubListener):
    """Project a selected AG2 channel's accepted text envelopes to events."""

    def __init__(
        self,
        event_store: SQLiteEventStore,
        *,
        project_id: str,
        work_id: str,
        actors_by_ag2_id: dict[str, dict[str, str]],
        message_types_by_ag2_id: dict[str, str],
        correlation_id: str | None = None,
        run_ids_by_ag2_id: dict[str, str] | None = None,
    ) -> None:
        self.event_store = event_store
        self.project_id = project_id
        self.work_id = work_id
        self.actors_by_ag2_id = actors_by_ag2_id
        self.message_types_by_ag2_id = message_types_by_ag2_id
        self.correlation_id = correlation_id
        self.run_ids_by_ag2_id = run_ids_by_ag2_id or {}
        self.errors: list[str] = []
        self.mirrored_envelope_ids: dict[str, str] = {}

    async def on_envelope_posted(self, envelope: Any, metadata: Any) -> None:
        if envelope.event_type != EV_TEXT:
            return
        try:
            text = envelope.event_data.get("text")
            if not isinstance(text, str):
                raise ValueError("AG2 text envelope has no string 'text' field")
            actor = self.actors_by_ag2_id.get(envelope.sender_id)
            if actor is None:
                raise ValueError(f"unmapped AG2 sender {envelope.sender_id!r}")
            if self.run_ids_by_ag2_id and envelope.sender_id not in self.run_ids_by_ag2_id:
                raise ValueError(f"AG2 sender {envelope.sender_id!r} has no canonical run assignment")

            visible_to: list[str] = []
            if envelope.audience is None:
                recipients = [participant.agent_id for participant in metadata.participants]
            else:
                recipients = envelope.audience
            for recipient in recipients:
                mapped = self.actors_by_ag2_id.get(recipient)
                if mapped is not None and mapped["actorId"] not in visible_to:
                    visible_to.append(mapped["actorId"])
            if actor["actorId"] not in visible_to:
                visible_to.append(actor["actorId"])

            message_type = envelope.event_data.get("canonical_message_type")
            if message_type is None:
                message_type = self.message_types_by_ag2_id.get(envelope.sender_id, "proposal")
            if message_type not in MESSAGE_TYPES:
                raise ValueError(f"invalid canonical message type {message_type!r}")

            content_ref, content_hash = self.event_store.put_blob(text)
            event_id = _stable_id("event.ag2", envelope.channel_id, envelope.envelope_id)
            thread_id = _stable_id("thread.ag2", envelope.channel_id)
            causation_id = None
            if envelope.causation_id:
                causation_id = self.mirrored_envelope_ids.get(
                    envelope.causation_id,
                    _stable_id("event.ag2", envelope.channel_id, envelope.causation_id),
                )
            event = {
                "recordType": "event",
                "schemaVersion": "0.1",
                "eventId": event_id,
                "projectId": self.project_id,
                "eventType": "discussion.message_added",
                "actor": actor,
                "occurredAt": envelope.created_at,
                "causationId": causation_id,
                "correlationId": self.correlation_id or thread_id,
                "idempotencyKey": event_id,
                "payload": {
                    "workId": self.work_id,
                    "threadId": thread_id,
                    "messageType": message_type,
                    "contentRef": content_ref,
                    "contentHash": content_hash,
                    **({"runId": self.run_ids_by_ag2_id[envelope.sender_id]} if envelope.sender_id in self.run_ids_by_ag2_id else {}),
                    "visibleTo": visible_to,
                },
            }
            self.event_store.append(event)
            self.mirrored_envelope_ids[envelope.envelope_id] = event_id
        except Exception as exc:
            detail = f"AG2 envelope {getattr(envelope, 'envelope_id', '<unknown>')}: {type(exc).__name__}: {exc}"
            self.errors.append(detail)
            raise

    def raise_if_degraded(self) -> None:
        """Surface listener failures that AG2 intentionally isolates/swallow."""
        if self.errors:
            raise RuntimeError("canonical mirror degraded: " + " | ".join(self.errors))
