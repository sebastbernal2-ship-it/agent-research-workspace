# Canonical contracts — draft v0.1 plus additive v0.2

This folder starts issue [#2](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/2): define a language-neutral boundary before adding another runtime.

## Decision so far

- JSON Schema Draft 2020-12 is the draft wire/record format so Node and Python adapters can validate the same payloads.
- Event records are intended to be the authoritative history; project, work, run, question, artifact and evidence records are current projections/references that can be rebuilt or fetched from canonical history.
- IDs and content references are opaque. No credential, executable path, local filesystem path, unrestricted tool payload, or private user profile belongs in these contracts.
- This is a **draft contract**, not a production event store, authentication system, DB schema, workflow engine, or commitment to Python/Node for the product.

## Records included

`contracts/v0.1/schema.json` defines the original closed project/work/run/question/capability/artifact/evidence/event vocabulary. Additive v0.2 definitions in `contracts/v0.2/schema.json` retain those v0.1 record shapes and add an immutable `effect_intent` plus `effect.intent_created` and `effect.status_changed` events. `harness/effect_ledger.py` records those events; no provider relay is implemented. v0.1 records remain validated by their original schema.

`contracts/v0.1/schema.json` defines:

- revisioned project charter (goals, non-goals, acceptance criteria);
- hierarchical work item with owner role, dependencies, scoped capability-grant references, budgets and acceptance criteria;
- explicit capability grant snapshots scoped to project/work, agent, capability, opaque resource IDs, approver and expiry, with immutable revocation events;
- agent run with adapter/version, context hash, grants, status, usage, and canonical output message/artifact/evidence references; optional `parentRunId` records delegation lineage, which replay checks against an active parent and shared correlation ID;
- separate blocking/nonblocking human question and answer record;
- immutable artifact metadata and claim-linked evidence record; run attribution, source hashes, content blobs, input-artifact/evidence derivation links, and run-finished output references are cross-checked during replay. `harness/evidence.py` provides a bounded registrar for already-retrieved bytes; it does not fetch sources or authenticate their claims;
- append-only event envelope with sequence, actor, idempotency/correlation/causation refs, closed event/payload vocabulary, and content hashes that point to full immutable project/work/run/question/artifact/evidence snapshots where state must be reconstructed.

## Validate

```bash
python3 -m pip install -r contracts/requirements-test.txt
python3 -m unittest discover -s contracts/tests -v
```

Migration and compatibility policy: [MIGRATION_POLICY.md](MIGRATION_POLICY.md). The SQLite store dispatches validation by version: existing project/work/run/question/artifact/evidence records and historical events remain v0.1; new immutable `effect_intent` snapshots and effect lifecycle events use additive v0.2. A project event history may mix versions, validated per event. No v0.1 schema or stored event is rewritten. This v0.2 change is still draft/unreleased; golden export/replay compatibility and a migration dispatcher for future semantic migrations remain required before release.

## Explicitly incomplete

- A SQLite reference store and deterministic projector enforce event ordering/idempotency, snapshot hashes, selected cross-record references, replay, restart/export, and append-only behavior. A local adapter-invocation journal stages canonical receipt batches and atomically imports them with their imported state, but it is not a distributed transaction or externally effecting adapter outbox. This is still a single-host prototype, not a production transactional service.
- A local capability-authority prototype enforces exact project/work/agent/action/resource and expiry scope, plus user-actor grant/revocation and replay tests. It is not an authenticated identity service, is not wired to actual tools/adapters, and is not an OS sandbox or security approval. Schema migrations, retention/deletion, multi-process stress/race behavior, crash-consistency fault injection, and broader dependency-graph rules remain open.
- Each version has a closed event/payload vocabulary; schema changes require a new compatible version/migration decision, not adding arbitrary fields. v0.2 effect intent records are projected canonically, but no outbox relay/provider dispatch is implemented or enabled.
- The user-alignment ledger is a separate workstream (#10); no personal profile fields are included here.
- The question record and reference projector establish separate durable question state and a synthetic pause/answer/resume trace, but not UI, notification, expiry policy, or production scheduler pause/resume.

Do not use these schemas for real user data until the remaining controls in issue #2 and #7 are implemented and reviewed.
