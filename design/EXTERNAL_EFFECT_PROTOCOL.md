# External-effect intent, dispatch, and recovery protocol

**Status:** design contract for issue #15; not implemented as a general-purpose outbox. The adapter-specific dispatch-marker crash boundary has a new deterministic regression test in `harness/tests/test_event_store.py`; the Meldwork invocation journal still covers only its adapter receipt path. Do not enable real external effects on the strength of this document.

## Goal and non-goal

Persist the exact authorized intent before an agent can cause an externally visible effect, retain enough information to reconcile after a crash, and prevent automatic replay when the remote outcome is ambiguous. The protocol must distinguish “not dispatched,” “may have been dispatched,” “remote result known,” and “canonical result committed.”

It cannot promise exactly-once behavior for a remote service that does not provide idempotency or a queryable operation receipt. A lease, database transaction, or local receipt journal cannot roll back an effect already accepted by a remote system.

## Required intent

An effect intent is project-scoped and immutable and contains:

- unique `effectId` and one stable provider-facing idempotency key;
- project, work, run, adapter/connector name and pinned version;
- authenticated requesting principal or trusted agent identity, plus the human approval record when policy requires it;
- capability grant ID/revision and the exact action/resource scope checked at dispatch;
- canonical request body reference and content hash (do not copy secrets into canonical events);
- provider/endpoint identity, data classification, created time, expiry/deadline, and bounded retry policy;
- parent/correlation/causation references and connector-specific reconciliation instructions.

Changing payload, destination, resource, authorization scope, or approval revision creates a **new effect ID and idempotency key**. It must not mutate an existing intent.

## State machine

```text
prepared -> dispatching -> confirmed -> recorded
     |            |             |
     |            +-> ambiguous  +-> reconciliation_required
     +-> cancelled (only before dispatch)

ambiguous -> confirmed | rejected | manual_review
```

- `prepared`: durable, validated, and authorized intent; no dispatch has started.
- `dispatching`: connector is about to call the provider. Persist this transition before the call. A crash immediately afterward is ambiguous, even if no bytes actually left the process.
- `confirmed`: provider response/receipt or authoritative read-back proves the outcome. Persist its immutable receipt hash and minimal redacted metadata.
- `recorded`: canonical project event/result is committed and verified; the effect is complete in the project view.
- `ambiguous`: no authoritative outcome is available. Never automatically send again unless the provider's idempotency contract and reconciliation establish that retry is safe.
- `rejected`: provider definitively rejected before effect. Record reason class, not credentials or sensitive response bodies.
- `manual_review`: operator decision is required; do not unlock dependent work automatically.

All transitions are compare-and-set on the current effect revision and idempotent by effect ID. Impossible transitions fail closed. A duplicate request with the same effect ID and same request hash returns the existing status; the same ID with different content is a conflict.

## Dispatch algorithm

1. Verify the worker lease and project/work/run state in the canonical transaction that persists/loads the intent.
2. Verify the current identity, exact-scope capability, approval revision/expiry, request hash, connector policy, and budget **immediately before dispatch**. Revocation blocks a new dispatch.
3. Persist `dispatching` and its revision before invoking the connector. Pass the stable idempotency key unchanged.
4. Bound request/response sizes and time; redact secrets from logs and receipts.
5. On definitive success, persist provider receipt evidence, then append the canonical completion result. If the process fails between those writes, reconcile from the durable provider receipt.
6. On timeout, connection loss, worker death, or malformed response after dispatch began, mark/recover to `ambiguous`; do not replay by default.
7. Reconciliation queries the provider by idempotency key/operation ID where supported. If the provider cannot answer reliably, require an authorized human/operator decision with reason and audit event.
8. Only a `recorded` effect can satisfy a dependent task's external-effect acceptance criterion.

## Lease and cancellation semantics

Lease checks fence the local transition into `dispatching`; they cannot fence the remote call after it begins. If lease loss or cancellation occurs before `dispatching`, cancel safely. After `dispatching`, stop further effects, but preserve `ambiguous` until reconciled. A stale worker must not write canonical completion; a trusted reconciler may import a receipt only under the explicit recovery procedure and matching effect ID/job generation.

Do not hold a SQLite write transaction open across network calls. That would block heartbeats without making the remote action transactional.

## Storage and canonical history

Before implementation, #2 must add versioned canonical effect intent/result/approval records and event schemas (or approve an equivalent canonical design). The current adapter invocation tables are operational adapter metadata and are not a general canonical outbox. Store immutable request/receipt bytes by content hash with explicit size and retention limits; never put credentials in those blobs. Persist schema version and connector version for migrations.

## Connector support declaration

Each enabled connector must document and test:

- whether idempotency keys are accepted, their scope and retention window;
- whether operations can be queried by key after timeout;
- definitive versus ambiguous error classes;
- provider receipt authenticity and verification method;
- retry/backoff/rate/budget bounds;
- cancellation and compensation behavior (if any);
- data residency, logging, and retention behavior.

Connectors without safe idempotency/reconciliation stay disabled for unattended use and require explicit human confirmation per effect.

## Minimum acceptance tests for issue #15

- crash before intent commit; no connector invocation;
- crash after intent but before `dispatching`; safe resume without changing payload/key;
- crash after `dispatching` before network call; ambiguity is surfaced, not silently replayed;
- remote success followed by lost response; query/retry uses the identical idempotency key and never duplicates a supported operation;
- provider without idempotency/query support enters manual review;
- receipt persisted but canonical completion append fails; restart imports the same receipt exactly once into canonical history;
- capability revoked/approval expired before dispatch; no call occurs;
- capability revoked after dispatch; no new call occurs, existing operation is reconciled;
- stale generation cannot dispatch or record completion; recovery binds to exact job/effect/run IDs;
- duplicated, reordered, late, oversized, malformed, and forged receipts fail closed;
- budgets, deadlines, redaction, audit attribution, and retention limits are enforced.

## Contract-version and implementation gate

Issue #2 remains open. The reader currently accepts only the closed v0.1 contract, and `Projector` rejects unknown event types. Do not mutate v0.1 or encode effect state as an unrelated artifact/message to avoid the versioning work. The first canonical implementation must land a new additive versioned effect record/event contract with a reader/projection path while preserving v0.1 fixtures byte-for-byte. The dispatchable outbox/relay row must be a rebuildable projection of those canonical events, updated atomically with the canonical append in the same SQLite transaction; it cannot be a competing state authority. Implement this contract/schema/version reader under #2 before general relay code under #15. Existing code provides neither that event projection nor a trusted identity source for grants, approvals, or recovery.

## Current boundary

The existing Meldwork path has a stable invocation ID, queued job/generation binding, a lease-checked prepared→executing transition, staged receipt import, and ambiguous no-auto-replay behavior. The regression test simulates a crash after the durable dispatch marker but before a provider call; restart marks the call ambiguous, a retry cannot start or stage a receipt, and canonical history stays unchanged. This proves only the adapter-specific local state transition. It lacks general capability/approval binding, a provider idempotency contract, authenticated reconciliation, and remote-effect test doubles. It is not an external-effect guarantee.
