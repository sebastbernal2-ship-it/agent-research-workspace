import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from harness.bounded_process import (
    BoundedProcessOutputLimit,
    BoundedProcessTimeout,
    run_bounded_subprocess_async,
)


class AsyncBoundedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="async-bounded-process-")
        self.root = Path(self.temp.name)
        self.script = self.root / "child.py"

    def tearDown(self):
        self.temp.cleanup()

    def write_script(self, text):
        self.script.write_text(text, encoding="utf-8")
        return [os.sys.executable, str(self.script)]

    def run_child(self, args, *, timeout=2, stdout_limit=4096, stderr_limit=4096):
        return run_bounded_subprocess_async(
            args,
            input_bytes=b"synthetic input",
            cwd=self.root,
            env={"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
            timeout=timeout,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
        )

    def test_wall_clock_timeout_is_enforced(self):
        args = self.write_script("import time\ntime.sleep(30)\n")
        with self.assertRaises(BoundedProcessTimeout):
            asyncio.run(self.run_child(args, timeout=0.1))

    def test_output_limit_is_enforced(self):
        args = self.write_script("import sys, time\nsys.stdout.write('x' * 100000)\nsys.stdout.flush()\ntime.sleep(30)\n")
        with self.assertRaises(BoundedProcessOutputLimit):
            asyncio.run(self.run_child(args, timeout=2, stdout_limit=1024))

    def test_running_child_does_not_block_event_loop(self):
        args = self.write_script("import time\ntime.sleep(0.2)\nprint('done')\n")

        async def scenario():
            ticks = 0
            finished = asyncio.Event()

            async def ticker():
                nonlocal ticks
                while not finished.is_set():
                    ticks += 1
                    await asyncio.sleep(0.01)

            ticker_task = asyncio.create_task(ticker())
            result = await self.run_child(args, timeout=2)
            finished.set()
            await ticker_task
            return result, ticks

        result, ticks = asyncio.run(scenario())
        self.assertEqual(result.stdout.strip(), b"done")
        self.assertGreaterEqual(ticks, 5)


if __name__ == "__main__":
    unittest.main()
