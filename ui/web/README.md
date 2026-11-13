# Project UI

The frontend dependencies and initial component baseline use shadcn's `dashboard-01` registry block, pinned at upstream commit [`dd41d19e2f80ff6891951f7ced74c2c9a650b065`](https://github.com/shadcn-ui/ui/tree/dd41d19e2f80ff6891951f7ced74c2c9a650b065/apps/v4/registry/new-york-v4/blocks/dashboard-01). The shell and light palette are informed by Amp's public [thread documentation](https://ampcode.com/docs/threads), but the main workspace is goal-centered: project direction, alignment state, and the outcome/workstream hierarchy come first; process detail and event history stay secondary. It does not use Amp source code, assets, or branding. The shadcn upstream MIT license is included as `SHADCN-LICENSE.md`; React/Vite and Lucide are pinned in `package-lock.json`.

Project creation offers New and Existing paths. Existing-project onboarding displays a bounded, read-only scan of user-selected tracked docs, then walks through five canonical alignment questions in sequence; this is deterministic rather than model-led, and does not inspect code, ignored/untracked files, bulk data, or Git working-tree status. Proposed work remains visible while alignment is partial. A separate task-board view groups canonical statuses into Proposed, Ready, In progress, Waiting, Done, and Stopped. Each proposed item can be approved into Ready, which is not execution. The project-wide auto-run control stays off and disabled until provider/data policy, enforceable spend limits, and hardened isolation exist. A Chat view remains disabled because no project-scoped AI service is connected. The prototype cannot generate an AI-authored initial board, execute tasks, or report agent progress.

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
