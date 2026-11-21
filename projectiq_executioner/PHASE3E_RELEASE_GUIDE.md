# ProjectIQ Phase 3E — Cross-Repository Validation and Release Gate

## Scope and release decision

Phase 3E closes the deterministic Phase 3 test-plan foundation **only after** the
real-repository benchmark reports `PASS`. Phase 3 is non-executing: no student
code is imported, built, installed or run; there is no Ollama, QEMU or VM call.

**What this validates:** Phase 1 manifests produce versioned schema 1.1 JSON,
unique scenarios, consistent grounded-oracle statuses, no unsupported execution
claims, reproducible plans under inventory reordering, and no silently dropped
**eligible statically discovered** HTTP/GraphQL/gRPC operations up to caps.

**What it does not validate:** actual application correctness, completeness of
Phase 1's heuristic detection, availability of expected results for arbitrary
features, compatibility of a new runtime, or safety of executing unreviewed code.
`READY_FOR_PHASE4` describes an assertion specification, NOT authorization.

## What's included in this 3E patch

- `phase3/benchmark.py`: offline benchmark CLI and reusable functions.
- Stronger `phase3/contracts.py`: SHA and schema checks, method/path validation,
  evidence/oracle matching, readiness-only GET/200 contracts, source hash
  provenance, duplicate/summary checks, and refusal of forged execution claims.
- Small `phase3/generator.py` fix: input-error JSON now has an internally
  consistent, zero-valued inventory-count shape.
- `tests/test_phase3e_benchmark.py`: 12 architecture shapes, mutation/dynamic
  route cases, duplicate and missing-route detection, tampering, CLI and
  versioned result checks.

No Phase 1/2 or VM/runtime change. **Phase 3 test_plan.json stays schema 1.1**.
The benchmark's own `benchmark.json` is schema 0.1, separate from test plans.

## Apply on top of Phase 3D (do not reapply previous patches)

From `C:\Users\varun\Code\ProjectIQ` on the branch containing Phase 3D:

```powershell
$patch = "$HOME\Downloads\projectiq_phase3e_release_gate.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw '3E patch does not apply cleanly' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw '3E apply failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed' }

python -m unittest `
  projectiq_executioner.tests.test_phase3_generator `
  projectiq_executioner.tests.test_phase3d_quality `
  projectiq_executioner.tests.test_phase3e_benchmark -q
if ($LASTEXITCODE -ne 0) { throw '3E regression suite failed' }
```

**Expected on this tested code:** 90 tests passing (possibly one skipped when
symlink privileges are unavailable). This local proof does not replace your
Windows run or real-repository benchmark.

## First pass: 7 offline replays + 1 existing Express test plan

The Phase 2D batch already contains seven replayable `phase1/manifest.json`
files, whereas the Express case failed Phase 2 planning and your saved Phase
3D Express result contains `test_plan.json` only. This command requires **no
GitHub re-download**:

```powershell
$batch = 'outputs/executioner/batch_20261121_205242'
$flask = 'outputs/executioner/flask_replan_20261121_213856'
$expressPlan = 'outputs/executioner/phase3d_express_01/test_plan.json'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase3e_initial_$stamp"

python -m projectiq_executioner.phase3.benchmark `
  --batch-root $batch `
  --candidate-root $flask `
  --plan $expressPlan `
  --out $out

$r = Get-Content "$out/benchmark.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r.summary | Format-List
$r.results | Select-Object name,input,status,plan_status, `
  @{Name='Cases';Expression={$_.summary.total}}, `
  @{Name='Ready';Expression={$_.summary.ready_for_phase4}}, `
  @{Name='MissingRoutes';Expression={if ($_.route_coverage) {$_.route_coverage.missing_http_operations + $_.route_coverage.missing_non_http_operations} else {'N/A'}}} |
  Format-Table -AutoSize
```

**Expected overall `LIMITED` even if everything is valid.** The existing
Express test plan is verified structurally, but without its Phase 1 manifest
we cannot independently replay it or check its route coverage. This is
intentional: `LIMITED` must not be misreported as a fully completed release.

## Final full eight-repository release benchmark

Recreate the **Express Phase 1 manifest once** (still no project execution),
then reuse it offline going forward. This is only one repository download:

```powershell
python -m projectiq_executioner `
  'https://github.com/krnpatel88/nodejs-sample-api' `
  --ref 'b6d84feb52e605a788ccafbddfb6d2dc3508bcae' `
  --out 'outputs/executioner/phase3e_express_phase1'
if ($LASTEXITCODE -ne 0) { throw 'Express Phase 1 ingestion failed' }

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase3e_final_$stamp"
python -m projectiq_executioner.phase3.benchmark `
  --batch-root 'outputs/executioner/batch_20261121_205242' `
  --candidate-root 'outputs/executioner/flask_replan_20261121_213856' `
  --manifest 'outputs/executioner/phase3e_express_phase1/manifest.json' `
  --out $out
if ($LASTEXITCODE -ne 0) { throw 'Phase 3E release benchmark not fully PASS' }

Get-Content "$out/benchmark.md" -Raw -Encoding UTF8
```

**Target:** eight real-repository inputs, `PASS` for every replay,
`missing_http_operations=0`, `missing_non_http_operations=0`, no unexpected
`TEST_PLAN_TRUNCATED`. The status is not guaranteed: investigate real findings
instead of altering the threshold or fabricating missing oracles.

Even a `PASS` does **not** imply all scenarios are Phase 4-ready. A project
with no sound expected response may legitimately return seven `NEEDS_ORACLE`
cases, as Express did. That is correct, not a generator failure.

## Validate a newly encountered or unusual repository

Run Phase 1 on its immutable GitHub commit (or local folder) first:

```powershell
python -m projectiq_executioner 'https://github.com/owner/repo' `
  --ref '<full 40-character SHA>' --out outputs/executioner/repo_phase1

python -m projectiq_executioner.phase3.benchmark `
  --manifest outputs/executioner/repo_phase1/manifest.json `
  --out outputs/executioner/new_repo_benchmark_001
```

The Phase 2 candidate is **optional**; discovery and contract validation work
on unsupported stacks. The result then marks unsupported readiness separately.

For an additional pinned source-backed static HTML check, use the Phase 3
`--repo ... --candidate ...` CLI in a fresh output directory, then validate
its `test_plan.json` via `benchmark --plan ...`. An existing plan can only be
`LIMITED` until paired with a replayable Phase 1 manifest.

## Status interpretation and acceptance

| Benchmark status | Meaning | Action |
|---|---|---|
| `PASS` | Generated replay validated; deterministic; eligible discovered routes retained | Suitable to freeze Phase 3 contract for that input |
| `LIMITED` | Input was validation-only, or a bounded cap caused incomplete coverage | Regenerate from Phase 1 or review cap/evidence |
| `FAIL` | Corrupt/mismatched input, invalid test plan, unstable output, or missing eligible route | Diagnose and fix before declaring 3E closed |

A *valid* `DISCOVERY_ONLY` or `NO_TEST_TARGETS` plan is acceptable for a repo
where Phase 1 found no testable operations. This doesn't prove the code has no
behavior—it states that our current evidence is insufficient to infer it.

We deliberately avoid time-dependent hashes, file modification times,
repository-specific hardcoding, LLM oracles and arbitrary student scripts.

## Phase 4 handoff and safety boundary

Phase 4 should consume schema 1.1 through `validate_test_plan()` and then
**independently verify** exact source SHA, operator approval, VM adapter,
source body/contract provenance, test eligibility, guest test limits and clean
VM teardown. Phase 3 `READY_FOR_PHASE4` is not evidence that these gates have
passed or that the student app behaves correctly.

**Known limitations:** Phase 1 cannot detect every dynamically generated route
or program requirement; OpenAPI v3 *JSON* subset and approved static-page
assertions are currently the principal non-readiness oracle sources; the
Phase 4 runner and browser/stateful assertions remain future work.
