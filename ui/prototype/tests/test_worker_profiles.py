import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.bounded_process import BoundedProcessResult
from ui.prototype import vp as V

CONFIG = {
    "profiles": {
        "codex": {"kind": "codex"},
        "deepseek": {
            "kind": "claude",
            "env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic", "ANTHROPIC_MODEL": "deepseek-chat", "ANTHROPIC_API_KEY": ""},
            "secretEnv": {"ANTHROPIC_AUTH_TOKEN": "DEEPSEEK_API_KEY"},
        },
    },
    "roles": {"vp": "codex", "orchestrator": "codex", "worker": "deepseek", "reviewer": "codex"},
}


def write(config):
    d = tempfile.mkdtemp()
    p = Path(d) / "workers.json"
    p.write_text(json.dumps(config))
    return p


class WorkerProfileTests(unittest.TestCase):
    def test_valid_config_loads_and_routes_roles(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k"}), mock.patch("shutil.which", return_value="/bin/true"):
            profiles, roles = V.load_worker_config(write(CONFIG))
            runner = V.RoutedRunner(profiles, roles)
            self.assertTrue(runner.available())
            self.assertIs(runner._pick("worker"), profiles["deepseek"])
            self.assertIs(runner._pick("vp"), profiles["codex"])
            self.assertEqual(runner.describe()["worker"], "deepseek")

    def test_unsafe_env_overrides_and_bad_secret_references_are_rejected(self):
        for bad in ({"PATH": "/evil"}, {"LD_PRELOAD": "x.so"}):
            cfg = {"profiles": {"p": {"kind": "claude", "env": bad}}}
            with self.assertRaises(ValueError):
                V.load_worker_config(write(cfg))
        with self.assertRaises(ValueError):  # a secret must be a variable NAME, not the key itself
            V.load_worker_config(write({"profiles": {"p": {"kind": "claude", "secretEnv": {"ANTHROPIC_AUTH_TOKEN": "sk-live-abc123def"}}}}))
        with self.assertRaises(ValueError):
            V.load_worker_config(write({"profiles": {"p": {"kind": "claude"}}, "roles": {"worker": "missing"}}))

    def test_key_reaches_the_child_env_only_never_argv_and_other_env_is_not_inherited(self):
        captured = {}
        def fake_run(args, **kw):
            captured["args"], captured["env"] = list(args), dict(kw["env"])
            return BoundedProcessResult(returncode=0, stdout=json.dumps({"result": "hi"}).encode(), stderr=b"")
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "SECRET-KEY-123", "UNRELATED_TOKEN": "nope"}), \
             mock.patch("shutil.which", return_value="/usr/bin/claude"), mock.patch.object(V, "run_bounded_subprocess", fake_run):
            profiles, _ = V.load_worker_config(write(CONFIG))
            self.assertEqual(profiles["deepseek"].work("sys", "do it", Path(tempfile.mkdtemp())), "hi")
        env = captured["env"]
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "SECRET-KEY-123")
        self.assertNotIn("UNRELATED_TOKEN", env)
        self.assertFalse(any("SECRET-KEY-123" in a for a in captured["args"]))

    def test_profile_without_its_key_is_unavailable_and_routing_falls_back(self):
        env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("shutil.which", return_value="/bin/true"):
            profiles, roles = V.load_worker_config(write(CONFIG))
            self.assertFalse(profiles["deepseek"].available())
            self.assertIs(V.RoutedRunner(profiles, roles)._pick("worker"), profiles["codex"])  # falls back, never crashes

    def test_invalid_config_falls_back_to_the_default_runner(self):
        p = write({"profiles": {"p": {"kind": "nope"}}})
        with mock.patch.dict(os.environ, {"NORTHSTAR_WORKERS_CONFIG": str(p)}):
            self.assertNotIsInstance(V.default_runner(), V.RoutedRunner)

    def test_system_prompts_map_to_roles(self):
        r = V.RoutedRunner({}, {})
        self.assertEqual(r._by_system[V.ORCHESTRATOR_SYSTEM], "orchestrator")
        self.assertEqual(r._by_system[V.REVIEWER_SYSTEM], "reviewer")
        self.assertEqual(r._by_system[V.MEMORY_SYSTEM], "vp")


if __name__ == "__main__":
    unittest.main()
