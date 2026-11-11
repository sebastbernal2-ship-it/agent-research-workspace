# Project UI

The frontend dependencies and initial component baseline use shadcn's `dashboard-01` registry block, pinned at upstream commit [`dd41d19e2f80ff6891951f7ced74c2c9a650b065`](https://github.com/shadcn-ui/ui/tree/dd41d19e2f80ff6891951f7ced74c2c9a650b065/apps/v4/registry/new-york-v4/blocks/dashboard-01). The shell and light palette are informed by Amp's public [thread documentation](https://ampcode.com/docs/threads), but the main workspace is now goal-centered: project intent and a concise update first, success criteria and decisions next, with implementation details and event history collapsed until requested. It does not use Amp source code, assets, or branding. The shadcn upstream MIT license is included as `SHADCN-LICENSE.md`; React/Vite and Lucide are pinned in `package-lock.json`.

This is a presentation-layer change only. Project state and commands continue to use the project-owned local API and canonical SQLite history. No project-store or agent runtime from either visual reference is adopted. The UI uses no remote fonts or external assets. The selected project is reflected in the local URL (`?project=<project-id>`) so a project view can be reopened directly. The prototype has no connected agent and cannot report actual agent progress or verified outcomes; hidden work-plan details are canonical records, not subagent chat.

## Build and serve

From the repository root:

```bash
cd ui/web
npm ci
npm run build
cd ../..
python3 -m ui.prototype.server
```

The build writes to the ignored `ui/prototype/build/`; the existing Python service serves those assets from IPv4 loopback. For frontend development, keep the Python API on port 4173 and run `npm run dev` in this directory; Vite listens on 127.0.0.1:5173 and proxies only `/api` to the loopback API.

The same prototype warning applies: no model, agent execution, browser worker, or real authentication is connected. Do not enter sensitive data.
