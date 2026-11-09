# Temporal runtime spike

This spike tests Temporal as a replaceable coordinator for a durable, human-gated project workflow. It does **not** make Temporal authoritative for project state and is not production integration.

## Pinned test inputs

- Python SDK: `temporalio==1.35.0` (`requirements.txt`).
- Temporal CLI: `1.10.0`; its Linux x86_64 archive SHA-256 was verified against the published checksum (`521f461f3d108902763bb71e85cd0fd382dc9357b59604ae03da6ae734d16890`). CLI-reported dev server: `1.32.2`.
- Install the CLI from the official Temporal CLI release distribution; no upstream implementation source is copied here.

## Reproduce

Install the project schema validator and this spike's SDK in a disposable environment, then provide the CLI path:

```sh
uv venv /tmp/temporal-probe-venv
uv pip install --python /tmp/temporal-probe-venv/bin/python \
  -r contracts/requirements-test.txt \
  -r spikes/temporal-runtime/requirements.txt
PYTHONPATH=. TEMPORAL_CLI=/path/to/temporal \
  /tmp/temporal-probe-venv/bin/python spikes/temporal-runtime/probe.py
```

The CLI `start-dev` server binds to loopback (`127.0.0.1`) and stores its test persistence under `/tmp/temporal-canonical-probe/`. The canonical project database is a separate SQLite file in that directory. No Docker, network provider, model credentials, real user data, or tool calls are used.

## Verified behavior

The probe creates a synthetic canonical project, work item, agent run, and blocking inbox question. It starts a Temporal workflow and worker, stops both the worker and the dev server, answers the question through the canonical `InboxApplication`, and deliberately sends **no Temporal signal**. After restarting the server against the same Temporal SQLite file and starting a fresh worker, the workflow resumes its timer, reads the canonical answer, and uses retryable activities to:

1. append one run-attributed canonical message;
2. finish the canonical run as succeeded;
3. complete the canonical work item; and
4. verify the canonical project's event hash chain.

Observed output:

```text
before restart: 1
after restart: workflow completed from canonical answer; one run-attributed message; hash chain verified
```

The Temporal execution database and canonical project database remain separate. The workflow's approval decision comes from canonical replay, not a Temporal signal payload. Deterministic event IDs and existing run-ledger idempotency make the demonstration activity retry-safe for the tested event sequence. This is at-least-once activity execution, **not** an exactly-once external-effect guarantee.

## Assessment

Temporal is a strong candidate for long-lived timers, replay, retries, and worker/server restart recovery. The probe shows it can coordinate a workflow without becoming the project's system of record. It does not replace the existing control plane, inbox, or project event store. Compared with the custom local queue, it can supply a mature workflow-history and scheduling engine; that benefit comes with a separate service lifecycle, SDK/server compatibility, workflow versioning, persistence/backup, access-control, and operational burden.

The alternative-candidate scan found:

- **Hatchet** also advertises durable workflows, event waits, multi-tenancy, and PostgreSQL-backed history. Its upstream quickstart requires Docker; a fair local run was not performed in this environment. It remains an alternative, not a simultaneous second orchestrator.
- **LangGraph** provides durable checkpoints and human interrupts, but is a graph/agent runtime rather than a general distributed queue-and-worker service. Its checkpoint state would still need a strict adapter boundary from canonical project state.
- **OpenHands Agent Canvas** provides agent routing and Docker/remote backends; it is primarily a developer-agent control center, not the canonical project workflow engine. Its own docs warn that running without a sandbox gives the agent access to the host project paths.

These are source/README-level screening results, not equivalent hands-on evaluations. Temporal's Python workflow sandbox is explicitly a best-effort determinism guard, **not a security sandbox**. Worker isolation and tool/effect controls remain independent requirements.

## Limitations / next proof gates

- This is a synthetic smoke probe, not a complete queue/lease adapter. It does not acquire or enforce the project's queue lease inside Temporal activities.
- The demo polls the canonical store every 250 ms. Production should use a canonical transactional outbox/relay plus reconciliation, not this polling frequency or an unprotected dual-write of an inbox answer and workflow signal.
- No authenticated Temporal frontend, multi-tenant authorization, server HA, retention policy, database backup/restore, migration, worker deployment, failover, or load tests were performed. `start-dev` and its SQLite persistence are evaluation-only.
- No remote effect, model call, tool call, sandbox, or crash at each canonical write boundary was tested. Activity retries do not roll back outside effects.
- The probe's injected synthetic inbox principal/access policy exists only to exercise canonical APIs; it is not identity evidence.
- Temporal would add operational components alongside the existing queue. Adoption requires an explicit architecture decision, end-to-end lease fencing/outbox design, security review, and fault-injection evidence. No existing runtime or production path was changed by this spike.
