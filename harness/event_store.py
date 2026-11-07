"""Small append-only SQLite event store for the local harness prototype.

This is a reference implementation, not a multi-host production database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ZERO_HASH = "0" * 64
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "v0.1" / "schema.json"


class EventStoreError(Exception):
    """Base error for rejected or corrupt canonical history."""


class IdempotencyConflict(EventStoreError):
    """An idempotency key was reused with different event content."""


class SequenceConflict(EventStoreError):
    """A caller-supplied sequence does not match the next project sequence."""


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
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
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
                CREATE TRIGGER IF NOT EXISTS canonical_blobs_no_update
                BEFORE UPDATE ON canonical_blobs
                BEGIN SELECT RAISE(ABORT, 'canonical blobs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS canonical_blobs_no_delete
                BEFORE DELETE ON canonical_blobs
                BEGIN SELECT RAISE(ABORT, 'canonical blobs are immutable'); END;
                """
            )

    @staticmethod
    def _canonical_json(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _record_hash(previous_hash: str, event_json: str) -> str:
        return hashlib.sha256((previous_hash + "\n" + event_json).encode("utf-8")).hexdigest()

    def validate_record(self, record: dict[str, Any]) -> None:
        errors = sorted(self._validator.iter_errors(record), key=lambda err: list(err.path))
        if errors:
            detail = "; ".join(f"{list(err.path)}: {err.message}" for err in errors[:5])
            raise EventStoreError(f"canonical record schema validation failed: {detail}")

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

    def append_many(self, events: list[dict[str, Any]]) -> list[AppendResult]:
        """Append one logical batch atomically, assigning sequences under lock.

        Replaying a committed batch with the same idempotency keys and content
        returns the original rows. Any invalid event rolls the whole new batch
        back. This is important for atomic human-answer + work-resume changes.
        """
        if not events:
            return []
        conn = self._connect()
        results: list[AppendResult] = []
        try:
            conn.execute("BEGIN IMMEDIATE")
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
                results.append(AppendResult(candidate, next_sequence, record_hash, False))
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

    def append(self, event: dict[str, Any]) -> AppendResult:
        """Append one event; sequence is assigned transactionally if omitted."""
        return self.append_many([event])[0]

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

    def verify(self, project_id: str) -> bool:
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
        return True
