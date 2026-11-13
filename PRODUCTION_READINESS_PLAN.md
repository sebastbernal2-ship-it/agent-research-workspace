# Harness completion and production-readiness plan

**Purpose:** One actionable completion map for turning the current offline composition prototype into a usable, supportable project-first organization. This plan supplements (does not replace) GitHub issues #1–#18. Keep those issues open until their acceptance checks have evidence. Update this plan, `tracking/README.md`, and the relevant issue after each verified slice.

**Current state:** AG2, AgenticX, and Meldwork source snapshots are vendored and exercised through adapters under the project-owned canonical SQLite control plane. Deterministic end-to-end runs and loopback UI/inbox tests pass. Injected-principal application facades and SQLite lease fences exist, but they are opt-in prototypes. No production identity provider, hardened sandbox, supported deployment, approved model pilot, or operations team is in place. Do not describe the harness as production-ready or security-reviewed.

## What “ready” means

A release candidate is ready only when every **release gate** below has a named owner, documented decision, implementation, tests/evidence, rollback/recovery procedure, and review sign-off. A prototype or mocked test is not a substitute for a gate. The system must preserve canonical project history independently of candidate runtimes, default-deny unsafe actions, and make human approvals attributable and replayable.

## Phase 0 — decisions and ownership (blocking external inputs)

Record decisions in `decisions/` and link them from the relevant GitHub issue. Do not silently choose on the project owner’s behalf.

- [ ] Name implementation, security-review, operations, and product-decision owners; currently unassigned.
- [x] Select initial deployment target: single-user local WSL2 Ubuntu on the owner's machine; hosted multi-user service is deferred to Step 2. Decision: [`ADR-0002`](decisions/ADR-0002-wsl-local-first-v1.md). WSL is the chosen target, not yet a supported/reviewed deployment; loopback-only and no exposed ports remain required.
- [ ] Select identity/session provider and account lifecycle: enrollment, authentication, MFA/session expiry, revocation, project membership, service identities, and recovery authority.
- [ ] Define data classification, allowed retention, deletion, backup location, encryption/key management, and which data may leave the machine. The owner expects all data categories, including sensitive/confidential; the current prototype is not approved for them. Codex data transfer, provider terms/retention, credentials, and spend limits must be decided before enabling the connector. See ADR-0002.
- [ ] For any model pilot, explicitly select provider/model, credential method, spend cap, permitted data scope, logging/retention behavior, and reviewed terms. No model call is implied by this plan.
- [ ] Decide whether multi-laptop workers are in scope. HiperGator remains separately blocked pending policy review and case-specific authorization.
- [ ] Define support/SLO, incident response, operator access, and maintenance/update policy.

**Exit:** Decisions are written down with an approver and date, or the corresponding product surface remains explicitly disabled.

## Phase 1 — canonical contract, storage, and migration (issues #2, #18)

- [ ] Select the canonical storage/deployment architecture based on expected concurrency, durability, backup, privacy, and hosting needs. SQLite is presently a local reference implementation, not a multi-host service database.
- [ ] Specify compatible event/record evolution, schema versions, migration ordering, downgrade/rollback constraints, and retention/deletion semantics.
- [ ] Implement at least one real old-version → new-version migration with preflight, transactional application where possible, preserved hashes/provenance, and a verified rollback/restore procedure.
- [ ] Add backup/restore drills covering WAL state, content blobs, queue leases, adapter invocation fences, staged receipts, and interrupted-run recovery. Define RPO/RTO and operator verification.
- [ ] Extend corruption/fault tests: partial writes, disk full, permission loss, process kill at each transaction boundary, corrupted blobs, missing tables/indexes, concurrent upgrades, and restore from a known-good snapshot.
- [ ] Define project deletion, evidence/blob retention, account deletion, export, and legal hold behavior before real/private data is admitted.

**Acceptance:** migration and restore tests execute from documented commands on supported environments; a restore reproduces verified canonical history and operational metadata; unsupported schema versions fail closed.

## Phase 2 — identity, authorization, approvals, and API boundary (issues #6, #7, #14, #16)

Current `InboxApplication` and `ProjectApplication` are reusable injected-principal boundaries; they reject malformed principal values and require an exact nonempty human-user identity shape. They do not authenticate. NAC's vendored permission broker is a candidate for tool-invocation policy reuse, but its local/session scope is not project membership or a product identity provider. The demo still uses a fixed fake user, and direct lower-level APIs can bypass the facades.

- [ ] Implement the selected identity/session adapter; derive principals only from verified server-side sessions/tokens. Reject client-supplied actor identity for every command.
- [ ] Implement a durable project membership/role policy with explicit actions; cover project creation/invitation, membership change/removal, service accounts, and permission revocation.
- [ ] Route **all** project reads/commands through authorization: projects, charter, work/DAG, questions, messages, artifacts/evidence, runs, capability grants, queue submission/inspection/recovery, exports, and backups. `ProjectApplication` now has separate membership-policy gates for run, evidence, artifact, discussion, and capability-list reads, plus an authorized single verified state/event snapshot used by the UI; remaining routes and direct control-plane/queue entry points are not yet covered.
- [ ] Define distinct permissions for plan creation, work assignment, approval, cancellation, evidence acceptance, capability grant/revoke, and recovery. Enforce authorization in the service/execution boundary, not only a UI facade.
- [ ] Require explicit approval records for plans, dangerous capabilities, irreversible/external actions, and recovery. Bind each approval to project, exact object/revision, scope, approver principal, time/expiry, and idempotency key. Reject stale or replayed approvals. The project facade's work approve/cancel commands now require the expected revision and re-check it against the control-plane snapshot before the event append (rejected/stale-replayed transitions conflict); plan/capability/effect/recovery approvals still need this complete record model.
- [ ] Complete CSRF/session-cookie or bearer-token handling for the chosen client, rate/body limits, audit redaction, request validation, and error policy. Keep loopback restrictions as defense-in-depth, not identity.
- [ ] Add adversarial tests for anonymous users, forged actors, cross-project IDs, wrong role/action, removed memberships, stale revisions, replayed approval IDs, duplicate submissions, and authorization races.
- [ ] Ensure direct control-plane and queue access is confined behind a trusted service API in deployment; do not expose Python methods or SQLite files as a user authorization mechanism.

**Acceptance:** independent security review confirms every exposed route and operation is authorized; cross-project and forged-principal tests fail closed; approval and revocation events identify the authenticated approver and exact approved object.

## Phase 3 — organization planning and durable lifecycle (issue #13)

The current coordinator accepts a caller-provided DAG/factory; it does not yet plan a durable organization autonomously or provide an operator-grade lifecycle. A deterministic Codex queue producer now builds descriptors from canonical ready work and trusted route configuration, binds a bounded context hash to the project charter/work revision/parent lineage/completed dependencies/route and verifier identity, revalidates that hash before dispatch, and exposes explicit enqueue separately from worker execution. It rejects incomplete dependencies, blocking questions, active runs, and oversized contexts; this is stale-plan protection only, not a durable human-approval record, identity policy, provider/data/budget gate, sandbox, or autonomous organization planner. Before adding equivalent worker/thread lifecycle code, evaluate a typed adapter to NAC's durable thread/episode dispatch and determine whether its coding-session topology can preserve our canonical work and run identities. NAC's complete upstream clone passed `make test` and `make ci` at four Rust test threads; its browser suite had three recorded failures. See `spikes/nac-harness/README.md`; no NAC adapter has been evaluated against our canonical work contract yet.

- [ ] Define canonical plan records: objective, decomposed tasks, dependencies, agent roles, budgets, required evidence, acceptance criteria, risk level, capability needs, and approval state.
- [ ] Add durable plan submission, review, approval/rejection, revision, assignment, and cancellation. Model-generated plans are proposals only and never authorize themselves.
- [ ] Persist scheduler state and trusted route registrations across restart without deserializing executable callbacks. Define how deployment re-registers trusted adapters and rejects unavailable/changed versions.
- [ ] Complete worker startup/shutdown/readiness/liveness, graceful cancellation, heartbeat loss handling, lease renewal races, duplicate workers, clock failure, and stalled-job/operator views. Local worker stop now drains the claimed job and takes no new claim; forced cancellation closes a verified canonical cancellation as a failed queue job. This does not replace deployed lifecycle/health management or recovery for partial/stale-lease cancellation.
- [ ] Keep generation/token validation at canonical write and adapter dispatch boundaries; add tests for every run/work/evidence/message/receipt path, detached task/thread/process, same-work non-run-attributed write, and backup/restore.
- [ ] Replace boolean recovery claims with an authenticated operator recovery command under the selected identity model. Audit the attestation, exact job/generation, reason, time, and affected runs. Require independent confirmation for high-risk recovery.
- [ ] Make recovery ordering explicit and safe across receipt import, run interruption, work retry/requeue, and outbox reconciliation. Make each step idempotent and document partial-completion repair.
- [ ] Add subprocess and multi-process crash tests at every boundary: claim, run start, adapter prepare/dispatch, effect, receipt stage/import, run finish, verification, queue completion, and backup/restore.
- [ ] Document what is *not* exactly-once. Define idempotent retry, ambiguous effect, operator review, and dead-letter behavior.

**Acceptance:** approved plans survive restart, run through dependency and human gates, and recover without duplicate active runs or silent replay; fault injection covers ambiguous external effects and each recovery step.

## Phase 4 — worker isolation, tools, and external effects (issues #7, #15, #4)

Bounded child processes are not sandboxes. Current workers have no production tool integration, and queue fencing does not revoke an external action already dispatched. NAC has a stronger candidate tool permission kernel and Local/SSH/Podman backends; assess those existing boundaries before implementing a parallel sandbox or tool-policy stack. NAC's thread receipt journal is not a general external-effect outbox.

- [ ] Select and implement OS/container/VM isolation for each supported platform: filesystem mounts, network egress, process tree, CPU/memory/PID limits, syscall/privilege policy, working directory, temp cleanup, and escape testing.
- [ ] Define secret acquisition through a broker or short-lived scoped injection; no inherited environment, user home, provider credential files, or uncontrolled log capture.
- [ ] Enforce canonical capability grants at the actual tool/MCP/filesystem/network/remote-call boundary immediately before each operation. Bind subject, project, work, action, resource, expiry, budget, and revocation; deny by default.
- [ ] Implement the durable effect-intent/outbox protocol in [`design/EXTERNAL_EFFECT_PROTOCOL.md`](design/EXTERNAL_EFFECT_PROTOCOL.md): persist intent/idempotency key before dispatch, record provider receipt/result, reconcile after crash, and surface ambiguous outcomes without automatic replay. Ensure each external connector documents its idempotency semantics. The design is written; no general outbox is implemented.
- [ ] Couple cancellation/lease loss to process-tree termination and prevent stale workers from starting additional effects; test delayed, duplicated, reordered, partially completed, and timeout responses.
- [ ] Review isolation and effect controls independently before enabling tools or user data.

**Acceptance:** adversarial workers cannot escape declared resource boundaries; denied/revoked capabilities cause no effect; crash/retry tests prove the documented effect semantics for every enabled connector.

## Phase 5 — project UI, service, and user workflow (issues #6, #11, #16)

The loopback UI presentation was replaced with a React/Vite adaptation of shadcn's MIT `dashboard-01` block, pinned and attributed in `ui/web/README.md`; the project service and canonical SQLite history remain authoritative. The shell now supports multi-project switching, proposed-work creation, and project inbox answers. This is a test-data-only prototype, not the authenticated multi-project workflow below.

- [ ] Replace the fixed-principal loopback server with the authenticated service from Phase 2; add multi-project navigation and enforce authorization on every data fetch and mutation.
- [ ] Implement durable plan submission/review, role assignment, approval and rejection, run progress, pause/recovery, evidence review, and project-scoped VP conversation backed by canonical events.
- [ ] Keep inbox decisions distinct from chat; show exact affected work, revisions, consequences, approval identity, and stale-state conflicts.
- [ ] Add notifications with delivery/retry semantics that never imply approval. Ensure missing/late notification cannot resume work.
- [ ] Complete browser/device matrix, keyboard-only, screen-reader, accessibility, responsive, privacy-leak, CSRF, and concurrency tests; conduct a human workflow walkthrough.
- [ ] Define operator/admin workflows, audit access, account lockout/revocation, support diagnostics, and safe export.

**Acceptance:** users can manage more than one authorized project, review and approve explicit plans, and resume only the exact approved work; browser tests prove isolation across accounts/projects and restart.

## Phase 6 — trusted evidence and bounded model workflows (issues #2, #8, #12, #17)

- [ ] Define source policy, retrieval allowlist, robots/terms handling, SSRF/DNS/private-IP protections, download/type/size limits, malware handling, and source freshness policy.
- [ ] Implement authenticated/authorized retrieval with immutable source bytes, URL/redirect chain, retrieval time, content hash, extractor/version, and provenance; distinguish a hash from source authenticity or truth.
- [ ] Provide human review/acceptance and correction/retraction. Keep competing claims and provenance visible; never convert model output into a trusted source automatically.
- [ ] Enforce per-project/role model profiles, provider terms, redaction/data-scope filters, explicit budget/token/time limits, refusal behavior, audit policy, and no implicit provider fallback.
- [ ] Add deterministic contract tests, provider sandbox tests, privacy/leak tests, budget enforcement, outage/rate-limit tests, and blind quality rubrics.
- [ ] Run a real-model pilot only after the Phase 0 choices are approved. Record prompts/data sent, costs, rubric, failures, and reviewer conclusions. Do not treat mocks as quality evidence.

**Acceptance:** evidence is traceable and reviewable; unauthorized sources/data never leave the boundary; the approved pilot meets preregistered quality and spend limits.

## Phase 7 — packaging, deployment, operations, and maintenance (issues #5, #18)

- [ ] Remediate or explicitly accept with a signed risk decision the reported Meldwork desktop dependency findings; rerun automated dependency/security review against the exact release tree.
- [ ] Verify supported Meldwork packaged behavior on its supported macOS environment; separately test every platform the harness intends to support. Vendored source tests do not prove packaged app behavior.
- [ ] Define build provenance, pinned/reproducible dependency installation, artifact signing, update/rollback, SBOM/security review, vulnerability response, and source-change policy.
- [ ] Choose production DB, object/blob storage, queue, backup, monitoring, secrets, network perimeter, and deployment topology. SQLite remains a single-host prototype unless a documented architecture proves otherwise.
- [ ] Add service health/readiness, structured privacy-safe logs, metrics/traces, alert thresholds, capacity tests, backup validation, recovery runbooks, incident response, and on-call ownership.
- [ ] Define data retention/deletion and key rotation; conduct restore, failover, disk-full, corrupted-backup, and upgrade drills.
- [ ] Set release gates and rollback authority; perform independent security/privacy/deployment review before admitting users or private data.

**Acceptance:** repeatable installation and upgrade/rollback on supported targets; operational drills meet approved RPO/RTO; monitoring, response ownership, and maintenance cadence are active.

## Cross-cutting verification and evidence

Run from the repository root. Preserve logs/results with commit hash and environment in issue comments:

```bash
python3 -m unittest discover -s contracts/tests -v
python3 -m unittest discover -s alignment/tests -v
python3 -m unittest discover -s ui/prototype/tests -v
python3 -m unittest discover -s harness/tests -t . -v
(cd spikes/ag2-collaboration && PYTHONPATH=../.. .venv/bin/python -m unittest \
  harness.integration_tests.test_ag2_mirror \
  harness.integration_tests.test_ag2_agenticx \
  harness.integration_tests.test_offline_organization_run -v)
python3 -m unittest \
  harness.integration_tests.test_meldwork_bridge \
  harness.integration_tests.test_meldwork_agenticx_runtime -v
```

These prove deterministic behavior only. Separately record real-platform package tests, security review, restore drills, browser/accessibility review, authorized-provider evidence, and model-quality results when those gates are approved and performed.

## Ordered execution sequence

1. **Now, no external decisions needed:** finish #13 recovery/fault matrix, #15 relay/effect failure tests, and #18 storage migration/restore compatibility; the #2 contract acceptance is verified, but production tools remain disabled.
2. **Owner decisions:** complete Phase 0. Without identity/deployment/data/provider choices, keep hosted service, private data, and model pilot blocked.
3. **Identity first:** implement #14 and connect #6/#16 surfaces; verify every operation before adding tools.
4. **Isolation/effects:** complete #7/#15/#4 before any tool, external connector, or autonomous side effect.
5. **Operationalize:** complete #18 and #5 platform/dependency gates before a production deployment.
6. **Evidence/model pilot:** complete #17/#12 and obtain explicit #8 pilot approval; use non-sensitive data and a preregistered rubric.
7. **Scale only after local release gates:** #9 laptop/HPC federation remains separate; HiperGator requires separate institutional approval.

## Decision and evidence log template

For each checklist item, record:

- **Decision/implementation:** exact choice or code path and the responsible owner.
- **Threat/requirement:** why it exists and what it protects.
- **Evidence:** tests, commands, commit, environment, review report, or operational drill.
- **Known limits:** what the evidence does not prove.
- **Rollback/recovery:** how to safely reverse or recover.
- **Approval:** approver and date where a policy/product decision is required.

Never close an umbrella issue because its prototype exists. Close only when every required release gate in its issue has independently verified evidence and all external blockers are resolved or the feature is excluded from the release scope.
