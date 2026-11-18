import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ui.prototype import integrations as integ
from ui.prototype import vp as V
from ui.prototype.runtime import PrototypeRuntime
from ui.prototype.tests.test_vp import FakeRunner, VPBase

STDIO = {"kind": "mcp-stdio", "command": ["python3", "-m", "lf_tools"], "env": {"LF_SCHEMA": "clean"}, "secretEnv": {"LF_PASSWORD": "MY_LF_PASSWORD"},
         "description": "Latent Foundry research web"}


class ValidationTests(unittest.TestCase):
    def test_a_good_spec_normalizes_and_bad_ones_are_rejected_with_a_reason(self):
        self.assertEqual(integ.validate("lf", STDIO)["command"], ["python3", "-m", "lf_tools"])
        self.assertEqual(integ.validate("remote", {"kind": "mcp-http", "url": "https://example.org/mcp"})["url"], "https://example.org/mcp")
        self.assertEqual(integ.validate("local", {"kind": "mcp-http", "url": "http://127.0.0.1:8123/mcp"})["kind"], "mcp-http")
        bad = [("Bad-Name", STDIO), ("lf", {**STDIO, "command": "rm -rf /"}), ("lf", {**STDIO, "command": []}),
               ("lf", {"kind": "mcp-http", "url": "http://evil.example/mcp"}), ("lf", {"kind": "mcp-http", "url": "ftp://x/y"}),
               ("lf", {**STDIO, "env": {"OPENAI_API_KEY": "sk-x"}}), ("lf", {**STDIO, "env": {"lower": "x"}}),
               ("lf", {**STDIO, "secretEnv": {"LF_PASSWORD": "not a name!"}}), ("lf", {**STDIO, "kind": "carrier-pigeon"}),
               ("lf", {**STDIO, "cwd": "relative/path"}), ("lf", {**STDIO, "surprise": 1})]
        for name, spec in bad:
            with self.assertRaises(ValueError, msg=(name, spec)):
                integ.validate(name, spec)

    def test_secrets_resolve_from_the_environment_and_a_missing_one_marks_the_integration_unusable(self):
        spec = integ.validate("lf", STDIO)
        env, missing = integ.resolve_env(spec, {"MY_LF_PASSWORD": "pw"})
        self.assertEqual((env, missing), ({"LF_SCHEMA": "clean", "LF_PASSWORD": "pw"}, []))
        ok, blocked = integ.usable({"lf": spec}, {})
        self.assertEqual((ok, blocked), ({}, {"lf": ["MY_LF_PASSWORD"]}))

    def test_claude_and_codex_translations(self):
        spec = integ.validate("lf", {**STDIO, "cwd": "/srv/lf"})
        cfg = integ.claude_config({"lf": spec}, {"MY_LF_PASSWORD": "pw"})
        self.assertEqual(cfg["mcpServers"]["lf"], {"command": "python3", "args": ["-m", "lf_tools"], "env": {"LF_SCHEMA": "clean", "LF_PASSWORD": "pw"}})
        self.assertEqual(integ.claude_tools({"lf": spec}), ["mcp__lf"])
        args = integ.codex_args({"lf": spec}, {})
        self.assertIn('mcp_servers.lf.command="python3"', args)
        self.assertIn('mcp_servers.lf.args=["-m", "lf_tools"]', args)
        self.assertIn('mcp_servers.lf.cwd="/srv/lf"', args)
        self.assertIn('mcp_servers.lf.default_tools_approval_mode="approve"', args)
        self.assertTrue(any(a.startswith("mcp_servers.lf.env={") and '"LF_SCHEMA" = "clean"' in a for a in args))

    def test_store_persists_per_project_and_removes(self):
        with tempfile.TemporaryDirectory() as t:
            store = integ.IntegrationStore(Path(t) / "i.json")
            store.set("p1", "lf", STDIO)
            self.assertEqual(list(store.get("p1")), ["lf"])
            self.assertEqual(store.get("p2"), {})
            self.assertEqual(list(integ.IntegrationStore(Path(t) / "i.json").get("p1")), ["lf"])  # survives a restart
            self.assertTrue(store.remove("p1", "lf"))
            self.assertEqual(store.get("p1"), {})
            for i in range(integ.MAX_INTEGRATIONS):
                store.set("p3", f"s{i}", {"kind": "mcp-http", "url": "https://example.org/mcp"})
            with self.assertRaises(ValueError):
                store.set("p3", "one_too_many", {"kind": "mcp-http", "url": "https://example.org/mcp"})


class RunnerInjectionTests(unittest.TestCase):
    def _run(self, kind, specs, env=None):
        captured = {}
        runner = V.CliRunner(kind)
        def fake_run(args, stdin, cwd, timeout):
            captured["args"] = args
            if "--mcp-config" in args:
                captured["config"] = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())  # the file exists while the CLI runs
            return json.dumps({"result": "ok", "session_id": "s"})
        runner._run = fake_run
        with mock.patch("shutil.which", return_value="/bin/x"), mock.patch.dict(os.environ, env or {}, clear=False):
            try:
                runner.work("sys", "{}", Path("."), mcp=specs)
            except V.VPError:
                pass  # codex's fake writes no output file; only the arguments matter
        return captured

    def test_claude_gets_a_config_file_and_the_mcp_tools_allowed(self):
        spec = integ.validate("lf", STDIO)
        got = self._run("claude", {"lf": spec}, {"MY_LF_PASSWORD": "pw"})
        self.assertEqual(got["config"]["mcpServers"]["lf"]["env"]["LF_PASSWORD"], "pw")
        tools = got["args"][got["args"].index("--allowedTools") + 1]
        self.assertIn("mcp__lf", tools)
        self.assertIn("Bash", tools)                                            # the normal worker tools are still there

    def test_codex_gets_overrides_but_never_a_secret_on_the_command_line(self):
        plain = integ.validate("lf", {"kind": "mcp-stdio", "command": ["python3", "-m", "lf_tools"]})
        got = self._run("codex", {"lf": plain})
        self.assertIn('mcp_servers.lf.command="python3"', got["args"])
        secret = integ.validate("lf2", STDIO)
        got = self._run("codex", {"lf2": secret}, {"MY_LF_PASSWORD": "super-secret-pw"})
        self.assertNotIn("super-secret-pw", " ".join(got["args"]))
        self.assertFalse(any("lf2" in a for a in got["args"]))                  # skipped: codex cannot carry secrets safely

    def test_an_integration_missing_its_secret_is_not_handed_to_the_worker(self):
        got = self._run("claude", {"lf": integ.validate("lf", STDIO)}, {"MY_LF_PASSWORD": ""})
        self.assertNotIn("--mcp-config", got["args"])


class ProjectIntegrationTests(VPBase):
    def test_declared_integrations_reach_the_worker_and_its_payload_and_the_api_hides_values(self):
        seen = {}
        original = self.runner.work
        def work(system, prompt, cwd, mcp=None):
            seen["mcp"], seen["payload"] = mcp, json.loads(prompt)
            return original(system, prompt, cwd)
        self.runner.work = work
        self.align(); self.rt.draft_board(self.pid)
        listing = self.rt.set_integration(self.pid, {"name": "lf", "spec": {**STDIO, "env": {"LF_SCHEMA": "clean"}}})
        row = listing["integrations"][0]
        self.assertEqual((row["name"], row["envNames"], row["ready"], row["missingSecrets"]), ("lf", ["LF_SCHEMA"], False, ["MY_LF_PASSWORD"]))
        self.assertNotIn("env", row)                                             # values are never listed, only their names
        a = self.work()["Survey options"]
        self.rt.approve_work(self.pid, a["workId"], {"expectedRevision": a["revision"], "decision": "approve"})
        self.rt.vp.tick(self.pid)
        self.assertEqual(list(seen["mcp"]), ["lf"])
        self.assertEqual(seen["payload"]["integrations"][0]["name"], "lf")
        self.assertIn("Latent Foundry", seen["payload"]["integrations"][0]["description"])
        self.rt.set_integration(self.pid, {"name": "lf", "remove": True})
        self.assertEqual(self.rt.list_integrations(self.pid)["integrations"], [])
        with self.assertRaises(ValueError):
            self.rt.set_integration(self.pid, {"name": "lf", "spec": {"kind": "mcp-stdio", "command": "rm -rf /"}})
        with self.assertRaises(Exception):
            self.rt.list_integrations("project.nope")



class DataDirTests(unittest.TestCase):
    def _args(self, kind, mode, prompt):
        captured = {}
        runner = V.CliRunner(kind)
        runner._run = lambda args, stdin, cwd, timeout: (captured.setdefault("args", args), json.dumps({"result": "ok", "session_id": "s"}))[1]
        with mock.patch("shutil.which", return_value="/bin/x"):
            try:
                runner._ask("sys", prompt, Path("."), mode=mode, timeout=5)
            except V.VPError:
                pass  # codex's fake writes no output file; only the arguments matter
        return captured["args"]

    def test_workers_get_a_writable_data_folder_outside_the_repository_but_readers_do_not(self):
        with tempfile.TemporaryDirectory() as t:
            prompt = json.dumps({"dataDir": t})
            claude = self._args("claude", "write", prompt)
            self.assertEqual(claude[claude.index("--add-dir") + 1], t)
            self.assertIn(f"sandbox_workspace_write.writable_roots={json.dumps([t])}", self._args("codex", "write", prompt))
            for kind in ("claude", "codex"):
                self.assertNotIn(t, " ".join(self._args(kind, "read", prompt)))


class ProjectDataDirTests(VPBase):
    def test_the_worker_payload_names_a_data_folder_next_to_the_repo(self):
        seen = {}
        original = self.runner.work
        self.runner.work = lambda system, prompt, cwd: (seen.setdefault("p", json.loads(prompt)), original(system, prompt, cwd))[1]
        self.align(); self.rt.draft_board(self.pid)
        a = self.work()["Survey options"]
        self.rt.approve_work(self.pid, a["workId"], {"expectedRevision": a["revision"], "decision": "approve"})
        self.rt.vp.tick(self.pid)
        data = Path(seen["p"]["dataDir"])
        self.assertEqual(data, self.rt.workspace_root / self.pid / "data")
        self.assertTrue(data.is_dir())
        self.assertIn("dataDir", V.WORKER_SYSTEM)



class FastFailureRetryTests(unittest.TestCase):
    def _runner(self, results, elapsed=1.0):
        runner = V.CliRunner("codex")
        runner.executable = "/bin/codex"
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            return results.pop(0)

        clock = iter(x * elapsed for x in range(0, 100))
        return runner, calls, fake, clock

    def _result(self, code, out=b"", err=b""):
        return mock.Mock(returncode=code, stdout=out, stderr=err)

    def test_a_fast_failure_is_retried_with_pauses_and_succeeds(self):
        runner, calls, fake, clock = self._runner([self._result(1, err=b"usage limit"), self._result(1), self._result(0, b"done")])
        with mock.patch("ui.prototype.vp.run_bounded_subprocess", fake), mock.patch("ui.prototype.vp.time.sleep") as sleep, \
                mock.patch("ui.prototype.vp.time.monotonic", lambda: next(clock)):
            self.assertEqual(runner._run(["exec"], "p", Path("."), 5), b"done")
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [15, 45])

    def test_persistent_fast_failure_raises_with_the_real_error_tail(self):
        runner, calls, fake, clock = self._runner([self._result(1, err=b"ERROR: You've hit your usage limit.")] * 3)
        with mock.patch("ui.prototype.vp.run_bounded_subprocess", fake), mock.patch("ui.prototype.vp.time.sleep"), \
                mock.patch("ui.prototype.vp.time.monotonic", lambda: next(clock)):
            with self.assertRaises(V.VPError) as caught:
                runner._run(["exec"], "p", Path("."), 5)
        self.assertIn("usage limit", str(caught.exception))
        self.assertEqual(len(calls), 3)

    def test_a_failure_after_long_work_is_not_retried(self):
        runner, calls, fake, clock = self._runner([self._result(2, err=b"crashed")], elapsed=600)
        with mock.patch("ui.prototype.vp.run_bounded_subprocess", fake), mock.patch("ui.prototype.vp.time.sleep") as sleep, \
                mock.patch("ui.prototype.vp.time.monotonic", lambda: next(clock)):
            with self.assertRaises(V.VPError):
                runner._run(["exec"], "p", Path("."), 5)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()



class ModelMcpTests(unittest.TestCase):
    class Runner:
        def __init__(self):
            self.seen = []

        def think(self, system, prompt):
            self.seen.append((system, prompt))
            if "boom" in prompt:
                raise V.VPError("limit reached")
            return "answer:" + prompt

    def call(self, server, args, mid=7):
        return server.handle({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": "ask_model", "arguments": args}})["result"]

    def test_protocol_and_the_text_only_tool(self):
        from ui.prototype.model_mcp import ModelServer
        runner = self.Runner()
        server = ModelServer(runner)
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))  # notifications get no reply
        tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["ask_model"])
        ok = self.call(server, {"prompt": "hello", "system": "be brief"})
        self.assertEqual(ok["content"][0]["text"], "answer:hello")
        self.assertNotIn("isError", ok)
        self.assertEqual(runner.seen[-1], ("be brief", "hello"))
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "rm_rf"}})["error"]["code"], -32602)

    def test_bad_input_failures_size_and_call_caps_are_reported_not_raised(self):
        from ui.prototype import model_mcp
        server = model_mcp.ModelServer(self.Runner())
        for bad in ({}, {"prompt": ""}, {"prompt": 5}, {"prompt": "x", "system": 3}):
            self.assertTrue(self.call(server, bad)["isError"], bad)
        self.assertTrue(self.call(server, {"prompt": "x" * (model_mcp.MAX_PROMPT_CHARS + 1)})["isError"])
        failed = self.call(server, {"prompt": "boom"})
        self.assertTrue(failed["isError"])
        self.assertIn("limit reached", failed["content"][0]["text"])
        with mock.patch.object(model_mcp, "MAX_CALLS", 2):
            capped = model_mcp.ModelServer(self.Runner())
            self.assertFalse(self.call(capped, {"prompt": "a"}).get("isError"))
            self.assertFalse(self.call(capped, {"prompt": "b"}).get("isError"))
            self.assertIn("2 model calls", self.call(capped, {"prompt": "c"})["content"][0]["text"])

    def test_the_real_server_process_speaks_mcp_over_stdio(self):
        import subprocess
        import sys
        root = str(Path(__file__).resolve().parents[3])
        lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                 json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})]
        done = subprocess.run([sys.executable, "-m", "ui.prototype.model_mcp"], input="\n".join(lines) + "\n", capture_output=True, text=True,
                              cwd=root, env={**os.environ, "NORTHSTAR_MODEL_KIND": "claude", "PYTHONPATH": root}, timeout=60)
        replies = [json.loads(x) for x in done.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])                      # exactly one reply per request
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "ask_model")
        bad = subprocess.run([sys.executable, "-m", "ui.prototype.model_mcp"], input="", capture_output=True, text=True, cwd=root,
                             env={**os.environ, "NORTHSTAR_MODEL_KIND": "pi", "PYTHONPATH": root}, timeout=60)
        self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
