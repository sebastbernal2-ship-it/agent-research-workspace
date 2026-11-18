# Local project UI prototype

This is a loopback-only project/inbox prototype for issue #11. The React front end uses the shadcn `dashboard-01` visual baseline (source pin and MIT attribution: [`../web/README.md`](../web/README.md)). Project creation first asks **New** or **Existing**. Existing-project onboarding inspects only a user-selected local Git root: at most eight tracked Markdown/RST files (96 KiB total), with code, ignored/untracked files, data, credential-like paths, and repository working-tree status excluded. It makes no network or model calls. The app shows the inspected evidence, then records a five-part sequential alignment interview (vision, current state, ownership/scope, unresolved choices, success/approval preferences) in the canonical project history. Interview answers are input, not execution approval.

The task board uses five compact kanban columns (Proposed, Ready, In progress, Waiting, Done); stopped items are tucked into a collapsed section. Cards open a detail popup, where proposed items can be approved individually. Approval moves an item to Ready but does not start it. Open inbox questions can be discussed back and forth with Codex using that question's options and affected work; the discussion never records an answer, and the user still decides separately. Project-wide auto-run is visibly off and disabled because provider/data policy, enforceable spend limits, and hardened worker isolation are not configured. Chat has a separate server-side, chat-only Pi/OpenAI Codex bridge: it uses Pi's currently active local Codex sign-in (it does not read the separate Pi-harness named-account store, copy credentials, switch accounts, or expose secrets to the browser). A chat request starts only when the user presses Send. It sends the typed message and recent chat turns together with a bounded snapshot of the selected project's canonical charter, work items, open questions, recorded decisions, and automation state. Raw repository files, inspection excerpts, and source artifacts are never attached; canonical project text can reflect prior alignment answers. The bridge has no tools or ability to change the project. Chat is ephemeral in the browser and is not stored in canonical history. The adapter has a local request-count limit, not a dollar-spend guarantee; provider usage may apply. No live model request is made by tests or startup. The auth-status indicator checks only whether Pi's auth file exists, not whether the credential is valid. Consequently, alignment is repo-grounded but deterministic—not model-led or dynamically adaptive—and this prototype does not draft an AI-authored initial board, execute tasks, or report agent progress. Use test data only; the app is unauthenticated and must not be exposed or used with sensitive information.

## Run locally

Shortcut: `bin/northstar` checks prerequisites (a worker CLI, the built UI), builds the UI if needed, and starts the app; `bin/northstar --check` only reports readiness.

From the repository root:

```bash
python3 -m pip install -r contracts/requirements-test.txt
cd ui/web && npm ci && npm run build
cd ../..
python3 -m ui.prototype.server
```

Open <http://127.0.0.1:4173>; stop with Ctrl-C. The service hard-binds to IPv4 loopback, disables CORS, bounds JSON requests, and requires a demo write header. It uses a fixed fake principal, not authentication. Do not expose it through a LAN, tunnel, reverse proxy, or public host.

Canonical SQLite state is stored at `$XDG_STATE_HOME/northstar-ai-workspace-prototype/canonical.sqlite3`, or `~/.local/state/northstar-ai-workspace-prototype/canonical.sqlite3`. Per-project workspace directories are created under `~/AI-Projects/<project-id>` with mode 0700. The chat worker runs from a disposable empty temporary directory with tools, MCP, extensions, skills, context files, and sessions disabled; it is not an OS sandbox. To use chat, sign into OpenAI Codex through Pi so its normal `~/.pi/agent/auth.json` is available (or set `PI_CODING_AGENT_DIR` to a Pi agent directory). The prototype does not consume the Pi-harness account-switching store. Stop the service before moving or deleting its data.

For front-end development, start the Python service on 4173 and run `cd ui/web && npm run dev`; Vite binds only to 127.0.0.1:5173 and proxies the local API.

## Validation

```bash
python3 -m unittest discover -s ui/prototype/tests -v
cd ui/web && npm ci && npm run build
```

The Python HTTP tests cover create/list/reopen persistence, bounded repository inspection, sequential canonical alignment questions and restart recovery, per-item approval, nested work, inbox decisions, cross-project denial, loopback host/origin/write-header restrictions, request bounds, and built-asset serving/traversal rejection. A previous headless Chromium smoke exercised basic project switching and work creation. There is no broad browser/accessibility review, real authentication, production isolation, model-led interviewing, or agent execution. Do not treat this prototype as safe for sensitive data.


## VP, workers, and auto-run

After the alignment interview (new projects get the same five-part interview as existing ones), the project VP drafts a proposed task board from the goal and your recorded answers (`Draft board with VP` retries it). Approving a task makes it ready; a background loop then runs ready tasks one at a time in dependency order: an orchestrator decides whether to split the task, and workers do the work with their own tool loops in the project workspace (existing repos get an isolated `northstar/<project>` git worktree; your checkout is never edited). A worker that needs you opens an inbox question and the task waits. The per-project **Auto-run** switch lets the VP approve and run every proposed task. The synthetic demo project never runs agents.

Workers are headless Claude Code or Codex CLIs using their own sign-ins: `NORTHSTAR_RUNNER=claude|codex|pi` (default: Claude, then Codex). `NORTHSTAR_WORKERS=off` disables the background loop. These CLIs are not an OS sandbox; use test or non-sensitive projects. **Red mode** (VP chat on a local model) needs a loopback OpenAI-compatible server: set `NORTHSTAR_RED_ENDPOINT` (e.g. `http://127.0.0.1:8080/v1`) and `NORTHSTAR_RED_MODEL`.

**Questions are a chat.** The VP asks each question as the first chat bubble, with its options as tap-to-choose chips and a box to type anything. The VP judges each message: a question or side remark ("btw") is answered in place (reading the repo if needed) and never recorded; a message that settles the question yields a *proposal*, a concise consolidation of everything you established in the discussion, and you confirm it with one button. Only that consolidation is recorded as the answer; the raw thread is not copied into it, but it stays in the permanent record. All turns are immutable, hash-chained messages in the project record (a hidden `work.project-chat` item holds project-level chat). Each model call gets a fresh, bounded context built from that record; the browser history is ignored, and long threads are folded into a saved `SUMMARY:` message.

**Project memory.** What you say in any chat (question discussions or the Chat tab) is distilled into short durable notes. The VP periodically curates these, together with your recorded answers, into a structured memory (vision, decisions, definitions, constraints, postponed, still open, changed-your-mind): duplicates merged, superseded statements replaced, chit-chat dropped, nothing invented. Every VP call (chat, board drafting, orchestrators, workers) receives the curated memory plus not-yet-merged notes. The Overview shows "What the VP remembers"; correct it by telling the VP in chat. Curation runs after you record an answer and whenever 6+ notes accumulate; it is best effort and never blocks you.

**Agent collaboration.** A task receives the reports of the tasks it depends on. When a task has acceptance criteria, an independent reviewer agent checks the worker's report against them and may send it back for exactly one revision (the challenge appears in Activity); `NORTHSTAR_REVIEW=off` disables this. Independent ready tasks run in parallel (default 2, `NORTHSTAR_PARALLEL=1..4`); projects backed by an existing repo stay sequential because they share one worktree. All event-store writes are serialized behind one lock.


## Same harness, any model (worker profiles)

The workers are the vendors' own agent CLIs, so every model gets the same native tool loop instead of a custom harness. A *profile* is one CLI pointed at one provider: Codex natively, or **Claude Code on any Anthropic-compatible endpoint** (e.g. DeepSeek, Qwen via Alibaba's service, or a local model behind a translating proxy such as claude-code-router). Copy `workers.example.json` to `~/.config/northstar/workers.json` and route roles (`vp`, `orchestrator`, `worker`, `reviewer`) to profiles. API keys are referenced by environment-variable name (`secretEnv`) and never stored in the file; only `ANTHROPIC_/CLAUDE_CODE_/OPENAI_/CODEX_` variables can be overridden. A profile whose key variable is unset is skipped and routing falls back to an available one. Without the file, `NORTHSTAR_RUNNER` applies as before. No extra sandbox is layered on top: each CLI runs with its own native permission model.

Verified live: VP calls on Codex with worker calls on Claude Code. DeepSeek and Qwen profiles are covered by unit tests (env handling, key never on argv, unsafe overrides rejected) but have **not** been run against those providers (no keys here); check each provider's current endpoint and model names first.

## Integrations: external tools for a project's workers

A project can declare MCP servers its workers may call (a research database, a literature service) without Northstar
knowing anything about them. The same declaration is passed to whichever harness runs the worker: Claude Code via
`--mcp-config`, Codex via `-c mcp_servers.<name>...` (verified live with both).

```bash
curl -X POST localhost:4173/api/v1/projects/$PID/integrations -H 'Content-Type: application/json' -H 'X-Local-Prototype: 1' -d '{
  "name": "lf",
  "spec": {"kind": "mcp-stdio", "command": ["python3", "-m", "lf_mcp"], "cwd": "/abs/path",
           "env": {"LF_SCHEMA": "latent_foundry_clean"}, "secretEnv": {"LF_PASSWORD": "MY_LF_PASSWORD"},
           "description": "Latent Foundry research web: search, verify, fold back"}}'
```

- `command` is an argv list, never a shell string. `kind` is `mcp-stdio` or `mcp-http` (https, or http on localhost).
- `env` holds non-secret values. Secrets are never stored: `secretEnv` maps a variable to the NAME of an environment
  variable set when the app starts. An integration missing its secret is skipped, and `GET .../integrations` shows `ready: false`.
- Codex cannot carry secrets (they would appear in process arguments), so secret-bearing integrations are used by Claude Code workers only.
- Declaring a server approves its tools to run without a prompt. Declare only servers you trust.
- Workers are told which integrations exist and are asked to cite what they returned.
