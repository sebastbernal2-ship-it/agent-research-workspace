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
        runner, calls, fake, clock = self._runner([self._result(1, err=b"temporary network error"), self._result(1), self._result(0, b"done")])
        with mock.patch("ui.prototype.vp.run_bounded_subprocess", fake), mock.patch("ui.prototype.vp.time.sleep") as sleep, \
                mock.patch("ui.prototype.vp.time.monotonic", lambda: next(clock)):
            self.assertEqual(runner._run(["exec"], "p", Path("."), 5), b"done")
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [15, 45])

    def test_persistent_fast_failure_raises_with_the_real_error_tail(self):
        runner, calls, fake, clock = self._runner([self._result(1, err=b"ERROR: connection reset by peer")] * 3)
        with mock.patch("ui.prototype.vp.run_bounded_subprocess", fake), mock.patch("ui.prototype.vp.time.sleep"), \
                mock.patch("ui.prototype.vp.time.monotonic", lambda: next(clock)):
            with self.assertRaises(V.VPError) as caught:
                runner._run(["exec"], "p", Path("."), 5)
        self.assertIn("connection reset", str(caught.exception))
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



class UsageLimitTests(VPBase):
    LIMIT = b"ERROR: You've hit your usage limit. Upgrade to Pro, visit https://chatgpt.com/settings/usage or try again at 3:50 PM."

    def test_the_reset_time_is_read_from_the_cli_message_in_local_time(self):
        from datetime import datetime
        noon = datetime(2026, 11, 18, 12, 0).timestamp()
        until = V.limit_reset_time("... try again at 3:50 PM.", now=noon)
        self.assertEqual(datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M"), "2026-11-18 15:51")      # a minute past, same day
        late = datetime(2026, 11, 18, 18, 0).timestamp()
        self.assertEqual(datetime.fromtimestamp(V.limit_reset_time("try again at 3:50 PM", now=late)).strftime("%d %H:%M"), "19 15:51")  # next day
        self.assertEqual(datetime.fromtimestamp(V.limit_reset_time("try again at 12:05 AM", now=noon)).strftime("%d %H:%M"), "19 00:06")
        self.assertEqual(V.limit_reset_time("no time given", now=noon), noon + V.DEFAULT_LIMIT_PAUSE)

    def test_a_limit_stops_further_calls_until_the_reset_and_is_not_retried_like_an_outage(self):
        runner = V.CliRunner("codex")
        runner.executable = "/bin/codex"
        calls = []
        runner_result = mock.Mock(returncode=1, stdout=b"", stderr=self.LIMIT)

        def fake(cmd, **kwargs):
            calls.append(cmd)
            return runner_result

        with mock.patch("ui.prototype.vp.run_bounded_subprocess", fake), mock.patch("ui.prototype.vp.time.sleep") as sleep:
            with self.assertRaises(V.UsageLimit) as first:
                runner._run(["exec"], "p", Path("."), 5)
            self.assertGreater(first.exception.until, __import__("time").time())
            self.assertFalse(runner.available())                         # the scheduler sees "no model right now"
            with self.assertRaises(V.UsageLimit):
                runner._run(["exec"], "p", Path("."), 5)
        self.assertEqual(len(calls), 1)                                  # one real call; the second never reached the CLI
        sleep.assert_not_called()
        runner.limited_until = 0.0
        self.assertTrue(runner.available())

    def test_a_task_that_hits_the_limit_is_paused_and_requeued_not_failed_and_runs_after_the_reset(self):
        self.align(); self.rt.draft_board(self.pid); self.rt.set_automation(self.pid, {"autoRun": True})
        raised = {"n": 0}
        original = self.runner.work

        def work(system, prompt, cwd):
            if raised["n"] == 0:
                raised["n"] += 1
                raise V.UsageLimit("limit", __import__("time").time() + 3600)
            return original(system, prompt, cwd)

        self.runner.work = work
        self.rt.vp.tick(self.pid)
        item = self.work()["Survey options"]
        self.assertEqual(item["status"], "ready")                         # back in the queue, never 'failed'
        notes = [a["text"] for a in self.rt.activity(self.pid) if "usage limit" in a["text"]]
        self.assertTrue(any("not a failure of the work" in n for n in notes))
        self.rt.vp.tick(self.pid)                                         # the limit has lifted
        self.assertEqual(self.work()["Survey options"]["status"], "completed")

    def test_the_summary_says_when_work_resumes_while_the_account_is_limited(self):
        self.assertIsNone(self.rt.project_summary(self.pid)["pausedUntil"])
        runner = V.CliRunner("codex")
        runner.executable = "/bin/codex"
        runner.limited_until = __import__("time").time() + 3600
        self.rt.vp.runner = runner
        self.assertIsNotNone(self.rt.project_summary(self.pid)["pausedUntil"])

    def test_tasks_an_exhausted_limit_already_marked_failed_are_retried_automatically(self):
        from ui.prototype.vp import ORCHESTRATOR, SUBAGENT, VP
        self.align()
        self.rt.set_automation(self.pid, {"autoRun": True})
        w = self.rt.vp._new_work(self.pid, "Victim", "obj", "research", "ready", SUBAGENT, [], ["done"], None, VP)
        orch = {"actorType": "agent", "actorId": "agent.orchestrator", "role": "orchestrator"}
        self.rt.control.transition_work(self.pid, w["workId"], "running", actor=orch)
        self.rt.vp._finish(self.pid, self.rt.control.projector.replay(self.pid)["workItems"][w["workId"]], "failed",
                           "The codex CLI failed (ERROR: You've hit your usage limit. try again at 3:50 PM.).", parent=False)
        self.assertEqual(next(x["status"] for x in self.rt.control.projector.replay(self.pid)["workItems"].values() if x["title"] == "Victim"), "failed")
        self.rt.vp.tick(self.pid)
        self.assertEqual(next(x["status"] for x in self.rt.control.projector.replay(self.pid)["workItems"].values() if x["title"] == "Victim"), "completed")
        other = self.rt.vp._new_work(self.pid, "Genuinely broken", "obj", "research", "ready", SUBAGENT, [], ["done"], None, VP)
        self.rt.control.transition_work(self.pid, other["workId"], "running", actor=orch)
        self.rt.vp._finish(self.pid, self.rt.control.projector.replay(self.pid)["workItems"][other["workId"]], "failed", "The tests do not pass.", parent=False)
        self.rt.vp.tick(self.pid)
        self.assertEqual(next(x["status"] for x in self.rt.control.projector.replay(self.pid)["workItems"].values() if x["title"] == "Genuinely broken"), "failed")  # real failures stay failed



class AccountChainTests(unittest.TestCase):
    def config(self, roles):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "workers.json"
        path.write_text(json.dumps({"profiles": {"codex_a": {"kind": "codex"}, "codex_b": {"kind": "codex", "env": {"CODEX_HOME": "/home/u/.codex-b"}},
                                                  "claude": {"kind": "claude"}}, "roles": roles}))
        return path

    def test_a_role_can_name_an_ordered_chain_and_bad_chains_are_rejected(self):
        profiles, roles = V.load_worker_config(self.config({"worker": ["codex_a", "codex_b", "claude"], "vp": "claude"}))
        self.assertEqual(roles, {"worker": ["codex_a", "codex_b", "claude"], "vp": ["claude"]})
        self.assertEqual(profiles["codex_b"].extra_env["CODEX_HOME"], "/home/u/.codex-b")      # a second Codex account is just another CODEX_HOME
        for bad in ({"worker": []}, {"worker": ["nope"]}, {"worker": ["codex_a", 5]}, {"nobody": ["claude"]}):
            with self.assertRaises(ValueError):
                V.load_worker_config(self.config(bad))

    def test_the_router_walks_the_chain_past_limited_accounts_and_back_when_they_recover(self):
        profiles, roles = V.load_worker_config(self.config({"worker": ["codex_a", "codex_b", "claude"]}))
        with mock.patch("shutil.which", return_value="/bin/x"):
            for p in profiles.values():
                p.executable = "/bin/x"
            router = V.RoutedRunner(profiles, roles)
            self.assertIs(router._pick("worker"), profiles["codex_a"])
            profiles["codex_a"].limited_until = __import__("time").time() + 3600           # account A is out of quota
            self.assertIs(router._pick("worker"), profiles["codex_b"])                      # account B takes over
            profiles["codex_b"].limited_until = __import__("time").time() + 3600
            self.assertIs(router._pick("worker"), profiles["claude"])                       # then Claude Code
            self.assertEqual(router.limit_until(), 0.0)                                      # still usable: the project is not paused
            profiles["claude"].limited_until = __import__("time").time() + 7200
            self.assertFalse(router.available())                                             # everything limited: now it pauses
            self.assertGreater(router.limit_until(), 0.0)
            profiles["codex_a"].limited_until = 0.0
            self.assertIs(router._pick("worker"), profiles["codex_a"])                      # back to the first account at its reset
            self.assertEqual(router.describe()["worker"], "codex_a > codex_b > claude")

    def test_the_model_tool_can_use_the_configured_chain(self):
        from ui.prototype import model_mcp
        root = str(Path(__file__).resolve().parents[3])
        import subprocess, sys
        path = self.config({"vp": ["codex_a", "claude"]})
        done = subprocess.run([sys.executable, "-m", "ui.prototype.model_mcp"], input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
                              capture_output=True, text=True, cwd=root, timeout=60,
                              env={**os.environ, "NORTHSTAR_MODEL_KIND": "auto", "NORTHSTAR_WORKERS_CONFIG": str(path), "PYTHONPATH": root})
        self.assertEqual(json.loads(done.stdout.splitlines()[0])["result"]["tools"][0]["name"], "ask_model")



class PiAccountRotationTests(unittest.TestCase):
    """The rotation is exercised with a fake `pi` and a fake account tool that follow the real contracts; no real credential is touched."""

    FAKE_TOOL = """#!{py}
import json, os, sys
live = os.path.join(os.environ["FAKE_DIR"], "live")
if sys.argv[1] == "list":
    print(json.dumps({{"ok": True, "live_name": open(live).read().strip()}}))
elif sys.argv[1] == "use":
    open(live, "w").write(sys.argv[2]); open(os.path.join(os.environ["FAKE_DIR"], "uses"), "a").write(sys.argv[2] + "\\n")
    print(json.dumps({{"ok": True, "active": sys.argv[2]}}))
"""
    FAKE_PI = """#!{py}
import os, sys
base = os.path.dirname(os.environ["PI_CODING_AGENT_DIR"])
live = open(os.path.join(base, "live")).read().strip()
open(os.path.join(base, "calls"), "a").write(live + "\\n")
spent = open(os.path.join(base, "spent")).read().strip().split(",") if os.path.exists(os.path.join(base, "spent")) else []
if live in spent:
    print("Codex error: The usage limit has been reached", file=sys.stderr); sys.exit(1)
print("answer-from-" + live)
"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        for name, body in (("tool", self.FAKE_TOOL), ("pi", self.FAKE_PI)):
            path = self.dir / name
            path.write_text(body.format(py=__import__("sys").executable))
            path.chmod(0o755)
        (self.dir / "live").write_text("current")
        (self.dir / "agent").mkdir()
        (self.dir / "agent" / "auth.json").write_text("{}")
        self.patch = mock.patch.dict(os.environ, {"FAKE_DIR": str(self.dir)})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def runner(self, rotation=True, now=None):
        from ui.prototype.accounts import AccountRotation
        rot = AccountRotation(self.dir / "tool", ["current", "roy", "sean"], state_path=self.dir / "blocks.json", **({"clock": now} if now else {})) if rotation else None
        pi = V.PiRunner(agent_dir=self.dir / "agent", rotation=rot)
        pi.executable = str(self.dir / "pi")
        return pi, rot

    def spend(self, *names):
        (self.dir / "spent").write_text(",".join(names))

    def uses(self):
        path = self.dir / "uses"
        return path.read_text().split() if path.exists() else []

    def test_a_spent_account_rotates_to_the_next_then_stays_there_and_remembers_across_restarts(self):
        self.spend("current")
        pi, rot = self.runner()
        self.assertEqual(pi.think("s", "p"), "answer-from-roy")          # current is spent: the tool switched to roy and the call was retried
        self.assertEqual(self.uses(), ["roy"])
        self.assertEqual(pi.think("s", "p"), "answer-from-roy")          # no more switching while roy works
        self.assertEqual(self.uses(), ["roy"])
        self.assertTrue(rot.is_blocked("current"))
        again, _ = self.runner()                                          # a restarted process knows current is spent: no churn back
        self.assertTrue(again.rotation.is_blocked("current"))
        self.assertEqual(again.think("s", "p"), "answer-from-roy")
        self.assertEqual(self.uses(), ["roy"])

    def test_every_account_spent_pauses_until_the_earliest_reset_and_the_limit_lifts_by_itself(self):
        self.spend("current", "roy", "sean")
        clock = {"t": 1_000_000.0}
        pi, rot = self.runner(now=lambda: clock["t"])
        with mock.patch("ui.prototype.vp.time.time", lambda: clock["t"]):
            with self.assertRaises(V.UsageLimit) as caught:
                pi.think("s", "p")
            self.assertEqual(self.uses(), ["roy", "sean"])                # it tried the others before giving up
            self.assertFalse(pi.available())
            self.assertGreater(caught.exception.until, clock["t"])
            calls_before = len((self.dir / "calls").read_text().split())
            with self.assertRaises(V.UsageLimit):
                pi.think("s", "p")
            self.assertEqual(len((self.dir / "calls").read_text().split()), calls_before)   # no calls are spent while everything is blocked
            clock["t"] += 7300                                            # the default block has passed
            self.spend()
            self.assertTrue(pi.available())

    def test_without_a_rotation_a_limit_just_pauses_the_profile(self):
        self.spend("current")
        pi, _ = self.runner(rotation=False)
        with self.assertRaises(V.UsageLimit):
            pi.think("s", "p")
        self.assertFalse(pi.available())
        self.assertEqual(self.uses(), [])                                 # nothing was ever switched

    def test_the_config_loader_builds_a_pi_profile_and_rejects_bad_rotations(self):
        cfg = self.dir / "workers.json"
        good = {"profiles": {"pi": {"kind": "pi", "rotation": ["current", "roy"], "accountCli": str(self.dir / "tool")}, "claude": {"kind": "claude"}},
                "roles": {"worker": ["pi", "claude"]}}
        cfg.write_text(json.dumps(good))
        profiles, roles = V.load_worker_config(cfg)
        self.assertEqual(profiles["pi"].rotation.names, ["current", "roy"])
        self.assertEqual(roles["worker"], ["pi", "claude"])
        for bad in ({"kind": "pi", "rotation": ["current"]}, {"kind": "pi", "rotation": "roy", "accountCli": "/x"},
                    {"kind": "pi", "rotation": ["Bad Name"], "accountCli": "/x"}, {"kind": "pi", "rotation": ["a"], "accountCli": "relative"}):
            cfg.write_text(json.dumps({"profiles": {"pi": bad}}))
            with self.assertRaises(ValueError):
                V.load_worker_config(cfg)

    def test_the_router_uses_pi_for_plain_calls_but_keeps_mcp_integrations_on_a_harness_that_supports_them(self):
        pi, _ = self.runner()
        claude = V.CliRunner("claude")
        codex = V.CliRunner("codex")
        for c in (claude, codex):
            c.executable = "/bin/x"
        codex.limited_until = __import__("time").time() + 3600            # codex is spent
        router = V.RoutedRunner({"codex": codex, "pi": pi, "claude": claude}, {"worker": ["codex", "pi", "claude"]})
        self.assertIs(router._pick("worker"), pi)                           # first available in the chain
        self.assertIs(router._pick("worker", need="mcp"), claude)           # pi cannot carry MCP tools: claude can
        self.assertIs(router._pick("worker", need="explore"), claude)

    def test_a_call_that_hits_a_limit_moves_to_the_next_profile_immediately(self):
        class Fake:
            def __init__(self, name, spent=False):
                self.name, self.spent, self.executable, self.limited_until, self.calls = name, spent, "/x", 0.0, 0

            def available(self):
                return self.limited_until < __import__("time").time()

            def think(self, system, payload):
                self.calls += 1
                if self.spent:
                    self.limited_until = __import__("time").time() + 3600
                    raise V.UsageLimit("limit", self.limited_until)
                return self.name

        a, b = Fake("a", spent=True), Fake("b")
        router = V.RoutedRunner({"a": a, "b": b}, {"vp": ["a", "b"]})
        self.assertEqual(router.think("sys", "p"), "b")                     # a hit its limit mid-call; b answered in the same call
        self.assertEqual((a.calls, b.calls), (1, 1))
        self.assertEqual(router.think("sys", "p"), "b")
        self.assertEqual(a.calls, 1)                                          # a is not tried again while limited
        b.spent = True
        with self.assertRaises(V.UsageLimit):
            router.think("sys", "p")                                          # nothing left: the scheduler pauses


class ModelIdentityTests(unittest.TestCase):
    def test_codex_configured_model_is_read_from_its_config_and_labelled_as_unconfirmed(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "config.toml").write_text('model = "gpt-test-1"\nmodel_reasoning_effort = "medium"\n')
            self.assertEqual(V.codex_configured_model(home), "gpt-test-1")
            self.assertIsNone(V.codex_configured_model(str(Path(home) / "missing")))

    def test_claude_reports_the_real_model_from_its_result_and_the_router_names_the_profile(self):
        runner = V.CliRunner("claude")
        runner.executable = "/bin/claude"
        runner._run = lambda *a, **k: json.dumps({"result": "hi", "session_id": "s", "modelUsage": {"claude-test-9": {}}})
        self.assertEqual(runner.think("sys", "p"), "hi")
        self.assertEqual(runner.last_identity, {"harness": "claude", "model": "claude-test-9", "source": "reported by the harness"})
        router = V.RoutedRunner({"main": runner}, {"vp": ["main"]})
        router.think("sys", "p")
        self.assertEqual(router.last_identity["profile"], "main")
        self.assertEqual(router.last_identity["model"], "claude-test-9")

    def test_ask_model_appends_who_answered_and_how_that_is_known(self):
        from ui.prototype.model_mcp import ModelServer

        class Runner:
            last_identity = {"harness": "codex", "model": "gpt-test-1", "source": "configured in config.toml; the CLI does not confirm it"}

            def think(self, system, prompt):
                return "answer"

        reply = ModelServer(Runner()).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ask_model", "arguments": {"prompt": "x"}}})
        blocks = [b["text"] for b in reply["result"]["content"]]
        self.assertEqual(blocks[0], "answer")
        self.assertIn('"model": "gpt-test-1"', blocks[1])
        self.assertIn("does not confirm", blocks[1])


if __name__ == "__main__":
    unittest.main()
