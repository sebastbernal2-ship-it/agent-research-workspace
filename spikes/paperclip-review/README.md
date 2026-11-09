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

This was evaluated at a fast-moving `master` revision. In the isolated `/tmp/paperclip-review` clone, the locked pnpm dependencies installed, `pnpm build` passed (including migration-numbering/safety checks and the Rust runner build), and 11 selected auth, authorization, approval, budget, tool-gateway and recovery test files passed (**488 tests**). A synthetic empty Paperclip instance also started with embedded PostgreSQL bound only to `127.0.0.1`; `/api/health` reached `ok` and `/api/companies` returned `[]`. It ran in `local_trusted` mode with an empty temporary home and no provider credentials, model calls, or private data. That mode intentionally has no login and is not evidence for authenticated deployment. A follow-up used a fresh loopback-only `authenticated`/`private` instance with isolated embedded PostgreSQL, temporary credentials, and synthetic data. `/api/health` reported `ok` in authenticated mode and the first Better Auth signup returned HTTP 200. Six focused Vitest files covering credential signup, sessions, cross-company/tenant routes, and authorization passed (**152 tests**). The dedicated Playwright `multi-user-authenticated` suite was then attempted after resolving a missing `libasound.so.2` dependency by unpacking Ubuntu `libasound2t64` 1.2.11-1ubuntu0.3 under `/tmp` (SHA-256 `5d17bfb5683eb99f1be951fe48e1be5bf3c52391913b6b74a8aecf9b4f5e779f`; no system install): **0/3 tests passed**. Source/screenshot inspection narrowed the failures: the first test stopped before clicking accept because it expects the obsolete heading “Bootstrap your Paperclip instance” while this revision renders “Set up Paperclip” (and the button is “Accept invite,” not “Accept bootstrap invite”); another scenario timed out with a secondary cleanup error, and an API-driven bootstrap acceptance returned non-success with a resulting “No organization access” page. Subsequent source inspection found additional obsolete UI selectors/routes in the first test (`layout-account-menu-trigger` and the former invite controls on General settings). The suite also shares one persistent server across three top-level scenarios even though each creates a new bootstrap invite and assumes it can claim the one-time first-admin slot; later claims can conflict after an earlier test succeeds. The original non-success response body was not captured, so the precise response path remains unverified.

To separate API authorization from the stale browser flow, [`authenticated_api_probe.mjs`](./authenticated_api_probe.mjs) was run against a **fresh** loopback-only authenticated instance using real Better Auth session cookies and synthetic users. It claimed the instance (202), created two companies, invited a second user as operator (acceptance 202), confirmed the user could not list members of the other company (403), then downgraded the user to viewer and confirmed invite creation was forbidden (403). The focused `bootstrap-claim-routes.test.ts` also passed (11 tests). This is evidence for those HTTP authorization paths only—not the UI, exhaustive route coverage, or production security. The unmodified three-test Playwright suite remains **0/3** and does not establish browser-level multi-user isolation. The full upstream suite, destructive/restore drills, external worker/sandbox operations and security review remain untested. Build output included Vite/CSS and Rust warnings; selected injected-failure tests emitted expected warning logs while passing.

**Reproduce the API probe** against a fresh, unclaimed instance (it refuses non-loopback HTTP targets):

```sh
PAPERCLIP_BASE_URL=http://127.0.0.1:3105 \
PAPERCLIP_HOME=/tmp/paperclip-auth-e2e \
PAPERCLIP_CONFIG=/tmp/paperclip-auth-e2e/config.json \
PAPERCLIP_UPSTREAM_DIR=/tmp/paperclip-review \
node spikes/paperclip-review/authenticated_api_probe.mjs
```

**Recommended next:** fix Playwright locator drift and give each bootstrap-dependent scenario its own fresh DB/instance (or restructure the suite around one first-admin claim), then rerun browser-level isolation and capture the remaining API failure response. Only after that should a disposable adapter proof for revision-bound approve/reject, budget stop, worker dispatch/lease recovery, and canonical event import after restart. Use synthetic data and a local scripted provider only. If the test requires Paperclip to own truth or accept unauthenticated dual writes, stop and reuse only design patterns. Do not vendor code until the license-notice constraint is resolved and the concrete code passes security review.
