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

`harness/codex_cli.py` now contains an opt-in adapter candidate for the installed CLI. It treats Codex as an agent runtime (which can use tools), not as a text-completion provider. The adapter requires an injected authorization policy and explicit project/work/run IDs, model, workspace, approval/data/terms/budget references, and sandbox mode. It invokes a fixed argument vector without a shell, uses `--json --ephemeral --ignore-user-config`, restricts sandbox mode to `read-only` or `workspace-write`, passes no additional writable directories, strips ambient API-key/token environment variables, and applies process time/output bounds. Adapter tests use a fake executable. After the user's full approval, one real call succeeded end-to-end through the explicit bundled model `gpt-6.1-sol`, the existing ChatGPT-authenticated Codex CLI, `read-only` sandbox, and an empty disposable Git workspace. It ran through `AgentRouter` → `CodexExecutionFactory` → `OrganizationCoordinator` → the durable canonical receipt bridge, and its synthetic output passed an exact test verifier in a disposable SQLite project. No repository or user-project content was sent. This verifies a synthetic library-level path, not production safety, model quality, or user-project performance.

Codex is connected to the **local library-level coordinator path** for synthetic work, but is not registered in the durable queue, project UI, or a production service. There is no production identity/authorization policy, enabled user-project route, per-run spend enforcement, hardened isolation, or user-project invocation. The CLI exposes workspace sandbox choices but does not provide Northstar identity, project authorization, data policy, capability enforcement, or a controllable monetary cap. Do not enable project work merely because the binary is installed/authenticated. Queue/service integration, restart handling for in-flight CLI jobs, and enforceable project/data/budget policy remain before real project use.

## Verification scope

`harness/tests/test_agent_router.py`, `test_codex_cli.py`, and `test_codex_runtime_bridge.py` cover deterministic routing, fake-process limits/denial, canonical receipt import, ambiguity/no-replay, and coordinator verification. One separately authorized live smoke used only a synthetic prompt and disposable workspace. These tests do not prove production authorization, hardened sandboxing, spend enforcement, model quality, or user-project execution.
