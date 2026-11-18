# ProjectIQ Executioner — locked eight-phase roadmap

**Architecture baseline (18 November 2026).** This roadmap is the agreed scope, not a claim that the later phases are implemented. `projectiq_executioner` is the chosen package name.

| Phase | Name | Exit criterion |
|---|---|---|
| **1** | GitHub ingestion and project intelligence | Accept a public GitHub URL, pin to commit, safely inspect it, and produce an evidence-backed versioned Project Manifest (JSON + Markdown). No code execution. |
| **2** | Secure full-stack environment orchestration | Spawn isolated frontend/backend/database services with health checks, resource constraints, disposable data, and teardown; no arbitrary code on host. |
| **3** | Project-specific test generation | Turn validated requirements, routes, UI and schema into grounded assertions with explicit test-oracle evidence and uncertainty labels. |
| **4** | Automated functional testing | Run existing and generated API, UI, database, and end-to-end tests against live isolated services; preserve traces/logs and reproducible outcomes. |
| **5** | Code quality assessment | Run language-specific linters and complexity/duplication checks; distinguish high-confidence issues from advisory judgments. |
| **6** | Failure diagnosis and triage | Distinguish application failures, setup failures, missing credentials, flaky tests, and defective generated tests using reproducible evidence. |
| **7** | Report reliability and benchmarking | Structured reports, coverage inventory, test-oracle calibration, clean/defective benchmark repos, reruns, false-pass/false-fail measurement. |
| **8** | ProjectIQ integration | Use a shared agent result contract; integrate with Research, Integrity, Summarizer and Rubric agents without coupling scores to unverified claims. |

## Guiding constraints

1. **Generalize.** Detectors use reusable technology and behavior evidence, not the sample repository name or project-specific paths.
2. **Ground expected behavior.** Generating a test does not prove its test oracle. A guessed expected response must not be given a trustworthy PASS.
3. **Make unknowns visible.** `PASS`, `FAIL`, `BLOCKED`, `UNTESTED`, `INVALID_TEST`, and `FLAKY` are distinct outcomes in later phases.
4. **Secure by default.** Fetch, extract, and analyze untrusted code as data in Phase 1. Future phases must use purpose-built execution isolation.
5. **Reproducible.** Every run points to an immutable Git commit and records sources/evidence for claims.
6. **Incremental vertical slices.** Validate at least one real full-stack app early, then expand to dissimilar stacks; never train or special-case only on MiniCollabApp.
7. **Do not inflate coverage.** A few passing smoke tests do not mean the entire application works.
8. **No implicit code-quality score.** Subjective issues require evidence, and objective tool findings should include locations and confidence.

## Phase 1 explicit non-goals

- No application installation, compilation, execution, authentication, browser navigation, database provisioning, or functional assertions.
- No LLM-generated functional test cases yet (only *targets* for later generation).
- No vulnerability scanning, comprehensive secret detection, quality scoring, or guaranteed stack identification.
- No private-repository auth or arbitrary self-hosted Git hosting in this first package.


## Implementation progress (November 2026)

Phase 1 has a locked baseline and additive `inventory` handoff metadata; see
`PHASE1_LOCK.md`. Phase 2A now compiles an inert execution plan from a Phase 1
manifest; see `PHASE2_DESIGN.md`. Phase 2B's container-oriented preflight remains for reproducibility. The new
Phase 2C includes a QEMU/QCOW2 disposable Linux VM pilot, operator-controlled
Ubuntu template baker, and reviewed-fixture adapters for static HTML and
PHP/MySQL. This code has offline regression coverage, **not a verified Windows
WHPX end-to-end run**; arbitrary untrusted submissions remain disabled pending
real worker acceptance testing. See `PHASE2C_VM_GUIDE.md`.
