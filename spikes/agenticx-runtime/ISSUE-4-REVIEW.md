# Issue #4 — AgenticX runtime gate review

**Pinned runtime:** AgenticX 0.6.5, commit `0fc089ec6bf0b45f48a7beda06907932ebc98998`.
**Scope guard:** Source-only review and deterministic local tests. No AgenticX Studio server/socket was started, no port was exposed, no provider credentials were added, and the vendored upstream tree was not modified.

## Review/test plan

1. Inventory every route exposed by `create_studio_app`, including mounted routers and websocket routes. For each, establish unauthenticated, wrong-token, and correct-token outcomes with a configured token; separately review health/readiness and webhook exemptions. Any execution test must run in-process, use temporary `HOME`/data paths, and use inert inputs so an auth test cannot accidentally trigger external effects.
2. Require a wrapper to reject non-loopback binding unless a separately reviewed TLS reverse-proxy mode is explicitly configured; generate a high-entropy per-run desktop token; create an isolated private home; and keep gateway/chat/MCP integrations disabled. Run only after those checks; no credentials or model providers in this gate.
3. Test actual cold process restart against an isolated durable store, including a crash/retry around completion and a side-effect counter that demonstrates at-most-once dispatch or explicit ambiguity (not exactly-once remote execution).
4. Exercise a deterministic non-coding task through a no-tools worker; include an approval pause/resume and a denied prohibited action. Assert no tool or network callback occurs.
5. Separate observed standalone behavior from cluster/HA claims; inspect actual coordination and durable ownership rather than relying on roadmap/features.
6. Decide whether to adopt the runtime, keep only a narrow component, or reject it. Close the issue only when every acceptance item has evidence; this document does not mark incomplete checks as passed.

## Route inventory and authentication findings

`create_studio_app()` was instantiated in-process using the pinned venv, a temporary `HOME`, and a nonempty test token. No lifespan was started and no network listener was created. The app exposed **264 API/websocket route-method entries**: 197 handlers where source inspection found an apparent auth check, 2 health/readiness exemptions, and **65 handlers with no auth check visible in the endpoint**. The 65 are all routes registered by `agenticx.studio.kb.routes`, `agenticx.brain.routes`, `agenticx.studio.code_index.routes`, `agenticx.studio.web_search.routes`, and `agenticx.studio.typesafe_routes`. They cover reads and writes (including document upload/delete, indexing, config changes, knowledge search, and provider test operations). This is a source-level audit classification, not proof the 197 apparent checks are correct.

Representative in-process TestClient requests with `AGX_DESKTOP_TOKEN` set:

| Route | No token | Wrong token | Correct token | Interpretation |
|---|---:|---:|---:|---|
| `GET /api/session` | 401 | 401 | 200 | Inline protected route behaves as expected. |
| `GET /api/health` | 200 | 200 | 200 | Unauthenticated health exemption. |
| `GET /api/ready` | 200 | 200 | 200 | Unauthenticated readiness exemption. |
| `GET /api/kb/config` | 200 | 200 | 200 | **Unauthenticated router route; misconfigured relative to fail-closed requirement.** |
| `GET /api/brains` | 200 | 200 | 200 | **Unauthenticated router route; misconfigured.** |
| `GET /api/code-index/config` | 200 | 200 | 200 | **Unauthenticated router route; misconfigured.** |
| `GET /api/web-search/config` | 200 | 200 | 200 | **Unauthenticated router route; misconfigured.** |

The mounted `changeplane_routes` webhook separately validates `AGENTICX_CHANGEPLANE_WEBHOOK_SECRET` and returns 403 if it is unset or incorrect. Other mounted route families (`run_replay`, voice, memory graph, data sources, commands) receive a token-check callback and were source-classified as apparently checked. Static presence is not an exhaustive behavioral verification. Framework docs/OpenAPI routes are outside this API count and must also be considered if the app is ever network-exposed.

**Gate result: FAIL. Do not launch Studio, even on loopback, as a security control.** A loopback bind limits remote reachability but does not repair the 65 missing route guards. No safe wrapper can claim the complete acceptance criteria while these remain.

## Other evidence and gaps

- `create_studio_app` sets `desktop_token = os.getenv("AGX_DESKTOP_TOKEN", "").strip()`. Its `_check_token` returns successfully when the value is empty; the CLI's documented default is wildcard bind with no token. Therefore the upstream server is not fail-closed by default.
- Upstream persistence tests: `tests/test_session_manager_persistence.py` and `tests/test_reasoning_chain_persistence.py`: **44 passed** in the pinned venv. These exercise persistence/reconstruction helpers, not a whole-server process restart or duplicate external side-effect behavior.
- Existing deterministic bridge and Meldwork composition tests: `harness/tests/test_agenticx_bridge.py` plus `harness/integration_tests/test_meldwork_agenticx_runtime.py`: **8 passed** using the pinned AgenticX environment. These use an injected fixed provider and no registered tools; they do not cover approval interruption or prohibited-action denial.
- An attempted combined harness run including `test_ag2_agenticx.py` could not collect because the AgenticX venv does not contain AG2 (`ModuleNotFoundError: ag2`). This is an environment mismatch, not a test failure. The separate AG2 test bootstrap is documented in `spikes/ag2-collaboration/README.md`.
- A two-process cold restart probe against an isolated `SessionStore` database succeeded: process A persisted a todo, scratchpad, and session summary metadata; a fresh process B reopened the same DB and recovered all three. Reproduce with `cd spikes/agenticx-runtime && .venv/bin/python restart_probe.py`. This verifies those SQLite records across process boundaries only. It does not exercise the Studio API, full message/workflow continuation, concurrent workers, or external effects.
- A deterministic no-tools non-coding AgenticX task was run through `AgenticXSubprocessWorker` with the fixed provider. It returned the supplied scripted evaluation criteria. This shows the task path runs with an empty tool list, not independent reasoning or safe-policy enforcement.
- Focused canonical tests: `test_capabilities.py`, `test_effect_projection.py`, `test_durable_queue.py`, and `test_event_store.py` selected by `-k 'capability or approval or ambiguous or dispatch_marker_crash'`: **13 passed, 46 deselected**. They demonstrate capability denial before an injected effect runs, human approval required before effect dispatch, ambiguity not automatically redispatched, and canonical crash-marker behavior. These are control-plane tests, not AgenticX Studio approval-pause/resume integration.
- Remaining: no end-to-end AgenticX approval interruption/resume or prohibited-action policy test; no duplicate external side-effect execution proof; no safe launcher is provided because the route-auth gap makes launching the Studio unsafe even on loopback. No HA/cluster claim is supported by this evidence.

## Interim decision

**Reject AgenticX Studio as a control plane for this project; retain only the narrow bounded AgenticX worker component behind the existing adapter.** The observed missing authentication on 65 route-method entries is a release-blocking security finding, and this evaluation does not patch upstream. The deterministic subprocess boundary is useful composition evidence, but it is not a hardened sandbox and does not establish model quality. The narrow worker choice is an interim composition decision, not a claim that all issue acceptance criteria passed. Issue #4 remains open pending the remaining evidence and formal resolution of its acceptance checklist.
