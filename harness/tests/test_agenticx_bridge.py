import unittest

from harness.agenticx_bridge import AgenticXSubprocessWorker, AgenticXWorkerError


class AgenticXSubprocessBridgeTests(unittest.TestCase):
    def test_real_agenticx_executor_runs_with_deterministic_provider_in_isolated_process(self):
        worker = AgenticXSubprocessWorker()
        result = worker.run_task(
            task_id="task.bridge-smoke",
            description="Return a deterministic integration result.",
            scripted_result="AgenticX execution passed through the subprocess adapter.",
        )
        self.assertEqual(
            result,
            {"success": True, "result": "AgenticX execution passed through the subprocess adapter."},
        )

    def test_invalid_worker_request_fails_closed(self):
        worker = AgenticXSubprocessWorker()
        with self.assertRaises(AgenticXWorkerError):
            worker.run_task(task_id="task.empty", description="test", scripted_result="")

    def test_oversized_request_is_rejected_before_process_start(self):
        worker = AgenticXSubprocessWorker()
        with self.assertRaisesRegex(AgenticXWorkerError, "30 KB"):
            worker.run_task(task_id="task.large", description="x" * 16000, scripted_result="y" * 16000)


if __name__ == "__main__":
    unittest.main()
