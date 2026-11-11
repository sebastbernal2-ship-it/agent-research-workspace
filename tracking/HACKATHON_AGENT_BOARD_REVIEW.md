# Hackathon Agent Board pattern review

**Reviewed:** 2026-11-11. Sources: [Notion walkthrough](https://marvelous-relative-5de.notion.site/hack), its linked [`azizu06/hackathon-agent-board`](https://github.com/azizu06/hackathon-agent-board) template (main at `91a9f01`), the template script/rules/hooks/issue template, and samples from the linked [Farsight board](https://github.com/azizu06/farsight/issues/24) and [Backrooms board](https://github.com/azizu06/backrooms/issues/1). Public boards were read-only; no comments were posted and no credentials were used.

## What the pattern does

It separates three things:

1. **Work ownership:** split folders by teammate and record owners in `AGENTS.md`.
2. **Work tracking:** pick/file a normal GitHub issue, take it, branch from fresh `main`, make small commits, open a PR with `Closes #N`, get one review, then merge. The board template does not replace this workflow.
3. **Fast coordination:** pin one “Message Board” issue. Agents post short progress, blocker, and handoff comments there, address a teammate by GitHub login, and poll for mentions. Each comment starts with a human/agent identity such as `[Sam's Codex]`.

The template's `tools/board` Bash script wraps `gh` for `post`, `read`, `unread`, `wait`, and `whoami`. Its default issue is #1, with `BOARD_ISSUE` and `BOARD_REPO` overrides. `unread` stores a local cursor under `.board/`; `wait` polls every 30 seconds by default. Claude Code hooks surface unread comments on session start and each prompt; other agents are told to run the command themselves. The rules say to respond to relevant mentions, avoid acknowledgement-only replies, request a handoff before editing another owner's files, and let only the team lead reassign work.

The author reports 1,182 comments on Farsight and 988 on Backrooms (2,170 total). Samples show the board being used for cross-agent handoffs, issue/PR references, status/evidence reports, review requests, and operational coordination. This demonstrates a high-tempo communication feed—not that comments alone provide structured or reliable task tracking.

## Fit for this repository

**Useful as an optional coordination layer; not a replacement for our issue register or canonical project history.** This repository already tracks work in GitHub issues and `tracking/README.md`, keeps acceptance evidence in the production-readiness plan and tests, and preserves product/project truth in its own canonical control plane. Preserve those authorities:

- GitHub work issues remain the source for engineering scope, dependencies, acceptance criteria, ownership, and close state.
- A coordination issue, if approved, should contain only short pointers/handoffs (issue, PR, commit, files, question); it must not become a second backlog, approvals ledger, or product history.
- The project-owned event store remains canonical for product work, approvals, and run history.
- Do not put secrets, private data, credentials, or sensitive project content in GitHub comments. A private repository is still a transfer to GitHub and its integrations.

## Adaptation risks to resolve before using the script

The template is a small hackathon utility, not a durable or security-hardened relay. The public template has no declared license and no `.github/workflows` CI configuration at the reviewed revision; its board script has no accompanying automated tests. Bash syntax validation alone is not a functional or security review.

Static source review identified these relevant limitations:

- **Shared GitHub identity:** `unread` filters out every comment authored by the current GitHub login. Multiple agents using one user's `gh` account will therefore hide one another's posts. This is likely for our desired single-user Codex + local-Qwen setup. The message prefix does not change GitHub's author identity.
- **Shared local cursor:** all agents in one checkout use the same `.board/cursor-<repo>-<issue>` file. One agent's read advances the cursor for the others. Separate checkouts avoid that specific collision, but do not fix same-login filtering.
- **First-run cursor behavior:** the first unread check reports only the latest ten comments as context and then advances to the newest one. Older unprocessed handoffs are not automatically delivered.
- **Notification is not acknowledgement:** `unread`/`wait` advances the local cursor when displaying comments, not when a human or agent has completed the requested action. The GitHub comments remain available, but a consumed notification can be missed by the next poll/session.
- **Untrusted text:** issue comments are external input. Claude hooks place their text into agent context; the rules do not create a prompt-injection boundary or authorize actions. Any adopted workflow must treat posts as requests to verify, not executable instructions.
- **No enforceable ownership or task states:** the owner table is blank in the template, folder boundaries are procedural, and comments do not enforce issue status, approvals, or exclusive file access.
- **Configuration validation:** source validates repository/login strings but does not strictly validate `BOARD_ISSUE` as a positive issue number or verify the selected issue is the intended open/pinned board before posting. The `gh` token can write wherever its configured scopes permit.
- **Volume/noise:** 1,000+ comments per board can make a single stream hard to search and summarize. Keep detailed decisions and acceptance evidence on their actual work issues/PRs.

I ran `bash -n` on the template's board and Claude hook scripts; syntax passed. I did not execute them, authenticate `gh`, or test a post/poll against either live board.

## Recommended next step

Use the existing GitHub issues as the tracker and adopt the issue/branch/PR discipline incrementally for future work. A single private-repository coordination issue could help only if the owner explicitly wants it; before setup, we should decide who the human/agent actors are, whether GitHub-hosted comments are acceptable for the data involved, and whether we will write a safer actor-specific cursor/mention adapter rather than use this script unchanged. Until then, record handoffs on the relevant work issue/PR and keep project state canonical here.
