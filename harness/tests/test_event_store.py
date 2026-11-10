import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

from harness.event_store import (
    ConcurrentWriteConflict,
    EventStoreError,
    IdempotencyConflict,
    IntegrityFailure,
    SQLiteEventStore,
    SequenceConflict,
)


NOW = "2026-04-16T12:00:00Z"
HASH = "a" * 64
OWNER = {"actorType": "human", "actorId": "user.owner", "role": "user"}
AGENT = {"actorType": "agent", "actorId": "agent.researcher", "role": "specialist"}


def make_event(event_id, key, event_type, payload, *, sequence=None):
    event = {
        "recordType": "event",
        "schemaVersion": "0.1",
        "eventId": event_id,
        "projectId": "project.alpha",
        "eventType": event_type,
        "actor": OWNER if event_type == "project.created" else AGENT,
        "occurredAt": NOW,
        "causationId": None,
        "correlationId": "trace.alpha",
        "idempotencyKey": key,
        "payload": payload,
    }
    if sequence is not None:
        event["sequence"] = sequence
    return event


def initial_events():
    return [
        make_event(
            "event.project-created",
            "idem.project-created",
            "project.created",
            {"name": "Synthetic project", "charterHash": HASH, "recordHash": HASH},
        ),
        make_event(
            "event.work-created",
            "idem.work-created",
            "work.created",
            {
                "workId": "work.research",
                "title": "Review sources",
                "objective": "Build a sourced answer.",
                "owner": AGENT,
                "parentWorkId": None,
                "recordHash": HASH,
            },
        ),
    ]


class SQLiteEventStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "canonical.sqlite3"
        self.store = SQLiteEventStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_unknown_schema_version_fails_closed(self):
        self.assertEqual(self.store._schema_registry.versions, ("0.1", "0.2"))
        event = initial_events()[0]
        event["schemaVersion"] = "9.9"
        with self.assertRaisesRegex(EventStoreError, "unsupported canonical schema version"):
            self.store.append(event)
        self.assertEqual(self.store.events("project.alpha"), [])

    def test_shared_memory_store_persists_across_internal_connections(self):
        memory_store = SQLiteEventStore(":memory:")
        first = memory_store.append(initial_events()[0])
        self.assertEqual(first.sequence, 1)
        self.assertEqual(memory_store.events("project.alpha"), [first.event])
        self.assertTrue(memory_store.verify("project.alpha"))

    def test_assigns_sequence_and_retries_idempotently(self):
        event = initial_events()[0]
        first = self.store.append(event)
        self.assertEqual(first.sequence, 1)
        self.assertFalse(first.duplicate)

        retry = self.store.append(event)
        self.assertEqual(retry.sequence, 1)
        self.assertTrue(retry.duplicate)
        self.assertEqual(len(self.store.events("project.alpha")), 1)

    def test_expected_sequence_rejects_stale_command_without_append(self):
        events = initial_events()
        self.store.append(events[0])
        with self.assertRaisesRegex(ConcurrentWriteConflict, "expected sequence 0, found 1"):
            self.store.append(events[1], expected_sequence=0)
        self.assertEqual([event["eventId"] for event in self.store.events("project.alpha")], [events[0]["eventId"]])
        self.assertTrue(self.store.verify("project.alpha"))

    def test_adapter_receipt_is_durable_and_import_is_atomic_idempotent(self):
        invocation = self.store.prepare_adapter_invocation(
            "invocation.test", "project.alpha", "b" * 64,
        )
        self.assertEqual(invocation["state"], "prepared")
        self.assertEqual(self.store.start_adapter_invocation("invocation.test")["state"], "executing")
        self.store.stage_adapter_receipt("invocation.test", initial_events())

        reopened = SQLiteEventStore(self.path)
        self.assertEqual(reopened.get_adapter_invocation("invocation.test")["state"], "receipt_staged")
        committed = reopened.commit_adapter_receipt("invocation.test")
        self.assertEqual([item.sequence for item in committed], [1, 2])
        self.assertTrue(all(not item.duplicate for item in committed))
        self.assertEqual(self.store.get_adapter_invocation("invocation.test")["state"], "imported")

        retry = self.store.commit_adapter_receipt("invocation.test")
        self.assertEqual([item.sequence for item in retry], [1, 2])
        self.assertTrue(all(item.duplicate for item in retry))
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE adapter_invocations SET result_json='not-json' WHERE invocation_id='invocation.test'"
            )
            conn.commit()
        finally:
            conn.close()
        repaired_state = self.store.get_adapter_invocation("invocation.test")
        self.assertEqual(len(repaired_state["results"]), 2)
        repaired = self.store.commit_adapter_receipt("invocation.test")
        self.assertEqual([item.sequence for item in repaired], [1, 2])
        self.assertEqual(len(self.store.events("project.alpha")), 2)
        self.assertTrue(self.store.verify("project.alpha"))

    def test_adapter_receipt_cannot_be_replaced_after_staging(self):
        invocation_id = "invocation.immutable-receipt"
        self.store.prepare_adapter_invocation(invocation_id, "project.alpha", "a" * 64)
        self.store.start_adapter_invocation(invocation_id)
        original = initial_events()
        self.store.stage_adapter_receipt(invocation_id, original)
        changed = initial_events()
        changed[0]["payload"] = {
            "name": "Changed after staging", "charterHash": HASH, "recordHash": HASH,
        }
        with self.assertRaises(IdempotencyConflict):
            self.store.stage_adapter_receipt(invocation_id, changed)
        self.assertEqual(self.store.get_adapter_invocation(invocation_id)["state"], "receipt_staged")
        self.assertEqual([item.sequence for item in self.store.commit_adapter_receipt(invocation_id)], [1, 2])

    def test_only_one_concurrent_dispatcher_claims_an_invocation(self):
        self.store.prepare_adapter_invocation("invocation.claim", "project.alpha", "9" * 64)
        peer = SQLiteEventStore(self.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(
                lambda store: store.start_adapter_invocation("invocation.claim"),
                [self.store, peer],
            ))
        self.assertEqual(sum(claim["started"] for claim in claims), 1)
        self.assertEqual(self.store.get_adapter_invocation("invocation.claim")["state"], "executing")

    def test_adapter_invocation_with_no_receipt_is_not_replayed_or_committed(self):
        self.store.prepare_adapter_invocation("invocation.ambiguous", "project.alpha", "c" * 64)
        self.store.start_adapter_invocation("invocation.ambiguous")
        reopened = SQLiteEventStore(self.path)
        self.assertEqual(reopened.start_adapter_invocation("invocation.ambiguous")["state"], "executing")
        with self.assertRaisesRegex(EventStoreError, "no staged receipt"):
            reopened.commit_adapter_receipt("invocation.ambiguous")
        self.assertEqual(reopened.events("project.alpha"), [])

    def test_process_crash_after_synthetic_side_effect_never_dispatches_it_twice(self):
        side_effect_path = self.path.with_suffix(".synthetic-effect")
        child = r'''
import os
import sys
from harness.event_store import SQLiteEventStore

store = SQLiteEventStore(sys.argv[1])
invocation = store.prepare_adapter_invocation("invocation.effect-crash", "project.alpha", "f" * 64)
if not store.start_adapter_invocation(invocation["invocationId"])["started"]:
    raise SystemExit("dispatch was not claimed")
with open(sys.argv[2], "a", encoding="utf-8") as marker:
    marker.write("effect-attempt" + chr(10))
    marker.flush()
    os.fsync(marker.fileno())
os._exit(71)  # effect occurred, process died before recording its receipt
'''
        child_process = subprocess.run(
            [sys.executable, "-c", child, str(self.path), str(side_effect_path)],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(child_process.returncode, 71, child_process.stderr)
        self.assertEqual(side_effect_path.read_text(encoding="utf-8").splitlines(), ["effect-attempt"])

        restarted = SQLiteEventStore(self.path)
        self.assertEqual(
            restarted.mark_ambiguous_adapter_invocations("project.alpha"),
            ["invocation.effect-crash"],
        )
        same = restarted.prepare_adapter_invocation(
            "invocation.effect-crash", "project.alpha", "f" * 64,
        )
        self.assertEqual(same["state"], "ambiguous")
        self.assertFalse(restarted.start_adapter_invocation("invocation.effect-crash")["started"])
        self.assertEqual(side_effect_path.read_text(encoding="utf-8").splitlines(), ["effect-attempt"])
        self.assertEqual(restarted.events("project.alpha"), [])

    def test_dispatch_marker_crash_is_ambiguous_and_never_replays(self):
        invocation_id = "invocation.crash-before-provider-call"
        request_hash = "e" * 64
        self.store.prepare_adapter_invocation(invocation_id, "project.alpha", request_hash)
        dispatch = self.store.start_adapter_invocation(invocation_id)
        self.assertTrue(dispatch["started"])
        self.assertEqual(dispatch["state"], "executing")

        # Simulate a process crash after the durable dispatch marker but before
        # any provider call. Recovery cannot know that no bytes reached a provider.
        restarted = SQLiteEventStore(self.path)
        self.assertEqual(
            restarted.mark_ambiguous_adapter_invocations("project.alpha"), [invocation_id],
        )
        retry = restarted.prepare_adapter_invocation(invocation_id, "project.alpha", request_hash)
        self.assertEqual(retry["state"], "ambiguous")
        retry_dispatch = restarted.start_adapter_invocation(invocation_id)
        self.assertEqual(retry_dispatch["state"], "ambiguous")
        self.assertFalse(retry_dispatch["started"])

        provider_calls = []
        if retry_dispatch["started"]:
            provider_calls.append("would-be external effect")
        self.assertEqual(provider_calls, [])
        with self.assertRaisesRegex(EventStoreError, "cannot be staged before invocation dispatch"):
            restarted.stage_adapter_receipt(invocation_id, initial_events())
        self.assertEqual(restarted.events("project.alpha"), [])

    def test_adapter_receipt_batch_rolls_back_fully_on_import_conflict(self):
        invocation_id = "invocation.rollback"
        self.store.prepare_adapter_invocation(invocation_id, "project.alpha", "d" * 64)
        self.store.start_adapter_invocation(invocation_id)
        batch = initial_events()
        batch[1]["eventId"] = batch[0]["eventId"]
        self.store.stage_adapter_receipt(invocation_id, batch)
        with self.assertRaises(EventStoreError):
            self.store.commit_adapter_receipt(invocation_id)
        self.assertEqual(self.store.events("project.alpha"), [])
        self.assertEqual(self.store.get_adapter_invocation(invocation_id)["state"], "receipt_staged")

    def test_reused_idempotency_key_with_changed_content_fails(self):
        event = initial_events()[0]
        self.store.append(event)
        changed = dict(event)
        changed["payload"] = {"name": "Different project", "charterHash": HASH, "recordHash": HASH}
        with self.assertRaises(IdempotencyConflict):
            self.store.append(changed)

    def test_sequence_is_contiguous_and_project_scoped(self):
        reordered = initial_events()
        reordered[0]["sequence"] = 2
        reordered[1]["sequence"] = 1
        with self.assertRaises(SequenceConflict):
            self.store.append_many(reordered)
        self.assertEqual(self.store.events("project.alpha"), [])

        events = initial_events()
        self.store.append(events[0])
        with self.assertRaises(SequenceConflict):
            self.store.append(make_event(
                "event.bad-gap", "idem.bad-gap", "work.created", events[1]["payload"], sequence=4
            ))
        result = self.store.append(events[1])
        self.assertEqual(result.sequence, 2)

        other = dict(events[0])
        other["projectId"] = "project.beta"
        other["eventId"] = "event.beta-created"
        other["idempotencyKey"] = "idem.beta-created"
        self.assertEqual(self.store.append(other).sequence, 1)

    def test_cross_instance_compare_and_swap_allows_only_one_stale_writer(self):
        self.store.append(initial_events()[0])
        peer = SQLiteEventStore(self.path)
        candidates = [
            make_event(
                f"event.cas.{index}", f"idem.cas.{index}", "work.created",
                {
                    "workId": f"work.cas.{index}", "title": f"CAS {index}",
                    "objective": "Compete on one expected project sequence.",
                    "owner": AGENT, "recordHash": HASH,
                },
            )
            for index in range(2)
        ]

        def attempt(args):
            store, event = args
            try:
                return store.append(event, expected_sequence=1).event["eventId"]
            except ConcurrentWriteConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [(self.store, candidates[0]), (peer, candidates[1])]))
        self.assertEqual(sum(outcome == "conflict" for outcome in outcomes), 1)
        self.assertEqual(len(self.store.events("project.alpha")), 2)
        self.assertTrue(self.store.verify("project.alpha"))

    def test_process_kill_mid_batch_recovers_without_partial_commit(self):
        signal_path = self.path.with_suffix(".inserted")
        child = r'''
import json
import sys
import time
from pathlib import Path
from harness.event_store import SQLiteEventStore

class PausingStore(SQLiteEventStore):
    def _connect(self):
        connection = super()._connect()
        def pause_after_insert():
            signal = Path(sys.argv[2])
            signal.write_text("inserted", encoding="utf-8")
            while True:
                time.sleep(0.1)
        connection.create_function("pause_after_insert", 0, pause_after_insert)
        return connection

store = PausingStore(sys.argv[1])
connection = store._connect()
connection.execute("CREATE TRIGGER pause_batch AFTER INSERT ON canonical_events BEGIN SELECT pause_after_insert(); END")
connection.close()
store.append_many(json.loads(sys.argv[3]))
'''
        process = subprocess.Popen(
            [sys.executable, "-c", child, str(self.path), str(signal_path), json.dumps(initial_events())],
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline and not signal_path.exists():
                if process.poll() is not None:
                    self.fail(f"crash-injection child exited before its transaction pause: {process.returncode}")
                time.sleep(0.02)
            self.assertTrue(signal_path.exists(), "child never reached the uncommitted first insert")
            process.kill()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

        recovered = SQLiteEventStore(self.path)
        self.assertEqual(recovered.events("project.alpha"), [])
        self.assertTrue(recovered.verify("project.alpha"))

    def test_batch_append_is_atomic_and_retryable(self):
        events = initial_events()
        invalid = make_event(
            "event.invalid",
            "idem.invalid",
            "discussion.message_added",
            {"workId": "work.research"},
        )
        with self.assertRaises(EventStoreError):
            self.store.append_many([*events, invalid])
        self.assertEqual(self.store.events("project.alpha"), [])

        first_result = self.store.append_many(events)
        self.assertEqual([result.sequence for result in first_result], [1, 2])
        retry = self.store.append_many(events)
        self.assertTrue(all(result.duplicate for result in retry))
        self.assertEqual([result.sequence for result in retry], [1, 2])
        self.assertTrue(self.store.verify("project.alpha"))

    def test_invalid_event_is_rejected_before_append(self):
        event = initial_events()[0]
        event["payload"] = {"name": "Missing charter hash", "recordHash": HASH}
        with self.assertRaises(EventStoreError):
            self.store.append(event)
        self.assertEqual(self.store.events("project.alpha"), [])

    def test_restart_export_and_hash_chain_verification(self):
        for event in initial_events():
            self.store.append(event)
        self.assertTrue(self.store.verify("project.alpha"))

        restarted = SQLiteEventStore(self.path)
        events = restarted.events("project.alpha")
        self.assertEqual([e["sequence"] for e in events], [1, 2])
        exported = [json.loads(line) for line in restarted.export_jsonl("project.alpha").splitlines()]
        self.assertEqual([row["event"] for row in exported], events)
        self.assertTrue(all(len(row["recordHash"]) == 64 for row in exported))

    def test_concurrent_appends_assign_unique_contiguous_sequences(self):
        self.store.append(initial_events()[0])
        events = [
            make_event(
                f"event.concurrent.{index}",
                f"idem.concurrent.{index}",
                "work.created",
                {
                    "workId": f"work.concurrent.{index}",
                    "title": f"Concurrent {index}",
                    "objective": "Synthetic sequence stress test.",
                    "owner": AGENT,
                    "recordHash": HASH,
                },
            )
            for index in range(20)
        ]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.store.append, events))
        self.assertEqual(sorted(result.sequence for result in results), list(range(2, 22)))
        self.assertEqual([event["sequence"] for event in self.store.events("project.alpha")], list(range(1, 22)))
        self.assertTrue(self.store.verify("project.alpha"))

    def test_rows_are_append_only_and_hash_chain_detects_bypassed_trigger(self):
        self.store.append(initial_events()[0])
        conn = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE canonical_events SET event_json='{}' WHERE project_id='project.alpha'")
        conn.rollback()
        conn.execute("DROP TRIGGER canonical_events_no_update")
        conn.execute("UPDATE canonical_events SET event_json=replace(event_json, 'Synthetic project', 'Altered project')")
        conn.commit()
        conn.close()
        with self.assertRaises(IntegrityFailure):
            self.store.verify("project.alpha")

    def test_content_addressed_blobs_are_immutable_and_verified(self):
        ref, digest = self.store.put_blob("a discussion message")
        self.assertEqual(ref, f"blob.{digest}")
        self.assertEqual(self.store.get_blob(digest), b"a discussion message")
        self.assertEqual(self.store.put_blob("a discussion message"), (ref, digest))
        self.assertNotEqual(self.store.put_blob("changed message")[1], digest)


if __name__ == "__main__":
    unittest.main()
