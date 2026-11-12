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

`harness/codex_cli.py` now contains an opt-in adapter candidate for the installed CLI. It treats Codex as an agent runtime (which can use tools), not as a text-completion provider. The adapter requires an injected authorization policy and explicit project/work/run IDs, model, workspace, approval/data/terms/budget references, and sandbox mode. It invokes a fixed argument vector without a shell, uses `--json --ephemeral --ignore-user-config`, restricts sandbox mode to `read-only` or `workspace-write`, passes no additional writable directories, strips ambient API-key/token environment variables, and applies process time/output bounds. Tests use a fake executable; no real Codex command was invoked.

This does **not** connect Codex to the project coordinator or queue. There is no production policy implementation, enabled Codex route, authenticated/approved `CODEX_HOME`, model/account terms decision, canonical run/receipt integration, per-run spend enforcement, hardened isolation, or real provider call. The CLI exposes workspace sandbox choices but does not provide Northstar identity, project authorization, data policy, capability enforcement, or a controllable monetary cap. Do not enable or route project work to Codex merely because the binary is installed/authenticated. A real integration still requires approved provider/account terms, model, data scope, enforceable budget, an isolated project workspace, and restart/recovery wiring.

## Verification scope

`harness/tests/test_agent_router.py` uses in-memory profile fixtures only. It proves exact work-type routing, project-specific disambiguation, and closed `ambiguous`/`unknown` outcomes. It does not prove authorization, sandboxing, model quality, or a working Codex connector.
