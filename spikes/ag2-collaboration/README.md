# AG2 Network collaboration spike

**Purpose:** Test AG2 as a candidate for the agent-message / discussion layer—not as the whole product.

**Upstream:** `upstream/` is a Git submodule pinned to tag `v1.0.0`, commit `22f5d2e156dde56f61145bd4c9b10fca9d3e25cc` (Apache-2.0).
**Environment:** Linux, CPython 3.12.3. No provider credentials or real model calls.

## Why this component

AG2 v1.0 has a protocol-driven Network: a Hub owns identity registry, typed channel state, message write-ahead logs, audit events, and policy/expectation hooks. Its built-in channel types have explicit behavior: two-party free-form `conversation`, one-question/one-reply `consulting`, multi-party round-robin `discussion`, and a transition-graph `workflow`. It also includes a `HumanClient` so a UI can join the same protocol as a non-LLM participant.

This is a substantially better **message exchange primitive** for our needs than the legacy `agenticx.core.BroadcastCommunication` usage exercised in the prior AgenticX demo. It does not supply our project VP, project/task database, alignment profile, web UI, or product-level inbox.

## Reproduction / installation

The v1.0.0 repo explicitly separates the new `ag2` API from the maintained `ag2-classic`/`autogen` namespace; this spike evaluates the new Network API. Python >=3.10; Apache-2.0.

```bash
cd spikes/ag2-collaboration
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e upstream
```

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
- No Internet-facing server was started by this spike. Upstream tests used only local ephemeral loopback sockets.

## Composition hypothesis

AG2 may be the strongest current candidate for the **collaboration transport/protocol**. Keep the product control plane authoritative and expose work/project/decision identities in a versioned envelope contract. Initially use AG2 `AgentClient` agents end-to-end. Only after that is stable should we test an AgenticX-to-AG2 bridge; wrapping AgenticX now would combine two runtimes before either's product role is proven. If the whole team eventually uses AG2 agents, this avoids the bridge and treats AgenticX as a set of optional worker capabilities instead.

**Decision:** continue AG2 as the message-transport candidate behind a local canonical adapter. Do not make it the project database or product security boundary. A real-model non-coding pilot is future optional work requiring explicit provider/model/spend approval; it does not block the current offline harness.

## Harness follow-up (2026-11-07)

A separate adapter now mirrors accepted AG2 discussion envelopes into canonical project/work events. Tests run the pinned in-process round-robin Hub, AgenticX mock-worker challenge/revision, persistent DiskKnowledgeStore WAL restart/replay, idempotency, and canonical history after deleting AG2 state. A combined test also routes a synthetic Meldwork V4 verification receipt into the same project stream. These validate protocol/storage convergence only; no remote Hub, network auth deployment, provider, or model reasoning was tested. Upstream HubListener callbacks swallow errors, so the adapter exposes degradation and WAL replay remains the recovery path; a production outbox/reconciliation boundary is still open.
