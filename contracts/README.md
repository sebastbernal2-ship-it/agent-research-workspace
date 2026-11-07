# Canonical contracts — draft v0.1

This folder starts issue [#2](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/2): define a language-neutral boundary before adding another runtime.

## Decision so far

- JSON Schema Draft 2020-12 is the draft wire/record format so Node and Python adapters can validate the same payloads.
- Event records are intended to be the authoritative history; project, work, run, question, artifact and evidence records are current projections/references that can be rebuilt or fetched from canonical history.
- IDs and content references are opaque. No credential, executable path, local filesystem path, unrestricted tool payload, or private user profile belongs in these contracts.
- This is a **draft contract**, not a production event store, authentication system, DB schema, workflow engine, or commitment to Python/Node for the product.

## Records included

`contracts/v0.1/schema.json` defines:

- revisioned project charter (goals, non-goals, acceptance criteria);
- hierarchical work item with owner role, dependencies, capability-grant references, budgets and acceptance criteria;
- agent run with adapter/version, context hash, grants, status and usage;
- separate blocking/nonblocking human question and answer record;
- immutable artifact metadata and claim-linked evidence record;
- append-only event envelope with sequence, actor, idempotency/correlation/causation refs, closed event/payload vocabulary, and content hashes that point to full immutable project/work/run/question/artifact/evidence snapshots where state must be reconstructed.

## Validate

```bash
python3 -m pip install -r contracts/requirements-test.txt
python3 -m unittest discover -s contracts/tests -v
```

## Explicitly incomplete

- A SQLite reference store and deterministic projector now enforce event ordering/idempotency, snapshot hashes, selected cross-record references, replay, restart/export, and ordinary append-only behavior. This is still a single-host prototype, not a production transactional service.
- Capability authorization, schema migrations, retention/deletion, multi-process stress/race behavior, crash-consistency fault injection, and broader transition/dependency-graph rules are not yet implemented or fully tested.
- `eventPayload` is a closed v0.1 union; schema changes require a new compatible version/migration decision, not adding arbitrary fields.
- The user-alignment ledger is a separate workstream (#10); no personal profile fields are included here.
- The question record and reference projector establish separate durable question state and a synthetic pause/answer/resume trace, but not UI, notification, expiry policy, or production scheduler pause/resume.

Do not use these schemas for real user data until the remaining controls in issue #2 and #7 are implemented and reviewed.
