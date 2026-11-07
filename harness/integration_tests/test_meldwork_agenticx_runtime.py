import json
import tempfile
import unittest
from pathlib import Path

from harness.event_store import SQLiteEventStore
from harness.meldwork_runtime_bridge import MeldworkAgenticXRuntimeBridge
from harness.projection import Projector
from harness.testing import seed_synthetic_project


class MeldworkAgenticXRuntimeIntegrationTests(unittest.TestCase):
    def test_full_meldwork_v4_flow_uses_agenticx_mock_workers_and_canonical_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteEventStore(Path(temp_dir) / "canonical.sqlite3")
            seed_synthetic_project(
                store,
                project_id="project.meldwork-agenticx",
                work_id="work.meldwork-agenticx",
            )

            report = MeldworkAgenticXRuntimeBridge(store).run(
                project_id="project.meldwork-agenticx",
                work_id="work.meldwork-agenticx",
            )

            self.assertEqual(report["orchestrationPhase"], "completed")
            self.assertEqual(report["invocationCount"], 8)
            self.assertEqual(report["eventSequences"], list(range(3, 11)))
            self.assertEqual(len(set(report["eventIds"])), 8)

            messages = Projector(store).replay("project.meldwork-agenticx")["messages"]
            self.assertEqual(len(messages), 8)
            events = [
                event for event in store.events("project.meldwork-agenticx")
                if event["eventType"] == "discussion.message_added"
            ]
            self.assertEqual(
                [event["payload"]["messageType"] for event in events],
                ["proposal", "proposal", "challenge", "challenge", "status", "status", "decision", "evidence"],
            )
            for event in events:
                blob = json.loads(store.get_blob(event["payload"]["contentHash"]))
                self.assertEqual(blob["format"], "meldwork-v4-phase-v1")
                self.assertTrue(blob["visibleText"].endswith("deterministic AgenticX AgentExecutor result."))
            self.assertTrue(store.verify("project.meldwork-agenticx"))


if __name__ == "__main__":
    unittest.main()
