# AgenticX deep dive: candidate comparison for the project VP system

**Upstream inspected:** [`DemonDamon/AgenticX`](https://github.com/DemonDamon/AgenticX), primarily README, `pyproject.toml`, `requirements.lock`, `INSTALL.md`, `SECURITY.md`, repository metadata, and the listed architecture/progress sections. Checked 2026-11-07. **No install or code execution performed.** Everything identified as a capability below is an upstream claim unless explicitly described as source inspection.

## Bottom line

AgenticX is a **more platform-shaped candidate than Meldwork** for our web/server/multi-role direction, and it deserves a serious technical pilot. It is not yet a proven better foundation. Its largest advantage is breadth in one repository: general Python runtime/SDK, Studio server, multi-agent roles/group chat, memory, tools, sandbox abstractions, observability/evaluation, and a separate Near desktop. Its central weakness is that the repo's own architecture describes important pieces as **separate or planned**: Near defaults to a local server; remote mode is one server; cluster/HA runtime is planned; Enterprise's web portal/gateway is independent from the Python runtime and is not itself a full agent runtime. The README also says Studio does not yet route every operation through one complete safety pipeline.

**Recommendation:** include AgenticX in a non-coding comparative pilot. Do not replace the project control plane or declare it the base until we demonstrate the actual runtime, web surface, pause/resume, collaboration, authz, and deployment path together. Meldwork remains valuable as the strongest observed agent-discussion UX reference; AgenticX is the broader server/platform candidate.

## What is materially promising

- **Non-coding generality:** its advertised roles, knowledge tools, group chat, skills, document ingestion, channels, retrieval and data integrations extend beyond coding.
- **Hierarchy:** README describes a Meta-Agent/“CEO” dispatcher, agent-team management, dynamic subagent orchestration, concurrency limits, session isolation, and user-directed/meta-routed/round-robin group-chat strategies. This resembles the VP→orchestrator→specialist hierarchy concept more than a flat swarm.
- **Inter-agent work:** advertised delegation, role-playing, conversation management, task locks, and collaboration metrics make it a plausible place to examine whether agents can work together, not just appear in a multi-pane interface.
- **Runtime/control API:** Studio is described as FastAPI REST + SSE over sessions, messages, avatars, group chat, MCP, skills, knowledge, and execution. SDK/CLI can be used without Near Desktop. This could be more adaptable to a custom project UI than a desktop-only product.
- **Persistent memory/state:** it advertises hierarchical/core/episodic/semantic memory and project state machines; storage options include local files/SQLite and optional Redis/PostgreSQL modules. Exact default product semantics need verification.
- **Monitoring and evaluation:** it advertises trace/span monitoring, trajectory analysis and evaluation components, useful for testing hierarchy/alignment rather than judging by transcript impression.
- **Portability:** core package declares Python >=3.10 and OS-independent. Near desktop is Electron. Its setup docs include Linux system dependencies. This is much less Apple-dependent than Meldwork.
- **License:** root project says Apache-2.0, suitable for forking with license obligations; inspect bundled/submodule/dependency licenses separately.

## Critical limits for our specific product

1. **No complete remote cluster yet.** README says Near has optional **single remote `agx serve`** mode; cluster/HA runtime remains planned. It does not demonstrate capability-aware workers across two laptops and HiperGator.
2. **Web surface and agent runtime are not one shipped end-to-end path.** Enterprise Portal/Admin/Go Gateway has authentication, quotas, policy/audit and model routing, but the README explicitly says this gateway is not the full Agent Runtime and that Enterprise and Near deployment paths are independent. The Enterprise Edge Agent is an MVP, and Cluster Runtime has not started. Do not assume the enterprise website fronts the multi-agent Python runtime.
3. **Safety pipeline is incomplete by its own account.** Safety components exist, but README says Studio does not yet route every operation through the complete `SafetyLayer` pipeline. That is a blocker for unattended autonomous operation until specific paths are mapped/tested.
4. **Persistence is not yet the durable work ledger we need.** README says Studio defaults to `~/.agenticx/sessions` and optionally Redis; a database session module is separate and not the default Studio path. A claimed coding project state machine should not be assumed to cover arbitrary research/business tasks or the VP's whole project record.
5. **Alignment/VP features are not verified.** Memory features do not prove durable user preference provenance, correction, conflict handling, or a distinct user-question inbox.
6. **Rapid, broad change surface.** PyPI package describes itself as Beta. The repository contains core, desktop, enterprise, mobile, research, ML, data, and agent runtime surfaces. Active commits and extensive tests are positive signals, but code breadth creates integration and security review burden.
7. **Supply-chain signal to investigate.** README itself warns that LiteLLM 1.82.7/1.82.8 were malicious and removed. The checked `requirements.lock` pins LiteLLM 1.80.11, not those cited malicious releases, but the Python constraint in `pyproject.toml` is broad (`>=1.50,<2`). Before any install, inspect lock reproducibility, dependency provenance, release pipeline and current advisories; install only from a reviewed pinned lock in an isolated environment.

## Relative to Meldwork

| Dimension | Meldwork | AgenticX |
|---|---|---|
| Strongest point | Explicit multi-agent discussion/critique, human gates, task/run evidence | Breadth: general server/runtime, meta-agent, memory/tools, SDK, desktop and separate enterprise product |
| User interaction | Strong collaboration experience, but Electron-local | Multi-pane/group-chat UI and REST/SSE server; web enterprise surface is not yet integrated with Python runtime |
| Web/server potential | Significant re-architecture from local preload/local stores | More server primitives already exist; still no demonstrated integrated web+agent product |
| Multi-machine | No account/tenant/remote conversation store in architecture; remote agents via connectors not a worker cluster | Single remote server supported; cluster/HA explicitly planned |
| Safety evidence | Explicit local trust boundaries, but local CLI access/read-only enforcement partly delegated to provider CLIs | Safety primitives exist, complete Studio pipeline explicitly incomplete |
| Verdict | Best collaboration behavior reference | Stronger architecture candidate to pilot, not yet a safe/complete base |

## Pilot that would decide if AgenticX advances

Run the same bounded non-coding research question as in [`first-research-pilot.md`](../pilots/first-research-pilot.md). Before using any model credentials or private profile, in a clean disposable environment:

1. Pin a release/commit and install from a reviewed lock; inspect licenses and dependency advisories first.
2. Bring up only the Python core/Studio path (not the broad optional extras or enterprise stack unless needed); use public, non-sensitive sources.
3. Create one VP/research-lead role, two independent researcher roles, and one critic. Test whether role ownership, agent discussion, challenge/revision, and synthesis are real and inspectable.
4. Test that a consequential question is stored separately, dependent work pauses, unrelated work proceeds, and the human answer resumes the right step.
5. Restart the server mid-run. Confirm accepted state and outputs recover with no duplicate side effects.
6. Probe each tool/path against its advertised safety/permission controls. A documented incomplete safety pipeline means no unrestricted autonomous tools.
7. Test the available remote single-server mode only on controlled hosts. Do not infer cluster scheduling or HPC suitability from that result.

Advance only if the same critical gates as the first pilot pass: source provenance, charter alignment, permission boundary, inbox pause/resume, boundedness, recovery, and independent human quality review. Record missing features as gaps, not passing behavior.

## Decision

**AgenticX is the best next platform candidate to evaluate; Meldwork is still the best collaboration reference.** If AgenticX passes the non-coding pilot but not remote worker needs, it may still supply a reusable runtime/Studio component behind a custom project control plane. If it fails safety, recoverability, or role-collaboration gates, keep only isolated components/patterns. No HiperGator integration is approved or technically verified by this review.

## Sources

- Repository/README: https://github.com/DemonDamon/AgenticX
- Architecture/progress (same README): system architecture, core features, near desktop, AgenticX Enterprise, safety/sandbox, storage, and development-progress sections.
- Packaging/dependencies: https://github.com/DemonDamon/AgenticX/blob/main/pyproject.toml ; https://github.com/DemonDamon/AgenticX/blob/main/requirements.lock
- Install guide: https://github.com/DemonDamon/AgenticX/blob/main/INSTALL.md
- Security policy: https://github.com/DemonDamon/AgenticX/blob/main/SECURITY.md
- Baseline pilot protocol: [`first-research-pilot.md`](../pilots/first-research-pilot.md)
