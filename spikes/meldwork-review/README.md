# Meldwork V1.0.9 source and Linux test review

**Purpose:** Review the collaboration specialist candidate and run isolated, offline Node tests on Linux. This is not a Meldwork product integration or model-backed pilot.

**Upstream:** `upstream/` is a Git submodule pinned to release commit `1ddf3fbdd76ef6e6b3baddfa6a460a72570aa7fd` (Meldwork V1.0.9; Apache-2.0).
**Environment:** Linux, Node v24.22.1 / npm 11.19.1. No Agent CLI, model credentials, user data, or real model calls were used.

## What the source shows

V1.0.9 has a much more mature collaboration domain than “desktop chat with agents.” Its V4 Auto Discussion specification and implementation cover:

- independent proposals and a challenge phase;
- participant agreement on a normalized responsibility graph before work;
- typed work receipts and peer artifact/evidence verification;
- synthesis, candidate acceptance, controlled convergence, and durable commit/delivery state;
- persisted Human Gates, pause/resume, retry boundaries, crash recovery, idempotent writes, and artifact/outcome provenance;
- local Agent connectors and per-run permission/budget controls.

This makes Meldwork the strongest **collaboration behavior/reference implementation** inspected so far. The distinction between reasoning trace, artifacts/evidence, acceptance, and human decisions maps well to our requirements. These claims describe the source implementation and test suite; they do not establish model correctness.

### Product boundary still matters

Meldwork's supported product is a **local Electron application**; its published V1.0.9 artifact is Apple-silicon macOS. The source architecture explicitly has no required account/tenant server, hosted web product, remote conversation store, scheduled jobs, or production remote-Agent federation. Cloud/Channel connectors exist as opt-in seams, but their production implementations are outside the release boundary. The local project state and event/recovery system are owned by Electron main-process services and file stores.

Thus Meldwork is not a ready-made web/multi-laptop control plane. Reusing its collaboration code likely means extracting/adapting the V4 domain and state contracts, not embedding its UI/database wholesale. Agent Connector interfaces are promising boundaries, but AG2's `Agent`/`HubClient` and Meldwork's local Agent runtime do not currently share a tested bridge.

## Offline test results

Dependencies were installed with lifecycle scripts disabled; Electron's postinstall/binary download was not run. The NPM test suites use fixtures, fake Agents, and temporary data paths.

```bash
npm --prefix desktop ci --ignore-scripts
npm --prefix desktop test -- --suite collaboration
npm --prefix desktop test -- --suite security

cd desktop
node --test \
  test/workspace/local-workspace-v4-agent-negotiation.test.cjs \
  test/workspace/local-workspace-v4-auto-durability.test.cjs \
  test/workspace/local-workspace-v4-convergence.test.cjs \
  test/workspace/local-workspace-v4-gate-pause.test.cjs \
  test/workspace/local-workspace-v4-natural-discussion.test.cjs \
  test/workspace/local-workspace-v4-receipt.test.cjs \
  test/workspace/local-workspace-v4-task-decision.test.cjs
```

Results:

- Collaboration domain/record suite: **86 passed**.
- Desktop trust-boundary/security suite: **78 passed**.
- Focused V4 execution, discussion, gate, recovery, convergence, and receipt suites: **139 passed** (56.6 seconds).
- These are meaningful deterministic behavioral tests, not end-to-end real-model quality evidence. We did not run the entire desktop suite, frontend suite, package/build, or native Agent acceptance. Upstream README release notes report real six-Agent runs; those are **upstream claims not independently reproduced here**.

## Dependency audit finding

`npm --prefix desktop ci --ignore-scripts` completed, but NPM reported **14 dependency vulnerabilities (6 moderate, 8 high, 0 critical)** in the pinned desktop package tree. `npm audit` specifically flags direct Electron, electron-builder, and Zod dependencies plus transitives; some suggested fixes require major upgrades. This needs maintainer remediation/version review before adopting or distributing the desktop product. It does not invalidate offline domain tests, but it blocks treating this pinned package tree as production-ready.

No security changes were made upstream. The detailed audit summary is recorded in this README rather than committing generated `node_modules` or an audit dump.

## Component decision

**Advance Meldwork as the collaboration-behavior benchmark, not yet as the product base.** Among the evaluated components:

- Meldwork has the richest tested propose → challenge → agree → work → verify → synthesize/accept path and local recovery/gates.
- AG2 has a cleaner protocol/network substrate (typed channels, Hub/WAL, optional durable store, HumanClient, authenticated WebSocket links) but its discussion semantics are round-robin or declared workflows, and its agents need an adapter for AgenticX/Meldwork.
- AgenticX offers a broad runtime and Studio Workforce delegation path, but that path currently establishes execution/delegation + synthesis, not the full negotiated peer review loop; secure server deployment needs further work.

**Do not fuse source trees.** Keep a project-owned control plane and contract; use Meldwork as a collaboration-behavior reference. A later local harness prototype now runs the pinned Node `LocalWorkspace` V4 orchestration with deterministic AgenticX workers and maps its parsed/sanitized receipts into canonical SQLite. This is an adapter/protocol proof only—not Electron, the supported Apple-silicon app, or model-backed behavior. The product dependency audit and portability gates remain open; real-model comparison is future work and is not blocking offline development.

## Harness follow-up (2026-11-07)

`harness/meldwork_runtime_worker.cjs` now exercises the pinned `LocalWorkspace` V4 pipeline through proposal, challenge, negotiated work, synthesis, and verification. It routes eight deterministic mock outputs through actual AgenticX `AgentExecutor` subprocess calls, returns structured V4 receipts, and sends those receipts through the pinned parser/sanitizer into one atomic canonical event batch. The end-to-end test verifies full replay, content hashes, and event sequence. No Electron process, real model, credential, or user data is involved. This is stronger than the parser-only boundary test, but it does not change the supported-product/platform/security assessment above.
