# Local project UI prototype

This is a loopback-only project/inbox demo for issue #11. The front end now uses a React implementation adapted from the shadcn `dashboard-01` visual/layout base (source pin and MIT attribution: [`../web/README.md`](../web/README.md)); it preserves the Python project API and canonical SQLite event history. The current interface creates and switches projects, adds proposed work, displays project history, and records inbox answers/rejections. No agent execution, model calls, browser worker, or external tools are connected. Use test data only; do not enter private or sensitive data.

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

The Python HTTP tests cover create/list/reopen persistence, inbox decisions, cross-project denial, loopback host/origin/write-header restrictions, request bounds, and built-asset serving/traversal rejection. A headless Chromium smoke exercised inbox answer, project creation/switching, and proposed work using stubbed API responses. There is no broad browser/accessibility review, real authentication, production isolation, or agent execution. Do not treat this prototype as safe for sensitive data.
