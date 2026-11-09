# Paperclip organization-harness evaluation

**Upstream reviewed:** [`paperclipai/paperclip`](https://github.com/paperclipai/paperclip), `master` at `8fe524f796eb80d95d4384164850ca2734f1bb97` (2026-11-09). Static source/docs inspection only; no source snapshot or implementation code was copied into this workspace. The upstream project identifies its license as MIT. This repository's existing source-vendoring rule excludes license-related documents, so no Paperclip source was vendored; retain the required license notice if a later approved integration copies or redistributes code.

## Why this candidate matters

Paperclip is the closest repository inspected so far to an end-to-end AI-organization product: a company/org chart, goals and task hierarchy, agent adapters, scheduled runs, user approvals, budget controls, activity attribution, secret management, tool access policy, and sandbox providers are all present in one product. It is substantially broader than NAC, which is a coding-session worker/runtime candidate.

Source pointers at the pinned revision:

- Human sessions and mixed human/agent authentication: `server/src/auth/better-auth.ts`, `server/src/middleware/auth.ts`.
- Company membership and action grants: `packages/db/src/schema/company_memberships.ts`, `packages/db/src/schema/principal_permission_grants.ts`, `server/src/routes/authz.ts`.
- Task/run lifecycle and recovery: `server/src/services/heartbeat/`, `server/src/modules/run-dispatch/`, `server/src/modules/active-run-watchdog/`.
- Approval surfaces: `server/src/routes/approvals.ts`, `server/src/services/issue-approvals.ts`.
- Budget admission/enforcement: `server/src/services/budget-admission-controls.ts`, `server/src/services/budget-enforcement-worker.ts`.
- Tool gateway, per-action authorization and activity: `server/src/routes/tool-gateway.ts`, `server/src/services/tool-access/`.
- Execution/sandbox seams: `packages/adapter-utils/src/`, `packages/paperclip-runner/`, `doc/architecture/paperclip-runner.md`.
- Storage operations and schema evolution: `packages/db/src/migrations/` (hundreds of numbered PostgreSQL migrations), `packages/db/src/backup.ts`.

These are concrete implementation leads, not a security certification. The source tree itself includes extensive tests and migration checks, but they have not been run here.

## Critical mismatch with our decisions

Paperclip is an application with its own PostgreSQL domain schema and an `activity_log`; the activity table is not our immutable, hash-chained project event store. Its `company` / `issue` / `heartbeat_run` concepts do not directly equal our project / work / run contracts. Replacing our authority with Paperclip would be a major product and data-model decision, not a drop-in feature import. Running both systems as writable control planes would create competing approvals, membership, task state, and recovery decisions.

For the current project-first decision, the safe investigation path is a **one-way, narrow adapter experiment**: our authenticated service remains authoritative; Paperclip may receive an explicitly scoped task and provide an execution/approval signal, while every accepted state transition and output is validated and written to our canonical event store. Before that experiment, test whether Paperclip APIs expose stable idempotency/receipt and authorization hooks without requiring us to mirror its entire schema. Do not expose Paperclip directly as another user-facing authority.

## Reuse assessment

| Gap | Potential value | Boundary / next check |
|---|---|---|
| Human identity and membership | Strong patterns: Better Auth sessions, company membership, principal grants and role-aware routes. | Pick our identity provider first; compare exact route coverage, default deployment mode, session handling, and authorization-at-mutation behavior. Do not inherit Paperclip identities as project authority. |
| Approvals and governance | First-class review queues, approval records, agent lifecycle gates and task decision flows. | Map to canonical revision-bound human commands; verify approval cannot race with cancellation, membership change, or stale task state. |
| Durable execution and recovery | Run lifecycle, heartbeats, dispatch admission, watchdogs, recovery actions, process evidence and extensive operational tests. | Runtime/database receipts must not supersede canonical run/lease state. Exercise orphan-process and ambiguous-provider windows; no system can undo arbitrary remote effects. |
| Budgets and secrets | Budget policies/admission and per-agent secret/tool-connection controls. | Provider/account terms and data scope remain project-owner decisions. Verify actual tool process cannot read secrets outside its grant. |
| Tool policy | Governed tool gateway with per-action authorization/approval and attributed calls. | Must bind authenticated principal + project/work + canonical lease/capability snapshot; test revocation at invocation time. |
| Sandbox and worker adapters | Local and remote execution targets and provider plugin seams. | Inspect concrete isolation backend, host privileges, egress, mounts, secret delivery, cancellation and receipts; provider names alone are not security evidence. |
| Canonical history / evidence | Activity attribution can inform projections and operator UX. | Not a substitute for immutable snapshots, project event replay, artifact provenance, or evidence trust. Keep the existing canonical store unless the owner deliberately changes the architecture. |

## Validation limits and next step

This was static inspection at a fast-moving `master` revision. No install, server startup, API test, migration test, security test, or deployment run was performed. The local environment has Node 24 but no `pnpm`, Docker, or PostgreSQL CLI/service. Record a stable release/commit and obtain the toolchain before any runtime claim.

**Recommended next:** compare Paperclip against our project contract with a disposable two-project test: prove route auth and membership isolation, revision-bound approve/reject, budget stop, worker dispatch/lease recovery, and canonical event import after restart. Use synthetic data and a local scripted provider only. If the test requires Paperclip to own truth or accept unauthenticated dual writes, stop and reuse only design patterns. Do not vendor code until the license-notice constraint is resolved and the concrete code passes security review.
