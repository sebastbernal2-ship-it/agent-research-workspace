# Codex CLI connectivity smoke

Date: 2026-11-12 UTC  
Authorization: user explicitly granted full approval in this conversation.

## What ran

- Codex CLI `0.166.0`, signed in through the user's existing ChatGPT account; no API key was passed.
- Explicit model `gpt-6.1-sol`, selected from the locally bundled `codex debug models --bundled` catalog (priority 1).
- Two successful CLI invocations total, both with `--sandbox read-only`, `--ephemeral`, `--ignore-user-config`, bounded wall time/output, and a temporary empty Git workspace.
- First invocation checked CLI transport with a synthetic exact-response prompt.
- Second invocation used only synthetic test records and a synthetic prompt. It exercised `AgentRouter` → `CodexExecutionFactory` → `OrganizationCoordinator` → Codex CLI → canonical adapter receipt import → exact synthetic verifier. Observed run status `succeeded`, work status `completed`, receipt state `imported`, and response `CODEX_CANONICAL_SMOKE_OK`.
- A temporary Git workspace and canonical SQLite database were removed after each check. No repository files, predictor checkout, user project data, or credential material was included in the prompt or passed through the child environment; the CLI used its existing ChatGPT authentication for these provider calls.

## Limits

This verifies a local CLI call and one synthetic library-level harness path. It is not a quality evaluation, project-data authorization, production security review, durable queue integration, or production Codex service. No per-run monetary amount was reported or enforced; the calls used the existing ChatGPT sign-in and remained subject to that account's usage limits. A separate synthetic queue-to-verifier path now passes with a fake Codex executable, but the live call was not made through the queue. The project still needs a production policy, a supervised worker service, worktree lifecycle and restart/cancellation handling before Codex can execute user-project work.

The checked-in offline tests use a fake executable and cover fixed arguments, ambient credential stripping, approval denial, output bounds, canonical receipt/replay, ambiguous no-replay, queue descriptor matching, and verifier-gated completion.
