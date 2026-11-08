# Project-first UI foundation evaluation

Status: initial source/license review, not a product selection or deployment approval. Candidate source snapshots checked 2026-11-08; evaluate again before adopting because upstream terms and features change. This project remains private and canonical project/task state stays in our own control plane.

## Product requirements

The workspace needs distinct project homes with charter, work/dependency status, agent discussion/runs, evidence/artifacts, and a separate human-question inbox. The project VP chat is user-initiated; inbox answers are durable commands. Model choice must be per role/task and replaceable independently of the UI. A UI candidate must not silently become the canonical store or authorization authority.

“Free” here means no required license or hosted SaaS fee for the self-hosted software. It does not include hardware/electricity, provider inference, storage, operations, or security review.

## Candidates reviewed

| Candidate | Source evidence | Fit | Main concern |
|---|---|---|---|
| **Custom project UI + our service** (recommended direction; not yet implemented) | No third-party UI dependency to adopt. Existing canonical event store/read models/application boundary are local Python. | Best fit for project-first navigation, work DAG, inbox, and explicit interaction with our API. Can use permissively licensed UI components after checking each dependency. | Most frontend work; we must implement accessibility, responsive behavior, auth/session, and security. Do not expose current test/fake access-policy seam as auth. |
| **LibreChat** | MIT at root; source `LibreChat-AI/LibreChat`, commit `f4154b649efa37e1345ccc517a4d8d1ed2178c82` (2026-10-31). README describes multi-provider chat, Agents/subagents, MCP, custom endpoints and Chat Projects. Current compose includes API, MongoDB, Meilisearch, and RAG services. React/Vite client; Node API. | Strong option for a standalone chat/model UX and a useful feature/design reference. The advertised provider list includes Ollama, DeepSeek, and Qwen; verify exact adapter behavior in a pinned run before depending on it. | Chat Projects are not our project/work control plane. Integrating it as the main UI risks a second identity, conversation, and project state system; embedding or sharing internals is not assumed safe. Larger runtime footprint than a thin project UI. |
| **Open WebUI** | `open-webui/open-webui`, commit `b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245` (2026-11-08). README documents self-hosting and Ollama/OpenAI-compatible endpoints; frontend is SvelteKit/Vite and backend Python. | Fast way to try local Ollama models and generic chat; could remain an optional standalone chat interface. | Root license has a branding condition: removal/alteration is permitted only for deployments/distributions with at most 50 end users, prior written permission, or enterprise license. That is not a clean permissive base for a fully rebranded product. It is chat-first, not our project control plane. Review full license and LICENSE_HISTORY before any use. |
| **LobeHub / LobeChat** | `lobehub/lobehub`, commit `c43d09937d64b10c0179c5a9091d6da035b79a28` (2026-11-08). Root LICENSE is the LobeHub Community License and requires a commercial license to develop/distribute derivative works; repository/package metadata is not consistent about license, so inspect exact files and obtain legal clarity. | Agent/chat oriented and visually polished; potentially useful as a design reference. | License ambiguity and derivative-work restriction make it a poor default foundation for our private, extensible product without clarification. It is not a proven replacement for our project control plane. |

## Recommendation

Build a **small, project-first custom UI against a versioned service contract**, rather than forking Open WebUI or LibreChat now. Reuse established, permissively licensed components where useful; keep third-party chat products optional and isolated. Do not add or share their internal databases. A first framework-neutral, mock-backed prototype now exists at [`../ui/prototype/`](../ui/prototype/), with project overview, work, VP chat, separate inbox, agent/model assignments, and evidence screens. It has no backend or persistence and must not be exposed or treated as security.

The first prototype is plain HTML/CSS/JavaScript so the product navigation and interaction model can be reviewed before selecting React, Svelte, or another stack. A later small framework spike should compare implementation friction, keyboard/mobile behavior, maintenance, and API fit. FastAPI is a reasonable Python service candidate, but it is not selected yet; first define API auth, command/idempotency, and error semantics.

## Gates before adoption

1. Obtain the user's missing candidate-site links/names and include them in this comparison.
2. Pin commits/tags and inspect complete licenses, dependency health, auth boundaries, data retention, and deployment requirements.
3. Prototype the essential project and inbox workflows with deterministic data; run keyboard/accessibility and cross-project isolation checks.
4. Keep service APIs framework-neutral and canonical state outside the UI runtime. No public/remote deployment before real authenticated principal binding, membership authorization, CSRF/session protections, and independent security review.
5. Never let a UI setting grant tools, budgets, or project access; those are server-side controls.

## Sources

- [LibreChat repository](https://github.com/LibreChat-AI/LibreChat), [root license](https://github.com/LibreChat-AI/LibreChat/blob/f4154b649efa37e1345ccc517a4d8d1ed2178c82/LICENSE), [README](https://github.com/LibreChat-AI/LibreChat/blob/f4154b649efa37e1345ccc517a4d8d1ed2178c82/README.md), [compose](https://github.com/LibreChat-AI/LibreChat/blob/f4154b649efa37e1345ccc517a4d8d1ed2178c82/docker-compose.yml).
- [Open WebUI repository](https://github.com/open-webui/open-webui), [license](https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/LICENSE), [README](https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/README.md), [package metadata](https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/package.json).
- [LobeHub repository](https://github.com/lobehub/lobehub), [root license](https://github.com/lobehub/lobehub/blob/c43d09937d64b10c0179c5a9091d6da035b79a28/LICENSE), [package metadata](https://github.com/lobehub/lobehub/blob/c43d09937d64b10c0179c5a9091d6da035b79/package.json).

This is a first-pass source review, not a full build, runtime, security, legal, or license-compliance review. It does not claim that any candidate is secure or suitable for production.
