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

## Initial evidence

- `create_studio_app` sets `desktop_token = os.getenv("AGX_DESKTOP_TOKEN", "").strip()`. Its `_check_token` returns successfully when the value is empty; the CLI's documented default is wildcard bind with no token. Therefore the upstream server is not fail-closed by default.
- The Studio app mounts several route modules with a supplied token checker, but also mounts `changeplane_routes` independently. That webhook uses its own `AGENTICX_CHANGEPLANE_WEBHOOK_SECRET` and fails closed when unset; it is a separate authentication path that must be included in the inventory. This is a concrete reason not to infer full route coverage from searching inline `_check_token` calls.
- `/api/health` and `/api/ready` are documented token exemptions in the current spike notes; their exact output/data exposure remains to be verified.
- Upstream persistence tests: `tests/test_session_manager_persistence.py` and `tests/test_reasoning_chain_persistence.py`: **44 passed** in the pinned venv. These exercise persistence/reconstruction helpers, not a whole-server process restart or duplicate external side-effect behavior.
- Existing deterministic bridge and Meldwork composition tests: `harness/tests/test_agenticx_bridge.py` plus `harness/integration_tests/test_meldwork_agenticx_runtime.py`: **8 passed** using the pinned AgenticX environment. These use an injected fixed provider and no registered tools; they do not cover approval interruption or prohibited-action denial.
- An attempted combined harness run including `test_ag2_agenticx.py` could not collect because the AgenticX venv does not contain AG2 (`ModuleNotFoundError: ag2`). This is an environment mismatch, not a test failure. The separate AG2 test bootstrap is documented in `spikes/ag2-collaboration/README.md`.
- No server restart, route-by-route auth matrix, safe launcher, or side-effect replay test has been completed in this gate. No HA/cluster claim is supported by this evidence.

## Interim decision

**Retain only the narrow bounded AgenticX worker component behind the existing adapter; do not adopt AgenticX Studio as a control plane or expose its API.** The deterministic subprocess boundary is useful composition evidence, but it is not a hardened sandbox and does not establish model quality. Continue the gate before making a broader adoption decision. Issue #4 remains open pending the listed missing acceptance evidence.
