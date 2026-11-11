# Offline project-first UI prototype

This is a framework-neutral, loopback-only project/inbox demo for issue #11. It seeds one synthetic example, and also lets the local user create projects and proposed work that persist in the canonical SQLite event store; inbox answers/rejections persist and replay. Newly created projects are not synthetic-fixture-validated and must still be treated as test data only. VP chat and model assignments are illustrative and do not call a model. There are no provider SDKs, credentials, agent execution, web access, or external tools. Do not enter private or sensitive data.

## Run locally

From the repository root (install the existing schema dependency once if it is not already available):

```bash
python3 -m pip install -r contracts/requirements-test.txt
python3 -m ui.prototype.server
```

Open <http://127.0.0.1:4173>. Stop the server with Ctrl-C. The service hard-binds to IPv4 loopback, accepts only its local host/origin, disables CORS, bounds JSON requests, and requires a local-demo header for writes. Project reads and work creation pass through project application checks; inbox commands use `InboxApplication`. Both use the same fixed fake principal, which is not authentication. This is not a hardened or deployable service. Do not expose it through a LAN, tunnel, reverse proxy, or public host.

The SQLite database persists at `$XDG_STATE_HOME/northstar-ai-workspace-prototype/canonical.sqlite3`, or `~/.local/state/northstar-ai-workspace-prototype/canonical.sqlite3` when `XDG_STATE_HOME` is unset. Project directories are created under `~/AI-Projects/<project-id>` (or the configured `workspace_root` in tests), with mode 0700; canonical history remains in the SQLite state directory. No agents can read/write project folders yet. Stop the server before moving or deleting the database. Startup creates the synthetic Northstar example if absent and preserves other locally created projects.

## Screens represented

- Project home with charter summary, work status, recent project activity, and a visibly separate inbox preview.
- Dependency-aware work list read from canonical project state; users can add proposed work, but there is no work approval or execution yet.
- Create and switch between local canonical projects. New project and work records persist across server restart.
- User-initiated VP conversation, clearly marked as an unconnected local-Qwen profile.
- Separate inbox with impact, explicit options, affected work, persisted answer/reject commands, and resolved history. Clarification is visibly unavailable because its command is not implemented.
- Agent/model profiles illustrating local VP and potential remote orchestrator/specialist assignments. Codex terms and provider connections are intentionally marked unreviewed/unavailable.
- Project charter plus read-only run, evidence, and artifact metadata from scoped canonical endpoints; the seeded demo starts with no runs/evidence/artifacts, and source/artifact bytes are not served by these lists.

The UI is plain HTML/CSS/JavaScript; a small Python standard-library HTTP adapter serves it and exposes local project creation/reads, proposed work creation, inbox decisions, and read-only run/evidence/artifact endpoints under `/api/v1/`. This validates the workflow without selecting React, Svelte, or a production backend framework. It has keyboard-operable navigation and question controls, visible focus styles, semantic landmarks/headings, responsive layout, reduced-motion handling, and no external fonts or assets. This has not received a browser-matrix or independent accessibility review.

## Checks

```bash
python3 -m unittest discover -s ui/prototype/tests -v
node --check ui/prototype/app.js
```

The HTTP tests exercise answer/reject persistence across runtime restart, safe identical retries, stale ETag denial, cross-project denial, host/origin/write-header restrictions, invalid-command rejection, and request bounds. Headless Chromium exercised answering, rejecting, updating affected-work/inbox state, resolved history, and persistence after page reload. This is not a broad browser matrix or independent accessibility/security review. The next gate is a human workflow walkthrough, keyboard-only/accessibility review, and authenticated identity/authorization design before any remotely reachable service.
