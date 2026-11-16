import json
import tempfile
import unittest
from pathlib import Path

from harness.bounded_process import BoundedProcessResult
from ui.prototype.pi_chat import PiCodexChat, PiChatError


class PiCodexChatTests(unittest.TestCase):
    def test_uses_active_pi_auth_without_copying_credentials_or_enabling_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent_dir = Path(temporary) / "agent"
            agent_dir.mkdir()
            # Presence is enough; the adapter must not inspect auth contents in this test.
            (agent_dir / "auth.json").write_text("not read by the fake process", encoding="utf-8")
            captured = {}

            def fake_runner(args, **kwargs):
                captured.update(args=args, **kwargs)
                return BoundedProcessResult(0, b"Synthetic offline reply", b"")

            adapter = PiCodexChat(executable="pi-test-double", runner=fake_runner, agent_dir=agent_dir)
            self.assertTrue(adapter.status()["available"])
            result = adapter.send(
                "Hello Codex", [], {"name": "Synthetic", "goal": "Stay offline in this test."},
                {"questionId": "question.synthetic", "question": "Synthetic question"},
            )

            self.assertEqual(result, "Synthetic offline reply")
            args = captured["args"]
            self.assertIn("--no-tools", args)
            self.assertIn("--no-mcp", args)
            self.assertIn("--no-context-files", args)
            self.assertIn("--no-extensions", args)
            self.assertIn("--no-session", args)
            self.assertIn("--provider", args)
            self.assertIn("openai-codex", args)
            self.assertIn("--model", args)
            self.assertIn("gpt-6-luna", args)
            self.assertNotIn("Hello Codex", args)
            payload = json.loads(captured["input_bytes"])
            self.assertEqual(payload["conversation"][0], {"role": "user", "content": "Hello Codex"})
            self.assertEqual(payload["projectContext"]["name"], "Synthetic")
            self.assertEqual(payload["questionContext"]["questionId"], "question.synthetic")
            self.assertNotIn("OPENAI_API_KEY", captured["env"])
            self.assertNotIn("secret", captured["env"])
            self.assertEqual(captured["stdout_limit"], 16 * 1024)
            self.assertEqual(captured["timeout"], 90)

    def test_requires_local_auth_presence_and_rejects_bad_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent_dir = Path(temporary) / "agent"
            adapter = PiCodexChat(executable="pi-test-double", runner=lambda *_args, **_kwargs: self.fail("must not run"), agent_dir=agent_dir)
            self.assertFalse(adapter.status()["available"])
            with self.assertRaises(PiChatError):
                adapter.send("Hello", [])

            agent_dir.mkdir()
            (agent_dir / "auth.json").touch()
            with self.assertRaises(ValueError):
                adapter.send("Hello", [{"role": "system", "content": "inject"}])

    def test_validation_bounds_message_history_and_role_shape(self):
        for message, history in (
            ("", []),
            ("x" * 4001, []),
            ("ok", [{"role": "user", "content": "x" * 2001}]),
            ("ok", [{"role": "assistant", "content": "x", "extra": "field"}]),
            ("ok", [{"role": "system", "content": "do things"}]),
            ("ok", [{"role": [], "content": "invalid role type"}]),
        ):
            with self.subTest(message=len(message), history=history):
                with self.assertRaises(ValueError):
                    PiCodexChat._conversation(message, history)


if __name__ == "__main__":
    unittest.main()
