"""Canonical v0.2 external-effect intent and status commands.

This ledger records intent; it does not dispatch providers or authorize a live
identity. Hosting services must authenticate and authorize before calling it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable
from urllib.parse import urlsplit

from .event_store import SQLiteEventStore
from .projection import Projector


class EffectLedgerError(ValueError):
    """Invalid or stale canonical effect command."""


_ALLOWED = {
    "prepared": {"dispatching", "cancelled"},
    "dispatching": {"confirmed", "ambiguous", "rejected", "manual_review"},
    "confirmed": {"recorded", "manual_review"},
    "ambiguous": {"confirmed", "rejected", "manual_review"},
    "manual_review": {"confirmed", "rejected", "cancelled"},
    "recorded": set(), "rejected": set(), "cancelled": set(),
}


class EffectLedger:
    """Append immutable effect intents and compare-and-set lifecycle events."""

    def __init__(self, store: SQLiteEventStore, *, clock: Callable[[], str] | None = None):
        self.store = store
        self.projector = Projector(store)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def create_intent(
        self, intent: dict[str, Any], *, actor: dict[str, str], event_id: str,
        expected_sequence: int | None = None,
        queue_lease: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.store.validate_record(intent)
        if intent.get("recordType") != "effect_intent" or intent.get("schemaVersion") != "0.2":
            raise EffectLedgerError("effect intent must use canonical v0.2")
        try:
            created_at = datetime.fromisoformat(intent["createdAt"].replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(intent["expiresAt"].replace("Z", "+00:00"))
            endpoint = urlsplit(intent["endpoint"])
            now = datetime.fromisoformat(self.clock().replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise EffectLedgerError("effect timestamps and endpoint must be valid") from exc
        if any(value.tzinfo is None for value in (created_at, expires_at, now)):
            raise EffectLedgerError("effect timestamps must be timezone-aware")
        if created_at > now or expires_at <= now or expires_at <= created_at or expires_at - created_at > timedelta(days=7):
            raise EffectLedgerError("effect intent must have a current expiry within seven days")
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment:
            raise EffectLedgerError("effect endpoint must be credential-free HTTPS without a fragment")
        project_id = intent["projectId"]
        state, events = self.projector.replay_snapshot(project_id)
        effect_id = intent["effectId"]
        existing = state["effects"].get(effect_id)
        if existing is not None:
            if existing["intent"] == intent:
                return existing
            raise EffectLedgerError(f"effect ID {effect_id!r} is already bound to another intent")
        run = state["agentRuns"].get(intent["runId"])
        work = state["workItems"].get(intent["workId"])
        if work is None or run is None or run["workId"] != intent["workId"] or run["status"] != "running":
            raise EffectLedgerError("effect intent requires its active canonical run and work")
        if actor != run["agent"] or intent["principal"] != actor:
            raise EffectLedgerError("effect intent actor must match the active run principal")
        grant = state["capabilityGrants"].get(intent["capabilityGrantId"])
        if (
            grant is None or grant["status"] != "active"
            or grant["revision"] != intent["capabilityRevision"]
            or intent["workId"] not in grant["workIds"]
            or grant["subject"] != actor
            or intent["resourceId"] not in grant["resourceIds"]
            or grant["capability"] != intent["action"]
        ):
            raise EffectLedgerError("effect intent does not match an active exact-scope capability grant")
        run_start = next((event for event in reversed(events) if event["eventType"] == "run.started" and event["payload"]["runId"] == intent["runId"]), None)
        if run_start is None:
            raise EffectLedgerError("effect intent run has no canonical start event")
        correlation_id = run_start["correlationId"]
        if intent.get("correlationId") not in (None, correlation_id):
            raise EffectLedgerError("effect intent correlation must match canonical run context")
        record_hash = self.store.put_record(intent)
        event = self._event(
            project_id, "effect.intent_created", actor,
            {"effectId": effect_id, "recordHash": record_hash}, event_id,
            correlation_id, self.clock(), causation_id=intent.get("causationId"),
        )
        self.store.append_many(
            [event], expected_sequence=state["_eventSequence"] if expected_sequence is None else expected_sequence,
            queue_lease=queue_lease,
        )
        return self.projector.replay(project_id)["effects"][effect_id]

    def transition(
        self, project_id: str, effect_id: str, target: str, *, actor: dict[str, str],
        event_id: str, receipt_hash: str | None = None, receipt_ref: str | None = None,
        reason: str | None = None,
        expected_sequence: int | None = None,
        queue_lease: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state, events = self.projector.replay_snapshot(project_id)
        effect = state["effects"].get(effect_id)
        if effect is None:
            raise EffectLedgerError(f"unknown effect {effect_id!r}")
        payload: dict[str, Any] = {
            "effectId": effect_id, "workId": effect["intent"]["workId"],
            "runId": effect["intent"]["runId"], "from": effect["status"], "to": target,
        }
        if receipt_hash is not None:
            payload["receiptHash"] = receipt_hash
        if receipt_ref is not None:
            payload["receiptRef"] = receipt_ref
        if reason is not None:
            payload["reason"] = reason
        previous = next((item for item in events if item["eventId"] == event_id), None)
        if previous is not None:
            if previous["eventType"] != "effect.status_changed" or previous["payload"] != payload:
                raise EffectLedgerError("effect transition event ID was reused with different content")
            return effect
        if target not in _ALLOWED.get(effect["status"], set()):
            raise EffectLedgerError(f"forbidden effect transition {effect['status']!r} -> {target!r}")
        if target == "dispatching":
            intent = effect["intent"]
            grant = state["capabilityGrants"].get(intent["capabilityGrantId"])
            run = state["agentRuns"].get(intent["runId"])
            now = datetime.fromisoformat(self.clock().replace("Z", "+00:00"))
            expiry = datetime.fromisoformat(intent["expiresAt"].replace("Z", "+00:00"))
            grant_expiry = datetime.fromisoformat(grant["expiresAt"].replace("Z", "+00:00")) if grant else None
            if (
                actor != intent["principal"] or run is None or run["status"] != "running"
                or grant is None or grant["status"] != "active"
                or grant["revision"] != intent["capabilityRevision"]
                or now.tzinfo is None or expiry <= now or grant_expiry is None or grant_expiry <= now
            ):
                raise EffectLedgerError("effect dispatch requires active run, unexpired intent, and current grant")
        if target == "confirmed":
            if not receipt_hash or not receipt_ref:
                raise EffectLedgerError("confirmed effect requires a receipt reference and hash")
            if not receipt_ref.startswith("blob.") or receipt_ref[5:] != receipt_hash:
                raise EffectLedgerError("effect receipt reference must match its hash")
            try:
                receipt = self.store.get_blob(receipt_hash)
            except KeyError as exc:
                raise EffectLedgerError("effect receipt blob is missing") from exc
            if sha256(receipt).hexdigest() != receipt_hash:
                raise EffectLedgerError("effect receipt blob failed hash verification")
        if target == "recorded" and not effect.get("receiptHash"):
            raise EffectLedgerError("effect cannot be recorded before receipt confirmation")
        if target in {"ambiguous", "manual_review", "rejected", "cancelled"} and not (reason and reason.strip()):
            raise EffectLedgerError("effect recovery/terminal transition requires a reason")
        event = self._event(
            project_id, "effect.status_changed", actor, payload, event_id,
            effect["correlationId"], self.clock(),
        )
        self.store.append_many(
            [event],
            expected_sequence=state["_eventSequence"] if expected_sequence is None else expected_sequence,
            queue_lease=queue_lease,
        )
        return self.projector.replay(project_id)["effects"][effect_id]

    @staticmethod
    def _event(project_id: str, event_type: str, actor: dict[str, str], payload: dict[str, Any], event_id: str, correlation_id: str, occurred_at: str, *, causation_id: str | None = None) -> dict[str, Any]:
        if not isinstance(event_id, str) or not event_id.strip():
            raise EffectLedgerError("event_id must be non-empty")
        return {
            "recordType": "event", "schemaVersion": "0.2", "eventId": event_id,
            "projectId": project_id, "eventType": event_type, "actor": actor,
            "occurredAt": occurred_at, "causationId": causation_id,
            "correlationId": correlation_id, "idempotencyKey": f"idem.{event_id}",
            "payload": payload,
        }
