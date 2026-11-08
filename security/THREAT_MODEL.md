# Threat model: project-first agent workspace

Status: living design for local synthetic development. This is **not a security approval** and does not authorize private-data, network-service, or HPC use. Last reviewed against the prototype on 2026-11-08.

## Security objectives and assets

Protect, in priority order:

1. User authority: agents must not turn silence, a model response, or a replayed event into user consent.
2. Cross-project confidentiality and integrity: a project/run/worker may access only explicitly scoped records and artifacts.
3. Credentials and service identities: secrets never enter prompts, agent messages, artifacts, source control, logs, or model-visible context unless separately approved.
4. Canonical project history: accepted events and content blobs are immutable, verifiable, attributable, and independently exportable from candidate runtimes.
5. Work policy: tool/network/filesystem capabilities, placement, budgets, cancellation, and side effects are controlled outside agent-generated plans.
6. Evidence quality: hostile or false source content is untrusted input, not executable instructions or verified evidence.

There are currently no real credentials, personal profile data, provider calls, or HiperGator workloads in this prototype. Synthetic fixtures are not evidence that future private data is safe.

## Trust boundaries

| Boundary | Current prototype | Required before broader use |
|---|---|---|
| User ↔ project control plane | A loopback-only local demo serves one synthetic project and injects a fixed fake user. It checks Host/Origin, disables CORS, requires a custom write header, and bounds bodies; these checks do not authenticate a person. No real authenticated principal exists; lower-level commands still accept actor fields. | Real authenticated principal/session, CSRF protections, explicit confirmation UX, membership authorization on every command, independent review. |
| Control plane ↔ SQLite | Single-host SQLite, schema validation, append-only triggers, hashes and event replay. No per-project authorization or encryption. | Capability checks before reads/writes, protected backups, key management if private data is allowed, concurrency and crash tests. |
| Canonical store ↔ candidate runtime | Separate AG2/Meldwork/AgenticX adapters; JSON boundary; canonical store remains authoritative. | Signed/validated adapter identity, bounded input/output, per-project grants, atomic outbox/reconciliation and replay controls. |
| Harness ↔ AgenticX subprocess | Separate pinned venv, temporary HOME/cwd, reduced environment, bounded JSON/stdout/stderr/time, deterministic provider, no tools in tests; POSIX timeout/overflow kills the child process group. | OS-level sandbox, CPU/memory/filesystem/network limits, cross-platform process-tree cancellation, verified executable/dependency provenance. Current process split is **not** a sandbox. |
| Harness ↔ Meldwork | Pinned Node V4 `LocalWorkspace`/parser subprocesses use bounded request/response pipes and timeouts; parser/sanitizer is used. No Electron/product service boundary. | Product packaging/platform review, Electron IPC review, dependency remediation, explicit filesystem/tool permissions, and process-tree containment. |
| Worker ↔ public/untrusted sources | No real retrieval or network access in integrated tests. | Treat fetched text, files, images, MCP output and citations as hostile data; isolate instructions from content; sanitize/render safely; require evidence provenance. |
| Local laptops ↔ remote/HPC | Not implemented. HiperGator is deferred. | Separate official policy review, authorization, queue identity, data classification, network boundary, and revocable worker leases. |

## Adversaries and abuse cases

- **Prompt injection in public content:** a source tells the agent to reveal secrets, alter its task, or use a tool. Content must not grant capabilities or override the project charter/system policy.
- **Confused deputy:** an agent asks an adapter/worker with broader credentials or filesystem access to perform an action outside the work item's grant.
- **Cross-project IDOR:** a caller substitutes another project's ID, blob hash, run ID, artifact ID, question ID, or thread ID.
- **Spoofed/replayed adapter output:** a candidate submits duplicate, stale, forged, or cross-project events; retries must be idempotent and mismatched content must fail closed.
- **Malicious or compromised worker/dependency:** subprocess output, package install hooks, transitive code, or candidate storage may be hostile.
- **Credential exfiltration:** a secret is included in environment variables, prompt context, generated output, error trace, event content, or a public-source request.
- **Human-gate bypass:** model-generated text, timeout, expiry, notification failure, or another actor is mistaken for the user's approval.
- **Resource exhaustion:** oversized receipts, loops, recursive delegation, huge artifacts, CPU/memory/process fan-out, or repeated retries exhaust the laptop or create unbounded provider spend.
- **History tampering/deletion:** direct DB manipulation, backup rollback, or candidate-state deletion hides work or changes an audit trail.
- **Supply-chain/licensing risk:** pinned candidate dependencies have known vulnerabilities or incompatible terms.

## Required invariants

1. **Default deny:** no tool, network, filesystem write, secret, remote worker, or spending capability exists unless a canonical, scoped, time-bounded, revocable grant authorizes it.
2. **Authority is external to agents:** agents may propose tasks/grants; only the control plane and authenticated human can issue/approve them. Scheduler placement is policy-driven, not model-selected.
3. **Project/work scope:** every grant and artifact access names a project and (where applicable) work item. Never authorize by prompt text or candidate session ID alone.
4. **Human approvals:** only the authenticated user may approve; expiry, rejection, cancellation, missing notification, or agent text never implies approval. Paused work stays non-runnable until an explicit safe transition.
5. **Adapter validation:** validate schema, project/work references, actor binding, sizes, timestamps, idempotency, content hashes and visibility before append; reject unknown fields and unknown versions.
6. **Secret boundary:** providers and candidate runtimes receive only explicitly approved credentials, scoped to their required endpoint and never serialized into canonical events. Redact errors without hiding security failures.
7. **Side-effect discipline:** classify operations as read-only/idempotent/irreversible; require explicit grant and, for consequential/irreversible effects, a human gate and reconciliation record.
8. **Isolation:** candidate databases are noncanonical. Deleting/resetting a candidate store must not delete project history; replay must not duplicate canonical effects.
9. **Auditability:** grant, revoke, question, approval, run start/end, adapter failure, and side-effect outcomes have actor/time/correlation provenance. Logs are not a substitute for canonical state.
10. **Resource bounds:** enforce limits on input/output bytes, tokens/cost, time, recursion, concurrency, file size, and subprocess trees outside the LLM.

## Prototype controls and known gaps

- **Present/tested:** closed JSON Schema records; no user-profile/secrets in fixtures; SQLite transaction/sequence/idempotency/hash-chain tests; immutable content-addressed records; verified-snapshot sequence compare-and-swap for control/capability writes with stale single/batch command race tests; exact-scope capability-grant/revocation policy prototype; question rejection/expiry/supersession fail-closed tests; canonical agent-run start/finish records with atomic ready→running transition, actor-attribution replay checks and interrupted-run recovery; AG2 WAL replay; receipt parser/sanitizer bridge; bounded stdio AgenticX worker; loopback-only synthetic inbox HTTP tests; one offline AG2/AgenticX/Meldwork workflow with a human gate and canonical restart replay; no real model/provider/tool calls.
- **Partial:** a test-only guarded executor checks a grant before an in-memory callback; capability checks are not wired into any actual adapter/tool and are vulnerable to TOCTOU if used as a standalone preflight. Grant approver/actor IDs are caller-supplied, not authenticated; no quotas/lease binding. AG2 listeners swallow callback exceptions (adapter records degradation); replay works only while AG2 WAL is available; multi-adapter V4 batch is atomic at canonical event append but blobs may be orphaned on batch failure; IDs are not access-control checks.
- **Absent:** real authenticated project-level authorization (the local demo's fixed synthetic principal is not auth); production UI/API; hardened OS sandbox; encryption/key management; tool/MCP enforcement; scheduler leases; secret broker; data retention/deletion; remote transport security; backup/rollback protection; cross-platform process-tree cancellation; dependency patch policy automation.
- **Candidate concerns:** AG2 Network defaults to no auth unless configured; AgenticX Studio selected routes have not established a full auth boundary; Meldwork's pinned desktop dependency audit reported 14 findings (8 high). No candidate is trusted as a security boundary based on this prototype.

## Required denial tests before any non-synthetic use

- Cross-project read/write, blob/artifact reference, event replay, thread/message visibility and question-answer attempts.
- Missing, expired, revoked, wrong-project, wrong-work, wrong-tool, and overbroad capability grants.
- Forged actor, user-role impersonation, stale lease, duplicate/reordered event, idempotency-key content conflict and replayed approval.
- Prompt-injection text requesting secrets, tools, network access, policy changes or unrelated project data; verify no authority expansion.
- Secret-shaped input/output and exception/traceback; verify no persistence or log disclosure.
- Oversized/invalid JSON, parser crashes, timeout, child-process survival after cancellation, disk-full/SQLite failure and restart recovery.
- Candidate DB loss/rebuild and canonical backup/restore; no state loss, duplicate side effect, or silent hash-chain reset.
- Dependency/license inventory, pinned artifact hashes, vulnerability remediation and update cadence.

## Readiness gates

- **Local synthetic development:** allowed with deterministic fixtures and no secrets; clearly label limitations.
- **Private single-user data or real provider calls:** blocked until #2 contract/migration review, #6 authenticated human gate, #7 capability/auth/secret controls, and explicit user privacy/provider approval pass.
- **Tools, filesystem writes, MCP or autonomous side effects:** blocked until scoped capability enforcement, hardened isolation, cancellation, and idempotency/reconciliation tests pass.
- **Networked laptop workers:** blocked until authenticated transport, leases, identity, revocation, data-classification and host policy pass.
- **HiperGator:** separately blocked pending official UF Research Computing policy review and case-specific authorization (#9); this threat model does not grant access.

## Next security work

1. Define canonical capability grants and decision points before wiring any tools (#7).
2. Add authenticated actor binding and cross-project denial tests at the control-plane boundary.
3. Extend question state transitions without permitting silent/automatic approval (#6).
4. Design AG2 mirror outbox/reconciliation, process cancellation and crash/replay tests (#3/#4).
5. Reassess all gates whenever a provider, filesystem, tool, network, data class, host or dependency changes.
