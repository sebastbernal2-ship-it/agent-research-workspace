# Dependency, license-metadata, and patch review

**Status:** Initial source-level review for the synthetic local harness, recorded 2026-11-10. Not a legal opinion, complete SBOM, vulnerability clearance, or authorization to distribute/deploy.

## Scope and evidence

The harness currently runs offline and does not call real providers or expose a service. The table distinguishes source snapshots used by deterministic tests from runtime dependencies; a vendored candidate is not automatically adopted. Commit/version and metadata were read from the pinned snapshot documentation and package manifests. The Meldwork npm audit result below is a point-in-time observation from its evaluation README, not a refreshed audit.

| Component | Pinned source / declared license metadata | Dependency evidence | Current disposition |
|---|---|---|---|
| AgenticX | `v0.6.5`, `0fc089ec6bf0b45f48a7beda06907932ebc98998`; `upstream/pyproject.toml` declares `Apache-2.0`. | `requirements.lock` is generated for the `desktop-runtime` extra and pins versions, including `litellm==1.80.11`; the spike README records that artifact hashes are not pinned. The isolated venv is ignored. No Python advisory audit is recorded. | Narrow deterministic worker candidate only. Studio is rejected as a project control plane; pending approval recovery is incomplete. No provider credentials. |
| AG2 | `v1.0.0`, `22f5d2e156dde56f61145bd4c9b10fca9d3e25cc`; `upstream/pyproject.toml` declares `Apache-2.0`. | Vendored `uv.lock` exists. The test bootstrap installs a minimal runtime/test environment into an ignored venv. No current vulnerability audit is recorded. | Message protocol behind the canonical adapter; no network Hub is deployed. |
| Meldwork | `v1.0.9`, `1ddf3fbdd76ef6e6b3baddfa6a460a72570aa7fd`; `desktop/package.json` declares `Apache-2.0`. | Desktop and frontend npm lockfiles are pinned. The 2026-11-08 isolated `npm ci --ignore-scripts`/audit reported **14 findings (6 moderate, 8 high, 0 critical)** in the desktop dependency tree; suggested fixes included major-version changes. No remediation or fresh audit is recorded. | V4 collaboration behavior is used by offline tests; Electron product distribution is not adopted. Treat the recorded findings as unresolved for any packaged use. |
| NAC (evaluated, not adopted) | `dev`, `f27b5b4a92b38567580a1a43576137bf5b436f95`; `package.json` declares `Apache-2.0`; root Cargo workspace does not declare a single workspace license. | Cargo/npm lockfiles were used in the separate complete-clone evaluation. The filtered snapshot omits legal notices and generated dependency trees. A complete dependency-vulnerability disposition is not recorded. | Candidate only; do not infer that security tests establish license clearance or production readiness. |
| Temporal / Paperclip | Candidate evaluations only; no runtime source integrated into the harness. | Their spike READMEs record the relevant test/install scope; no ongoing dependency monitoring is established here. | Not adopted. |

The upstream license/notice materials intentionally omitted from snapshots under the repository's source-history constraints have **not** been recreated or copied here. SPDX/package metadata above is not a substitute for the complete license text, notices, attribution, or legal review. Before redistribution or packaging, resolve the repository's approved license-history/notice process and verify every included transitive dependency's terms. In particular, do not treat successful local builds as redistribution clearance.

## Proposed maintenance cadence (owner not assigned)

1. **Before each dependency or source-pin change:** record the upstream commit, package-lock/requirements-lock digest, declared license metadata, direct and transitive dependency diff, advisory results, and relevant targeted tests. Do not silently refresh locks.
2. **Monthly while a component remains in active local development:** review upstream security advisories and supported versions for each active lockfile. Review immediately after a credible actively exploited critical advisory; disable the affected integration until triage establishes a safe mitigation.
3. **Before any release, service exposure, real credentials, private data, or provider pilot:** regenerate an SBOM from the exact build inputs, perform vulnerability and license/notice review, remediate or explicitly accept each finding through a named owner, and rerun reproducible tests. None of those approvals/owners are currently assigned.
4. **For high/critical findings:** do not merge an automated version bump as the sole response. Check exploitability in enabled features, supported upgrade path, lockfile integrity, and regression/security tests; document mitigation and residual risk if the dependency cannot yet be upgraded.
5. **If a lock, source pin, or build provenance cannot be reproduced:** stop packaging/deployment rather than falling back to an unpinned latest dependency.

This cadence is a proposal, not an established operating process. No automated advisory watcher, SBOM pipeline, security owner, or patch-response SLA exists today.

## Explicit unresolved risks

- The Meldwork desktop dependency findings remain unresolved and the audit is stale relative to the current date.
- AgenticX's large desktop-runtime lock has no recorded vulnerability audit or artifact-hash verification.
- AG2/NAC transitive dependencies have not received a current, complete vulnerability and license scan in this repository.
- License metadata does not establish that required legal notices are present in the filtered snapshots; legal notices were deliberately excluded.
- No named dependency/security reviewer, release approver, patch owner, or risk-acceptance authority is assigned.
- The review says nothing about vulnerabilities in the host OS, Python/Node/Rust toolchains, container images, or deployment environment.
