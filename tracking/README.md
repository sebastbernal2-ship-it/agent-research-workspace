# GitHub issue register

Canonical live tracking is in the [GitHub issues](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues), the [roadmap parent issue #1](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/1), and the [First validated collaboration adapter milestone](https://github.com/sebastbernal2-ship-it/agent-research-workspace/milestone/1). The repository is private.

## Ownership and status convention

- **Product decisions, provider/budget approval, private-data approval, and HiperGator authorization:** project owner (user).
- **Implementation/execution owners:** currently unassigned; do not infer ownership or notify/assign people until the project owner confirms contributors.
- Status vocabulary: `VERIFIED` (specific test/evidence attached), `UNVERIFIED`, `BLOCKED` (named prerequisite), `REJECTED` (documented gate failure). Scripted fixtures never count as real-model quality evidence.

## Workstream register

| Issue | Workstream | Current status / blocker | Depends on |
|---|---|---|---|
| [#2](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/2) | Canonical project/work/event/evidence contracts | OPEN; capability/question event contracts, migration policy, local SQLite replay, failed-run/conflicting-evidence, dependency DAG validation and prerequisite execution gates added; migration implementation, broader crash/fault matrix, and production store decision remain | — |
| [#3](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/3) | AG2 → canonical event adapter | OPEN; local AG2 WAL replay, restart, candidate-state deletion, and shared-stream composition tested; listener/outbox/auth gaps remain | #2 |
| [#4](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/4) | AgenticX API/recovery/capability gate | OPEN; deterministic AgentExecutor bridge runs inside AG2 and Meldwork V4 prototypes; API/auth/recovery/sandbox gates remain | — |
| [#5](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/5) | Meldwork extraction, portability, dependency remediation | OPEN; pinned Node LocalWorkspace V4 runtime tested with mocks; Electron/macOS product gate and 14 NPM audit findings remain | — |
| [#6](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/6) | Separate question/approval inbox | OPEN; terminal lifecycle and exact pause safety tested; local project-scoped inbox read model now has filters, work status and ETag/history tests; HTTP auth, notification UX, scheduler remain | #2, #7 |
| [#7](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/7) | Capability permissions and threat model | OPEN; threat model and exact-scope local grants/revocation tests added; identity binding, adapter enforcement and sandbox remain | #2 |
| [#8](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/8) | Blind rubric and bounded real-model pilot | FUTURE; local harness proceeds with deterministic mocks; no provider call required | #2, #4, #5, #6, #7 |
| [#9](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/9) | Laptop worker lifecycle / HiperGator policy gate | FUTURE; neither multi-laptop nor HPC work blocks the local harness | #2, #7 |
| [#10](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/10) | Transparent VP alignment memory | OPEN; provenance/scope/confirmation/expiry/correction/conflict conformance tests use synthetic data only | #2 |

## Current recommended sequence

1. #2 finish migration/fault contract gates; a written migration policy and local contract implementation now exist.
2. #7 continue threat-model implementation: authenticate actor binding and wire grants into tool/adapter gates before effects.
3. #6 durable terminal question states now fail closed; next add UI/notification and scheduler continuation semantics only after dependency updates are versioned.
4. #3 and #5 adapters now converge on the canonical event store in isolated offline tests; keep their databases separate and continue fault/recovery/portability gates.
5. #4 and #7 candidate-specific API, security, and capability gates; no tools or deployment until reviewed.
6. #8 real-model evaluations are optional future work; current integration uses deterministic mocks and does not wait on provider or budget decisions. Meldwork's packaged app still needs its supported Mac environment for product-level evaluation.
7. #9 multi-laptop/HiperGator work is deferred and does not block local development.
8. #10 synthetic VP memory/conformance tests; provenance and conflict rules are exercised, but no private profile import until explicitly approved.

The full component evidence is in [`../spikes/`](../spikes/); this register intentionally tracks remaining work, not marketing capability claims.
