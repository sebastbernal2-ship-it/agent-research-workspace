import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ag2.network import EV_TEXT

from harness.ag2_mirror import CanonicalAG2Mirror
from harness.event_store import SQLiteEventStore
from harness.projection import Projector
from harness.run_ledger import RunLedger
from harness.testing import seed_synthetic_project

PROJECT = "project.ag2-contract"
WORK = "work.ag2-contract"
NOW = "2026-04-16T12:00:00Z"
AG2_ID = "ag2-agent-1"
ACTOR = {"actorType": "agent", "actorId": "agent.ag2-1", "role": "specialist"}


def envelope(*, sender=AG2_ID, event_id="envelope.1", project_text="A canonicalized message.", data=None):
    return SimpleNamespace(
        event_type=EV_TEXT, sender_id=sender, event_data=data or {"text": project_text},
        audience=None, causation_id=None, envelope_id=event_id, channel_id="channel.contract",
        created_at=NOW,
    )


class AG2MirrorContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteEventStore(Path(self.temp.name) / "canonical.sqlite3")
        seed_synthetic_project(self.store, project_id=PROJECT, work_id=WORK, occurred_at=NOW)
        RunLedger(self.store, clock=lambda: NOW).start(
            PROJECT, WORK, run_id="run.ag2-contract", agent=ACTOR,
            adapter={"name": "ag2", "version": "1.0.0"}, context_hash="a" * 64,
            correlation_id="trace.ag2-contract", occurred_at=NOW,
        )
        self.actors = {AG2_ID: ACTOR}
        self.runs = {AG2_ID: "run.ag2-contract"}
        self.mirror = self.make_mirror()
        self.metadata = SimpleNamespace(participants=[SimpleNamespace(agent_id=AG2_ID)])

    def tearDown(self):
        self.temp.cleanup()

    def make_mirror(self, *, project_id=PROJECT, work_id=WORK, actors=None, runs=None):
        return CanonicalAG2Mirror(
            self.store, project_id=project_id, work_id=work_id,
            actors_by_ag2_id=self.actors if actors is None else actors,
            message_types_by_ag2_id={AG2_ID: "proposal"},
            run_ids_by_ag2_id=self.runs if runs is None else runs,
            correlation_id="trace.ag2-contract",
        )

    async def test_schema_scoped_run_attribution_and_replay_are_idempotent(self):
        msg = envelope()
        await self.mirror.on_envelope_posted(msg, self.metadata)
        RunLedger(self.store, clock=lambda: NOW).finish(
            PROJECT, "run.ag2-contract", terminal_status="succeeded", actor=ACTOR,
            occurred_at=NOW,
        )
        # Reconcile after the run has closed: the previously accepted event is
        # idempotent, while a new stale message would be rejected.
        await self.mirror.on_envelope_posted(msg, self.metadata)
        self.mirror.raise_if_degraded()
        events = [event for event in self.store.events(PROJECT) if event["eventType"] == "discussion.message_added"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["runId"], "run.ag2-contract")
        self.assertEqual(events[0]["correlationId"], "trace.ag2-contract")
        self.assertEqual(Projector(self.store).replay(PROJECT)["messages"][0]["content"], "A canonicalized message.")

    async def test_unknown_agent_and_wrong_project_or_work_fail_before_append(self):
        baseline = len(self.store.events(PROJECT))
        with self.assertRaisesRegex(ValueError, "unmapped AG2 sender"):
            await self.mirror.on_envelope_posted(envelope(sender="ag2-unmapped"), self.metadata)
        wrong_project = self.make_mirror(project_id="project.not-canonical")
        with self.assertRaisesRegex(ValueError, "unknown canonical project"):
            await wrong_project.on_envelope_posted(envelope(event_id="envelope.wrong-project"), self.metadata)
        wrong_work = self.make_mirror(work_id="work.not-in-project")
        with self.assertRaisesRegex(ValueError, "outside canonical project"):
            await wrong_work.on_envelope_posted(envelope(event_id="envelope.wrong-work"), self.metadata)
        self.assertEqual(len(self.store.events(PROJECT)), baseline)

    async def test_missing_run_assignment_and_bad_artifact_evidence_refs_fail_closed(self):
        baseline = len(self.store.events(PROJECT))
        bad_run = self.make_mirror(runs={AG2_ID: "run.not-canonical"})
        with self.assertRaisesRegex(ValueError, "active matching canonical run"):
            await bad_run.on_envelope_posted(envelope(event_id="envelope.bad-run"), self.metadata)
        for data in (
            {"text": "bad artifact", "artifact_ids": ["artifact.missing"]},
            {"text": "bad evidence", "evidence_ids": ["evidence.missing"]},
            {"text": "unsupported hidden refs", "artifact_ref": "artifact.missing"},
            {"text": "invalid canonical type", "canonical_message_type": "unrecognized"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                await self.mirror.on_envelope_posted(envelope(event_id=f"envelope.invalid.{len(data)}", data=data), self.metadata)
        self.assertEqual(len(self.store.events(PROJECT)), baseline)

    async def test_run_mapping_must_cover_every_agent_mapping(self):
        with self.assertRaisesRegex(ValueError, "exactly one canonical run"):
            CanonicalAG2Mirror(
                self.store, project_id=PROJECT, work_id=WORK,
                actors_by_ag2_id=self.actors, message_types_by_ag2_id={AG2_ID: "proposal"},
                run_ids_by_ag2_id={},
            )


if __name__ == "__main__":
    unittest.main()
