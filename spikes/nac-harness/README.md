# NAC harness evaluation

**Upstream inspected:** [`arcee-ai/nac`](https://github.com/arcee-ai/nac), branch `dev`, commit `f27b5b4a92b38567580a1a43576137bf5b436f95` (repository default branch at inspection). `upstream/` is a filtered source snapshot at that exact revision (2,310 files, 34 MiB). It contains application source, tests, technical documentation, examples, and lockfiles; it does not contain Git history, repository instruction files, generated dependency trees, generated frontend bundles, legal-notice documents, or the legal-notice design-preview component and icon. The root README's legal badge/footer was omitted; the legal-notice design-preview component was also omitted from its preview page; the rest of upstream implementation code was not changed.

**Review and validation:** source/docs were reviewed statically, then NAC was built and exercised from a separate complete clone at `/tmp/nac-upstream` (the filtered vendored snapshot was not built). Rust 1.99.1, Node 24.22.1, locked Cargo/npm dependencies, and Playwright Chromium were installed for this evaluation. `RUST_TEST_THREADS=4 make test` and `RUST_TEST_THREADS=4 make ci` passed; the focused `make test-durability` gates also passed, as did `make test-mcp-conformance` for revisions `2025-11-25` and `2026-07-28` plus the seeded omitted-tool-call failure. The first unrestricted-parallel Rust run had 27 timeout failures; rerunning at four test threads passed. `make test-e2e` reported 285 passed, 5 skipped, and 3 failed: two repeatable `setup-form-review` save-retry cases, plus the portal-launch host-mismatch check timing out on `https://localhost` in this environment (the BasicAuth companion passed). A minimal browser smoke initially lacked `libasound.so.2`; the library was unpacked under `/tmp` without system changes, after which the suite ran. No real model, provider credential, or private data was used. The managed-load test attempted an uncredentialed Smithery registry listing and received HTTP 500; that failure was logged but did not fail its deterministic test.

## What NAC is

NAC is a Rust + React coding-agent harness and local web application. Its central orchestrator decomposes work into named threads; workers return durable structured episodes. It has three persisted session topologies, a model/tool runtime, SQLite-backed session state, a server/API, and optional managed-host features. The repository is much closer to a complete local coding harness than AG2, AgenticX, or Meldwork individually, and it has particularly mature code around permissioned tool execution and durable process/session lifecycle.

## Reuse opportunities against our completion tracks

| Track | Relevant NAC implementation | Fit and limits for this organization |
|---|---|---|
| **#13 durable runtime** | `nac-core` session service/store; named worker threads and retained episodes; persisted dispatch UUID, session/run identity, and increasing thread generation; atomic episode + dispatch receipt; startup recovery; serialized SQLite store ownership; bounded typed command executor; shutdown/cancellation and child-process supervision. | Strong candidate for a **worker/runtime adapter** and a source of lifecycle patterns. Its durable unit is a coding session/thread/episode, not our canonical organization DAG or project event history. Do not mirror NAC's DB as project truth. |
| **#14 identity, approvals, permissions** | `nac-core::permissions` evaluates prepared native-tool resources; hard/configured denials cannot be overridden by remembered allows; backend and resource binding are explicit; approval modes fail closed without an approver; human commands are separated from agent approval. | Substantially richer than our test-only `GuardedCapabilityExecutor`; investigate adapting the actual broker/resource model instead of recreating one. This is **tool/session permissioning**, not a demonstrated multi-user identity provider or per-project membership/role service for our canonical data. |
| **#15 worker isolation/effects** | Local/SSH/Podman backend selection; bounded tool calls; process-tree lifecycle; sandbox mount checks; durable host-admitted worker episode receipts and generation fencing. | Useful runtime and sandbox candidate. It does not make a remote service call transactional, nor provide a general provider outbox with idempotency/reconciliation for arbitrary external effects. Podman execution has a documented crash window and the source guide requires validating the actual runtime. |
| **#16 project UI/service** | React dashboard, Rust application-service layer, HTTP/OpenAPI/MCP delivery, session/project APIs. | Could provide a server/client substrate or worker console. It is coding-session oriented and its documented security model treats a remote client as equivalent to the local user behind an authenticated/encrypted boundary; that is not our multi-tenant project/role model or our project-first workflow. |
| **#17 evidence/models** | Provider catalog/configuration, model clients, web/MCP tools, retained task history. | Useful provider and tool implementation patterns; not proof of source authenticity, evidence review, project-claim provenance, or model quality. No NAC model or source fetch was exercised. |
| **#18 storage/operations** | Extensive SQLite schema migrations, WAL checks, process/store ownership, crash-window tests, diagnostics, load/fault documentation, backup/restore rules, release/CI lanes. | Strongest reuse/reference candidate for storage/lifecycle testing. Its session schema and store ownership cannot simply replace the canonical project event store; assess migration and serialization ideas before adding dependencies. |

## Specific strengths worth reusing rather than reimplementing

- **Permission at the prepared invocation boundary:** each native tool declares canonical permission resources, policy evaluates the whole set, hard denials win, and execution receives resource bindings produced during authorization. Remembered allowances remain narrower than an unrestricted “allow all.”
- **Separate human intent from agent permission:** an authenticated product command is not allowed to impersonate an agent or create a remembered tool grant. Human-initiated operations retain their own ownership, readiness, and backend checks.
- **Worker receipt fencing:** the host admits a dispatch before spawning; the result is tied to session, run, thread, dispatch ID, and generation. Durable episode and terminal receipt commit together; exact replay returns the existing episode while conflicting or stale generations fail. Recovery does not treat a lost acknowledgment as permission to rerun work.
- **Store ownership and typed persistence commands:** one server process owns the SQLite path; cross-process open is excluded by an OS lock; a bounded typed FIFO executes mutations; accepted work drains at shutdown; uncertain acknowledgments are reconciled rather than automatically retried.
- **Explicit backend invariants:** authorization cannot switch execution from the selected Local/SSH/Podman backend or escape that backend's safety policy. Tool visibility is not authorization.
- **Tests and operational evidence:** the full upstream Rust/frontend/asset/managed-load/CI gates passed at four Rust test threads; browser results were 285 passed, 5 skipped, and 3 failed. Three browser failures are recorded above and remain unresolved. Passing tests characterize this pinned candidate and environment, not production security or multi-tenant readiness.

## Important mismatch / boundaries

1. **NAC is not our organization control plane.** Threads and episodes organize coding work inside a NAC session. They do not supply our charter, cross-agent organization roles, canonical evidence graph, human question semantics, immutable project event history, or replaceable AG2/Meldwork/AgenticX adapters.
2. **Its default deployment is local-user shaped.** The CLI binds to loopback by default. Remote bind is an explicit opt-in and the documented warning is that any connecting client receives control equivalent to the local user, behind an authenticated/encrypted network boundary. This is not a drop-in authenticated multi-tenant service.
3. **The valuable permission system is scoped to NAC tool calls.** It cannot authorize our direct SQLite/API/project operations until a trusted service boundary maps authenticated project principals and actions into NAC or an equivalent policy port.
4. **Sandbox support is not a blanket guarantee.** Local, SSH, and optional Podman are different backends; permission cannot change backends. Source documentation identifies a Podman startup crash window and live-mount fallback conditions that need runtime-specific verification.
5. **External effects remain an explicit concern.** Durable thread/episode receipts make worker completion replay-safe; they do not provide exactly-once semantics for arbitrary network or provider side effects.
6. **Adoption cost is real.** NAC is Rust/React with its own domain and schema. Replacing our Python/SQLite control plane with it would be a substantial architecture change, not importing a small helper. A narrower subprocess/HTTP worker adapter or selective design reuse is more plausible.

## Recommendation

**Do not write replacement capability/sandbox/worker-receipt machinery until the NAC integration option is evaluated.** First use a disposable, deterministic adapter experiment to answer:

1. Can a NAC thread be launched as a typed worker for one canonical `workId` without giving it ownership of canonical project state?
2. Can our queue lease, capability snapshot, time/budget limits, cancellation, artifact refs, and completion receipt be enforced at NAC's native-tool and worker boundaries?
3. Can a host-owned episode receipt be translated to our canonical outputs and safely reconciled after NAC/server restart without duplicate external effects?
4. Does NAC's role/thread decomposition produce the peer critique, revision, and verification cycle we need, or is it principally delegated coding work?
5. Which user/session authentication context can actually be asserted for the adapter, and how will it map to our project membership policy?

Use source-level API characterization and scripted tools first. Do not wire the production UI, real credentials, private data, or network tools. If the adapter cannot preserve our canonical state, approvals, and lease contract without forking NAC deeply, keep the current harness boundaries and reuse only patterns/tests.

## Snapshot hygiene

- The snapshot is pinned to the recorded commit; it is not a moving branch checkout.
- Vendored source/docs/tests are ordinary tracked files; dependency trees and Git metadata are absent.
- License/notice documents were not imported, per the user's instruction.
- Validation used the complete temporary clone, not the filtered vendored copy; generated bundles and omitted legal-notice UI mean the latter is for inspection and adapter design, not a turnkey checkout.
