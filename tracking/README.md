# GitHub issue register

Canonical live tracking is in the [GitHub issues](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues), the [roadmap parent issue #1](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/1), and the [First validated collaboration adapter milestone](https://github.com/sebastbernal2-ship-it/agent-research-workspace/milestone/1). The repository is private.

## Ownership and status convention

- **Product decisions, provider/budget approval, private-data approval, and HiperGator authorization:** project owner (user).
- **Implementation/execution owners:** currently unassigned; do not infer ownership or notify/assign people until the project owner confirms contributors.
- Status vocabulary: `VERIFIED` (specific test/evidence attached), `UNVERIFIED`, `BLOCKED` (named prerequisite), `REJECTED` (documented gate failure). Scripted fixtures never count as real-model quality evidence.

## Workstream register

| Issue | Workstream | Current status / blocker | Depends on |
|---|---|---|---|
| [#2](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/2) | Canonical project/work/event/evidence contracts | OPEN; capability/question event contracts, migration policy, local SQLite replay, dependency/prerequisite invariants and verified-snapshot sequence CAS added; targeted process-kill recovery test passes; migration implementation, broader crash/fault matrix, and production store decision remain | — |
| [#3](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/3) | AG2 → canonical event adapter | OPEN; local AG2 WAL replay, restart, candidate-state deletion, injected canonical-append outage repair, and shared-stream composition tested; listener/outbox/auth gaps remain | #2 |
| [#4](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/4) | AgenticX API/recovery/capability gate | OPEN; deterministic AgentExecutor bridge runs inside AG2 and Meldwork V4; bounded stdio protocol/output and timeout paths tested; POSIX process-group cancellation tested; Studio API/auth/recovery/hardened sandbox gates remain | — |
| [#5](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/5) | Meldwork extraction, portability, dependency remediation | OPEN; pinned Node LocalWorkspace V4 runtime tested with mocks; Electron/macOS product gate and 14 NPM audit findings remain | — |
| [#6](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/6) | Separate question/approval inbox | OPEN; terminal lifecycle and exact pause safety tested; local read model/facade plus loopback synthetic HTTP answer/reject slice tested across restart; ETags and sequence CAS required; real auth/membership, notifications and scheduler remain | #2, #7 |
| [#7](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/7) | Capability permissions and threat model | OPEN; threat model and exact-scope local grants/revocation tests added; identity binding, adapter enforcement and sandbox remain | #2 |
| [#8](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/8) | Blind rubric and bounded real-model pilot | FUTURE; local harness proceeds with deterministic mocks; no provider call required | #2, #4, #5, #6, #7 |
| [#9](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/9) | Laptop worker lifecycle / HiperGator policy gate | FUTURE; neither multi-laptop nor HPC work blocks the local harness | #2, #7 |
| [#10](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/10) | Transparent VP alignment memory | OPEN; provenance/scope/confirmation/expiry/correction/conflict conformance tests use synthetic data only | #2 |
| [#11](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/11) | Select project-first UI and service foundation | OPEN; source/license review and browser-verified loopback project/inbox slice persist synthetic answer/reject decisions across reload/restart; no real auth, models, or production framework; awaiting candidate links | #6 |
| [#12](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/12) | Define model routing and provider adapter contract | OPEN; offline deterministic route/profile contract tests exist; no real provider SDK, credentials, or network calls; official Codex account/service terms remain unreviewed | #2, #7 |

## Current recommended sequence

1. #11 and #12 develop in parallel using mock data only: prototype the project/inbox UI against a service contract and exercise model routing with deterministic providers.
2. #6 and #7 define authenticated principal binding, project authorization, and race-safe command semantics before any remotely reachable API or tool execution.
3. #2 continue broader crash/fault coverage and migration validation; a written migration policy and local contract implementation exist.
4. #3 and #5 adapter recovery/portability gates continue; keep candidate databases separate from canonical state.
5. #4 and #7 candidate-specific API, security, and capability gates; no tools or deployment until reviewed.
6. #8 real-model evaluation requires separate provider, data, and budget approval; current integration remains deterministic. Meldwork's packaged app still needs its supported Mac environment for product-level evaluation.
7. #9 multi-laptop/HiperGator work remains deferred pending policy and authorization.
8. #10 memory/conformance tests use synthetic data; no private profile import until explicitly approved.

The full component evidence is in [`../spikes/`](../spikes/); this register intentionally tracks remaining work, not marketing capability claims.
