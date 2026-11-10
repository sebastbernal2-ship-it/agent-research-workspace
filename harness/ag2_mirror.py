"""Mirror accepted AG2 discussion text into the canonical event store.

AG2's own Hub/WAL remains transport-local. The mirror uses deterministic
idempotency keys so a WAL-replay reconciliation can safely retry events.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ag2.network import EV_TEXT, BaseHubListener

from .event_store import SQLiteEventStore
from .projection import Projector


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
        run_ids_by_ag2_id: dict[str, str],
    ) -> None:
        self.event_store = event_store
        self.project_id = project_id
        self.work_id = work_id
        self.actors_by_ag2_id = actors_by_ag2_id
        self.message_types_by_ag2_id = message_types_by_ag2_id
        self.correlation_id = correlation_id
        self.run_ids_by_ag2_id = run_ids_by_ag2_id
        if set(self.actors_by_ag2_id) != set(self.run_ids_by_ag2_id):
            raise ValueError("every AG2 actor mapping must have exactly one canonical run mapping")
        self.errors: list[str] = []
        self.mirrored_envelope_ids: dict[str, str] = {}

    async def on_envelope_posted(self, envelope: Any, metadata: Any) -> None:
        if envelope.event_type != EV_TEXT:
            return
        try:
            event_data = envelope.event_data
            if not isinstance(event_data, dict) or set(event_data) - {
                "text", "canonical_message_type", "artifact_ids", "evidence_ids",
            }:
                raise ValueError("AG2 message contains unsupported fields")
            text = event_data.get("text")
            if not isinstance(text, str):
                raise ValueError("AG2 text envelope has no string 'text' field")
            actor = self.actors_by_ag2_id.get(envelope.sender_id)
            if actor is None:
                raise ValueError(f"unmapped AG2 sender {envelope.sender_id!r}")
            state, canonical_events = Projector(self.event_store).replay_snapshot(self.project_id)
            event_id = _stable_id("event.ag2", envelope.channel_id, envelope.envelope_id)
            already_recorded = any(item["eventId"] == event_id for item in canonical_events)
            if state["project"] is None:
                raise ValueError(f"unknown canonical project {self.project_id!r}")
            work = state["workItems"].get(self.work_id)
            if work is None:
                raise ValueError(f"work {self.work_id!r} is outside canonical project {self.project_id!r}")
            run_id = self.run_ids_by_ag2_id[envelope.sender_id]
            run = state["agentRuns"].get(run_id)
            if (
                run is None or run["workId"] != self.work_id or run["agent"] != actor
                or (run["status"] != "running" and not already_recorded)
            ):
                raise ValueError(f"AG2 sender {envelope.sender_id!r} lacks an active matching canonical run")
            run_start = next((
                item for item in reversed(canonical_events)
                if item["eventType"] == "run.started" and item["payload"]["runId"] == run_id
            ), None)
            if run_start is None:
                raise ValueError("canonical AG2 run has no start event")
            correlation_id = self.correlation_id or run_start["correlationId"]
            if correlation_id != run_start["correlationId"]:
                raise ValueError("AG2 mirror correlation does not match the canonical run")

            def refs(name: str, collection: str) -> list[str]:
                values = event_data.get(name, [])
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    raise ValueError(f"AG2 {name} must be a list of canonical IDs")
                if len(values) != len(set(values)):
                    raise ValueError(f"AG2 {name} must not contain duplicate IDs")
                valid_work_ids = Projector._work_dependency_scope(self.work_id, state["workItems"])
                for value in values:
                    record = state[collection].get(value)
                    if record is None or record["workId"] not in valid_work_ids:
                        raise ValueError(f"AG2 message references unknown or out-of-scope {collection[:-1]} {value!r}")
                return values

            artifact_ids = refs("artifact_ids", "artifacts")
            evidence_ids = refs("evidence_ids", "evidence")
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

            message_type = event_data.get("canonical_message_type")
            if message_type is None:
                message_type = self.message_types_by_ag2_id.get(envelope.sender_id, "proposal")
            if message_type not in MESSAGE_TYPES:
                raise ValueError(f"invalid canonical message type {message_type!r}")

            content_ref, content_hash = self.event_store.put_blob(text)
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
                "correlationId": correlation_id,
                "idempotencyKey": event_id,
                "payload": {
                    "workId": self.work_id,
                    "threadId": thread_id,
                    "messageType": message_type,
                    "contentRef": content_ref,
                    "contentHash": content_hash,
                    "runId": run_id,
                    "visibleTo": visible_to,
                    **({"artifactIds": artifact_ids} if artifact_ids else {}),
                    **({"evidenceIds": evidence_ids} if evidence_ids else {}),
                },
            }
            self.event_store.append(event, expected_sequence=state["_eventSequence"])
            self.mirrored_envelope_ids[envelope.envelope_id] = event_id
        except Exception as exc:
            detail = f"AG2 envelope {getattr(envelope, 'envelope_id', '<unknown>')}: {type(exc).__name__}: {exc}"
            self.errors.append(detail)
            raise

    def raise_if_degraded(self) -> None:
        """Surface listener failures that AG2 intentionally isolates/swallow."""
        if self.errors:
            raise RuntimeError("canonical mirror degraded: " + " | ".join(self.errors))
