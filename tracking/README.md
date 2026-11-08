# GitHub issue register

Canonical live tracking is in the [GitHub issues](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues), the [roadmap parent issue #1](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/1), and the [First validated collaboration adapter milestone](https://github.com/sebastbernal2-ship-it/agent-research-workspace/milestone/1). The repository is private.

## Ownership and status convention

- **Product decisions, provider/budget approval, private-data approval, and HiperGator authorization:** project owner (user).
- **Implementation/execution owners:** currently unassigned; do not infer ownership or notify/assign people until the project owner confirms contributors.
- Status vocabulary: `VERIFIED` (specific test/evidence attached), `UNVERIFIED`, `BLOCKED` (named prerequisite), `REJECTED` (documented gate failure). Scripted fixtures never count as real-model quality evidence.

## Harness completion epics

The user requested six explicit completion issues. These are umbrella tracks; existing issues #2–#12 remain component-level work. Update this table and the matching GitHub issue checklists/comments after each verified implementation slice. Do not close an epic while an acceptance item is blocked or only prototyped.

| Issue | Track | Status | Verified baseline / next gate |
|---|---|---|---|
| [#13](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/13) | Durable organization runtime | **IN PROGRESS** | `run_queued` now claims/heartbeats SQLite leases, resolves only trusted callbacks, and resumes blocked jobs after canonical readiness (including inbox answers) with fresh run generations. Versioned job descriptors now bind the trusted route key, actor/verifier/adapter, context, and correlation to a re-registered factory; the factory must return a fresh run ID for each lease generation, descriptors are bounded, and mismatches fail closed. A supervised local polling loop and atomic lease validation at root `run.started` now exist; the run record stores queue job/generation and stale claims cannot start a canonical run. Trusted factories are re-registered at startup; deployed health lifecycle, authenticated recovery, lease enforcement on output/effect boundaries, and broader fault tests remain. |
| [#14](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/14) | Authenticated identity, authorization, approvals | **NOT STARTED** | Fake identity/caller actors and prototype capability policy remain; select a real identity/session model and enforce membership/capabilities at service boundaries. |
| [#15](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/15) | Worker isolation and external-effect recovery | **PARTIAL** | Bounded subprocesses and Meldwork canonical receipt journal are tested; no hardened sandbox or adapter-side outbox/effect guarantee. |
| [#16](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/16) | Authenticated project UI/service | **PARTIAL** | Synthetic one-project loopback UI/inbox exists; multi-project product flows, real API/auth, VP interaction, and plan approvals remain. |
| [#17](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/17) | Trusted evidence and bounded model workflows | **PARTIAL / GATED** | Caller-supplied evidence registration and offline routing contract exist; authorized fetching, authenticity/review, and approved provider/model pilot remain. |
| [#18](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/18) | Migrations, deployment, and operations | **PARTIAL** | Verified SQLite backup/restore and schema-version validator dispatch (currently v0.1 only) exist; actual version-to-version migrations, storage/deployment decision, retention/security/monitoring, and recovery operations remain. |

**Completion rule:** each issue closes only when its acceptance criteria pass with evidence and no unresolved prerequisite is mislabeled complete. Identity-provider choice, provider/model/spend/data scope, policy authorization, and production security review are explicit external gates—not silently assumed.

## Workstream register

| Issue | Workstream | Current status / blocker | Depends on |
|---|---|---|---|
| [#2](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/2) | Canonical project/work/event/evidence contracts | OPEN; capability/question/run contracts, migration policy, SQLite replay, DAG invariants and verified-snapshot CAS; sequential coordinator and replay-checked parent/output lineage added; interrupted runs with canonical outputs verifier-reconcile without replay; authorized source retrieval/authenticity, migration implementation, wider fault matrix and store decision remain | — |
| [#3](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/3) | AG2 → canonical event adapter | OPEN; WAL replay/restart, candidate-state deletion and listener-outage repair tested; AG2 proposal/AgenticX critique/revision composes with Meldwork V4 verification through the coordinator; all outputs link to parented runs and verifier-only recovery works after canonical commit; Meldwork receipt staging/import is now atomic with its canonical journal; AG2/WAL and external-effect delivery are still not an atomic dual-write, and adapter-side outbox/reconciliation remains | #2 |
| [#4](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/4) | AgenticX API/recovery/capability gate | OPEN; deterministic AgentExecutor bridge runs as AG2 critic and in Meldwork V4 workers in one end-to-end offline project run; bounded stdio protocol/output and timeout paths tested; Studio API/auth/recovery/hardened sandbox gates remain | — |
| [#5](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/5) | Meldwork extraction, portability, dependency remediation | OPEN; pinned Node LocalWorkspace V4 runtime tested with mocks; Electron/macOS product gate and 14 NPM audit findings remain | — |
| [#6](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/6) | Separate question/approval inbox | OPEN; terminal lifecycle and exact pause safety tested; local read model/facade plus loopback synthetic HTTP answer/reject slice tested across restart; ETags and sequence CAS required; a standalone SQLite queue/lease prototype exists but is not coordinator-integrated; real auth/membership, authenticated lease recovery and notifications remain | #2, #7 |
| [#7](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/7) | Capability permissions and threat model | OPEN; threat model and exact-scope local grants/revocation tests added; identity binding, adapter enforcement and sandbox remain | #2 |
| [#8](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/8) | Blind rubric and bounded real-model pilot | FUTURE; user has generally approved provider calls if useful, but no concrete provider/model, spend cap, credential setup, or data scope is selected; current reliability work needs no provider call | #2, #4, #5, #6, #7 |
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
6. #8 real-model evaluation: general provider-call approval exists; select provider/model, spend cap, credential method and non-sensitive data scope before a pilot. Current integration remains deterministic. Meldwork's packaged app still needs its supported Mac environment for product-level evaluation.
7. #9 multi-laptop/HiperGator work remains deferred pending policy and authorization.
8. #10 memory/conformance tests use synthetic data; no private profile import until explicitly approved.

The full component evidence is in [`../spikes/`](../spikes/); this register intentionally tracks remaining work, not marketing capability claims.
