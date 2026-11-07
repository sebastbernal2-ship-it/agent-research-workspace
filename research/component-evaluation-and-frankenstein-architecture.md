# Component evaluation and the “Frankenstein harness” architecture

**Status:** Initial research proposal, updated by isolated runtime spikes. AgenticX 0.6.5 and AG2 1.0.0 have now been installed/tested separately; no real model calls or product integration. See [`spikes/agenticx-runtime/README.md`](../spikes/agenticx-runtime/README.md) and [`spikes/ag2-collaboration/README.md`](../spikes/ag2-collaboration/README.md).
**Purpose:** Evaluate Meldwork and AgenticX in distinct roles, compare better candidates for specific layers, and define how to integrate only what passes.

## Thesis

Do not force a single product to be “the winner.” The envisioned system has distinct layers. Meldwork is the strongest current **collaboration behavior reference**; AgenticX is a broad **agent runtime/workspace candidate**. Other systems may be better for durable orchestration, VP memory, workflow execution, or remote workers. The Frankenstein approach is sensible only if we fuse them behind explicit interfaces and maintain one source of truth—not by tightly coupling several application codebases and their databases.

## System layers and best candidates to investigate

| Layer | Candidate(s) | What appears useful | Known mismatch / decision test |
|---|---|---|---|
| **Agent-to-agent collaboration** | [Meldwork](https://github.com/Ryder-Sun/Meldwork); AgenticX collaboration runtime | Meldwork describes auto-discussion, proposals, peer requests/reviews, revisions, delivery owner, evidence/artifact records, human gates. AgenticX describes CEO/meta-agent, group chat, role strategies, delegation and collaboration patterns. | Meldwork is desktop/local Electron with local stores; no server/tenant/project federation. AgenticX collaboration semantics need source/test validation; group chat may not be equivalent to deep iterative work. Pilot both patterns separately, then integrate a common collaboration API, not native internal state. |
| **General agent runtime / tool execution** | [AgenticX](https://github.com/DemonDamon/AgenticX); [OpenClaw](https://github.com/openclaw/openclaw); [Letta](https://github.com/letta-ai/letta) / [Letta Code](https://github.com/letta-ai/letta-code) | AgenticX offers broad Python runtime, server, memory/tools and agent team. OpenClaw offers broad assistant gateway/channel/tool ecosystem. Letta focuses on stateful agents/memory. | AgenticX web Enterprise runtime is separate from Python/Near path and cluster runtime planned. OpenClaw is personal-assistant/gateway shaped, not project workforce. Letta is not canonical project task control. Compare provider, tool permission, non-coding, lifecycle, and export boundaries. |
| **Hierarchical agent flow** | [LangGraph](https://github.com/langchain-ai/langgraph); AgenticX's orchestration engine; [CrewAI](https://github.com/crewAIInc/crewAI) as an alternative to inspect | Graph/role workflows, subagents, checkpoints and interrupt patterns. | Frameworks, not complete collaboration products. Evaluate whether they preserve message-level deliberation and durable role ownership, not only sequential task DAGs. |
| **Durable work / autonomy** | [Temporal](https://github.com/temporalio/temporal); [Hatchet](https://github.com/hatchet-dev/hatchet); LangGraph checkpointing | Long waits, retries, recovery, schedules, worker tasks, observability. | Temporal/Hatchet operate the workflow lifecycle; they do not decide alignment, inter-agent interaction, permissions, or user UX. Compare operations burden and “pause for human, resume correctly.” Do not adopt two workflow engines. |
| **VP/user memory** | [Letta](https://github.com/letta-ai/letta); project-owned preference/decision ledger | Letta may support stateful agent memory; canonical, versioned facts/preferences and project charter should remain in product DB. | “Memory” does not equal knowing the user correctly. Require provenance, correction/retraction, scopes (personal vs project), contradictions, and conformance evaluations. No hidden memory as authoritative truth. |
| **Tool & agent interoperability** | [MCP](https://modelcontextprotocol.io/); [A2A](https://github.com/a2aproject/A2A) | MCP for tools/context; A2A for interoperable agent task exchange. | Neither creates shared project state, authority, durable scheduling, or trustworthy consensus. Start with internal typed contracts; add protocol adapters where needed. |
| **Worker compute / sandboxing** | Custom worker protocol; AgenticX sandbox backends as reference; Slurm adapter for HiperGator | Advertised sandbox support can inform local isolation; remote workers need capability registration, leases, artifacts, logs, cancellation. | HiperGator cluster runtime is not proven by AgenticX. Use official UF policy and a Slurm batch adapter; no agent daemon assumption, no deployment before approval. |
| **Project-first product UI** | Custom web UI; Meldwork/AgenticX/Orkas as interaction references | Project charter, VP conversation, hierarchy, task/artifact ledger, live deliberation, separate user-question inbox. | Existing apps are chat/session-oriented or local desktop. Do not inherit their chat history as the product system of record. |

## Additional role-specific candidates surfaced in the deeper search

These are frameworks or task-workforce projects, not replacements for the whole product; they may outperform a chosen component on their narrow specialty.

- **[AG2](https://github.com/ag2ai/ag2)** (Apache-2.0; ~5k stars, active): its current README describes GroupChat, swarms/nested chats, a Network with conversation/discussion/workflow channels, human-input hooks that can be connected to a queue or web UI, and opt-in persistent-knowledge/context-assembly primitives. This is a serious candidate for the **inter-agent communication/orchestration layer**. It is not a full project workspace or durable product control plane. Its README warns AG2 v1.0 is not a drop-in upgrade from Classic, so pin and test the exact API.
- **[CrewAI](https://github.com/crewAIInc/crewAI)** (MIT; ~60k stars, active): provides role-based autonomous Crews plus event-driven Flows, human-input patterns, memory and checkpointing. Candidate for **orchestration/workflow patterns**, not the project's UI, canonical task ledger, or worker scheduler. CrewAI AMP is a separate commercial control-plane offering; verify OSS/core boundary and license before relying on any AMP claim.
- **[CAMEL-AI OWL](https://github.com/camel-ai/owl)** and [CAMEL](https://github.com/camel-ai/camel) (Apache-2.0; active): describe general multi-agent workforce automation, dynamic interactions, a Web UI and non-coding examples. Useful **workforce/task-completion comparison**. The repository is a framework/demo with benchmark claims, not demonstrated persistent project governance, VP identity, separate user inbox, or multi-machine durable scheduling.
- **[Letta Code](https://github.com/letta-ai/letta-code)** is more interesting for the VP-memory subproblem than the previous summary implied: its README describes stateful agent identity/memory, subagents, and Git-tracked memory (MemFS), with optional sync/remote execution. Still treat this as an **agent identity/memory implementation candidate**, not canonical user/project truth; evaluate privacy, self-editing memory governance, portability, and coding-centric assumptions.

The practical comparison is therefore: **Meldwork for human-readable multi-agent discussion and gates; AG2 for agent-network/message mechanics; AgenticX for broad runtime/platform scope; CrewAI or LangGraph for workflow logic; Letta for persistent agent memory.** These remain role hypotheses, not integration findings. AgenticX and AG2 have isolated Linux smoke/test evidence, but neither has been run with a real model or combined with another component. Meldwork, CrewAI, LangGraph and Letta remain source-level candidates only.

## Distinct roles: Meldwork and AgenticX

### Meldwork: collaboration specialist / behavioral reference

Treat Meldwork as the candidate for **how agents collaborate**, not as the assumed whole platform. Inspect its V4 records and tests for independent proposals, negotiated responsibilities, contribution requests, review, delivery approval, and human gates. Capture the behavioral contracts and user-visible trace. The current app keeps core state locally behind Electron and has no required server/account/tenant/remote conversation store. Forking it to Linux packaging is plausibly much smaller than making it the web/multi-machine control plane. If we reuse code, isolate its collaboration domain behind our own task/artifact/message interfaces and replace its Electron-owned persistence and lifecycle as needed.

### AgenticX: general runtime and platform component candidate

Treat AgenticX as a candidate for **agent execution, role management, memory/tools, Studio API, and possibly desktop workflows**. Its README describes broad capabilities, but also states: Near remote mode is single-server, cluster/HA is planned, Enterprise Gateway is not the Python runtime, Enterprise and Near deployment paths are independent, and the complete safety pipeline is not yet used for every Studio operation. Therefore, first test Python runtime + Studio on a single controlled server; test group collaboration and state recovery. Do not mistake breadth of repository or its Enterprise portal for one integrated production web agent workforce.

### Fusion hypothesis—not yet proven

Potential design: custom project control plane owns users, project charter, hierarchy, events, work, questions, permissions and canonical artifacts; AgenticX runs role-specific agents behind a versioned adapter; Meldwork-inspired protocol supplies rich agent discussion/review; Temporal or Hatchet may run durable workflows if tests justify it; Letta may store explicitly scoped agent memory; worker agents submit work to laptops/HPC through a separate capability/lease API.

**Do not embed Meldwork as a whole desktop app inside AgenticX or share their internal DBs.** Instead, define interfaces and map their behavior:

```text
Product UI / Project VP
       │ project/work/question contracts
Canonical control plane + append-only event history
       ├── VP/orchestrator runtime adapter (AgenticX or other)
       ├── collaboration session service (Meldwork-inspired behavior)
       ├── optional durable workflow engine (one of Temporal/Hatchet/LangGraph checkpointing)
       ├── optional user/agent memory adapter (Letta; not source of truth)
       └── worker registry + lease API
              ├── laptop worker
              └── HiperGator Slurm submission/poll/result adapter (policy-gated)
```

## Additional candidates and their exact fit

- **CrewAI** (`crewAIInc/crewAI`): evaluate as another role/team orchestration framework, not a full product. Compare actual collaborative turn semantics, persistence, human interrupts, provider/tool permissions, and event export with AgenticX/LangGraph. Do not choose based on “crew” terminology.
- **Temporal** vs **Hatchet**: evaluate only if long-running human waits/recovery prove essential. Pick at most one. If LangGraph checkpointing passes the pilot, avoid introducing another workflow service prematurely.
- **Letta**: inspect current active source (`letta-ai/letta` points to `letta-ai/letta-code` for current harness/runtime work) and deploy boundaries. It may be a VP memory implementation, but consider implementing the explicit user-profile ledger in our control plane first.
- **OpenClaw**: consider as a channel/tool gateway or pluggable agent executor, not as hierarchical project system. Its security and tool-host permissions need a specific threat-model review.
- **Dify / n8n / BettaFish**: not shortlisted as central system: Dify is app/workflow builder with additional license conditions; n8n is fair-code/source-available rather than OSI open source; BettaFish is a domain-specific public-opinion research platform. They may still supply connector/workflow/domain patterns, but avoid app-stack duplication.

## Integration contracts required before code fusion

1. **Project contract:** stable project ID, charter revision, goals/non-goals, user/project preference scopes, source-of-truth references.
2. **Work contract:** stable work ID, parent/orchestrator ID, role, dependencies, accepted scope, budget, deadline, permissions, acceptance criteria, verification evidence.
3. **Discussion contract:** message ID, sender/recipient role, work ID, type (proposal/question/challenge/evidence/revision/decision), referenced artifacts/claims, timestamps, and visibility.
4. **Agent-run contract:** adapter + version, model/provider class, capability set, tool permission grant, input-context hash, run lease, state transitions, output artifact refs, cost/usage, and terminal status. Never expose credentials in the contract.
5. **Human-question contract:** explicit blocking/nonblocking designation, options, impact, dependent work IDs, due/priority, answer event and resume semantics. User inbox is distinct from VP chat.
6. **Artifact/evidence contract:** immutable artifact IDs/hashes, provenance, claims/sources, creator role/run, scope and review status.
7. **Worker contract:** machine ID, capabilities/resources, availability/health, permitted workload, job submission/status/cancel/result interface, heartbeat, lease expiration, policy domain. Scheduler chooses eligible workers; agent does not choose a host or bypass constraints.
8. **Security:** scoped service identities between layers, least privilege, per-tool policy, sandbox, audit, idempotency, input/output validation, secret isolation, revocation, version pinning and threat model.

The adapters should be replaceable. The project/task DB and event log should not depend on Meldwork or AgenticX internal storage formats.

## Comparative pilot plan

Run **the same bounded non-coding research scenario** on Meldwork and AgenticX separately, then a narrow adapter/fusion spike only if each passes relevant gates. Use public upstream documentation, a synthetic project charter, no private profile data, no shell/code execution, no external side effects, and fixed source/time/cost limits. See [`first-research-pilot.md`](../pilots/first-research-pilot.md).

### Track A — Meldwork collaboration evaluation

- Pin release/commit; inspect V4 collaboration record schemas/tests and local storage boundary.
- Confirm availability of compatible Apple Silicon macOS environment (published app preview requirement) or explicitly budget a source build/port; do not imply Linux support is established.
- Test proposal → peer critique → contribution/revision → synthesis; record actual discussion, not just final answer.
- Test gate/inbox behavior and trace export. Mark separate web inbox and remote workers `NOT SUPPORTED` if absent.
- Evaluate whether its collaboration domain can be extracted via stable API/record schema without coupling to Electron/local JSON.

### Track B — AgenticX runtime/platform evaluation

- Review pinned release, package lock, dependency notices and `SafetyLayer`/Studio authorization code before install. Do not use `.[all]`; test minimal runtime in isolated environment.
- Run core/Studio server; verify non-coding role hierarchy, group discussion and event observability.
- Test a user decision interruption, process restart, persistence/export, API auth, and sandbox deny cases.
- Test single remote-server support only on controlled hosts. Record cluster/HA as not delivered until proven.
- Verify which Enterprise UI calls which runtime APIs; do not assume their README diagrams are integrated.

### Track C — role-specific alternatives

Before composition, compare the collaboration core of Meldwork against AG2 Network/GroupChat on the same discussion trace; compare AgenticX's CEO/group runtime against CrewAI or LangGraph on hierarchy, interrupts, and restart recovery; inspect Letta's persistent identity/memory against a transparent project-owned memory ledger. CAMEL-AI OWL is a workforce-task comparison if its setup fits the time budget. These are targeted component tests, not a full-product bake-off. Do not run all candidates with live credentials in parallel; pin versions and isolate each environment.

### Track D — composition spike

Only if the relevant candidates pass their critical gates: build a minimal adapter with one canonical project/work/task/event schema; AgenticX handles two researcher roles, Meldwork-inspired or AG2-backed interaction coordinates collaboration, control plane receives questions/results. Letta may be tested as a read-only/optional memory adapter only after its privacy and write policy are explicit. Do not merge source trees or share internal databases. Demonstrate the workflow can be replayed/exported and runtimes can be swapped without losing canonical project state.

## Decision matrix / gates

Score PASS/FAIL/NOT TESTED with evidence. Critical gates cannot be averaged away.

| Gate | Must pass before candidate advances |
|---|---|
| User/project charter alignment | No violations of explicit goals, non-goals, autonomy, or privacy boundaries; corrections recorded with provenance. |
| Meaningful inter-agent collaboration | A concrete evidence-based exchange changes/refines the result or resolves a disagreement; otherwise record absence, not “multi-agent success.” |
| Human question workflow | Question is separated from conversation, dependencies pause correctly, answer resumes correct work only. |
| Durable recovery | Restart/retry preserves accepted state and does not duplicate completed side effects. |
| Permission/security | Every tool path has a known policy; prohibited action denied/logged; credentials inaccessible to agent messages/artifacts. |
| Evidence quality | Final claims trace to sources; human blind review meets rubric; central unsupported claim is fail. |
| Portability/deployment | Actual supported architecture tested; no inferred remote/cluster capability. |
| Data/control ownership | Canonical project state exportable, backed up, and independent of vendor/proprietary service. |
| Operational burden | Setup, patching, model/provider routing, deployment and recovery documented and within owner capacity. |
| License/dependencies | OSI license obligations and dependency/submodule/cloud feature terms understood for intended distribution. |

## Recommendation and next decision

**Do not decide “Meldwork or AgenticX.”** Decide whether to pursue the composition hypothesis after two separate evaluations:

- If Meldwork's group behavior is genuinely superior and extractable, reuse that collaboration design/code only behind adapter contracts.
- If AgenticX's runtime/server is sound and safety/recovery pass, use selected runtime/server components rather than its enterprise product as a whole.
- If one fails, seek a better role-specific component; retain neither just because the prior search selected it.
- If both fail critical gates, build the collaboration/control plane from our contracts and choose only underlying frameworks/services whose operations and licensing are acceptable.

The next execution is Track B source-level safety/API validation, then Track A only if a compatible Mac is available. Actual installs and model calls should wait for explicit environment/provider setup. HiperGator remains outside scope pending UF Research Computing approval.

## Sources

- [Meldwork repository](https://github.com/Ryder-Sun/Meldwork), [architecture](https://github.com/Ryder-Sun/Meldwork/blob/main/architecture.md)
- [AG2](https://github.com/ag2ai/ag2) and [AG2 Network docs](https://docs.ag2.ai/docs/user-guide/network/overview/)
- [CrewAI](https://github.com/crewAIInc/crewAI)
- [CAMEL-AI OWL](https://github.com/camel-ai/owl) and [CAMEL](https://github.com/camel-ai/camel)
- [AgenticX repository](https://github.com/DemonDamon/AgenticX), [README](https://github.com/DemonDamon/AgenticX/blob/main/README.md), [pyproject](https://github.com/DemonDamon/AgenticX/blob/main/pyproject.toml), [lockfile](https://github.com/DemonDamon/AgenticX/blob/main/requirements.lock), [security policy](https://github.com/DemonDamon/AgenticX/blob/main/SECURITY.md)
- [Letta](https://github.com/letta-ai/letta), [Letta Code](https://github.com/letta-ai/letta-code)
- [LangGraph](https://github.com/langchain-ai/langgraph), [Temporal](https://github.com/temporalio/temporal), [Hatchet](https://github.com/hatchet-dev/hatchet), [CrewAI](https://github.com/crewAIInc/crewAI)
- [OpenClaw](https://github.com/openclaw/openclaw), [MCP](https://modelcontextprotocol.io/), [A2A](https://github.com/a2aproject/A2A)
- [UFIT Research Computing scheduling guide](https://docs.rc.ufl.edu/quickstart/scheduling/) (policy reference; obtain case-specific confirmation before execution)
