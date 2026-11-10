# Offline project-first UI prototype

This is a framework-neutral, local-only project/inbox vertical slice for issue #11. The project, work, and inbox screens read one synthetic project from the canonical SQLite event store; answer and reject commands are persisted and replayed. VP chat and model assignments are still illustrative and do not call a model. There are no provider SDKs, credentials, tools, external assets, or real user data. Do not enter private data.

## Run locally

From the repository root (install the existing schema dependency once if it is not already available):

```bash
python3 -m pip install -r contracts/requirements-test.txt
python3 -m ui.prototype.server
```

Open <http://127.0.0.1:4173>. Stop the server with Ctrl-C. The service hard-binds to IPv4 loopback, accepts only its local host/origin, serves only the seeded synthetic project, disables CORS, bounds JSON requests, and requires a local-demo header for writes. Project summary reads now pass through `harness.ProjectApplication`, while inbox commands use `InboxApplication`; both receive the same fixed fake principal and demo allowlist. It has **no real authentication** and is not a hardened or deployable service. Do not expose it through a LAN, tunnel, reverse proxy, or public host.

The SQLite database persists at `$XDG_STATE_HOME/northstar-ai-workspace-prototype/canonical.sqlite3`, or `~/.local/state/northstar-ai-workspace-prototype/canonical.sqlite3` when `XDG_STATE_HOME` is unset. Delete that demo directory while the server is stopped to reset the synthetic project. Startup refuses a database whose existing project is not the expected synthetic fixture.

## Screens represented

- Project home with charter summary, work status, recent project activity, and a visibly separate inbox preview.
- Dependency-aware work list read from canonical project state (read-only in this slice).
- User-initiated VP conversation, clearly marked as an unconnected local-Qwen profile.
- Separate inbox with impact, explicit options, affected work, persisted answer/reject commands, and resolved history. Clarification is visibly unavailable because its command is not implemented.
- Agent/model profiles illustrating local VP and potential remote orchestrator/specialist assignments. Codex terms and provider connections are intentionally marked unreviewed/unavailable.
- Project charter from canonical records; evidence view accurately reports that no evidence/artifact records exist yet.

The UI is plain HTML/CSS/JavaScript; a small Python standard-library HTTP adapter serves it and exposes local project/inbox plus read-only run, evidence, and artifact endpoints under `/api/v1/`. This validates the workflow without selecting React, Svelte, or a production backend framework. It has keyboard-operable navigation and question controls, visible focus styles, semantic landmarks/headings, responsive layout, reduced-motion handling, and no external fonts or assets. This has not received a browser-matrix or independent accessibility review.

## Checks

```bash
python3 -m unittest discover -s ui/prototype/tests -v
node --check ui/prototype/app.js
```

The HTTP tests exercise answer/reject persistence across runtime restart, safe identical retries, stale ETag denial, cross-project denial, host/origin/write-header restrictions, invalid-command rejection, and request bounds. Headless Chromium exercised answering, rejecting, updating affected-work/inbox state, resolved history, and persistence after page reload. This is not a broad browser matrix or independent accessibility/security review. The next gate is a human workflow walkthrough, keyboard-only/accessibility review, and authenticated identity/authorization design before any remotely reachable service.
