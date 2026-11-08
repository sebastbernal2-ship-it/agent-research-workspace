# Model/provider routing contract

Status: design for an offline prototype. No provider credentials, provider SDKs, network calls, Codex account integration, or real-model evaluation are enabled. Provider assignments are a user/project choice, not an agent capability or authority grant.

## Intent

Allow the user to select different providers/models by project and agent role—for example, a local Qwen-family VP and remote Codex/DeepSeek orchestrators—without binding project data, chat UX, or work state to one vendor. Local and remote models are both treated as fallible and untrusted for authorization.

## Routing contract

Every model invocation is resolved by a server-side routing table. The offline prototype currently implements only project-and-role assignment and configured role defaults; a user-approved per-run override is a later feature that must be authenticated and validated server-side.

1. (Future) An explicit user-approved per-run selection, if present; otherwise
2. a project-and-role assignment; otherwise
3. a configured role default; otherwise fail closed.

No implicit provider fallback. Agents cannot change their own provider/model, bypass the assigned route, authorize a new remote destination, or raise spending/data limits. A later UI can present safe choices, but the server validates them.

A resolved invocation contains:

- `projectId`, `workId`, `runId`, stable `agentId` and role;
- selected `profileId`, provider ID, exact model ID/revision, adapter name/version;
- endpoint class (`local`/`remote`), routing-config revision, and policy/approval reference;
- generation limits and requested capabilities (tool access is separate and not implied by model selection);
- request context hash, start/end time, status, and provider usage/cost when reported.

Do not put API keys, OAuth tokens, raw prompts, private profile data, or hidden provider payloads in routing configuration or canonical event records. Secrets are injected by a protected provider credential adapter, never sent to the browser or persisted in ordinary project events. Persist enough non-secret metadata to reproduce which profile actually ran; missing or unverifiable model identity must be explicit.

## Provider adapter boundary

The domain layer sends a normalized request to a provider adapter and receives normalized text/content blocks, finish status, provider request ID, and usage. The adapter owns provider-specific protocol/auth and error mapping. It must support bounded request/response sizes, timeout and cancellation, and must not silently retry non-idempotent tool actions. Streaming is optional and must preserve final accounting/cancellation semantics. No adapter may run tools merely because the model asks; tool execution remains under the capability/control-plane boundary.

Provider-specific adapters are separate candidates. Initial profiles to consider:

- **Local Qwen variant:** Ollama or another local OpenAI-compatible serving runtime is a candidate, not selected. Record exact weights/quantization, model license, runtime, and hardware. “Local” avoids remote inference charges but not hardware/electricity or model-license obligations. Abliterated variants may alter refusal behavior; do not use model refusals as the security boundary.
- **DeepSeek:** remote provider profile; network transmission and usage fees require an explicit provider/model/data approval and budget. Use documented supported API auth only.
- **Codex:** potential coding specialist/orchestrator profile. First verify the official supported API/SDK/account flow and terms for unattended service use, per-agent use, and token handling. Do not copy browser/session credentials, scrape private endpoints, share personal OAuth tokens across workers, or assume a ChatGPT/Codex subscription grants API/service use.
- **Deterministic mock:** first and only adapter used in this prototype. It is replayable and makes no network calls.

## Privacy, budget, and fallback gates

- Treat local and remote as distinct data destinations. Remote calls are denied until the user approves the provider/model, data categories, budget/spend limit, and retention assumptions. Per-run context is minimized.
- Routing is not permission. Capability grants separately control tools/effects, and model choice never expands a grant.
- Enforce per-run/project input/output/cost/deadline limits outside model output. Unknown provider pricing/usage is not assumed to be zero; fail or require approval if a configured limit cannot be evaluated.
- Fallback is disabled by default. A fallback requires explicit compatible data/retention policy, budget, model behavior, and approval; log the actual fallback. Never silently send private data to a different provider.
- Record provider/model/profile/config revision and reported usage in the agent-run projection. Do not record secrets or chain-of-thought. The exact event-schema changes are deferred until the request/result types are tested.

## Current decision

The offline `harness/model_routing.py` prototype implements role defaults, project/role overrides, normalized provider protocol types, input/output bounds, exact selected-model identity checks, and no implicit failover. Its invocation-policy dependency is only an interface; the tests use fake allow/deny implementations and this is not an authorization mechanism. Implement only these framework-neutral types and deterministic mocks. Do not install provider SDKs or create a web endpoint. Real providers require separate policy/terms review and explicit user approval; model quality requires the blind, bounded pilot documented in `pilots/first-research-pilot.md`.
