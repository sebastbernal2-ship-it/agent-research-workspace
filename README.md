# Agent Research Workspace

Research and development for a project-first collaborative AI workspace.

## Current artifacts

- [Agent-system foundation evaluation](research/agent-system-foundation-evaluation.md) — source-backed comparison and recommended composition.
- [AgenticX deep dive](research/agenticx-deep-dive.md) — platform architecture, gaps, safety and dependency review, and pilot decision gates.
- [Component evaluation and Frankenstein architecture](research/component-evaluation-and-frankenstein-architecture.md) — role-specific alternatives, integration contracts, and a staged fusion/pilot plan.
- [First non-coding research pilot](pilots/first-research-pilot.md) — candidate roles, safety gates, collaboration trace, inbox scenario, scorecard, and run record.
- [AgenticX 0.6.5 runtime spike](spikes/agenticx-runtime/README.md) — pinned isolated checkout, install/test results, and current blockers.
- [AG2 Network collaboration spike](spikes/ag2-collaboration/README.md) — pinned v1.0.0, offline message/recovery tests, WebSocket/auth review, and component fit.
- [Meldwork V1.0.9 source review](spikes/meldwork-review/README.md) — V4 collaboration behavior, isolated Linux test results, platform boundary, and dependency-audit warning.
- [GitHub issue register](tracking/README.md) — owners, dependencies, blockers, and the recommended sequence for remaining work.
- [Local harness prototype](harness/README.md) — canonical SQLite event store, project-scoped inbox, agent-run lifecycle, and an offline end-to-end organization run across AG2, AgenticX, and Meldwork adapters.
- [Security threat model](security/THREAT_MODEL.md) — trust boundaries, abuse cases, current gaps, and release gates; not a security approval.
- [Alignment memory prototype](alignment/README.md) — synthetic, provenance-first conformance checks kept separate from project charter, permissions, and private profile data.
- [ADR-0001](decisions/ADR-0001-canonical-control-plane-and-adapters.md) — why the control plane is canonical and how candidate runtimes stay replaceable.
- [Inbox API/UI contract](design/INBOX_API.md) — proposed product/API contract, with the current loopback-only synthetic HTTP adapter documented separately from real authentication and production service requirements.
- [UI foundation evaluation](design/UI_FOUNDATION_EVALUATION.md) — first-pass source/license review and provisional custom-UI recommendation.
- [Local UI/inbox vertical slice](ui/prototype/README.md) — canonical project/work/inbox reads and persisted answer/reject commands; synthetic data and loopback-only service, no real auth/models.
- [Model routing contract](design/MODEL_ROUTING.md) — offline profile/adapter design and approval gates for local and remote models.

This repository is separate from `pi-harness`; experiments and development should happen here unless explicitly agreed otherwise.
