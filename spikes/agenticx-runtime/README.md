# AgenticX runtime spike

**Purpose:** Isolated first-component evaluation of AgenticX 0.6.5. Not product integration.  
**Upstream:** `upstream/` is the complete vendored source snapshot from tag `v0.6.5`, commit `0fc089ec6bf0b45f48a7beda06907932ebc98998`. The AgenticX `FinnewsHunter` example source is included at commit `d7a20a1f7ee891feee98a3cc8150614b96cb4f4e`.
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
| Studio API auth / restart persistence | **PARTIAL / NOT TESTED** | Selected session routes passed in-process TestClient with a temporary token; no socket server, full route audit, or restart recovery test. |
| Non-coding research task with an actual model | **NOT TESTED** | No API key/provider configured, by design. |

The mock demo wrote `sales_analysis_report_report.md` in an isolated `/tmp` directory while its final status message claimed `sales_analysis_report.md`; this is an example-level artifact-name mismatch, not proof of a general runtime failure.

## Safety observations and hold points

- `agx serve` in this tag defaults to `0.0.0.0` and an empty token; its own CLI prints an unauthenticated warning when no token is configured. **Do not run with defaults.** Selected API routes were tested in-process with auth enabled. Before a socket server test, complete the route-level auth review and use `--host 127.0.0.1`, temporary `HOME`, a generated token, and isolated data paths.
- We intentionally did not start Studio, enable gateway/chat integrations, connect tools, provide credentials, or expose any port.
- Existing mock demo uses a fake provider and tool callbacks. Its success count is not evidence of reasoning quality, meaningful critique, actual agent-to-agent exchange, or recovery.
- The code has broad scope; the next gate is not importing it into the product. Next compare its newer group path with AG2 on the same collaboration trace, and separately test restart persistence/recovery before considering an adapter.

## Follow-up: Studio group router and API checks (2026-11-07)

### Newer collaboration path

The earlier M6 mock demo exercises the legacy `agenticx.core.BroadcastCommunication`; its zero-message result should **not** be generalized to the separate Studio group router. Studio's `GroupChatRouter` has a newer path: group context, mention-triggered follow-ups, workforce task decomposition/assignment, a DAG scheduler, progress events, shared artifact delivery, and a leader summary. Five targeted suites covering group workforce graph scheduling, broadcast-all, mention follow-ups, control-plane behavior, and artifact delivery passed (**83 passed**).

That is meaningful implementation evidence, but it still does **not** demonstrate a Meldwork-like peer deliberation loop:

- In the complex Workforce path, tasks are decomposed and workers run assigned nodes (parallel where dependencies permit); completed responses are appended to the group context for the leader summary.
- Worker prompts are assembled before their parallel execution. The Workforce path does not itself run a critique/revision round or route each worker's results to peer agents for challenge; it is principally delegated execution + synthesis.
- In ordinary mention follow-ups, an agent can address another member and that member gets a later turn with recent group dialogue. This is actual sequential handoff behavior, but it depends on the model producing a mention and is not a guaranteed evidence-review protocol.
- `graph.social` creates message edges for UI projection. Its debate heat helper can be triggered by human→agent fan-out alone: we reproduced four such edges causing the “discussion is hot” nudge with no agent↔agent messages. Treat debate graph/nudge as visualization, not proof of substantive debate.
- The legacy `BroadcastCommunication` failure remains real for its documented/example usage; the newer Studio router is a distinct path and should be judged separately.

A broader targeted test command that also included `test_smoke_group_workforce_bridge.py` produced **83 passed, 2 failed**: both failures expect tool argument/result previews in `_runtime_event_to_progress_text`, but actual text contains only the tool name. The other tests in that run passed. This is a pinned-release test failure to record, not evidence of message-delivery failure.

Reproduction for the passing Studio-group subset:

```bash
cd spikes/agenticx-runtime/upstream
../.venv/bin/python -m pytest -q --no-cov \
  tests/test_smoke_group_workforce_graph_schedule.py \
  tests/test_smoke_group_broadcast_all.py \
  tests/test_smoke_group_mention_followup.py \
  tests/test_smoke_group_control_plane.py \
  tests/test_group_shared_artifact_delivery.py
```

### Studio API authentication check

Using FastAPI's in-process `TestClient` (no socket/server, no lifespan integration, no model call) and a temporary `HOME`/workspace:

- `/api/session`: no token → **401**; correct `x-agx-desktop-token` → **200**.
- `/api/session/messages`: no token → **401**; correct token → **200**.
- The auth behavior is conditional: `_check_token` permits requests when `AGX_DESKTOP_TOKEN` is empty. The CLI defaults to `--host 0.0.0.0` and empty token, warning that the API is unauthenticated. Therefore, **never use those defaults**. The later full route inventory found **65 API handlers with no auth check visible in the endpoint**, beyond health/readiness; representative KB, Brain, code-index, and web-search routes returned 200 regardless of token. Details and limits are in [`ISSUE-4-REVIEW.md`](ISSUE-4-REVIEW.md). The upstream Studio server remains unsuitable for direct exposure. We did not start a socket server.

### Fail-closed wrapper probe

`safe_studio.py` is a spike-only outer ASGI authentication wrapper. It requires the desktop-token header on every non-health/readiness HTTP route and every WebSocket route, and its optional launcher refuses non-loopback binds, requires the pinned venv, generates a 256-bit token, uses a temporary private HOME, removes inherited environment credentials, and disables the gateway. Its in-process tests cover all 260 non-exempt API method entries for missing/wrong tokens, and forward valid-token requests for all 262 API method entries plus both WebSockets to an inert ASGI sink; actual route handlers have representative valid-token tests. **The launcher itself has not been run; no socket or port was opened.** This wrapper is neither a hardened sandbox nor product adoption, and does not cure the upstream source defects. Deterministic worker/capability probes are in `test_mock_policy.py`; `test_studio_approval_flow.py` drives the real in-process Studio chat/approval routes with a deterministic LLM and an intercepted subprocess; it also demonstrates that pending gate state is missing from restored turn checkpoints. `restart_probe.py` checks SQLite session metadata across fresh processes. Reproduce the offline tests with:

```bash
cd spikes/agenticx-runtime
.venv/bin/python -m pytest -q test_safe_studio.py test_mock_policy.py test_studio_approval_flow.py
.venv/bin/python restart_probe.py
```

### Revised decision

**Use AgenticX as the bounded worker runtime, not as the project control plane or sole collaboration orchestrator.** The vendored `AgentExecutor` is already called through `harness/agenticx_bridge.py` and is injected as a deterministic worker into Meldwork V4 and the AG2 critic workflow. Its Studio group path remains an optional candidate; it does not itself establish the desired evidence-citing peer review loop. The API token is optional and binding defaults to all interfaces, so the Studio server is not exposed by this product composition and would require wrapper-level fail-closed configuration and a complete route/auth review before use.

## Harness follow-up (2026-11-07)

`harness/agenticx_bridge.py` starts the pinned AgenticX venv in a child process with temporary HOME/cwd, reduced environment, bounded JSON input/output, timeout, no tool configuration, and an injected deterministic provider result. It is used in the AG2 proposal–critique–revision test and Meldwork V4 offline orchestration test. This process boundary is **not a hardened sandbox**. No credentials, actual model call, Studio HTTP server, network, tool, or HiperGator execution was introduced. AG2's local message path and Meldwork's Node `LocalWorkspace` path are now tested separately; neither changes the model-quality/security findings above.

## Cleanup / reset

The local environment is disposable: `rm -rf .venv`. The vendored upstream source snapshot is included directly in this repository; keep product integration changes in `harness/` or a documented patch rather than silently modifying the source mirror.
