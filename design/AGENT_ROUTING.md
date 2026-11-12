# Agent routing boundary

Status: offline deterministic routing contract. It does not enable a worker, invoke a model, authorize filesystem access, or grant tools.

## Adapted router pattern

The Pi harness has a useful deterministic router shape: explicit `matched`, `ambiguous`, and `unknown` outcomes; bounded, reviewable route tables; and tests proving ambiguous or unsupported intent does not silently choose an action. Its skill router classifies chat prompts and injects skill/task guidance into a Pi session. That is not an authorization boundary and is not the right input contract for project execution.

Northstar instead routes a **canonical, user-approved work item** by its explicit schema `workType`. It never infers a route from the title, objective, chat, repository text, or model output. The small implementation in `harness/agent_router.py` is original project code inspired by the pattern, not a copied Pi extension.

## Resolution

1. The work item must be canonically `ready` and carry a supported `workType`.
2. A server-owned `(projectId, workType)` assignment takes precedence only if it selects one of that type's configured candidates.
3. Without an explicit project assignment, zero enabled candidates resolves to `unknown`; one resolves to `matched`; multiple resolve to `ambiguous`.
4. Only a matched decision can resolve to a configured `AgentRouteProfile`. There is no implicit default, keyword fallback, or automatic choice among agents.

The agent route profile identifies an agent role, adapter, adapter version, endpoint class, supported work types, and enabled state. It does not select a model; `ModelRouter` separately resolves provider/model by project and role. Neither router authorizes capabilities or external effects. Project membership, user approval, capability enforcement, provider/data/budget policy, canonical run lifecycle, and trusted callback registration remain separate mandatory gates.

## Codex status

There is no enabled Codex route, Codex adapter, provider call, or project worker in the prototype. The installed `codex` CLI is a local executable with its own authentication and tool runtime; it is not interchangeable with a text-completion provider. A later adapter must be a separately reviewed, explicitly configured execution boundary with a clean project-scoped workspace, approved model/account and data scope, enforceable spend/usage limits, bounded execution, durable run/recovery handling, and tests against a fake executable. `codex exec --help` exposes workspace sandbox choices but does not provide Northstar's identity, data-policy, authorization, per-project capability, or cost-enforcement guarantees. Do not route a project to Codex merely because the binary is installed or authenticated.

## Verification scope

`harness/tests/test_agent_router.py` uses in-memory profile fixtures only. It proves exact work-type routing, project-specific disambiguation, and closed `ambiguous`/`unknown` outcomes. It does not prove authorization, sandboxing, model quality, or a working Codex connector.
