# Transparent alignment memory prototype

This workstream (#10) is deliberately separate from project truth and from candidate-agent memory. The repo contains a closed JSON Schema and a pure conformance evaluator over **synthetic records only**; it does not persist a user profile, ingest private data, call a model, or grant permissions.

## Data boundaries

- User preferences/goals/constraints are typed, scoped, timestamped, sourced claims. `observed` records are advisory. A record becomes binding only after explicit human-user confirmation.
- Scope is exactly `user`, `project`, or `task`; scope IDs are compared literally. Project/task rules do not leak to a different project/task. A correction must point to an included prior record with the same scope and topic.
- A project charter remains the revisioned canonical project artifact under `contracts/v0.1`; it is not silently imported into or overwritten by global preference memory. A memory entry may cite a charter location as provenance, but cannot replace that charter.
- Agent-local memory is noncanonical and outside this schema. It must be disableable/exportable/deletable without changing project events.
- User correction is represented as a new immutable record superseding an earlier ID. The evaluator never edits an old record or silently chooses between contradictory confirmed rules.

## Evaluator contract

`ConformanceEngine.evaluate(...)` returns:

- `alignmentStatus`: `consistent`, `conflict`, `ambiguous`, `review`, or `unknown`;
- `bindingMemoryIds`, `advisoryMemoryIds`, violations, and ambiguity IDs to explain the result;
- `authorizationStatus: not_checked` in every result. Alignment consistency is **not** permission to use a tool, spend money, contact a service, or execute a side effect.

Conflicting confirmed requirements cause `needsUserClarification`; stale/observed records do not bind. No rule yields `unknown`, not a default approval. The engine does not resolve the project charter's natural-language content; structured constraints need explicit provenance and human review.

## Validate

```bash
python3 -m unittest alignment.tests.test_conformance -v
```

Tests cover confirmation, contradiction, expiry, scope isolation, correction/supersession, unknown fields and the strict separation between alignment and authorization. Private profile import still requires a separate privacy review and explicit opt-in.
