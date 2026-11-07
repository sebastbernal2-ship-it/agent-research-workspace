# Agent-system foundations for a project-first collaborative AI workspace

**As of:** 2026-11-07 (upstream repository metadata and documentation reviewed)  
**Scope:** Initial bounded desk research for a non-coding research pilot; subsequent isolated component installs/tests are linked below. No system was deployed or run with real model/provider credentials.

## Executive recommendation

No single open-source project reviewed here provides the entire vision. Do not adopt a desktop agent product as the system of record or hierarchy runtime. Keep project/task state in a canonical control plane with typed work records and an append-only event history. Use the existing task-control lifecycle as the initial orchestrator; evaluate LangGraph for stateful, interruptible agent flows. Add Temporal only if the pilot shows a need for cross-process retries, long waits, recovery, or independently deployed workers. Consider Letta for agent-specific memory only after data/privacy review, never as canonical project/task state.

Treat Meldwork, AgenticX, and Orkas as product and UX references, not as vetted foundations. OpenClaw is primarily an assistant/gateway and tool ecosystem, not a project hierarchy or durable-work system. For HiperGator, define a worker adapter, but do not deploy until UF Research Computing policy and constraints are checked. No source reviewed establishes HiperGator suitability or authorization.

### Local-harness update (2026-11-07)

The repo now contains a local, deterministic reference implementation for canonical project/work events, replay, question pause/resume/closure, scoped capability-grant records, and separate AG2/Meldwork/AgenticX adapters. An offline V4+AgenticX integration and AG2 WAL recovery tests pass. This increases implementation evidence about adapter plumbing only; no real model, provider credentials, Electron app, private user data, or HiperGator was used. See [ADR-0001](../decisions/ADR-0001-canonical-control-plane-and-adapters.md), [the harness](../harness/README.md), and [the threat model](../security/THREAT_MODEL.md).

## Vision translated into system requirements

| Requirement | Required system behavior |
|---|---|
| Project-first interaction | Project is the durable home; VP conversation is persistent, not a disposable chat/session. |
| Aligned VP | Explicit user preferences, project charter, corrections, provenance, uncertainty; never treat inferred beliefs as confirmed facts. |
| Hierarchy | VP delegates to workstream orchestrators; orchestrators own scoped tasks, budgets, and acceptance; subagents report evidence/artifacts upward. |
| Deep agent collaboration | Shared deliberation: proposals, critique, evidence requests, disagreements, revision, synthesis—not just one-shot delegation. |
| Autonomous execution | Durable run/task records, pause/resume, retries, idempotency, leases, schedules, budgets, recovery. |
| Separate human inbox | Consequential unresolved questions pause only dependent work and appear outside the VP conversation; answer resumes work. |
| Distributed resources | Workers report capabilities/availability; laptop adapters first; HiperGator scheduler adapter only after policy review. |
| Trust and audit | Scope/permission enforcement, artifacts linked to claims and decisions, traceable events and external effects. |

## Candidate systems

These are upstream documentation/repository claims, not independently verified runtime behavior. No project was installed or executed. Stars and commit activity are weak maturity signals, not proof of correctness or security.

| Candidate | What upstream says it provides | License / evidence | Fit, gaps, validation needs |
|---|---|---|---|
| **Meldwork** — [repo](https://github.com/Ryder-Sun/Meldwork) | Local-first multi-agent workspace; direct, independent-response and auto-discussion modes; agent contributions/reviews; scoped permissions; run ledger and human gates. Explicit use cases include research, analysis, writing, and planning. | Apache-2.0; created 2026-07-29; active; ~1.5k stars when checked. Current published release is an Apple Silicon macOS preview. | Apple Silicon is a current release limitation, not proven to be a fundamental source-code requirement: the app is JavaScript/Electron. However, architecture docs call the packaged Electron app the supported product surface; the UI relies on a local preload bridge, data lives in local user-data stores, and docs say there is no required server/account/tenant model or remote conversation store. Packaging for Linux/Windows may be feasible; making this a web-based, multi-machine platform requires substantial host/backend/identity/persistence redesign. No independent build test. |
| **AgenticX** — [repo](https://github.com/DemonDamon/AgenticX) | Python runtime/SDK, CLI, Studio server, Near desktop, agent roles/group chat, memory, MCP, skills, sandbox, observability/eval and enterprise gateway. README also claims long-run orchestration and disk-backed project state. | Apache-2.0; created 2024-03-15; active; ~1k stars when checked. | Broadest platform scope. README says desktop/runtime and enterprise online paths are independent. Validate feature integration, permissions, deployment, recovery tests, dependency licenses, and whether collaboration is genuinely agent-to-agent vs UI grouping. Broad feature surface raises maintenance risk. |
| **Orkas** — [repo](https://github.com/Orkas-AI/Orkas) | Desktop AI team: projects, goals/rules/memory/files, to-dos, auto-advance, schedules, specialist agents, connectors. | MIT; created 2026-04-29; active; ~9k stars when checked. | Strong project continuity UX. Critical caveat: its README says shared projects, cloud sync, and web publishing are only in the packaged app; open-source build lacks them. Desktop product, no demonstrated general worker fleet/HPC. Understand feature split, local data, provider calls, and execution isolation. |
| **OpenClaw** — [repo](https://github.com/openclaw/openclaw) | Self-hosted assistant gateway, persistent state, messaging channels, tools, skills, plugins, device integrations and control UI. | MIT; created 2025-11-24; active; very high adoption signal (~394k stars at lookup); fast-moving. | Strong assistant/integration reference, not the project VP hierarchy or shared durable work ledger. README warns tools can run on host unless sandboxing is configured. Audit gateway exposure/auth/sandbox and pin versions. No HPC validation. |
| **Letta** — [repo](https://github.com/letta-ai/letta) | Stateful agents and advanced memory; local/self-host server and agent APIs. The README points active development toward Letta Code. | Apache-2.0; created 2023-10-11; active; ~25k stars. | Candidate for persistent agent identity/recall, not authoritative project/task state, event sourcing, collaboration, or fleet scheduling. Review retention, provider flow, access control, backup and database behavior. |
| **Letta Code** — [repo](https://github.com/letta-ai/letta-code) | Agentic coding product/runtime with persistent agent context. | Apache-2.0; created 2025-10-25; active; ~3.8k stars. | Coding-oriented; not appropriate as the central non-coding system. Could offer runtime/persistence patterns only; exclude coding tools from research pilot. |
| **LangGraph** — [repo](https://github.com/langchain-ai/langgraph) | Stateful graph orchestration, checkpoints/persistence, durable execution, human interrupts and subgraphs. | MIT; created 2023-08-09; active; ~44k stars. | Useful agent-flow runtime, not complete product/control plane. Application must implement project schema, role authority, durable task ledger, inbox, policy, budgets, worker adapter. Candidate for pilot. |
| **Temporal** — [repo](https://github.com/temporalio/temporal) | Durable workflow service with persistent histories, retries, timers, signals, and workers. | MIT; created 2019-10-16; active; ~24k stars. | Strong long-running execution substrate; adds service/database/operations. Does not make LLM outputs correct or provide agent hierarchy/memory/UI. Consider only if pilot needs its durability across machines/restarts. |

### Protocols and adjacent tools

- **MCP** ([spec](https://modelcontextprotocol.io/specification/2025-11-25)): tool/resource integration, not agent hierarchy, durable execution, or authorization policy.
- **A2A** ([project](https://github.com/a2aproject/A2A)): can inform interoperable agent-to-agent task handoffs; does not itself create shared project state, trust, memory, or deep deliberation.
- Begin with narrow versioned internal schemas for task, message, artifact, question, decision, and result. Add protocol adapters where a real integration requires them.

## Recommended composition to test

1. **Canonical project control plane:** relational or append-only event/task store. Model `project`, `charter`, `user_preference`, `role`, `work_item`, `dependency`, `lease`, `artifact`, `question`, and `event`. Enforce scope, idempotency, budgets, provenance, approval boundaries, and resumable states.
2. **VP and hierarchy:** a project VP owns interpretation of the charter and delegates work to orchestrators. Orchestrators own workstreams and acceptance. Agents receive typed packets with scope/tools/budget and return evidence/artifacts. Record actual message and delegation history.
3. **Agent-flow runtime:** start with existing task-control lifecycle. Pilot LangGraph for branching, pause/resume, and human gates. Add Temporal only if restart/retry/long-wait behavior warrants infrastructure.
4. **Memory:** keep authoritative preferences, project charter, decisions, and task state in project records. Test Letta as optional agent-specific recall only; memory must be inspectable, correctable, provenance-bearing, and unable to overwrite confirmed decisions silently.
5. **Deep collaboration:** persist free-form discussion and typed events (proposal, critique, evidence request, disagreement, revision, decision, synthesis) in a project event stream. Require useful exchange, not artificial “debate.” An orchestrator should stop/resolve loops by budgets and task criteria.
6. **Question inbox:** create a durable `question` record with options/context/impact and blocked dependencies. VP chat remains user-initiated; inbox answers append events that resume only dependent tasks.
7. **Worker adapters:** laptop worker first; generic batch interface later. HiperGator likely needs Slurm submission/poll/cancel/artifact retrieval, but policy, account, network and data constraints must be verified with UF Research Computing before implementation.
8. **Product UI:** project dashboard with VP conversation, goals/charter, orchestrator tree, agent collaboration/activity, work/artifacts, and a separate pending-questions panel. Chat is a view, not the database or only control surface.

## Bounded non-coding research pilot

The detailed protocol, role cards, safety gates, collaboration trace, inbox scenario, scorecard, and run-record template are in [`pilots/first-research-pilot.md`](../pilots/first-research-pilot.md).

**Candidate order:** Meldwork remains first for a real-model collaboration pilot because agent-to-agent interaction is the highest-risk requirement; AgenticX is the breadth comparison, and AG2 is now a promising message-network component. The AgenticX and AG2 offline component spikes are not the non-coding research pilot and do not decide the foundation. Meldwork's published preview requires Apple Silicon macOS, while the current research environment is Linux. Provider configuration and any use of non-public project context require explicit user setup/approval. Do not import the user's private profile into an unverified system.


### Pilot choice and execution status

**Selected first candidate: Meldwork**, because the highest-risk product requirement to test first is genuine agent-to-agent collaboration, not code-generation quality. **AgenticX is the comparison candidate** for breadth of platform capabilities. This is a pilot choice, not a foundation decision.

**Non-coding pilot not yet executed.** The later isolated component spikes installed AgenticX 0.6.5 and AG2 1.0.0 and ran mock/scripted tests only; no real model calls, credentials, private profile, or project data were used. Before a real-model pilot, confirm the approved provider/model and budget; use a non-sensitive question and synthetic/public project charter. Meldwork still requires a compatible Apple Silicon Mac for its published preview or a separately tested source build/port.

**Run order:** (1) inspect the pinned Meldwork release, permission model, data storage, and agent integrations; (2) run the bounded pilot below with public sources only; (3) separately evaluate AgenticX on the same task and rubric, if its runtime can be deployed in the user's environment without excessive setup; (4) compare artifacts and event traces, not marketing claims; (5) retain no candidate-specific state as canonical until export/recovery is verified.

**Limits:** one project; maximum four role instances, three decomposition levels, ten work items, thirty unique sources, twenty minutes active agent time, and a fixed model/token/cost ceiling selected before starting. Stop on budget exhaustion or unresolved safety/policy issue.

**Pass/fail gates (all required):**

- Recovery: restart orchestrator mid-task; recover all accepted state and produce zero duplicate completed side effects.
- Traceability: 100% of final factual claims link to source and producing role/step; unsupported central claim is a failure.
- Collaboration: two independent reports are attributable; critic reports a concrete disagreement/uncertainty or explicitly none; synthesis preserves unresolved conflicts.
- Inbox: consequential question appears outside chat; dependent work pauses; answer is recorded and resumes only dependent steps; all transitions correct.
- Alignment: include known user/project preferences and one corrected preference; VP follows charter and reflects correction with provenance. Any confirmed preference contradicted without asking is a failure.
- Isolation: research roles have no shell/code tools; prohibited action is denied and logged.
- Boundedness: no further work scheduled after source/task/time/cost cap; no overrun beyond measurement tolerance.
- Quality: blind reviewer scores evidence relevance, coverage, and uncertainty ≥4/5; otherwise fail.
- HiperGator: not part of pilot and remains unvalidated pending official policy review.

## Evidence and limitations

This is desk research, not a benchmark, security review, source-code audit, or deployment. Repository metadata/readmes and linked upstream docs were accessed 2026-11-07. No system was run. Before adoption, inspect pinned release tags, authz and sandbox implementation, persistence/recovery tests, data export/backup, threat model, dependency licenses, provider-data flows, and maintainer response history. Confirm constraints around research data and external model APIs. HiperGator use requires official UF Research Computing confirmation, not inference from a project's generic HPC support.

## Upstream sources

1. [Meldwork](https://github.com/Ryder-Sun/Meldwork)
2. [AgenticX](https://github.com/DemonDamon/AgenticX)
3. [Orkas](https://github.com/Orkas-AI/Orkas)
4. [OpenClaw](https://github.com/openclaw/openclaw)
5. [Letta](https://github.com/letta-ai/letta)
6. [Letta Code](https://github.com/letta-ai/letta-code)
7. [LangGraph](https://github.com/langchain-ai/langgraph), [overview](https://docs.langchain.com/oss/python/langgraph/overview), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
8. [Temporal](https://github.com/temporalio/temporal), [docs](https://docs.temporal.io/)
9. [MCP specification](https://modelcontextprotocol.io/specification/2025-11-25)
10. [A2A](https://github.com/a2aproject/A2A)
11. [UFIT Research Computing scheduling docs](https://docs.rc.ufl.edu/quickstart/scheduling/) — HiperGator policy/workflow reference to verify before any execution.

*Eleven principal upstream sources plus linked documentation; no non-source search snippets treated as evidence.*
