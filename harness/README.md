# Local harness prototype

A **piece-by-piece integration prototype**, not yet a production harness. It connects canonical project events to AG2 Network, invokes an AgenticX worker across a subprocess boundary, and imports validated Meldwork V4 phase receipts through a separate Node adapter. The upstream source trees remain pinned and unmodified.

## Data flow

```text
AG2 v1.0 Network/WAL ──listener + replay──┐
                                          ├─> canonical SQLite events + content-addressed blobs
AgenticX 0.6.5 Executor ──JSON subprocess─┘

Meldwork V4 receipt + public committed text ──Node parser/sanitizer adapter──> same event contract/store
```

AG2 owns message transport/turn order; AgenticX executes one bounded worker task in its own venv; Meldwork's pinned V4 parser/sanitizer validates receipt-shaped data. The SQLite store, not any candidate's internal DB, is canonical for the prototype.

## What currently works (offline, deterministic)

- SQLite event append validates the v0.1 JSON Schema, assigns per-project sequence in a transaction, enforces event/idempotency uniqueness, rejects ordinary update/delete, stores content-addressed blobs and immutable record snapshots, supports atomic multi-event commits, hash-chains events, verifies/reopens, and exports JSONL.
- The local control plane creates and revises projects/work through immutable snapshots, checks parent/dependency references and rejects cycles, and freezes the dependency closure of work under an open blocking question. It keeps human questions outside discussion threads, pauses only the explicit affected IDs, and commits a user's answer plus exact resume set atomically. Rejected/cancelled/expired questions leave blocking work `blocked`; supersession carries the exact pause set.
- A small capability authority atomically records explicit user-actor grants and work references, revocation, expiry (maximum seven-day prototype TTL), and exact project/work/agent/action/opaque-resource checks. Tests deny cross-project, wrong-subject, wrong-action/resource, expired, revoked, forged-event and non-runnable-work access. This prototype is not authenticated identity or effect enforcement.
- A separate synthetic alignment-memory contract/evaluator covers provenance, scope, user confirmation, expiry, corrections, and conflicting confirmed constraints. It can flag a conflict and request clarification, but it does not authorize actions, rewrite project truth, or import private user data.
- A deterministic reference projector reconstructs project/work/run/question/capability/artifact/evidence/message state. Tests cover restart/export, failed runs, contradictory evidence preservation, artifact-content tampering, forged transitions, dependency cycles/closures, and append contention.
- AG2's real in-process round-robin Hub accepts proposal → AgenticX critique → revision. The AgenticX task runs in a separate process using the pinned AgenticX venv, temporary HOME/cwd, restricted environment, no tools, and an injected deterministic provider response.
- AG2's durable DiskKnowledgeStore WAL can be reopened; a new mirror instance can replay it into canonical SQLite without duplicate events. The end-to-end AG2 + AgenticX integration also passes restart/replay.
- The Meldwork adapter calls the pinned V4 receipt parser and sanitizer in a Node subprocess, maps supported phases to the shared discussion-event contract, persists sanitized content, and rejects invalid receipts.
- A full offline V4 integration runs Meldwork's real Node `LocalWorkspace` orchestration with eight deterministic AgenticX `AgentExecutor` subprocess calls, sends its proposal/challenge/work/synthesis/verification receipts through the pinned V4 parser/sanitizer, and replays the resulting records from the same canonical project/work log. It does not run Electron or call a real model.
- A separate composition test routes AG2 discussion (including an AgenticX subprocess critique) and a **synthetic** Meldwork V4 verification receipt into the same project/work event log. This tests AG2/Meldwork adapter convergence independently; it is not a real Meldwork run.

These tests prove protocol, adapter, persistence and deterministic mock execution paths—not real-model reasoning quality, product safety, or alignment.

## Reproduce

From the repository root:

```bash
python3 -m pip install -r contracts/requirements-test.txt
python3 -m unittest discover -s contracts/tests -v
python3 -m unittest discover -s harness/tests -v
python3 -m unittest discover -s alignment/tests -v
python3 -m unittest harness.integration_tests.test_meldwork_bridge -v
python3 -m unittest harness.integration_tests.test_meldwork_agenticx_runtime -v
```

The AG2 venv is pinned/isolated under its spike; install the schema test dependency there once if needed, then run:

```bash
uv pip install --python spikes/ag2-collaboration/.venv/bin/python 'jsonschema>=4.10,<5'
spikes/ag2-collaboration/.venv/bin/python -m unittest \
  harness.integration_tests.test_ag2_mirror \
  harness.integration_tests.test_ag2_agenticx -v
```

`harness/tests/test_agenticx_bridge.py` launches the ignored AgenticX spike venv automatically. The full Meldwork/AgenticX runtime integration launches the pinned Meldwork V4 Node runtime and AgenticX spike venv; it uses only deterministic mock responses, not Electron or a model API. Meldwork parser-only tests require Node.js and the pinned submodule checkout.

## Current limits / known failure surfaces

- This remains a prototype. No web UI, user-facing VP, project browser, separate inbox screen, autonomous background scheduler, multi-laptop worker, or deployment service exists. The proposed inbox/API design is in `design/INBOX_API.md`; notification UX and scheduler continuations are absent. Capability actor fields are caller-supplied (not authenticated), no adapter/tool invokes the authority yet, and the policy check is not a sandbox or TOCTOU-safe side-effect boundary. Dependency revisions are locally validated/frozen under blocking questions, but no scheduler consumes the DAG and remote concurrency is untested.
- The reference projector covers the current closed v0.1 event vocabulary; a written migration policy exists, but no migration dispatcher/second-version test exists. Independent contract review, process-level concurrency/crash fault injection, and production-store decision remain. Contract issue #2 remains open.
- AG2's HubListener is observational and deliberately swallows listener exceptions. The mirror records degradation and exposes it to the caller; WAL replay repairs missed events only while the AG2 WAL still exists. This is not an atomic dual-write/outbox guarantee. Issue #3 remains open.
- Current AG2 tests use local transports and no external network auth. The AG2 subprocess/adapter path has no production identity boundary yet.
- AgenticX's bridge proves one deterministic AgentExecutor request over stdio. The worker is process-isolated, **not a hardened sandbox**. No Studio API/server, real model, persistent AgenticX session, tools, or approval resume was exercised. Issue #4 remains open.
- The Meldwork runtime bridge runs the actual Node `LocalWorkspace` V4 orchestration, but not the Electron application or a model-backed agent. Its AgenticX workers and V4 receipt contents are deterministic mocks. Apple-silicon app behavior and the pinned NPM audit findings remain unresolved. Issue #5 remains open.
- This local harness makes no provider/model calls and does not need budget approval. Real-model comparison and HiperGator policy/deployment are future work, tracked separately in issues #8/#9 and do not block local integration.

Treat errors as component-local until traces prove otherwise: AG2 transport/WAL, AgenticX subprocess/JSON, Meldwork receipt parser, canonical schema, SQLite append/hash chain, or recovery/replay. Keep the raw candidate stores exportable and inspectable while debugging; do not hide adapter errors as successful runs.
