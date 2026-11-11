# ADR-0002: WSL local-first initial version

- **Status:** Accepted as the initial development/deployment target; implementation and safety gates remain open.
- **Decision date:** 2026-06-12
- **Decision owner:** Project owner (conversation decision)

## Decision

The first usable version targets one user on the user's WSL2 Ubuntu environment. It is not a network-hosted or multi-user service. Projects and runtime data should live in a dedicated directory in the WSL Linux filesystem (proposed default `~/AI-Projects` for project workspaces, with application state under `$XDG_STATE_HOME` or `~/.local/state`). Do not put the project workspace under `/mnt/c` by default; the Linux filesystem is the intended boundary and avoids treating Windows-drive paths as implicitly available.

The intended product is an autonomous AI organization, not a chat-only demo. Project agents should carry out routine work without per-step user approval, with project-scoped authority and orchestration. The desired agent roles include coding, web research, project maintenance/orchestration, and a separate cross-project liaison/"embassy" role. The liaison may convey approved information/files into another project; it must not silently edit the source project.

The owner expects to use public, private, confidential, regulated, or personal data. Therefore these data categories are in the intended product scope, but the current prototype is **not approved or safe for that use**: identity, privacy, deletion, secret handling, hardened isolation, and provider data handling have not been implemented/reviewed. This ADR records desired scope, not authorization to process sensitive data with the current code.

Codex is the intended primary model route, and a locally hosted Qwen model is also desired. Using a hosted Codex connector necessarily sends the selected request/context to the service; local WSL execution does not make that content local. Provider terms, exact connector/auth mechanism, data flow, and retention remain to be verified before connecting it. Local Qwen can avoid sending prompts to a remote model only if the actual connector and model runtime are verified to make no such calls.

Web research should be able to browse and download project-relevant material, but must not authenticate to sites, post, or submit forms. Downloaded content is untrusted and may not be treated as instructions or verified evidence. The desired filesystem boundary is the current project; cross-project access is a separate, narrow liaison function.

After restart, the desired experience is a visible paused project with a resume action, and safe work resumes autonomously. Recovery must inspect partial changes and ambiguous external effects rather than blindly replay them. Automatic restart/service lifecycle is not yet implemented.

## Initial implementation boundary

1. WSL2 Ubuntu, one local user, loopback-only UI/service; no ports exposed to LAN or public networks.
2. Dedicated Linux-filesystem project root, proposed `~/AI-Projects`; worker receives only the selected project's declared working directory and files. No agent-wide access to the user's home, Windows drives, SSH keys, provider credential files, or unrelated projects.
3. Start with offline deterministic workers and synthetic fixtures. Do not wire hosted Codex, local model, browser automation, external side effects, or real sensitive data until their connector/boundary and recovery checks are implemented.
4. A first useful project workflow must let the user create a project, add/approve work, run a bounded project-scoped worker, inspect progress/history, and pause/resume. The existing UI is a seeded synthetic one-project prototype and does not meet this yet.
5. The only allowed web scope after a future authorized enablement is read/download into the current project; no login/post/form submission. Network egress enforcement must be at the actual sandbox/container boundary, not merely a UI convention.
6. Backup must be automatic, verified, and documented for the chosen WSL path. Local-only backup is not protection from computer/disk loss; destination and retention need a user decision before private-data reliance.
7. Hosted multi-user Step 2, project-to-project embassy access, autonomous external actions, and broad privacy/deletion protections are later milestones, not claims of the initial version.

## Consequences and open gates

- WSL2 is the target environment, but WSL compatibility is not yet a supported-platform claim. Pin dependencies and run test/install/restore checks in this environment before declaring it supported.
- The user requested broad autonomy, but no per-step approval is required only within explicitly bounded capabilities. Filesystem, network, process, resource, and effect boundaries are mandatory so a worker cannot consume the whole machine or escape the project.
- The current loopback UI is synthetic and uses a fixed fake principal. It must not be described as authenticated or used for sensitive projects.
- Do not claim resumed work is exactly-once. Interrupted file edits and external effects require reconciliation or visible review.
- Before enabling Codex, document and approve which project data may be sent, establish spend controls, and verify connector behavior. A user account/CLI integration is not automatically a safe service credential boundary.
- Before enabling real web access, implement an isolated fetch/browser worker with blocked login/post/form submission, redirect/DNS/IP protections, bounded downloads, and untrusted-content handling. No fetching is enabled by this decision.
