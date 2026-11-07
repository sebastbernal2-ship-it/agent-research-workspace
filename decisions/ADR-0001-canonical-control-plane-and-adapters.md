# ADR-0001: Canonical project control plane with replaceable runtimes

- **Status:** Accepted for the local prototype; not a production database decision.
- **Date:** 2026-11-07
- **Scope:** architecture for the next local build; supersedes no candidate's own state model.

## Context

The goal is a project-first AI organization, not a coding-only agent loop: user → project VP → multiple orchestrators → specialists, with durable work and evidence, rich agent discussion/revision, a separate human question inbox, and eventually autonomous continuations. Candidate runtimes each have their own session or workspace state. Treating one as canonical would couple project truth to a replaceable component and make adapter failures hard to diagnose.

Current isolated evidence: AG2 Network provides message transport/turn coordination; Meldwork V4 `LocalWorkspace` provides substantive proposal/challenge/work/synthesis/verification stages; AgenticX `AgentExecutor` can run a bounded task in an isolated Python process. These are role-specific tested routes, not a validated whole product or evidence of reasoning quality.

## Decision

1. **The project/work control plane owns durable truth.** The local prototype stores validated event records, immutable record snapshots and content-addressed blobs in SQLite. Project/work/question/run/artifact/evidence projections are rebuilt from this history.
2. **Candidate storage is adapter-local.** AG2 WAL and Meldwork workspaces may be replayed/imported, but cannot be authoritative. Candidate state can be discarded without losing already accepted canonical events.
3. **Adapters normalize through closed contracts.** Each source has an explicit adapter and tests at its boundary. Do not merge candidate databases, copy source trees into the canonical runtime, or rely on undocumented internal tables.
4. **Use components by role, not winner-take-all.** AG2 is the collaboration transport candidate; Meldwork V4 is a richer workflow/orchestration candidate; AgenticX is an optional bounded specialist execution candidate. The prototypes do not yet establish production suitability.
5. **Human questions are separate durable records.** User answers are explicit. Blocking work pauses by exact ID set; rejection/cancellation/expiry leaves work blocked; supersession carries the same paused set. No silence or agent text grants approval.
6. **Permissions are external to model plans.** A synthetic grant record can scope an action to project, work, agent, opaque resource, and expiry. Actor fields are not authenticated, and no tool/adapter is yet permitted to rely on this check as a security boundary.
7. **Offline deterministic tests are the default until safety prerequisites pass.** No provider credentials, real-model calls, private profile data, or HiperGator execution are required or authorized by this ADR.

## Data flow under test

```text
Synthetic project/work records
       │
       ├─ AG2 Hub/WAL ── CanonicalAG2Mirror ─────────┐
       ├─ Meldwork V4 LocalWorkspace                 │
       │       └─ AgenticX deterministic workers      ├─> closed v0.1 event contract
       │              └─ V4 parser/sanitizer adapter ┘       │
       └─ Human question + capability commands              ▼
                                                    SQLite event/blob store
                                                             │
                                                             ▼
                                              deterministic projection/export
```

The combined Meldwork test runs the Node orchestration path and eight AgenticX `AgentExecutor` subprocess calls with fixed outputs. It does not start Electron or a model provider. The AG2 mirror has separate WAL-restart/deletion tests. All composed tests use synthetic project/work IDs and a disposable local database.

## Consequences

**Benefits**

- One component's failure is isolated at a clear boundary and easier to diagnose.
- Project history can be inspected/exported/replayed without candidate-specific state.
- Adapter replacement and comparison are possible without rewriting the project contract.
- Human decisions, evidence, and provenance have a durable project home.

**Costs / open risks**

- The prototype duplicates some state and needs reconciliation/outbox semantics for reliable dual writes.
- The event vocabulary and v0.1 schema still need independent review and migration implementation.
- SQLite is single-host reference code; no network identity, multi-host transaction, backup/restore, retention, or production availability guarantee.
- Capability checks are not authentication, sandboxing, or atomic side-effect authorization.
- The UI, VP chat, background scheduler, notifications, worker leases, and genuine autonomous continuation remain unbuilt.
- Scripted integration proves protocol plumbing only, not peer critique quality, alignment, or product safety.

## Rejected alternatives for this stage

- **Make AG2 or Meldwork the project database:** rejected because project truth must survive runtime replacement/reset.
- **Fuse repositories or directly share candidate DBs:** rejected because it expands dependency/security surface and obscures ownership.
- **Select a single orchestration framework now:** rejected because only role-specific isolated routes have evidence; richer model-backed behavior is not tested.
- **Start a real-model or HPC pilot now:** rejected because permissions, threat model, private-data policy, and provider approval are incomplete; they are not needed for deterministic integration work.

## Revisit when

Revisit the storage decision before multi-user/remote deployment, private-data use, real tools, provider-backed operation, or HiperGator. Revisit candidate choices after comparable, bounded non-coding collaboration tests with explicit quality rubrics and security/recovery gates.
