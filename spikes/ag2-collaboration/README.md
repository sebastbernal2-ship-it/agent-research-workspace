# AG2 Network collaboration spike

**Purpose:** Test AG2 as a candidate for the agent-message / discussion layer—not as the whole product.

**Upstream:** `upstream/` is the complete vendored source snapshot from tag `v1.0.0`, commit `22f5d2e156dde56f61145bd4c9b10fca9d3e25cc`.
**Environment:** Linux, CPython 3.12.3. No provider credentials or real model calls.

## Why this component

AG2 v1.0 has a protocol-driven Network: a Hub owns identity registry, typed channel state, message write-ahead logs, audit events, and policy/expectation hooks. Its built-in channel types have explicit behavior: two-party free-form `conversation`, one-question/one-reply `consulting`, multi-party round-robin `discussion`, and a transition-graph `workflow`. It also includes a `HumanClient` so a UI can join the same protocol as a non-LLM participant.

This is a substantially better **message exchange primitive** for our needs than the legacy `agenticx.core.BroadcastCommunication` usage exercised in the prior AgenticX demo. It does not supply our project VP, project/task database, alignment profile, web UI, or product-level inbox.

## Reproduction / installation

The v1.0.0 repo explicitly separates the new `ag2` API from the maintained `ag2-classic`/`autogen` namespace; this spike evaluates the new Network API. Python >=3.10.

```bash
spikes/ag2-collaboration/bootstrap_test_env.sh
cd "$(git rev-parse --show-toplevel)"
PYTHONPATH="$PWD:$PWD/spikes/ag2-collaboration/upstream" \
  spikes/ag2-collaboration/.venv/bin/python -m unittest \
  harness.integration_tests.test_ag2_mirror_contract \
  harness.integration_tests.test_ag2_mirror -v
```

The upstream snapshot intentionally omits license-related files, so pip's editable wheel build cannot satisfy its `license-files` glob. The bootstrap installs runtime dependencies and a version-only `dist-info` marker; imports resolve directly from the pinned `upstream/` source. It does not copy or modify upstream code.

The core install is small (~37 MB at initial install) and does not install an LLM provider. For this upstream's full `test/network` suite, test-only dependencies were added to the ignored venv: pytest/asyncio/cov/timeout, `watchdog` (DiskKnowledgeStore), `websockets` (transport), OpenTelemetry (transport test instrumentation), and the Anthropic SDK (a few test fixtures instantiate `AnthropicConfig`; no credential or network call was made).

```bash
uv pip install --python .venv/bin/python \
  'pytest==9.1.1' 'pytest-asyncio==1.4.0' 'pytest-cov==7.1.0' \
  'pytest-timeout>=2.4,<3' 'dirty-equals>=0.11,<0.12' \
  'watchdog>=4,<7' 'websockets>=14,<17' \
  'opentelemetry-sdk>=1.20' 'opentelemetry-exporter-otlp-proto-grpc>=1.20' \
  'anthropic>=0.116,<1'
cd upstream
../.venv/bin/python -m pytest -q --no-cov test/network
```

## Tests and offline scripted exchange (2026-11-07)

- **481 upstream Network tests passed** on the pinned checkout, including Hub/channel lifecycle, authorization, round-robin state, persistence/hydration, HumanClient paths, and real WebSocket tests. The tests use mock/scripted models; no API key was configured.
- An additional 3-agent scripted round-robin exchange produced this WAL sequence:
  1. Human-style seed: “Discuss why seasons occur. State a hypothesis, critique it, then revise. No tools or external actions.”
  2. Proposer: “Initial: seasons occur because Earth is closer to the Sun in summer.”
  3. Critic: “Opposite seasons across hemispheres contradict the distance-only claim; axial tilt is the better-supported explanation.”
  4. Synthesizer: “Withdraw the distance claim; test axial tilt against hemispheric season patterns and cite astronomy sources.”

  This proves agents can exchange and sequence real protocol messages, enforce round-robin order, and expose a replayable trace. The content was scripted, so it does **not** prove the models can reason, challenge honestly, or revise well.
- The selected persistence tests exercise a `DiskKnowledgeStore`, close a Hub mid-channel, reopen it, and reconstruct channel/turn state from the WAL. The default README demo uses `MemoryKnowledgeStore`, which is process-local and not durable; the backing store is an explicit deployment choice.
- The Network tests include `HumanClient` message delivery, but there is no ready-made project “pending questions” web inbox. `HumanClient.on_envelope` / `next_envelope` are primitives our UI/control plane can adapt.

## Fit, gaps, and security boundary

### Strong fit for

- Typed A2A envelopes and shared, replayable channel traces.
- Protocol-enforced invitations/acks, membership, turn order, workflow transitions, and audit hooks.
- Process/host separation through the built-in WebSocket transport; upstream tests bind real servers to `127.0.0.1` on ephemeral ports and exercise authenticated handshakes and delivery.
- A user-as-network-participant abstraction (`HumanClient`) that can be bridged to a custom inbox/UI.
- Optional durable Hub state when paired with a persistent KnowledgeStore and explicit versioned event contracts.

### Not a complete solution

- The `discussion` v1 adapter is **round-robin**, not open-ended dynamic debate. `conversation` is two-party; more complex, conditional multi-role flows use explicit `workflow` graphs. A convergence/critique/revision policy and turn/cost/time limits remain application design.
- A successful transport trace says nothing about evidence quality. Source citations, claim provenance, disagreement handling, and blinded evaluation remain our responsibility.
- The Hub registry/channel/WAL is not our canonical project charter, task tree, questions/approvals inbox, or decision ledger. Keep those product-owned.
- AG2 agents are its own `Agent` / `AgentClient` types. AgenticX is not a drop-in AG2 participant; combining them needs a deliberate adapter/bridge. AG2 documents a remote-agent proxy interface but ships no proxy implementations; its built-in WebSocket link is for AG2 network clients.
- The human-input hook `context.input(...)` is a callback seam; translating a suspended run into a separate persistent question record with correct resume semantics still requires a control-plane adapter.

### Security and operations

- **Authentication is opt-in.** `Hub.open()` defaults to `AuthRegistry.default()` with `NoAuth`. The included `ApiKeyAuth` validates per-name keys with constant-time comparison. Every deployed Hub must choose an explicit strict auth registry; do not rely on the default.
- `serve_ws` can receive an SSL context; `WsLinkClient` supports one. Use TLS/WSS for network traffic, secret-manager-backed key resolution, least-privilege identities, and per-agent `Rule`s. The transport performs one connection attempt—reconnect/backoff is the embedder's responsibility.
- Delivery is at-least-once/replay-oriented, not an exactly-once side-effect guarantee. Workers and tool adapters need idempotency keys and deduplication.
- No Internet-facing server was started by this spike. Upstream tests used only local ephemeral loopback sockets. Our project-adapter tests use AG2's in-process Hub only; they do not call `serve_ws`, bind a port, configure a public listener, or supply credentials. The in-process Hub is not an authenticated boundary. Any future network transport must use a strict explicit auth registry, TLS, least-privilege identities, and no `NoAuth` before enabling it.

## Composition hypothesis

AG2 supplies the **agent-message transport and turn protocol** in the combined workflow. The product control plane remains authoritative; project/work/run identities are mapped into canonical events by the local mirror. AgenticX is bridged to AG2 as a deterministic critic in the offline composition, while Meldwork V4 runs a separate dependent collaboration/verification stage. This composes all three sources across a single project DAG without pretending they share an internal database or native agent protocol.

**Decision:** use AG2 as the message-transport component behind the canonical adapter. Do not make its Hub/WAL the project database or product security boundary. The tested composition is local and deterministic; remote Hub authentication, adapter outbox/reconciliation, real-model quality, and deployment remain separate gates. A real-model non-coding pilot requires explicit provider/model/spend approval and does not block offline integration.

## Harness follow-up (2026-11-07)

`harness.integration_tests.test_offline_organization_run` runs a human-gated project DAG with an AG2 proposal → AgenticX subprocess critique → AG2 specialist revision, then invokes Meldwork V4 for the dependent verification task with AgenticX workers. The human's answer arrives through `InboxApplication` as a canonical question-answer event and is asserted absent from AG2/VP discussion messages. AG2 WAL messages and Meldwork phase receipts are imported into the same canonical project history with run attribution and restart replay. The focused adapter+composition run passed 8 tests after hardening. This is scripted protocol success, not real-model quality, native Meldwork/AG2 shared transport, remote auth, or production outbox evidence. Upstream HubListener callbacks swallow errors, so the adapter exposes degradation and WAL replay remains the recovery path. Project-adapter tests use an in-process Hub only; no network transport is exposed.
