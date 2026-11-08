# Offline project-first UI prototype

This is a framework-neutral, static interaction mockup for issue #11. It uses synthetic content only and has no backend, external assets, storage, authentication, API requests, provider SDKs, or model calls. Buttons that resemble consequential actions explain that they are demo-only; they do not answer questions, resume work, save chats, or change routing. Do not enter private data.

## Run locally

From this directory, serve only on loopback:

```bash
python3 -m http.server --bind 127.0.0.1 4173
```

Open <http://127.0.0.1:4173>. Stop the server with Ctrl-C. This is a static mock, not a security boundary or deployable service.

## Screens represented

- Project home with charter summary, work status, recent project activity, and a visibly separate inbox preview.
- Dependency-aware work list (read-only mock).
- User-initiated VP conversation, clearly marked as an unconnected local-Qwen profile.
- Separate inbox with impact, explicit options, affected work, clarification/reject/answer confirmations, and resolved history.
- Agent/model profiles illustrating local VP and potential remote orchestrator/specialist assignments. Codex terms and provider connections are intentionally marked unreviewed/unavailable.
- Project charter and synthetic evidence.

The prototype is plain HTML/CSS/JavaScript to validate information architecture without selecting React, Svelte, or a backend framework. It has keyboard-operable navigation and question controls, visible focus styles, semantic landmarks/headings, responsive layout, reduced-motion handling, and no external fonts or assets. This has not received a browser-matrix or independent accessibility review.

## Checks

```bash
python3 -m unittest discover -s tests -v
node --check app.js
```

These are structural and syntax checks, not browser interaction or visual regression tests. The next UI gate is a human walkthrough on desktop and mobile, keyboard-only review, and independent accessibility review before framework selection or API wiring.
