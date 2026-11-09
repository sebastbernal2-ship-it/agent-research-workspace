"""Small append-only SQLite event store for the local harness prototype.

This is a reference implementation, not a multi-host production database.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .schema_registry import SchemaRegistry, SchemaRegistryError


ZERO_HASH = "0" * 64
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "v0.1" / "schema.json"


class EventStoreError(Exception):
    """Base error for rejected or corrupt canonical history."""


class IdempotencyConflict(EventStoreError):
    """An idempotency key was reused with different event content."""


class SequenceConflict(EventStoreError):
    """A caller-supplied sequence does not match the next project sequence."""


class ConcurrentWriteConflict(EventStoreError):
    """The project changed after a command read its verified snapshot."""


class IntegrityFailure(EventStoreError):
    """Stored history or content-addressed data failed verification."""


@dataclass(frozen=True)
class AppendResult:
    event: dict[str, Any]
    sequence: int
    record_hash: str
    duplicate: bool


class SQLiteEventStore:
    """Validated, per-project append-only event log with a SHA-256 chain.

    Event order and idempotency are enforced inside ``BEGIN IMMEDIATE``.
    SQLite triggers reject updates/deletes through normal SQL. Hash-chain
    verification detects tampering that bypasses those triggers.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._uri = self.path == ":memory:"
        self._connect_target = (
            f"file:canonical-{uuid4().hex}?mode=memory&cache=shared"
            if self._uri else self.path
        )
        self._queue_lease_context: ContextVar[dict[str, Any] | None] = ContextVar(
            f"event_store_queue_lease_{id(self)}", default=None,
        )
        self._memory_keeper: sqlite3.Connection | None = None
        if self._uri:
            self._memory_keeper = sqlite3.connect(
                self._connect_target, uri=True, timeout=30, isolation_level=None,
            )
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._schema_registry = SchemaRegistry(SCHEMA_PATH.parent.parent)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._connect_target, timeout=30, isolation_level=None, uri=self._uri,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def queued_write_context(self, lease: dict[str, Any], clock: Any):
        """Require canonical writes in this execution context to carry its live lease fence."""
        if not isinstance(lease, dict) or not callable(clock):
            raise EventStoreError("queued write context requires lease data and a trusted clock")
        token = self._queue_lease_context.set({**lease, "clock": clock})
        try:
            yield
        finally:
            self._queue_lease_context.reset(token)

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS canonical_events (
                    project_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    event_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    PRIMARY KEY(project_id, sequence),
                    UNIQUE(project_id, event_id),
                    UNIQUE(project_id, idempotency_key)
                );
                CREATE TRIGGER IF NOT EXISTS canonical_events_no_update
                BEFORE UPDATE ON canonical_events
                BEGIN SELECT RAISE(ABORT, 'canonical events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS canonical_events_no_delete
                BEFORE DELETE ON canonical_events
                BEGIN SELECT RAISE(ABORT, 'canonical events are append-only'); END;
                CREATE TABLE IF NOT EXISTS canonical_blobs (
                    content_hash TEXT PRIMARY KEY,
                    content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS adapter_invocations (
                    invocation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('prepared', 'executing', 'receipt_staged', 'imported', 'ambiguous')),
                    receipt_json TEXT,
                    receipt_hash TEXT,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS scheduled_work (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    work_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued', 'leased', 'recovery_required', 'completed', 'failed')),
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_generation INTEGER NOT NULL DEFAULT 0 CHECK(lease_generation >= 0),
                    lease_expires_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scheduled_work_ready
                    ON scheduled_work(state, created_at, job_id);
                CREATE UNIQUE INDEX IF NOT EXISTS scheduled_work_one_open_per_work
                    ON scheduled_work(project_id, work_id)
                    WHERE state IN ('queued', 'leased', 'recovery_required');
                CREATE TABLE IF NOT EXISTS queued_run_fences (
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    work_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    lease_generation INTEGER NOT NULL,
                    PRIMARY KEY(project_id, run_id)
                );
                CREATE TRIGGER IF NOT EXISTS canonical_blobs_no_update
                BEFORE UPDATE ON canonical_blobs
                BEGIN SELECT RAISE(ABORT, 'canonical blobs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS canonical_blobs_no_delete
                BEFORE DELETE ON canonical_blobs
                BEGIN SELECT RAISE(ABORT, 'canonical blobs are immutable'); END;
                """
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                historical = conn.execute(
                    "SELECT project_id, event_json FROM canonical_events ORDER BY project_id, sequence"
                ).fetchall()
                for row in historical:
                    event = json.loads(row["event_json"])
                    payload = event.get("payload", {})
                    if event.get("eventType") != "run.started" or "queueJobId" not in payload:
                        continue
                    if not {"runId", "workId", "queueJobId", "leaseGeneration"}.issubset(payload):
                        raise IntegrityFailure("historical queued run start has incomplete fence metadata")
                    binding = (
                        row["project_id"], payload["runId"], payload["workId"],
                        payload["queueJobId"], payload["leaseGeneration"],
                    )
                    existing = conn.execute(
                        "SELECT work_id, job_id, lease_generation FROM queued_run_fences "
                        "WHERE project_id=? AND run_id=?", binding[:2],
                    ).fetchone()
                    if existing is not None:
                        if tuple(existing) != binding[2:]:
                            raise IntegrityFailure("queued run fence metadata conflicts with canonical history")
                    else:
                        conn.execute(
                            "INSERT INTO queued_run_fences "
                            "(project_id, run_id, work_id, job_id, lease_generation) VALUES (?, ?, ?, ?, ?)",
                            binding,
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    @staticmethod
    def _canonical_json(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _record_hash(previous_hash: str, event_json: str) -> str:
        return hashlib.sha256((previous_hash + "\n" + event_json).encode("utf-8")).hexdigest()

    def validate_record(self, record: dict[str, Any]) -> None:
        try:
            self._schema_registry.validate(record)
        except SchemaRegistryError as exc:
            raise EventStoreError(str(exc)) from exc

    def _validate(self, event: dict[str, Any]) -> None:
        self.validate_record(event)
        if event.get("recordType") != "event":
            raise EventStoreError("only canonical event records may be appended")

    def put_record(self, record: dict[str, Any]) -> str:
        """Store a validated immutable non-event record; return its SHA-256."""
        self.validate_record(record)
        if record.get("recordType") == "event":
            raise EventStoreError("events belong in append(), not the blob store")
        encoded = self._canonical_json(record)
        _, digest = self.put_blob(encoded)
        return digest

    def get_record(self, record_hash: str) -> dict[str, Any]:
        """Load and validate a content-addressed record by its SHA-256."""
        raw = self.get_blob(record_hash)
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrityFailure(f"record blob {record_hash} is not valid JSON") from exc
        if not isinstance(record, dict) or self._canonical_json(record).encode("utf-8") != raw:
            raise IntegrityFailure(f"record blob {record_hash} is not canonical JSON")
        self.validate_record(record)
        if record.get("recordType") == "event":
            raise EventStoreError("events are stored in the append-only event table")
        return record

    @staticmethod
    def _assert_queue_lease(
        conn: sqlite3.Connection,
        events: list[dict[str, Any]],
        lease: dict[str, Any],
    ) -> None:
        required = {"jobId", "workerId", "leaseToken", "leaseGeneration", "now"}
        if not isinstance(lease, dict) or not required.issubset(lease):
            raise EventStoreError("queue lease claim is incomplete")
        if any(not isinstance(lease[key], str) or not lease[key] for key in ("jobId", "workerId", "leaseToken")):
            raise EventStoreError("queue lease identity fields must be non-empty strings")
        generation = lease["leaseGeneration"]
        now = lease["now"]
        if (
            isinstance(generation, bool) or not isinstance(generation, int) or generation < 1
            or isinstance(now, bool) or not isinstance(now, (int, float))
            or not math.isfinite(now) or now <= 0
        ):
            raise EventStoreError("queue lease generation or validation time is invalid")
        if any(not isinstance(event, dict) for event in events):
            raise EventStoreError("queue-claimed event batches must contain event objects")
        starts = [event for event in events if event.get("eventType") == "run.started"]
        if len(starts) > 1:
            raise EventStoreError("a queue lease may authorize at most one run.started event per batch")
        if starts:
            start = starts[0]
            payload = start.get("payload", {})
            if not isinstance(payload, dict):
                raise EventStoreError("queue-claimed run.started payload must be an object")
            if payload.get("queueJobId") != lease["jobId"] or payload.get("leaseGeneration") != generation:
                raise EventStoreError("run.started event does not match its queue lease claim")
        row = conn.execute(
            "SELECT project_id, work_id, state, lease_owner, lease_token, lease_generation, lease_expires_at "
            "FROM scheduled_work WHERE job_id=?",
            (lease["jobId"],),
        ).fetchone()
        project_ids = {event.get("projectId") for event in events}
        if (
            row is None
            or (starts and (row["project_id"] != start["projectId"] or row["work_id"] != payload.get("workId")))
            or any(project_id != row["project_id"] for project_id in project_ids)
            or row["state"] != "leased"
            or row["lease_owner"] != lease["workerId"]
            or row["lease_token"] != lease["leaseToken"]
            or row["lease_generation"] != generation
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] <= now
        ):
            raise ConcurrentWriteConflict("queue lease expired or changed before canonical write")
        for event in events:
            payload = event.get("payload", {})
            actor = event.get("actor", {})
            if (
                isinstance(payload, dict) and payload.get("workId") is not None
                and isinstance(actor, dict) and actor.get("actorType") == "agent"
                and payload.get("workId") != row["work_id"]
            ):
                raise EventStoreError("agent canonical write is outside its queued work assignment")

    @staticmethod
    def _assert_active_run_fences(
        conn: sqlite3.Connection,
        events: list[dict[str, Any]],
        queue_lease: dict[str, Any] | None = None,
    ) -> None:
        starts = {
            event.get("payload", {}).get("runId"): event
            for event in events
            if isinstance(event, dict)
            and event.get("eventType") == "run.started"
            and isinstance(event.get("payload"), dict)
            and "queueJobId" in event["payload"]
        }
        run_work_refs: list[tuple[str, str, str | None]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            run_id = payload.get("runId")
            work_id = payload.get("workId")
            if isinstance(run_id, str):
                run_work_refs.append((event.get("projectId"), run_id, work_id if isinstance(work_id, str) else None))
            record_hash = payload.get("recordHash")
            question_ref = payload.get("questionRef")
            referenced_hash = record_hash if isinstance(record_hash, str) else question_ref
            if isinstance(referenced_hash, str) and event.get("eventType") in {
                "artifact.registered", "evidence.registered", "human.question_opened",
            }:
                blob = conn.execute(
                    "SELECT content FROM canonical_blobs WHERE content_hash=?", (referenced_hash,),
                ).fetchone()
                if blob is not None:
                    try:
                        record = json.loads(bytes(blob["content"]))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        record = None
                    if isinstance(record, dict) and isinstance(record.get("runId"), str):
                        record_work = record.get("workId")
                        run_work_refs.append((
                            event.get("projectId"), record["runId"],
                            record_work if isinstance(record_work, str) else None,
                        ))

        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                continue
            actor = event.get("actor")
            work_id = event["payload"].get("workId")
            if not isinstance(actor, dict) or actor.get("actorType") != "agent" or not isinstance(work_id, str):
                continue
            queued = conn.execute(
                "SELECT job_id, lease_generation FROM scheduled_work "
                "WHERE project_id=? AND work_id=? AND state IN ('leased', 'recovery_required')",
                (event.get("projectId"), work_id),
            ).fetchone()
            if queued is not None and queue_lease is None:
                raise ConcurrentWriteConflict("agent write on queued work requires its active execution lease context")
            if queued is not None and (
                queued["job_id"] != queue_lease.get("jobId")
                or queued["lease_generation"] != queue_lease.get("leaseGeneration")
            ):
                raise ConcurrentWriteConflict("agent write is not authorized by the active queued work lease")

        for project_id, run_id, event_work_id in run_work_refs:
            start_event = starts.get(run_id)
            if start_event is not None:
                start_payload = start_event["payload"]
                if (
                    queue_lease is None
                    or start_payload.get("queueJobId") != queue_lease.get("jobId")
                    or start_payload.get("leaseGeneration") != queue_lease.get("leaseGeneration")
                    or (event_work_id is not None and event_work_id != start_payload.get("workId"))
                ):
                    raise EventStoreError("run output does not match its queued run-start lease")
                continue
            # Recovery may close an abandoned run after its lease has been fenced.
            interrupted_finish = any(
                event.get("eventType") == "run.finished"
                and isinstance(event.get("payload"), dict)
                and event["payload"].get("runId") == run_id
                and event["payload"].get("terminalStatus") == "interrupted"
                for event in events if isinstance(event, dict)
            )
            if interrupted_finish:
                continue
            fence = conn.execute(
                "SELECT work_id, job_id, lease_generation FROM queued_run_fences "
                "WHERE project_id=? AND run_id=?",
                (project_id, run_id),
            ).fetchone()
            if fence is None:
                continue
            if event_work_id is not None and event_work_id != fence["work_id"]:
                raise EventStoreError("run output references work outside its queued lease assignment")
            if queue_lease is None:
                raise ConcurrentWriteConflict("queued run output requires its active execution lease context")
            lease = conn.execute(
                "SELECT state, lease_generation FROM scheduled_work WHERE job_id=? AND project_id=?",
                (fence["job_id"], project_id),
            ).fetchone()
            if (
                lease is None or lease["state"] != "leased"
                or lease["lease_generation"] != fence["lease_generation"]
                or (
                    queue_lease is not None
                    and (fence["job_id"] != queue_lease.get("jobId")
                         or fence["lease_generation"] != queue_lease.get("leaseGeneration"))
                )
            ):
                raise ConcurrentWriteConflict("queued run lease was fenced before canonical output append")

    def append_many(
        self,
        events: list[dict[str, Any]],
        *,
        expected_sequence: int | None = None,
        queue_lease: dict[str, Any] | None = None,
    ) -> list[AppendResult]:
        """Append one logical batch atomically, assigning sequences under lock.

        Replaying a committed batch with the same idempotency keys and content
        returns the original rows. Any invalid event rolls the whole new batch
        back. This is important for atomic human-answer + work-resume changes.
        """
        if not events:
            return []
        if expected_sequence is not None and (
            not isinstance(expected_sequence, int)
            or isinstance(expected_sequence, bool)
            or expected_sequence < 0
        ):
            raise EventStoreError("expected_sequence must be a nonnegative integer")
        conn = self._connect()
        results: list[AppendResult] = []
        lease_context = self._queue_lease_context.get()
        if lease_context is not None:
            contextual_lease = {key: value for key, value in lease_context.items() if key != "clock"}
            contextual_lease["now"] = lease_context["clock"]()
            if queue_lease is not None and any(
                queue_lease.get(key) != contextual_lease.get(key)
                for key in ("jobId", "workerId", "leaseToken", "leaseGeneration")
            ):
                conn.close()
                raise EventStoreError("explicit queue lease differs from the active execution lease")
            queue_lease = contextual_lease
        try:
            conn.execute("BEGIN IMMEDIATE")
            queued_starts = [
                event for event in events
                if isinstance(event, dict) and event.get("eventType") == "run.started"
                and isinstance(event.get("payload"), dict)
                and "queueJobId" in event["payload"]
            ]
            if queued_starts and queue_lease is None:
                raise EventStoreError("queued run starts require an active queue lease claim")
            if queue_lease is not None:
                self._assert_queue_lease(conn, events, queue_lease)
            self._assert_active_run_fences(conn, events, queue_lease)
            if expected_sequence is not None:
                project_ids = {event.get("projectId") for event in events}
                if len(project_ids) != 1 or not all(isinstance(project_id, str) for project_id in project_ids):
                    raise EventStoreError("expected_sequence requires one project-scoped batch")
                project_id = next(iter(project_ids))
                row = conn.execute(
                    "SELECT sequence FROM canonical_events WHERE project_id=? ORDER BY sequence DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                actual_sequence = 0 if row is None else row["sequence"]
                if actual_sequence != expected_sequence:
                    raise ConcurrentWriteConflict(
                        f"project {project_id!r} changed: expected sequence {expected_sequence}, found {actual_sequence}"
                    )
            for event in events:
                candidate = dict(event)
                project_id = candidate.get("projectId")
                event_id = candidate.get("eventId")
                idempotency_key = candidate.get("idempotencyKey")
                if not isinstance(project_id, str) or not isinstance(event_id, str) or not isinstance(idempotency_key, str):
                    raise EventStoreError("projectId, eventId, and idempotencyKey are required")

                previous = conn.execute(
                    "SELECT sequence, event_id, event_json, record_hash "
                    "FROM canonical_events WHERE project_id=? AND idempotency_key=?",
                    (project_id, idempotency_key),
                ).fetchone()
                if previous is not None:
                    stored_event = json.loads(previous["event_json"])
                    requested = dict(candidate)
                    requested.pop("sequence", None)
                    comparable = dict(stored_event)
                    comparable.pop("sequence", None)
                    if previous["event_id"] != event_id or requested != comparable:
                        raise IdempotencyConflict(f"idempotency key {idempotency_key!r} was reused with different content")
                    results.append(AppendResult(stored_event, previous["sequence"], previous["record_hash"], True))
                    continue

                supplied_sequence = candidate.pop("sequence", None)
                last = conn.execute(
                    "SELECT sequence, record_hash FROM canonical_events WHERE project_id=? ORDER BY sequence DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                next_sequence = 1 if last is None else last["sequence"] + 1
                if supplied_sequence is not None and supplied_sequence != next_sequence:
                    raise SequenceConflict(f"expected sequence {next_sequence}, received {supplied_sequence}")
                candidate["sequence"] = next_sequence
                self._validate(candidate)

                previous_hash = ZERO_HASH if last is None else last["record_hash"]
                event_json = self._canonical_json(candidate)
                record_hash = self._record_hash(previous_hash, event_json)
                conn.execute(
                    "INSERT INTO canonical_events "
                    "(project_id, sequence, event_id, idempotency_key, event_json, previous_hash, record_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project_id, next_sequence, event_id, idempotency_key, event_json, previous_hash, record_hash),
                )
                if candidate["eventType"] == "run.started" and "queueJobId" in candidate["payload"]:
                    conn.execute(
                        "INSERT INTO queued_run_fences "
                        "(project_id, run_id, work_id, job_id, lease_generation) VALUES (?, ?, ?, ?, ?)",
                        (
                            project_id, candidate["payload"]["runId"], candidate["payload"]["workId"],
                            candidate["payload"]["queueJobId"], candidate["payload"]["leaseGeneration"],
                        ),
                    )
                results.append(AppendResult(candidate, next_sequence, record_hash, False))
            if lease_context is not None:
                final_lease = {key: value for key, value in lease_context.items() if key != "clock"}
                final_lease["now"] = lease_context["clock"]()
                self._assert_queue_lease(conn, events, final_lease)
                self._assert_active_run_fences(conn, events, final_lease)
            conn.execute("COMMIT")
            return results
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise EventStoreError(f"event uniqueness constraint failed: {exc}") from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def append(
        self,
        event: dict[str, Any],
        *,
        expected_sequence: int | None = None,
    ) -> AppendResult:
        """Append one event; sequence is assigned transactionally if omitted."""
        return self.append_many([event], expected_sequence=expected_sequence)[0]

    def prepare_adapter_invocation(
        self, invocation_id: str, project_id: str, request_hash: str,
    ) -> dict[str, Any]:
        """Durably reserve one stable adapter invocation before executing it."""
        if not isinstance(invocation_id, str) or not invocation_id:
            raise EventStoreError("invocation_id must be a non-empty string")
        if not isinstance(project_id, str) or not project_id:
            raise EventStoreError("project_id must be a non-empty string")
        if not isinstance(request_hash, str) or len(request_hash) != 64 or any(ch not in "0123456789abcdef" for ch in request_hash):
            raise EventStoreError("request_hash must be a SHA-256 digest")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM adapter_invocations WHERE invocation_id=?", (invocation_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO adapter_invocations (invocation_id, project_id, request_hash, state) "
                    "VALUES (?, ?, ?, 'prepared')",
                    (invocation_id, project_id, request_hash),
                )
                state = "prepared"
            else:
                if row["project_id"] != project_id or row["request_hash"] != request_hash:
                    raise IdempotencyConflict(f"adapter invocation {invocation_id!r} was reused with a different request")
                state = row["state"]
            conn.execute("COMMIT")
            return {"invocationId": invocation_id, "projectId": project_id, "requestHash": request_hash, "state": state}
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def start_adapter_invocation(self, invocation_id: str) -> dict[str, Any]:
        """Mark a prepared invocation as dispatched; executing invocations are never auto-replayed."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM adapter_invocations WHERE invocation_id=?", (invocation_id,),
            ).fetchone()
            if row is None:
                raise EventStoreError(f"unknown adapter invocation {invocation_id!r}")
            dispatched = row["state"] == "prepared"
            if dispatched:
                conn.execute(
                    "UPDATE adapter_invocations SET state='executing' WHERE invocation_id=?",
                    (invocation_id,),
                )
                state = "executing"
            else:
                state = row["state"]
            conn.execute("COMMIT")
            return {
                "invocationId": invocation_id, "projectId": row["project_id"],
                "requestHash": row["request_hash"], "state": state, "started": dispatched,
            }
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def stage_adapter_receipt(self, invocation_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist a validated canonical receipt batch before importing it into project history."""
        if not isinstance(events, list) or not events:
            raise EventStoreError("adapter receipt must contain at least one event")
        normalized = []
        project_ids = set()
        for event in events:
            if not isinstance(event, dict) or "sequence" in event:
                raise EventStoreError("staged adapter events must be objects without assigned sequences")
            candidate = dict(event)
            project_ids.add(candidate.get("projectId"))
            candidate["sequence"] = 1
            self._validate(candidate)
            candidate.pop("sequence")
            normalized.append(candidate)
        if len(project_ids) != 1 or not all(isinstance(value, str) for value in project_ids):
            raise EventStoreError("adapter receipt events must belong to one project")
        receipt_json = self._canonical_json({"events": normalized})
        receipt_hash = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
        conn = self._connect()
        lease_context = self._queue_lease_context.get()
        queue_lease = None
        if lease_context is not None:
            queue_lease = {key: value for key, value in lease_context.items() if key != "clock"}
            queue_lease["now"] = lease_context["clock"]()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if queue_lease is not None:
                self._assert_queue_lease(conn, normalized, queue_lease)
            row = conn.execute(
                "SELECT * FROM adapter_invocations WHERE invocation_id=?", (invocation_id,),
            ).fetchone()
            if row is None:
                raise EventStoreError(f"unknown adapter invocation {invocation_id!r}")
            if row["project_id"] != next(iter(project_ids)):
                raise EventStoreError("adapter receipt project does not match its invocation")
            if row["state"] == "executing":
                self._assert_active_run_fences(conn, normalized, queue_lease)
            if row["state"] in {"receipt_staged", "imported"}:
                if row["receipt_hash"] != receipt_hash:
                    raise IdempotencyConflict(f"adapter invocation {invocation_id!r} returned a changed receipt")
            elif row["state"] == "executing":
                conn.execute(
                    "UPDATE adapter_invocations SET state='receipt_staged', receipt_json=?, receipt_hash=? "
                    "WHERE invocation_id=?",
                    (receipt_json, receipt_hash, invocation_id),
                )
            else:
                raise EventStoreError("adapter receipt cannot be staged before invocation dispatch")
            if lease_context is not None:
                final_lease = {key: value for key, value in lease_context.items() if key != "clock"}
                final_lease["now"] = lease_context["clock"]()
                self._assert_queue_lease(conn, normalized, final_lease)
                self._assert_active_run_fences(conn, normalized, final_lease)
            conn.execute("COMMIT")
            return self.get_adapter_invocation(invocation_id)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @staticmethod
    def _assert_recovery_receipt_fences(
        conn: sqlite3.Connection,
        events: list[dict[str, Any]],
        recovery_job_ids: set[str],
    ) -> None:
        run_refs = {
            (event.get("projectId"), event["payload"].get("runId"))
            for event in events
            if isinstance(event, dict) and isinstance(event.get("payload"), dict)
            and isinstance(event["payload"].get("runId"), str)
        }
        fenced_jobs: set[str] = set()
        for project_id, run_id in run_refs:
            fence = conn.execute(
                "SELECT job_id, lease_generation FROM queued_run_fences WHERE project_id=? AND run_id=?",
                (project_id, run_id),
            ).fetchone()
            if fence is None:
                continue
            fenced_jobs.add(fence["job_id"])
            lease = conn.execute(
                "SELECT state, lease_generation FROM scheduled_work WHERE job_id=? AND project_id=?",
                (fence["job_id"], project_id),
            ).fetchone()
            if (
                fence["job_id"] not in recovery_job_ids or lease is None
                or lease["state"] != "recovery_required"
                or lease["lease_generation"] != fence["lease_generation"]
            ):
                raise ConcurrentWriteConflict("staged receipt is not covered by explicit expired-lease recovery")
        if fenced_jobs and not fenced_jobs.issubset(recovery_job_ids):
            raise ConcurrentWriteConflict("recovery authorization does not cover every queued receipt run")

    def commit_adapter_receipt(
        self,
        invocation_id: str,
        *,
        recovery_job_ids: set[str] | None = None,
        attest_no_live_worker: bool = False,
    ) -> list[AppendResult]:
        """Atomically append a receipt under its live lease or explicit expired-job recovery."""
        conn = self._connect()
        lease_context = self._queue_lease_context.get()
        queue_lease = None
        if lease_context is not None:
            queue_lease = {key: value for key, value in lease_context.items() if key != "clock"}
            queue_lease["now"] = lease_context["clock"]()
        recovery_job_ids = set() if recovery_job_ids is None else recovery_job_ids
        if recovery_job_ids and (
            attest_no_live_worker is not True or queue_lease is not None
            or not all(isinstance(job_id, str) and job_id for job_id in recovery_job_ids)
        ):
            conn.close()
            raise EventStoreError("receipt recovery requires job IDs, an explicit no-live-worker attestation, and no active lease")
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM adapter_invocations WHERE invocation_id=?", (invocation_id,),
            ).fetchone()
            if row is None:
                raise EventStoreError(f"unknown adapter invocation {invocation_id!r}")
            already_imported = row["state"] == "imported"
            if already_imported:
                self.verified_events(row["project_id"])
            if row["state"] not in {"receipt_staged", "imported"} or row["receipt_json"] is None:
                raise EventStoreError(f"adapter invocation {invocation_id!r} has no staged receipt to import")
            receipt_json = row["receipt_json"]
            if hashlib.sha256(receipt_json.encode("utf-8")).hexdigest() != row["receipt_hash"]:
                raise IntegrityFailure(f"staged adapter receipt {invocation_id!r} failed its content hash")
            events = json.loads(receipt_json)["events"]
            if not already_imported:
                if recovery_job_ids:
                    self._assert_recovery_receipt_fences(conn, events, recovery_job_ids)
                else:
                    if queue_lease is not None:
                        self._assert_queue_lease(conn, events, queue_lease)
                    self._assert_active_run_fences(conn, events, queue_lease)
            results: list[AppendResult] = []
            for event in events:
                candidate = dict(event)
                project_id = candidate["projectId"]
                event_id = candidate["eventId"]
                idempotency_key = candidate["idempotencyKey"]
                previous = conn.execute(
                    "SELECT sequence, event_id, event_json, record_hash FROM canonical_events "
                    "WHERE project_id=? AND idempotency_key=?",
                    (project_id, idempotency_key),
                ).fetchone()
                if previous is not None:
                    stored_event = json.loads(previous["event_json"])
                    if previous["event_id"] != event_id or candidate != {k: v for k, v in stored_event.items() if k != "sequence"}:
                        raise IdempotencyConflict(f"idempotency key {idempotency_key!r} was reused with different content")
                    results.append(AppendResult(stored_event, previous["sequence"], previous["record_hash"], True))
                    continue
                if already_imported:
                    raise IntegrityFailure(
                        f"imported adapter receipt {invocation_id!r} references a missing canonical event"
                    )
                last = conn.execute(
                    "SELECT sequence, record_hash FROM canonical_events WHERE project_id=? ORDER BY sequence DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                sequence = 1 if last is None else last["sequence"] + 1
                candidate["sequence"] = sequence
                self._validate(candidate)
                previous_hash = ZERO_HASH if last is None else last["record_hash"]
                event_json = self._canonical_json(candidate)
                record_hash = self._record_hash(previous_hash, event_json)
                conn.execute(
                    "INSERT INTO canonical_events "
                    "(project_id, sequence, event_id, idempotency_key, event_json, previous_hash, record_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project_id, sequence, event_id, idempotency_key, event_json, previous_hash, record_hash),
                )
                results.append(AppendResult(candidate, sequence, record_hash, False))
            result_json = self._canonical_json([{
                "event": item.event, "sequence": item.sequence, "recordHash": item.record_hash,
            } for item in results])
            conn.execute(
                "UPDATE adapter_invocations SET state='imported', result_json=? WHERE invocation_id=?",
                (result_json, invocation_id),
            )
            if not already_imported and lease_context is not None:
                final_lease = {key: value for key, value in lease_context.items() if key != "clock"}
                final_lease["now"] = lease_context["clock"]()
                self._assert_queue_lease(conn, events, final_lease)
                self._assert_active_run_fences(conn, events, final_lease)
            elif not already_imported and recovery_job_ids:
                self._assert_recovery_receipt_fences(conn, events, recovery_job_ids)
            conn.execute("COMMIT")
            return results
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise EventStoreError(f"adapter receipt uniqueness constraint failed: {exc}") from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def list_adapter_invocations(self, project_id: str) -> list[dict[str, Any]]:
        """List adapter receipt states without exposing staged output contents."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT invocation_id, project_id, request_hash, state, receipt_hash "
                "FROM adapter_invocations WHERE project_id=? ORDER BY invocation_id",
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        return [{
            "invocationId": row["invocation_id"],
            "projectId": row["project_id"],
            "requestHash": row["request_hash"],
            "state": row["state"],
            "receiptHash": row["receipt_hash"],
        } for row in rows]

    def reconcile_staged_adapter_receipts(
        self,
        project_id: str,
        *,
        recovery_job_ids: set[str] | None = None,
        attest_no_live_worker: bool = False,
    ) -> list[str]:
        """Import receipts normally under lease or under explicitly approved expired-job recovery."""
        staged = [
            item["invocationId"] for item in self.list_adapter_invocations(project_id)
            if item["state"] == "receipt_staged"
        ]
        for invocation_id in staged:
            if recovery_job_ids:
                self.commit_adapter_receipt(
                    invocation_id, recovery_job_ids=recovery_job_ids,
                    attest_no_live_worker=attest_no_live_worker,
                )
            else:
                self.commit_adapter_receipt(invocation_id)
        return staged

    def mark_ambiguous_adapter_invocations(self, project_id: str) -> list[str]:
        """Mark dispatched calls with no durable receipt as ambiguous during recovery."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT invocation_id FROM adapter_invocations WHERE project_id=? AND state='executing'",
                (project_id,),
            ).fetchall()
            invocation_ids = [row["invocation_id"] for row in rows]
            conn.executemany(
                "UPDATE adapter_invocations SET state='ambiguous' WHERE invocation_id=?",
                [(invocation_id,) for invocation_id in invocation_ids],
            )
            conn.execute("COMMIT")
            return invocation_ids
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_adapter_invocation(self, invocation_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM adapter_invocations WHERE invocation_id=?", (invocation_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        results = None
        if row["state"] == "imported":
            results = [{
                "event": item.event, "sequence": item.sequence, "recordHash": item.record_hash,
            } for item in self.commit_adapter_receipt(invocation_id)]
        elif row["result_json"] is not None:
            results = json.loads(row["result_json"])
        return {
            "invocationId": row["invocation_id"],
            "projectId": row["project_id"],
            "requestHash": row["request_hash"],
            "state": row["state"],
            "receiptHash": row["receipt_hash"],
            "receipt": None if row["receipt_json"] is None else json.loads(row["receipt_json"]),
            "results": results,
        }

    def put_blob(self, content: str | bytes) -> tuple[str, str]:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        digest = hashlib.sha256(raw).hexdigest()
        ref = f"blob.{digest}"
        conn = self._connect()
        try:
            conn.execute("INSERT OR IGNORE INTO canonical_blobs(content_hash, content) VALUES (?, ?)", (digest, raw))
            row = conn.execute("SELECT content FROM canonical_blobs WHERE content_hash=?", (digest,)).fetchone()
            if row is None or bytes(row["content"]) != raw:
                raise IntegrityFailure(f"content-address collision or corrupt blob {digest}")
        finally:
            conn.close()
        return ref, digest

    def get_blob(self, digest: str) -> bytes:
        conn = self._connect()
        try:
            row = conn.execute("SELECT content FROM canonical_blobs WHERE content_hash=?", (digest,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(digest)
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != digest:
            raise IntegrityFailure(f"blob hash mismatch for {digest}")
        return content

    def events(self, project_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT event_json FROM canonical_events WHERE project_id=? ORDER BY sequence",
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        return [json.loads(row["event_json"]) for row in rows]

    def backup_to(self, destination: str | Path) -> Path:
        """Create and verify a consistent snapshot of events, blobs, receipts, and queue metadata."""
        destination_path = Path(destination).resolve()
        if self.path != ":memory:" and destination_path == Path(self.path).resolve():
            raise EventStoreError("backup destination must differ from the live database")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source = self._connect()
        target = sqlite3.connect(str(destination_path), timeout=30, isolation_level=None)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise IntegrityFailure(f"SQLite backup failed integrity_check: {None if result is None else result[0]}")
        finally:
            target.close()
            source.close()

        snapshot = SQLiteEventStore(destination_path)
        project_ids = snapshot._project_ids()
        expected_fences: dict[tuple[str, str], tuple[str, str, int]] = {}
        for project_id in project_ids:
            for event in snapshot.verified_events(project_id):
                payload = event.get("payload", {})
                if event.get("eventType") == "run.started" and "queueJobId" in payload:
                    expected_fences[(project_id, payload["runId"])] = (
                        payload["workId"], payload["queueJobId"], payload["leaseGeneration"],
                    )
        conn = snapshot._connect()
        try:
            blobs = conn.execute("SELECT content_hash, content FROM canonical_blobs").fetchall()
            invocations = conn.execute(
                "SELECT invocation_id, state, receipt_json, receipt_hash FROM adapter_invocations"
            ).fetchall()
            actual_fences = {
                (row["project_id"], row["run_id"]): (
                    row["work_id"], row["job_id"], row["lease_generation"],
                )
                for row in conn.execute(
                    "SELECT project_id, run_id, work_id, job_id, lease_generation FROM queued_run_fences"
                ).fetchall()
            }
        finally:
            conn.close()
        if actual_fences != expected_fences:
            raise IntegrityFailure("backup queued-run fence table does not match canonical queued run starts")
        for blob in blobs:
            if hashlib.sha256(bytes(blob["content"])).hexdigest() != blob["content_hash"]:
                raise IntegrityFailure(f"backup contains corrupt blob {blob['content_hash']}")
        for invocation in invocations:
            if invocation["state"] in {"receipt_staged", "imported"}:
                receipt_json = invocation["receipt_json"]
                if (
                    receipt_json is None
                    or hashlib.sha256(receipt_json.encode("utf-8")).hexdigest() != invocation["receipt_hash"]
                ):
                    raise IntegrityFailure(
                        f"backup contains corrupt adapter receipt {invocation['invocation_id']}"
                    )
            if invocation["state"] == "imported":
                snapshot.commit_adapter_receipt(invocation["invocation_id"])
        return destination_path

    def _project_ids(self) -> set[str]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT DISTINCT project_id FROM canonical_events").fetchall()
        finally:
            conn.close()
        return {row["project_id"] for row in rows}

    def export_jsonl(self, project_id: str) -> str:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT event_json, previous_hash, record_hash FROM canonical_events "
                "WHERE project_id=? ORDER BY sequence",
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        return "".join(
            json.dumps(
                {"event": json.loads(row["event_json"]), "previousHash": row["previous_hash"], "recordHash": row["record_hash"]},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        )

    def verified_events(self, project_id: str) -> list[dict[str, Any]]:
        """Read one consistent event snapshot and verify its complete hash chain."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT sequence, event_json, previous_hash, record_hash FROM canonical_events "
                "WHERE project_id=? ORDER BY sequence",
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        expected_previous = ZERO_HASH
        events: list[dict[str, Any]] = []
        for expected_sequence, row in enumerate(rows, start=1):
            event_json = row["event_json"]
            event = json.loads(event_json)
            if row["sequence"] != expected_sequence or event.get("sequence") != expected_sequence:
                raise IntegrityFailure(f"non-contiguous sequence in project {project_id!r}")
            if row["previous_hash"] != expected_previous:
                raise IntegrityFailure(f"broken hash-chain link at sequence {expected_sequence}")
            if self._canonical_json(event) != event_json:
                raise IntegrityFailure(f"non-canonical event encoding at sequence {expected_sequence}")
            self._validate(event)
            actual = self._record_hash(expected_previous, event_json)
            if actual != row["record_hash"]:
                raise IntegrityFailure(f"event hash mismatch at sequence {expected_sequence}")
            expected_previous = actual
            events.append(event)
        return events

    def verify(self, project_id: str) -> bool:
        self.verified_events(project_id)
        return True
