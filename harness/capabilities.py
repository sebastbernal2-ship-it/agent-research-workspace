"""Fail-closed local capability grants for synthetic, project-scoped tests.

This policy layer is not an OS sandbox and does not itself perform tools or
side effects. Every adapter must authorize immediately before each action.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .control_plane import ControlPlaneError
from .event_store import SQLiteEventStore
from .projection import Projector


MAX_GRANT_TTL = timedelta(days=7)


class CapabilityDenied(PermissionError):
    """A grant is missing, out of scope, expired, or revoked."""


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed.astimezone(timezone.utc)


class GuardedCapabilityExecutor:
    """Check a synthetic grant immediately before a test effect callback.

    This demonstrates adapter wiring only. It is neither atomic with revocation
    nor a sandbox and must not guard real external side effects.
    """

    def __init__(self, authority: "CapabilityAuthority"):
        self.authority = authority

    def execute(
        self,
        project_id: str,
        work_id: str,
        *,
        subject: dict[str, Any],
        capability: str,
        resource_id: str,
        effect: Callable[[], Any],
    ) -> Any:
        if not callable(effect):
            raise TypeError("effect must be callable")
        self.authority.authorize(
            project_id,
            work_id,
            subject=subject,
            capability=capability,
            resource_id=resource_id,
        )
        return effect()


class CapabilityAuthority:
    """Issue/revoke user-approved grants and check their exact scope."""

    def __init__(self, store: SQLiteEventStore, *, clock: Callable[[], str] | None = None):
        self.store = store
        self.projector = Projector(store)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _require_user(actor: dict[str, Any]) -> None:
        if actor.get("actorType") != "human" or actor.get("role") != "user":
            raise ControlPlaneError("only a user actor may issue or revoke a capability")

    def grant(self, record: dict[str, Any], *, actor: dict[str, Any]) -> dict[str, Any]:
        self._require_user(actor)
        self.store.validate_record(record)
        if record.get("recordType") != "capability_grant":
            raise ControlPlaneError("grant requires a capability_grant record")
        if record.get("status") != "active" or record.get("revision") != 1:
            raise ControlPlaneError("new capability grants must be active revision 1")
        if record.get("grantedBy") != actor:
            raise ControlPlaneError("grant approver must match the authenticated user")
        if record.get("subject", {}).get("actorType") != "agent":
            raise ControlPlaneError("capability subject must be an agent identity")
        try:
            now = _parse_time(self.clock())
            created_at = _parse_time(record["createdAt"])
            expires_at = _parse_time(record["expiresAt"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ControlPlaneError("grant timestamps must be valid timezone-aware values") from exc
        if created_at > now or expires_at <= now or expires_at <= created_at:
            raise ControlPlaneError("capability grant must be current and have a future expiry")
        if expires_at - created_at > MAX_GRANT_TTL:
            raise ControlPlaneError("capability grant expiry exceeds the seven-day prototype limit")

        project_id = record["projectId"]
        state = self.projector.replay(project_id)
        if any(work_id not in state["workItems"] for work_id in record["workIds"]):
            raise ControlPlaneError("capability grant references unknown work")
        grant_id = record["grantId"]
        existing = state["capabilityGrants"].get(grant_id)
        if existing is not None:
            if existing == record:
                return existing
            raise ControlPlaneError(f"capability grant {grant_id!r} already exists")

        if any(
            question.get("status") == "open" and question.get("blocking")
            and set(question.get("workIds", [])) & set(record["workIds"])
            for question in state["questions"].values()
        ):
            raise ControlPlaneError("cannot grant capabilities while a blocking question covers affected work")

        record_hash = self.store.put_record(record)
        events = [self._event(
            project_id,
            "capability.granted",
            actor,
            {
                "grantId": grant_id,
                "workIds": record["workIds"],
                "capability": record["capability"],
                "recordHash": record_hash,
            },
            f"event.capability.granted.{grant_id}",
            record["createdAt"],
        )]
        for work_id in record["workIds"]:
            old_work = state["workItems"][work_id]
            updated_work = deepcopy(old_work)
            updated_work["revision"] += 1
            updated_work["updatedAt"] = record["createdAt"]
            updated_work["capabilityGrantIds"] = sorted({*old_work["capabilityGrantIds"], grant_id})
            work_hash = self.store.put_record(updated_work)
            events.append(self._event(
                project_id,
                "work.revised",
                actor,
                {
                    "workId": work_id,
                    "fromRevision": old_work["revision"],
                    "toRevision": updated_work["revision"],
                    "recordHash": work_hash,
                },
                f"event.work.revised.{work_id}.r{updated_work['revision']}",
                record["createdAt"],
            ))
        self.store.append_many(events)
        return self.projector.replay(project_id)["capabilityGrants"][grant_id]

    def revoke(
        self,
        project_id: str,
        grant_id: str,
        *,
        actor: dict[str, Any],
        reason: str,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        self._require_user(actor)
        if not isinstance(reason, str) or not reason.strip():
            raise ControlPlaneError("capability revocation requires a reason")
        state = self.projector.replay(project_id)
        old = state["capabilityGrants"].get(grant_id)
        if old is None:
            raise ControlPlaneError(f"unknown capability grant {grant_id!r}")
        if old["status"] == "revoked":
            if old["revokedBy"] == actor and old["revokeReason"] == reason.strip():
                return old
            raise ControlPlaneError(f"capability grant {grant_id!r} is already revoked")

        when = occurred_at or self.clock()
        try:
            if _parse_time(when) < _parse_time(old["createdAt"]):
                raise ControlPlaneError("revocation time cannot precede grant creation")
        except (TypeError, ValueError, AttributeError, KeyError) as exc:
            raise ControlPlaneError("revocation time is invalid") from exc
        revoked = deepcopy(old)
        revoked.update({
            "revision": old["revision"] + 1,
            "status": "revoked",
            "revokedBy": actor,
            "revokedAt": when,
            "revokeReason": reason.strip(),
        })
        record_hash = self.store.put_record(revoked)
        event = self._event(
            project_id,
            "capability.revoked",
            actor,
            {"grantId": grant_id, "recordHash": record_hash, "revokeReason": reason.strip()},
            f"event.capability.revoked.{grant_id}.r{revoked['revision']}",
            when,
        )
        self.store.append(event)
        return self.projector.replay(project_id)["capabilityGrants"][grant_id]

    def authorize(
        self,
        project_id: str,
        work_id: str,
        *,
        subject: dict[str, Any],
        capability: str,
        resource_id: str,
        at: str | None = None,
    ) -> dict[str, Any]:
        try:
            now = _parse_time(at or self.clock())
        except (TypeError, ValueError, AttributeError) as exc:
            raise CapabilityDenied("invalid authorization time") from exc
        state = self.projector.replay(project_id)
        work = state["workItems"].get(work_id)
        if work is None or work["status"] not in {"ready", "running", "verifying"}:
            raise CapabilityDenied("unknown or non-runnable work scope")
        if any(state["workItems"][dependency]["status"] != "completed" for dependency in work["dependsOn"]):
            raise CapabilityDenied("work dependencies are not complete")
        for grant in state["capabilityGrants"].values():
            if (
                grant["status"] != "active"
                or grant["projectId"] != project_id
                or work_id not in grant["workIds"]
                or grant["subject"] != subject
                or grant["capability"] != capability
                or resource_id not in grant["resourceIds"]
            ):
                continue
            try:
                if _parse_time(grant["createdAt"]) <= now < _parse_time(grant["expiresAt"]):
                    return grant
            except (TypeError, ValueError, AttributeError):
                continue
        raise CapabilityDenied("no active capability grant matches this exact project/work/subject/action/resource")

    def _event(
        self,
        project_id: str,
        event_type: str,
        actor: dict[str, Any],
        payload: dict[str, Any],
        event_id: str,
        occurred_at: str,
    ) -> dict[str, Any]:
        if len(event_id) > 120:
            event_id = f"event.{sha256(event_id.encode('utf-8')).hexdigest()}"
        return {
            "recordType": "event",
            "schemaVersion": "0.1",
            "eventId": event_id,
            "projectId": project_id,
            "eventType": event_type,
            "actor": actor,
            "occurredAt": occurred_at,
            "causationId": None,
            "correlationId": project_id,
            "idempotencyKey": f"idem.{event_id}",
            "payload": payload,
        }
