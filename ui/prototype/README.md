# Local project UI prototype

This is a loopback-only project/inbox prototype for issue #11. The React front end uses the shadcn `dashboard-01` visual baseline (source pin and MIT attribution: [`../web/README.md`](../web/README.md)). Project creation first asks **New** or **Existing**. Existing-project onboarding inspects only a user-selected local Git root: at most eight tracked Markdown/RST files (96 KiB total), with code, ignored/untracked files, data, credential-like paths, and repository working-tree status excluded. It makes no network or model calls. The app shows the inspected evidence, then records a five-part sequential alignment interview (vision, current state, ownership/scope, unresolved choices, success/approval preferences) in the canonical project history. Interview answers are input, not execution approval.

The task board uses five compact kanban columns (Proposed, Ready, In progress, Waiting, Done); stopped items are tucked into a collapsed section. Cards open a detail popup, where proposed items can be approved individually. Approval moves an item to Ready but does not start it. Project-wide auto-run is visibly off and disabled because provider/data policy, enforceable spend limits, and hardened worker isolation are not configured. The Chat tab is also intentionally disabled: there is no project-scoped AI service. Consequently, alignment is repo-grounded but deterministic—not model-led or dynamically adaptive—and this prototype does not draft an AI-authored initial board, execute tasks, or report agent progress. Users can add proposed outcomes manually. Use test data only; do not expose the unauthenticated prototype or enter sensitive information.

## Run locally

From the repository root:

```bash
python3 -m pip install -r contracts/requirements-test.txt
cd ui/web && npm ci && npm run build
cd ../..
python3 -m ui.prototype.server
```

Open <http://127.0.0.1:4173>; stop with Ctrl-C. The service hard-binds to IPv4 loopback, disables CORS, bounds JSON requests, and requires a demo write header. It uses a fixed fake principal, not authentication. Do not expose it through a LAN, tunnel, reverse proxy, or public host.

Canonical SQLite state is stored at `$XDG_STATE_HOME/northstar-ai-workspace-prototype/canonical.sqlite3`, or `~/.local/state/northstar-ai-workspace-prototype/canonical.sqlite3`. Per-project workspace directories are created under `~/AI-Projects/<project-id>` with mode 0700. The prototype does not give agents access to those folders yet. Stop the service before moving or deleting its data.

For front-end development, start the Python service on 4173 and run `cd ui/web && npm run dev`; Vite binds only to 127.0.0.1:5173 and proxies the local API.

## Validation

```bash
python3 -m unittest discover -s ui/prototype/tests -v
cd ui/web && npm ci && npm run build
```

The Python HTTP tests cover create/list/reopen persistence, bounded repository inspection, sequential canonical alignment questions and restart recovery, per-item approval, nested work, inbox decisions, cross-project denial, loopback host/origin/write-header restrictions, request bounds, and built-asset serving/traversal rejection. A previous headless Chromium smoke exercised basic project switching and work creation. There is no broad browser/accessibility review, real authentication, production isolation, model-led interviewing, or agent execution. Do not treat this prototype as safe for sensitive data.
