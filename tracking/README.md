# GitHub issue register

Canonical live tracking is in the [GitHub issues](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues), the [roadmap parent issue #1](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/1), and the [First validated collaboration adapter milestone](https://github.com/sebastbernal2-ship-it/agent-research-workspace/milestone/1). The repository is private.

## Ownership and status convention

- **Product decisions, provider/budget approval, private-data approval, and HiperGator authorization:** project owner (user).
- **Implementation/execution owners:** currently unassigned; do not infer ownership or notify/assign people until the project owner confirms contributors.
- Status vocabulary: `VERIFIED` (specific test/evidence attached), `UNVERIFIED`, `BLOCKED` (named prerequisite), `REJECTED` (documented gate failure). Scripted fixtures never count as real-model quality evidence.

## Workstream register

| Issue | Workstream | Current status / blocker | Depends on |
|---|---|---|---|
| [#2](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/2) | Canonical project/work/event/evidence contracts | OPEN; start here; synthetic schema only | — |
| [#3](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/3) | AG2 → canonical event adapter | OPEN; adapter contract tests, no model needed | #2 |
| [#4](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/4) | AgenticX API/recovery/capability gate | OPEN; existing selected-route tests are partial | — |
| [#5](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/5) | Meldwork extraction, portability, dependency remediation | OPEN; Apple-silicon environment and 14 NPM audit findings | — |
| [#6](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/6) | Separate question/approval inbox | OPEN; define canonical semantics before UI | #2, #7 |
| [#7](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/7) | Capability permissions and threat model | OPEN; no deployment/HPC execution | #2 |
| [#8](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/8) | Blind rubric and bounded real-model pilot | BLOCKED; provider/model, spend limit, eligible environment need user approval | #2, #4, #5, #6, #7 |
| [#9](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/9) | Laptop worker lifecycle / HiperGator policy gate | OPEN for laptop design; HPC execution BLOCKED pending official approval | #2, #7 |
| [#10](https://github.com/sebastbernal2-ship-it/agent-research-workspace/issues/10) | Transparent VP alignment memory | OPEN; synthetic test cases only | #2 |

## Current recommended sequence

1. #2 define the minimum portable contract.
2. #7 threat model and capability grants; progress in parallel with #2.
3. #3 build/replay the small AG2 adapter against the synthetic contract. Do not bridge a second runtime yet.
4. #6 question/approval state and resume tests.
5. #4 and #5 candidate-specific adoption gates; resolve only blockers relevant to the pilot.
6. #8 run real-model evaluations only after the user approves provider/model, spend budget, and environment. Meldwork additionally needs the supported Mac build or an explicit source-port plan.
7. #9 multi-laptop worker proof; HiperGator remains a separate policy gate.
8. #10 synthetic VP memory/conformance tests; no private profile import until explicitly approved.

The full component evidence is in [`../spikes/`](../spikes/); this register intentionally tracks remaining work, not marketing capability claims.
