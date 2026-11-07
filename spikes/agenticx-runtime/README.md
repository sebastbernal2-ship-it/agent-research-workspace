# AgenticX runtime spike

**Purpose:** Isolated first-component evaluation of AgenticX 0.6.5. Not product integration.  
**Upstream:** `upstream/` is a Git submodule pinned to tag `v0.6.5`, commit `0fc089ec6bf0b45f48a7beda06907932ebc98998` (Apache-2.0).  
**Environment tested:** Linux, CPython 3.12.3. No provider credentials, user data, or external model calls.

## Reproduce

```bash
cd spikes/agenticx-runtime
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r upstream/requirements.lock
uv pip install --python .venv/bin/python --no-deps -e upstream
.venv/bin/agx --help
.venv/bin/agx version
```

The checked-in upstream lock is generated with the `desktop-runtime` extra, so this proof-of-install brings in more than the minimal core. It pins `litellm==1.80.11`; it does not pin artifact hashes. The resulting local venv was ~489 MB. The venv is ignored and is not in Git.

To run the targeted upstream collaboration-intelligence tests (pytest and pytest-cov were installed into the isolated venv for this check):

```bash
uv pip install --python .venv/bin/python 'pytest>=7.4,<9' 'pytest-asyncio>=0.21,<1' 'pytest-cov>=4,<6'
cd upstream
../.venv/bin/python -m pytest -q tests/test_m8_5_collaboration_intelligence.py
```

## Results (2026-11-07)

| Check | Result | What it establishes |
|---|---|---|
| Pinned source checkout | **PASS** | Tag resolves to the commit above. |
| Dependency install | **PASS** | Locked desktop-runtime dependency set and editable package install completed on this Linux/Python version. |
| CLI/import | **PASS** | `agx --help`, `agx version` (0.6.5), and `import agenticx` work. |
| Upstream collaboration-intelligence tests | **PASS, limited scope** | `tests/test_m8_5_collaboration_intelligence.py`: 22 passed in 49.22 seconds. These test scheduling/role/routing logic; they do not establish a durable end-to-end workforce. |
| Mock multi-agent demo | **PARTIAL** | `examples/m6_multi_agent_demo.py` exited 0; four scripted mock agents completed. No LLM/provider was called. This validates only that this example's executor paths run. |
| Actual message exchange in that demo | **FAIL** | It reports **0 messages sent/received** for all four agents. Its `send_team_message` example tool only returns a string; it does not route a message. |
| BroadcastCommunication handoff | **FAIL in this usage** | Reproduced with two instances: each instance has its own group list (`['alice']`, `['bob']`); Alice's broadcast has zero recipients and Bob has no pending messages. The M6 example constructs one instance per agent, so `join_group` does not create a shared group. |
| Studio/server, auth, restart persistence | **NOT TESTED** | We did not start a server or exercise API endpoints. |
| Non-coding research task with an actual model | **NOT TESTED** | No API key/provider configured, by design. |

The mock demo wrote `sales_analysis_report_report.md` in an isolated `/tmp` directory while its final status message claimed `sales_analysis_report.md`; this is an example-level artifact-name mismatch, not proof of a general runtime failure.

## Safety observations and hold points

- `agx serve` in this tag defaults to `0.0.0.0` and an empty token; its own CLI prints an unauthenticated warning when no token is configured. **Do not run with defaults.** Before a server test, review the route-level auth behavior and test only with `--host 127.0.0.1`, a temporary `HOME`, a generated temporary token, and isolated data paths. We have not verified that every route is protected correctly.
- We intentionally did not start Studio, enable gateway/chat integrations, connect tools, provide credentials, or expose any port.
- Existing mock demo uses a fake provider and tool callbacks. Its success count is not evidence of reasoning quality, meaningful critique, actual agent-to-agent exchange, or recovery.
- The code has broad scope; the next gate is not importing it into the product. First establish that its selected collaboration/runtime path can exchange messages through a shared bus and that the secured Studio API works under a local-only test.

## Current decision

**Keep AgenticX as a runtime candidate; do not integrate it yet.** Basic install/import on Linux passes, but this spike has not demonstrated the core required behavior of meaningful multi-agent discussion. The built-in `BroadcastCommunication` usage in the upstream M6 demo is not a working inter-agent message path as exercised. Before extending the spike, inspect the newer collaboration/group-router path and its shared transport; compare it with AG2 rather than patching upstream immediately. If we do patch or wrap it, keep the fix in our adapter/spike and add a regression test—do not silently fork behavior into the product.

## Cleanup / reset

The local environment is disposable: `rm -rf .venv`. Submodule checkout: `git submodule update --init --recursive`. Do not edit `upstream/` for the initial evaluation; changes belong in a separately documented patch or adapter.
