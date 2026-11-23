# ProjectIQ Executioner — Phase 6: Failure Diagnosis and Triage

**Release:** Evidence-first MVP v0.1 (November 2026)  
**Schema:** `0.1` (`phase6_diagnosis.json`)  
**Package:** `projectiq_executioner.phase6`  
**Execution:** Offline, read-only analysis of existing trusted-stage *reports*. The Phase 6 engine **never runs, imports, compiles or installs student code, launches a VM, navigates a browser, or downloads from GitHub**.

> **Scope:** Classify observable failures, distinguish unavailable infrastructure from application behavior, identify unsupported/invalid test evidence, group explicitly declared downstream blocked tests, produce cautious root-cause hypotheses and fixed safe remediation, compare historical report outcomes, and generate JSON + Markdown. **It does not prove a student defect or attest the sandbox for arbitrary untrusted submissions.**

## 1. Relationship to the eight-phase roadmap

- **Phase 2:** VM startup, readiness, backend, approved source SHA and cleanup. Phase 6 reads an existing Phase 2 VM `vm_execution.json` result. A verified startup is not proof that the app is correct or that arbitrary untrusted execution is safe.
- **Phase 3:** Project-specific test plans, test IDs, grounded assertions and explicit `NEEDS_ORACLE` / `UNSUPPORTED_BY_RUNNER`. Phase 6 uses the **existing `phase3.contracts.validate_test_plan`** before accepting a plan; it does not invent new test expectations.
- **Phase 4:** Operator-reviewed functional API/HTTP, browser, login, DB and CRUD results. Phase 6 consumes existing **host-side run reports**, not raw HTTP pages, untrusted logs or uncontrolled guest output. The originating Phase 4 producer must still enforce its approval, guest-bundle and VM validation contracts.
- **Phase 5:** Static quality and redacted secret exposure indicators. These remain **advisory separate concerns**, not proof that a runtime error was caused by high cyclomatic complexity or by a potential secret match. Phase 5 uses `github_pinned` / `local_unverified`, while other phases use `github` / `local`; Phase 6 safely normalizes these source-type synonyms and still requires the same repository and commit identity.
- **Phase 7:** Benchmark diagnostic false attribution, unsupported-stage coverage, rerun equivalence and quality-score calibration using clean/defective repositories and independently reviewed ground truth.
- **Phase 8:** Integrate Phase 6 as a separate, versioned, evidence-labeled agent result without converting hypotheses into a rubric grade.

## 2. Implemented capabilities and milestones

| Milestone | Implemented | Boundary |
|---|---|---|
| 6A: Evidence ingestion | Bounded strict JSON, duplicate-key rejection, safe path/symlink checks, SHA-256 input receipts, Phase 3 contract validation, source SHA reconciliation, Phase 4 stage adapters, Phase 5 source adapter | A well-formed file is not a cryptographic attestation of its author. |
| 6B: Failure classification | Cleanup/security, VM launch/setup, startup not ready, assertion mismatch, test inconclusive, missing oracle, unsupported stage, reviewed browser and database outcomes, static alerts | An HTTP 401 alone does **not** establish missing credentials. A 500 alone does not prove student code fault. |
| 6C: Root-cause candidates and grouping | Fixed diagnostic vocabulary, reviewer/operator/student audience, P0–P3 priority, redacted evidence refs, bounded `UNVERIFIED_HYPOTHESES`, explicit graph-based downstream triage | Dependencies must be operator supplied as data-only edges. Causation is never inferred from test naming or Phase 5 scores. |
| 6D: Historical comparison | Compare multiple supplied Phase 4 run reports with corresponding VM reports; match exact Git SHA, stage, adapter, reviewed oracle digest, test ID; emit inconsistent-outcome candidate | Does **not** launch reruns or establish identical execution environments. Does not assert `FLAKY` as proven. |
| 6E: Report and acceptance | Stable `phase6_diagnosis.json`, Markdown, safe fixed text, report contract, cross-stage and negative tests, guarded installer | A Phase 6 output must retain its provenance/limitations when used by Phase 7/8. |

## 3. Quick start — Windows PowerShell

Run from the ProjectIQ repository root on the `executioner` branch.

**One reviewed API run (best initial smoke test):**

```powershell
python -m projectiq_executioner.phase6 `
  --run "outputs\executioner\<reviewed-api-run-folder>\api_run.json" `
  --vm  "outputs\executioner\<reviewed-api-run-folder>\api_vm_execution.json" `
  --out "outputs\executioner\phase6_api_diagnosis"
```

`<reviewed-api-run-folder>` is a **placeholder**, not a directory we claim exists on your PC. Locate a previously saved Phase 4 API run that contains both files. Do not pass `api_review.json` as `--run`.

**With its Phase 3 plan and Phase 5 report** (all must identify the exact same repository and pinned Git commit):

```powershell
python -m projectiq_executioner.phase6 `
  --plan "PATH\TO\phase3_test_plan.json" `
  --run "PATH\TO\api_run.json" `
  --vm "PATH\TO\api_vm_execution.json" `
  --phase5 "PATH\TO\code_quality.json" `
  --out "outputs\executioner\phase6_complete_review"
```

The Phase 5 report must be from the **same pinned SHA**. A local unverified Phase 5 report is **not interchangeable** with a GitHub-pinned Phase 3/4 report. Source mismatch causes a safe refusal (`CROSS_ARTIFACT_SOURCE_MISMATCH`).

**VM-only failure diagnosis:**

```powershell
python -m projectiq_executioner.phase6 `
  --standalone-vm "PATH\TO\vm_execution.json" `
  --out "outputs\executioner\phase6_vm_diagnosis"
```

**Static-only analysis and credential indicators:**

```powershell
python -m projectiq_executioner.phase6 `
  --phase5 "PATH\TO\code_quality.json" `
  --out "outputs\executioner\phase6_static_diagnosis"
```

Output is `PARTIAL`, correctly reflecting that no runtime tests were provided.

**Historical comparisons (offline; no new QEMU runs):**

```powershell
python -m projectiq_executioner.phase6 `
  --run "RUN_A\api_run.json" --vm "RUN_A\api_vm_execution.json" `
  --run "RUN_B\api_run.json" --vm "RUN_B\api_vm_execution.json" `
  --out "outputs\executioner\phase6_two_runs"
```

Pair each `--vm` with the corresponding `--run` **in the same order**. If no VMs are available, omit all `--vm` inputs: results are only report assertions and won't be eligible for historical comparison. A different source SHA, test ID, adapter, stage or approved catalog digest disables direct comparison. Two identical copies of a report do not count as two runs. If outcomes differ, Phase 6 reports `RERUN_OUTCOME_INCONSISTENT` with **medium** confidence and a reminder that runtime/environment equivalence is still unverified.

## 4. Command options

| Option | Required? | Meaning |
|---|---|---|
| `--run PATH` | At least one source of evidence | Existing Phase 4 host-run report (can repeat, max 24) |
| `--vm PATH` | Optional | Corresponding Phase 2 VM execution report; 1:1 in order with `--run` |
| `--standalone-vm PATH` | Optional, exclusive to `--run` | Diagnose a Phase 2 VM report without a Phase 4 report |
| `--plan PATH` | Optional | Strict validated Phase 3 plan, schema 1.1 |
| `--phase5 PATH` | Optional | Phase 5 `code_quality.json`, schema 0.3 |
| `--dependencies PATH` | Optional | Explicit declarative dependency edges (below) |
| `--out DIR` | Required | New output directory for JSON and Markdown |

All inputs are existing local JSON files. This release does not support arbitrary raw console logs, compiler traces, uploaded source archives, browser recordings, or arbitrary user-provided scripts. The tool will not attempt to fetch missing dependencies or execute additional verification. Input paths can be absolute or relative to the working directory. Symlink inputs and output directories are refused. A previous Phase 6 output is never overwritten.

## 5. Supported Phase 4 families

- `reviewed_vm_functional_testing`: Phase 4B HTTP smoke assertions.
- `reviewed_vm_api_testing`: reviewed Phase 4C-C API assertions.
- `reviewed_browser_ui`: Phase 4D-C browser UI checks.
- `reviewed_browser_login`: Phase 4D-D1/D2 login/session checks.
- `reviewed_vm_database_postconditions`: reviewed DB persistence observations and authentication prerequisites.
- `reviewed_authenticated_crud`: authenticated CRUD aggregate findings.
- `reviewed_vm_dependent_api_workflow`: dependent workflow aggregate findings.
- `reviewed_database_fixture_seeding`: approved database fixture stage.

**Known-but-not-fully-normalized aggregate pilots:** `reviewed_vm_scenario_isolation` and `reviewed_cross_repository_functional_pilot` are reported as unsupported/partial, not confidently interpreted; their nested subreport structures require future dedicated adapters. Unknown stages are also marked unsupported.

When provided a VM report, Phase 6 validates the source/adapter/execution/cleanup fields against the corresponding Phase 4 report and checks the **correct Phase 4 guest validation stage** (e.g. `phase4c_status=VALIDATED` for API, `phase4dd_status=VALIDATED` for browser login). VM startup alone does not corroborate a functional test. This is evidence consistency, **not** an independent cryptographic assurance that reports cannot be forged; do not accept arbitrary untrusted JSON as operator-verified records.

## 6. Diagnosis semantics

**Observations and possible causes are separate.** The report's `events` contain test IDs, observed `PASS`/`FAIL`/`INCONCLUSIVE`, optional HTTP status, the supplied oracle state and VM corroboration. The `diagnoses` list uses a **closed vocabulary** of safe codes with fixed messages and actions. Unknown issue codes are not copied from input. The `root_cause_hypotheses` list offers possible explanations for specific failures, always with `cause_established=false` and no numeric probability. A 401 might involve credentials, fixture state, authorization or the reviewed expectation; none is automatically blamed.

All diagnoses have `student_defect='NOT_ESTABLISHED'`. Even if an assertion against a reviewed oracle fails, it does not prove the reviewer, fixture, runtime or setup is correct in all respects. Phase 7 must independently benchmark any future stronger attribution policy.

Priority is **for investigation**, not an academic penalty:

- **P0** — VM cleanup/security provenance requires operator intervention; block attribution.
- **P1** — Infrastructure/invalid evidence or potential sensitive exposure; investigate promptly.
- **P2** — Reviewed behavior mismatch, inconclusive test, missing oracle or rerun inconsistency.
- **P3** — Unsupported tests, blocked dependents and advisory static issues.

**Security precedence:** If a supplied report cannot verify VM cleanup, Phase 6 returns `SECURITY_HOLD` and suppresses root-cause hypotheses and student attribution. An operator must examine the worker; never retry by bypassing security checks.

**No oracle inflation:** a Phase 3 case marked `NEEDS_ORACLE` is not a valid application failure. Unsupported tests remain untested. Phase 4 report contradictions are rejected rather than interpreted as a failure. The engine uses the project's own Phase 3 strict contract before accepting a plan.

**Dependent tests:** require the optional, explicitly supplied dependency JSON:

```json
{
  "schema_version": "0.1",
  "source": {"kind":"github","repository":"example/sample","commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "edges": [
    {"test_id":"TC-BBBBBBBBBBBBBBBB","requires":"TC-AAAAAAAAAAAAAAAA"}
  ]
}
```

The source fields and IDs must match the analyzed tests. Cycles, self-dependencies, duplicate/missing IDs and cross-source edges are rejected. Dependency grouping is emitted only if the dependent was **already reported as BLOCKED, UNTESTED or INCONCLUSIVE** and its explicit prerequisite failed. A failing dependent is **not** relabeled as blocked and a dependency edge does not prove causal responsibility.

## 7. Report schema 0.1

`phase6_diagnosis.json` contains:

| Field | Purpose |
|---|---|
| `schema_version`, `phase`, `stage`, `status` | Fixed release and triage state |
| `source` | Normalized source kind / repository / pinned commit (no URLs or raw file content) |
| `execution`, `vm_launches`, `security_attestation` | Always `NOT_PERFORMED`, 0, `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` |
| `summary` | Run count, VM-corroborated reports, tests observed, PASS/FAIL/INCONCLUSIVE counts, diagnoses and comparisons |
| `evidence_receipts` | Bounded input roles and content SHA-256; labels such as `RUN001`, `VM001`, not absolute workstation paths |
| `events` | Redacted per-test observations; no bodies/cookies/headers or stack traces |
| `diagnoses` | Fixed category, priority, audience, confidence, title, explanation, next action, evidence refs, affected tests, student-defect status |
| `dependency_groups` | Explicit blocked-test grouping based on authored edges |
| `rerun_comparisons` | Historical status comparisons and unverified environment-equivalence warning |
| `root_cause_hypotheses` | Fixed plausible causes with `UNVERIFIED_HYPOTHESES` and `cause_established=false` |
| `static_context` | Bounded safe path/line/code references to Phase 5 maintainability findings; **not** evidence of runtime causation |
| `security_context` | Bounded safe file/line references to Phase 5 secret indicators; **never** includes matched tokens |
| `limitations` | Fixed disclaimers that downstream agents must retain |

The Markdown report is intentionally understandable by a student and an operator, but all text describing a diagnosis is from trusted fixed mappings. Host paths, raw exception messages, test-input bodies, browser cookies, credentials, SQL rows, and raw guest logs **never** appear as diagnostic prose.

## 8. Limits and security

Default bounded inputs: each JSON at most **8 MB**; total at most **24 MB**, at most **24 Phase 4 reports**, **400 per-run test-result entries**, **2,000 observed events**, **2,000 diagnoses**, **400 root-cause hypotheses**. Legacy Phase 4 reports can have fewer diagnostic fields; absent observations are not silently invented. Nonfinite JSON, duplicate keys, malformed status/result counts, contradictory test outcomes, and symlink paths are rejected. `phase6_diagnosis.json` and `.md` are exclusively created and not overwritten.

The code runs only trusted Python standard-library functions, reads only explicitly selected evidence files and imports the already installed **trusted** Phase 3 validator. It does not read arbitrary files named inside the reports. There are no new Python runtime dependencies and no LLM or external network access.

**Operational warning:** input evidence should come from your already approved, reviewed ProjectIQ workflow. A locally fabricated JSON file can claim a successful VM run; Phase 6 is not a signatures service, and its source/digest checks do not turn forged input into trusted execution. The report deliberately retains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## 9. Tests and acceptance

```powershell
python -m unittest discover -s projectiq_executioner/tests -p 'test_phase6*.py' -q
python -m compileall -q projectiq_executioner/phase6

git diff --check
```

Automated coverage includes strict source/JSON validation, Phase 3 oracle status, HTTP and API results, browser login/UI, DB prerequisite handling, CRUD and workflow aggregates, cleanup precedence, dependency cycles, secret redaction, cross-SHA rejection, historical status comparison, no-auto-rerun, cross-language source adapter compatibility, deterministic reports and Windows-safe installer tests. Tests use **synthetic, inert fixtures**; they do not run student services or QEMU.

The complete Phase 6 MVP is considered accepted after local regression tests, guarded installation/verification and offline smoke tests on saved real Phase 4 reports from at least two different app stacks. **Those latter real Windows report checks remain for the user to perform**; successful synthetic tests alone are not a real-world coverage claim.

## 10. Future scope (deferred; do not block Phase 7)

1. Dedicated strict adapters for Phase 4 aggregate cross-repository and multi-scenario isolation manifests, with nested parent report verification.
2. Trusted log classification for bounded, redacted, structured compiler/runtime error codes. Do not ingest raw logs into student reports without privacy review.
3. Source-location mapping from verified runtime stack traces to matched pinned source SHA, with explicit unverified mapping states.
4. Controlled rerun execution only through the operator-approved Phase 2 VM launch gates, with hash-bound runtime image, dependency/cache, fixtures and environment equality. Phase 6 never self-authorizes reruns.
5. Stronger causal inference from controlled experiments rather than generic text similarity or unsupported LLM assertions.
6. Extensive Phase 7 benchmarking of false application blame, invalid oracle acceptance, stage coverage, repeatability, and human-reviewer agreement.
7. Phase 8 integration into Research/Integrity/Summarizer/Rubric via a shared agent contract. Never use a Phase 6 hypothesis as a confirmed rubric deduction.

**Release boundary:** Phase 6 v0.1 is an evidence-first, read-only diagnostic MVP. It deliberately prefers explicit uncertainty to misleading confident blame. Complete cross-stack real-report acceptance before marking the release fully validated.
