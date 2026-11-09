"""SQLite-backed local work queue with expiring, fenced worker leases.

Queue rows are operational metadata, not canonical project state. Expired leases
enter recovery_required and are never silently handed to another worker.
"""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
from typing import Any

from .event_store import EventStoreError, IdempotencyConflict, SQLiteEventStore
from .projection import Projector


class QueueError(EventStoreError):
    """Invalid, conflicting, or stale durable queue operation."""


MIN_LEASE_SECONDS = 1
MAX_LEASE_SECONDS = 7 * 24 * 60 * 60


class SQLiteWorkQueue:
    def __init__(self, store: SQLiteEventStore):
        self.store = store
        self.projector = Projector(store)

    @staticmethod
    def _json(value: Any) -> str:
        try:
            encoded = json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise QueueError("job descriptor must contain only finite JSON values") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise QueueError("job descriptor exceeds the 64 KiB limit")
        return encoded

    @staticmethod
    def _positive(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise QueueError(f"{label} must be a positive finite number")
        return float(value)

    @classmethod
    def _lease_seconds(cls, value: float) -> float:
        seconds = cls._positive(value, "lease_seconds")
        if seconds < MIN_LEASE_SECONDS or seconds > MAX_LEASE_SECONDS:
            raise QueueError(
                f"lease_seconds must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}"
            )
        return seconds

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise QueueError(f"{label} must be a non-empty string")
        return value

    @classmethod
    def _validate_descriptor(cls, descriptor: Any) -> None:
        if not isinstance(descriptor, dict):
            raise QueueError("job payload must be a versioned execution descriptor")
        required = {
            "descriptorVersion", "executionKey", "actor", "verificationActor", "adapter",
            "contextHash", "correlationId",
        }
        if not required.issubset(descriptor) or set(descriptor) - required - {"parentRunId"}:
            raise QueueError("job descriptor has missing or unsupported fields")
        if type(descriptor["descriptorVersion"]) is not int or descriptor["descriptorVersion"] != 1:
            raise QueueError("unsupported job descriptor version")
        cls._required_text(descriptor["executionKey"], "executionKey")
        cls._required_text(descriptor["correlationId"], "correlationId")
        for actor_key in ("actor", "verificationActor"):
            actor = descriptor[actor_key]
            if not isinstance(actor, dict) or set(actor) != {"actorType", "actorId", "role"}:
                raise QueueError(f"descriptor {actor_key} must contain actorType, actorId, and role")
            for key in ("actorType", "actorId", "role"):
                cls._required_text(actor[key], f"{actor_key}.{key}")
        adapter = descriptor["adapter"]
        if not isinstance(adapter, dict) or set(adapter) != {"name", "version"}:
            raise QueueError("descriptor adapter must contain name and version")
        cls._required_text(adapter["name"], "adapter.name")
        cls._required_text(adapter["version"], "adapter.version")
        digest = descriptor["contextHash"]
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise QueueError("contextHash must be a lowercase SHA-256 digest")
        if "parentRunId" in descriptor and descriptor["parentRunId"] is not None:
            cls._required_text(descriptor["parentRunId"], "parentRunId")

    def enqueue(
        self,
        job_id: str,
        project_id: str,
        work_id: str,
        payload: dict[str, Any],
        *,
        now: float,
    ) -> dict[str, Any]:
        self._required_text(job_id, "job_id")
        self._required_text(project_id, "project_id")
        self._required_text(work_id, "work_id")
        self._positive(now, "now")
        self._validate_descriptor(payload)
        state = self.projector.replay(project_id)
        if work_id not in state["workItems"]:
            raise QueueError(f"unknown work item {work_id!r}")
        payload_json = self._json(payload)
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            if row is not None:
                if (
                    row["project_id"] != project_id
                    or row["work_id"] != work_id
                    or row["payload_json"] != payload_json
                ):
                    raise IdempotencyConflict(f"scheduled job {job_id!r} was reused with different content")
                conn.execute("COMMIT")
                return self._decode(row)
            conn.execute(
                "INSERT INTO scheduled_work "
                "(job_id, project_id, work_id, payload_json, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (job_id, project_id, work_id, payload_json, float(now), float(now)),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise QueueError(f"work already has an open scheduled job: {project_id}/{work_id}") from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.get(job_id)

    def claim_next(
        self,
        worker_id: str,
        *,
        now: float,
        lease_seconds: float,
    ) -> dict[str, Any] | None:
        self._required_text(worker_id, "worker_id")
        now = self._positive(now, "now")
        lease_seconds = self._lease_seconds(lease_seconds)
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE scheduled_work SET state='recovery_required', lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE state='leased' AND lease_expires_at<=?",
                (now, now),
            )
            candidates = conn.execute(
                "SELECT * FROM scheduled_work WHERE state='queued' ORDER BY created_at, job_id"
            ).fetchall()
            for row in candidates:
                state = self.projector.replay(row["project_id"])
                work = state["workItems"].get(row["work_id"])
                if work is None or not self._is_runnable(state, row["work_id"]):
                    continue
                token = secrets.token_urlsafe(32)
                generation = row["lease_generation"] + 1
                expires = now + lease_seconds
                conn.execute(
                    "UPDATE scheduled_work SET state='leased', lease_owner=?, lease_token=?, "
                    "lease_generation=?, lease_expires_at=?, updated_at=? WHERE job_id=? AND state='queued'",
                    (worker_id, token, generation, expires, now, row["job_id"]),
                )
                leased = conn.execute(
                    "SELECT * FROM scheduled_work WHERE job_id=?", (row["job_id"],),
                ).fetchone()
                conn.execute("COMMIT")
                return self._decode(leased, expose_token=True)
            conn.execute("COMMIT")
            return None
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def renew(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        generation: int,
        *,
        now: float,
        lease_seconds: float,
    ) -> dict[str, Any]:
        now = self._positive(now, "now")
        lease_seconds = self._lease_seconds(lease_seconds)
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            self._assert_lease(row, worker_id, lease_token, generation, now)
            expires = now + lease_seconds
            conn.execute(
                "UPDATE scheduled_work SET lease_expires_at=?, updated_at=? WHERE job_id=?",
                (expires, now, job_id),
            )
            renewed = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("COMMIT")
            return self._decode(renewed, expose_token=True)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def complete(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        generation: int,
        *,
        now: float,
    ) -> dict[str, Any]:
        now = self._positive(now, "now")
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            self._assert_lease(row, worker_id, lease_token, generation, now)
            state = self.projector.replay(row["project_id"])
            if state["workItems"][row["work_id"]]["status"] != "completed":
                raise QueueError("a scheduled job can complete only after canonical work is completed")
            if any(
                run["workId"] == row["work_id"] and run["status"] == "running"
                for run in state["agentRuns"].values()
            ):
                raise QueueError("scheduled work still has active agent runs")
            conn.execute(
                "UPDATE scheduled_work SET state='completed', lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE job_id=?",
                (now, job_id),
            )
            completed = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("COMMIT")
            return self._decode(completed)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def release(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        generation: int,
        *,
        now: float,
    ) -> dict[str, Any]:
        now = self._positive(now, "now")
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            self._assert_lease(row, worker_id, lease_token, generation, now)
            state = self.projector.replay(row["project_id"])
            target = self._post_lease_state(state, row["work_id"])
            conn.execute(
                "UPDATE scheduled_work SET state=?, lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE job_id=?",
                (target, now, job_id),
            )
            released = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("COMMIT")
            return self._decode(released)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def abandon(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        generation: int,
        *,
        now: float,
    ) -> dict[str, Any]:
        """Move a leased job to explicit recovery after an unsafe/unexpected worker failure."""
        now = self._positive(now, "now")
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            self._assert_lease(row, worker_id, lease_token, generation, now)
            conn.execute(
                "UPDATE scheduled_work SET state='recovery_required', lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE job_id=?",
                (now, job_id),
            )
            abandoned = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("COMMIT")
            return self._decode(abandoned)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def fail(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        generation: int,
        *,
        now: float,
    ) -> dict[str, Any]:
        """Close a leased job after canonical work is failed or cancelled."""
        now = self._positive(now, "now")
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            self._assert_lease(row, worker_id, lease_token, generation, now)
            state = self.projector.replay(row["project_id"])
            work = state["workItems"].get(row["work_id"])
            if work is None or work["status"] not in {"failed", "cancelled"}:
                raise QueueError("a scheduled job can fail only after canonical work fails or is cancelled")
            if any(
                run["workId"] == row["work_id"] and run["status"] == "running"
                for run in state["agentRuns"].values()
            ):
                raise QueueError("scheduled work still has active agent runs")
            conn.execute(
                "UPDATE scheduled_work SET state='failed', lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE job_id=?",
                (now, job_id),
            )
            failed = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("COMMIT")
            return self._decode(failed)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def recover_expired(
        self,
        job_id: str,
        *,
        now: float,
        attest_no_live_worker: bool = False,
    ) -> dict[str, Any]:
        now = self._positive(now, "now")
        if attest_no_live_worker is not True:
            raise QueueError("recovery requires an explicit no-live-worker attestation")
        conn = self.store._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise QueueError(f"unknown scheduled job {job_id!r}")
            if row["state"] != "recovery_required":
                raise QueueError("job is not awaiting expired-lease recovery")
            if now < row["updated_at"]:
                raise QueueError("recovery time cannot precede the recorded lease-expiry observation")
            state = self.projector.replay(row["project_id"])
            target = self._post_lease_state(state, row["work_id"])
            conn.execute(
                "UPDATE scheduled_work SET state=?, lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE job_id=?",
                (target, now, job_id),
            )
            recovered = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
            conn.execute("COMMIT")
            return self._decode(recovered)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, job_id: str) -> dict[str, Any]:
        conn = self.store._connect()
        try:
            row = conn.execute("SELECT * FROM scheduled_work WHERE job_id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise QueueError(f"unknown scheduled job {job_id!r}")
        return self._decode(row)

    @classmethod
    def _post_lease_state(cls, state: dict[str, Any], work_id: str) -> str:
        work = state["workItems"].get(work_id)
        if work is None:
            return "recovery_required"
        if work["status"] == "completed":
            return "completed"
        if work["status"] in {"failed", "cancelled"}:
            return "failed"
        if work["status"] in {"running", "verifying"} or any(
            run["workId"] == work_id and run["status"] == "running"
            for run in state["agentRuns"].values()
        ):
            return "recovery_required"
        return "queued"

    @staticmethod
    def _is_runnable(state: dict[str, Any], work_id: str) -> bool:
        if state["project"] is None or state["project"]["status"] != "active":
            return False
        work = state["workItems"].get(work_id)
        if work is None or work["status"] != "ready":
            return False
        if any(state["workItems"][dep]["status"] != "completed" for dep in work["dependsOn"]):
            return False
        if any(
            question["status"] == "open" and question["blocking"] and work_id in question["workIds"]
            for question in state["questions"].values()
        ):
            return False
        return not any(
            run["workId"] == work_id and run["status"] == "running"
            for run in state["agentRuns"].values()
        )

    @staticmethod
    def _assert_lease(row: Any, worker_id: str, lease_token: str, generation: int, now: float) -> None:
        if row is None:
            raise QueueError("unknown scheduled job")
        if (
            row["state"] != "leased"
            or row["lease_owner"] != worker_id
            or row["lease_token"] != lease_token
            or row["lease_generation"] != generation
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] <= now
        ):
            raise QueueError("lease is stale, expired, or owned by another worker")

    @staticmethod
    def _decode(row: Any, *, expose_token: bool = False) -> dict[str, Any]:
        return {
            "jobId": row["job_id"],
            "projectId": row["project_id"],
            "workId": row["work_id"],
            "payload": json.loads(row["payload_json"]),
            "state": row["state"],
            "leaseOwner": row["lease_owner"],
            "leaseToken": row["lease_token"] if expose_token else None,
            "leaseGeneration": row["lease_generation"],
            "leaseExpiresAt": row["lease_expires_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
