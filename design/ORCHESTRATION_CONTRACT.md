# Local orchestration and recovery contract

This contract describes the reusable offline coordinator in `harness/organization_runtime.py`. The project/work DAG and event log are canonical; execution callbacks and candidate-runtime state are not.

## Execution interface

A caller declares one `WorkExecution` per executable work item:

- `project_id`, `work_id`, `run_id`, and a shared `correlation_id` identify the durable assignment.
- `actor`, versioned `adapter`, and `context_hash` are written to the run ledger.
- optional `parent_run_id` links a delegated run to an active coordinator/agent run.
- `execute(context)` invokes one adapter stage and returns `ExecutionSignal.COMPLETED` or `WAITING_HUMAN`.
- `verify(context)` returns a boolean based on canonical project records, not candidate-local state or an in-memory response alone.
- The adapter must persist its accepted outputs to canonical storage before returning and attach `runId` to discussion/artifact/evidence outputs. Run closure snapshots their event/artifact/evidence IDs; replay verifies work, actor, source/content hashes, and output references. The coordinator then enters `verifying` and only marks work `completed` if the verifier passes.

`run_work` handles one work item. `run_plan` schedules a caller-declared set of work items in deterministic ready order, honoring the canonical dependency DAG and stopping at the first non-completed outcome. It does not invent a plan, assign roles, execute independent work in parallel, or choose adapters; those remain explicit VP/caller decisions. The offline end-to-end organization test composes a VP run with a reverse-ordered discussion/verification plan to exercise that DAG scheduling.

## Durable lifecycle and recovery

1. A blocking open inbox question affecting a work item prevents adapter start. Dependencies must be completed. The first root `run.started` and `ready → running` work transition are one canonical transaction. Only one root run may own a work item; concurrent root starts are rejected atomically. Delegated child runs may share that work only when linked to an active parent in the same run tree. A `running` work item with no active canonical run is recovery-required and cannot be claimed by a fresh run ID. Work cannot enter `completed`, `failed`, or `cancelled` while any run assigned to it remains active; command checks and replay enforce this invariant.
2. Each `runId` is immutable and idempotent for the same work, agent, adapter, context, correlation, and parent. It is never reused for a new attempt.
3. `parentRunId` is optional for compatibility. A new child can start only while its parent is active and only with the same correlation ID. Replay checks the link; a parent cannot finish while a direct child remains active. Recovery closes runs leaf-first.
4. If a run stops after outputs were committed but before run closure, `recover_project` closes it as `interrupted` while recording the already-accepted output IDs. Before doing so, `RunLedger` atomically imports any receipt already staged in the canonical invocation journal. `run_work` never replays that adapter: it runs only the canonical verifier. A passing verifier completes the work while preserving the run's `interrupted` history; a failing verifier marks work failed for explicit review/retry.
5. Call `recover_project` only during single-coordinator startup after ruling out a live competing worker. It marks active runs `interrupted`; it never restarts adapter code. When no output can be reconciled, a user must explicitly call `retry_work` with a reason. This changes interrupted work through `failed → ready`; the next attempt uses a fresh run ID.
6. Adapter failures are recorded as `failed`; retries are never automatic. Active descendants are interrupted leaf-first before a parent run is closed after failure; a callback that returns normally while leaving children active also fails rather than marking the parent successful. A work item with an open blocking question cannot be requeued. A cancellation delivered to the in-process async task marks active descendant runs `interrupted` leaf-first, then records the current run/work as `cancelled`; cancellation is terminal in this prototype. There is no cross-process cancel command.
7. If a run itself opens a question and returns `WAITING_HUMAN`, it must have created a canonical question linked to the affected work. The run is closed successfully (it completed the act of asking); after an answer, continuation uses a fresh run ID. A pre-start inbox pause has no run record, so the same declared run ID can start after the answer.

`WorkExecution` callbacks are process-local registrations, not persisted workflow code. After restart, the caller must re-register the adapter and verifier from trusted configuration, inspect canonical state, and follow the explicit recovery result. Caller-supplied `actor` fields and `retry_work`'s user-role check are prototype data checks, not authenticated authorization.

`SQLiteWorkQueue` persists bounded v1 execution descriptors (trusted route key, agent/verifier/adapter identity, context hash, correlation, optional parent) beside the canonical database and provides cross-process leases with random tokens and increasing generations. `OrganizationCoordinator.run_queued` claims one job, resolves execution callbacks through a trusted caller factory, heartbeats the lease, and returns blocked work for readiness re-check; after an inbox answer the factory must supply a fresh run ID for the new lease generation. Expired leases enter an explicit recovery state rather than being silently reclaimed. Queue rows are operational metadata, not project truth; callback registrations are not persisted, there is no autonomous worker service, leases are not enforced at every adapter side-effect boundary, and recovery attestations are not authenticated.

## Adapter durability boundary (not an outbox)

The coordinator and canonical SQLite store do **not** provide a distributed transaction with AG2, AgenticX, Meldwork, or any external effect. For run-assigned Meldwork execution, SQLite now keeps a stable invocation journal: it records dispatch, stages a validated canonical receipt, atomically imports the event batch with the imported-state transition, and replays staged receipts before marking runs interrupted. A dispatched invocation with no staged receipt is treated as ambiguous and is never automatically rerun. This is a canonical-side receipt inbox, not an adapter-side transactional outbox or effect guarantee; there is no cross-process lease or exactly-once guarantee. The current adapters use deterministic local mocks and no external provider/tool side effects.

Before adding a non-deterministic or externally effectful adapter, it must accept a stable invocation/idempotency ID (the run ID is the current attribution key), keep a durable adapter-side receipt/WAL, and provide reconciliation that can determine whether an invocation committed before a crash. Reconciliation must append validated output/evidence/artifact references to the canonical store before an explicit retry is allowed. If an effect happened but no canonical receipt was staged, the result remains unknown; a timeout or missing response is not proof the work did not happen. Content hashes, run attribution, and artifact input/evidence links are checked (inputs must be registered in the same work or its dependency closure). A bounded registrar stores caller-supplied source snapshots, but retrieval authorization, source authenticity, and adapter-side receipts for external effects remain incomplete.

## Explicit exclusions

This is not a production scheduler, authenticated authorization service, capability executor, hardened sandbox, provider connection, or remote cancellation mechanism. Subprocess limits and run/correlation lineage do not make adapter code safe. The test suite establishes deterministic lifecycle and replay behavior only.
