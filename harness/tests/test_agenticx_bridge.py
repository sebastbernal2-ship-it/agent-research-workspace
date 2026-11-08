import os
import tempfile
import unittest
from pathlib import Path

from harness.agenticx_bridge import AgenticXSubprocessWorker, AgenticXWorkerError


class AgenticXSubprocessBridgeTests(unittest.TestCase):
    def fake_interpreter(self, directory, body):
        executable = Path(directory) / "fake-python"
        executable.write_text("#!/usr/bin/env python3" + chr(10) + body, encoding="utf-8")
        executable.chmod(0o755)
        return executable

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

    def test_worker_protocol_errors_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            malformed = self.fake_interpreter(temp, "print('not-json')")
            with self.assertRaisesRegex(AgenticXWorkerError, "invalid JSON"):
                AgenticXSubprocessWorker(malformed).run_task(
                    task_id="task.bad-json", description="test", scripted_result="result",
                )
            nonzero = self.fake_interpreter(temp, "import sys; print('partial'); sys.exit(7)")
            with self.assertRaisesRegex(AgenticXWorkerError, "exited 7"):
                AgenticXSubprocessWorker(nonzero).run_task(
                    task_id="task.nonzero", description="test", scripted_result="result",
                )

    @unittest.skipUnless(os.name == "posix", "process-group cancellation is POSIX-specific")
    def test_timeout_kills_worker_process_group(self):
        with tempfile.TemporaryDirectory() as temp:
            spawner = self.fake_interpreter(
                temp,
                "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']); time.sleep(20)",
            )
            with self.assertRaisesRegex(AgenticXWorkerError, "exceeded 0.1s timeout"):
                AgenticXSubprocessWorker(spawner, timeout=0.1).run_task(
                    task_id="task.tree-timeout", description="test", scripted_result="result",
                )

    def test_worker_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            noisy = self.fake_interpreter(temp, "print('x' * 100000)")
            with self.assertRaisesRegex(AgenticXWorkerError, "byte limit"):
                AgenticXSubprocessWorker(noisy).run_task(
                    task_id="task.noisy", description="test", scripted_result="result",
                )

    def test_worker_timeout_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            sleeper = self.fake_interpreter(temp, "import time; time.sleep(2)")
            with self.assertRaisesRegex(AgenticXWorkerError, "exceeded 0.05s timeout"):
                AgenticXSubprocessWorker(sleeper, timeout=0.05).run_task(
                    task_id="task.timeout", description="test", scripted_result="result",
                )

    def test_oversized_request_is_rejected_before_process_start(self):
        worker = AgenticXSubprocessWorker()
        with self.assertRaisesRegex(AgenticXWorkerError, "30 KB"):
            worker.run_task(task_id="task.large", description="x" * 16000, scripted_result="y" * 16000)


if __name__ == "__main__":
    unittest.main()
