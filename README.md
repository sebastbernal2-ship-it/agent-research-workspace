# Agent Research Workspace

Research and development for a project-first collaborative AI workspace.

## Current artifacts

- [Harness completion and production-readiness plan](PRODUCTION_READINESS_PLAN.md) — phased, testable acceptance plan for issues #2–#18, explicit owner decisions, release gates, dependencies, and operational evidence. The current system remains an offline prototype until those gates pass.
- [Agent-system foundation evaluation](research/agent-system-foundation-evaluation.md) — source-backed comparison and recommended composition.
- [AgenticX deep dive](research/agenticx-deep-dive.md) — platform architecture, gaps, safety and dependency review, and pilot decision gates.
- [Component evaluation and Frankenstein architecture](research/component-evaluation-and-frankenstein-architecture.md) — role-specific alternatives, integration contracts, and a staged fusion/pilot plan.
- [Frankenstein component matrix](research/FRANKENSTEIN_COMPONENT_MATRIX.md) — capability-by-capability candidate, evidence, boundary, decision, and next test; separates limited adapters from untested candidates.
- [First non-coding research pilot](pilots/first-research-pilot.md) — candidate roles, safety gates, collaboration trace, inbox scenario, scorecard, and run record.
- [AgenticX 0.6.5 runtime spike](spikes/agenticx-runtime/README.md) — vendored upstream source snapshot, install/test results, and current blockers.
- [AG2 Network collaboration spike](spikes/ag2-collaboration/README.md) — vendored v1.0.0 source snapshot, offline message/recovery tests, WebSocket/auth review, and component fit.
- [Meldwork V1.0.9 source review](spikes/meldwork-review/README.md) — vendored full source snapshot, V4 collaboration behavior, isolated Linux test results, platform boundary, and dependency-audit warning.
- [GitHub issue register](tracking/README.md) — owners, dependencies, blockers, and the recommended sequence for remaining work.
- [Local harness prototype](harness/README.md) — canonical SQLite event store, project-scoped inbox, reusable DAG coordinator, parent-linked agent runs, and an offline end-to-end run across AG2, AgenticX, and Meldwork adapters.
- [Orchestration/recovery contract](design/ORCHESTRATION_CONTRACT.md) — adapter callback, verification, retry, pause, cancellation, parent-run, and crash-recovery semantics; documents the canonical receipt journal and remaining adapter-outbox/exactly-once gaps.
- [Security threat model](security/THREAT_MODEL.md) — trust boundaries, abuse cases, current gaps, and release gates; not a security approval.
- [Alignment memory prototype](alignment/README.md) — synthetic, provenance-first conformance checks kept separate from project charter, permissions, and private profile data.
- [ADR-0001](decisions/ADR-0001-canonical-control-plane-and-adapters.md) — why the control plane is canonical and how candidate runtimes stay replaceable.
- [Inbox API/UI contract](design/INBOX_API.md) — proposed product/API contract, with the current loopback-only synthetic HTTP adapter documented separately from real authentication and production service requirements.
- [Local UI/inbox vertical slice](ui/prototype/README.md) — canonical project/work/inbox reads and persisted answer/reject commands; synthetic data and loopback-only service, no real auth/models.
- [Model routing contract](design/MODEL_ROUTING.md) — offline profile/adapter design and approval gates for local and remote models.

This repository is separate from `pi-harness`; experiments and development should happen here unless explicitly agreed otherwise.
