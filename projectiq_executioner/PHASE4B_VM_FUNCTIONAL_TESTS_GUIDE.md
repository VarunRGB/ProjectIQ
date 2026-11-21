# ProjectIQ Executioner — Phase 4B: reviewed-VM functional tests

**Status:** Implementation and offline acceptance tests supplied; **a real Windows/QEMU
acceptance run remains required before calling 4B complete.**

**Security scope:** only **operator-approved immutable GitHub snapshots** that already
pass Phase 2's reviewed-fixture VM policy. This is **not** arbitrary untrusted
student-submission authorization. All application execution and HTTP requests
occur **inside the existing disposable QEMU Linux guest**, not on Windows.

## Architecture and ownership

```text
Phase 3 test_plan.json (schema 1.1)
       + Phase 2C candidate (exact repository@SHA)
       + operator-controlled vm_config.json
                |
                v
Phase 4A prepare_handoff()  ---> BLOCKED / NO_RUNNABLE_TESTS => no VM
                |
        STAGED_NOT_EXECUTED
                |
                v
Phase 2 execute_vm_candidate(..., test_bundle=...)
  * independently verifies operator-approved exact SHA, trusted image/assets,
    managed workspace, cgroup enforcement, network restrictions, watchdog
  * safely re-downloads pinned source and sends bounded test bundle + trusted
    guest_http.py to the Linux guest
                |
                v
Guest agent starts reviewed app (same unchanged Phase 2 server/cgroup policy)
  * verifies startup HTTP 200 and existing build/service probes
  * **while server is alive** runs guest_http.run_guest_tests() over 127.0.0.1
  * records fixed-schema PASS / FAIL / INCONCLUSIVE assertion evidence
  * stops application in the existing finally block
                |
                v
Host validates complete guest test report against exact submitted test bundle
  * malformed/missing report cannot produce STARTUP_VERIFIED in 4B
  * retains existing Phase 2 kernel/placement observations and VM watchdog
                |
                v
Phase 2 VM stop + independently verified managed workspace cleanup
                |
                v
phase4_run.json (status + bounded evidence)
```

## Files changed by Phase 4B

- `phase4/vm_run.py` — new explicitly executing CLI/API, status handling, durable JSON.
- `phase2/vm_worker/orchestrator.py` — **optional** `test_bundle` argument;
  existing calls with no bundle follow the old Phase 2 path.
- `phase2/vm_worker/guest_agent.py` — run tests **after readiness and before
  shutdown** in both single-server and full-stack paths.
- `tests/test_phase4b.py` — offline positive/failure/safety cases and source
  checks of Phase 2 integration.

The **Phase 4A CLI remains staging-only**:
`python -m projectiq_executioner.phase4 --plan ...` never starts a VM.
The new explicitly executing CLI is:
`python -m projectiq_executioner.phase4.vm_run ...`.

No generated scripts run; `guest_http.py` accepts only bounded GET paths on
`127.0.0.1:8765`, HTTP status = 200 and optional exact-body SHA-256 checks.
Missing inputs, authentication and stateful workflows remain for Phase 4C.
The runner does not resolve unresolved oracles by guessing.

## 1. Apply incremental patch to the pushed Phase 4A branch

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current) -ne 'executioner') { throw 'Use executioner branch' }
$patch = "$HOME\Downloads\projectiq_phase4b_vm_functional_tests.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch mismatch; do not partially apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Apply failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed' }
```

## 2. Focused offline checks (no guest application execution)

```powershell
python -m unittest `
  projectiq_executioner.tests.test_phase4a `
  projectiq_executioner.tests.test_phase4b `
  projectiq_executioner.tests.test_phase3_generator `
  projectiq_executioner.tests.test_phase3d_quality `
  projectiq_executioner.tests.test_phase3e_benchmark -q

if ($LASTEXITCODE -ne 0) { throw 'Phase 3/4 focused regression failed' }

# Phase 2 boundary touched; specifically check earlier multi-adapter VM contracts.
python -m unittest `
  projectiq_executioner.tests.test_phase2d_d3 `
  projectiq_executioner.tests.test_phase2d_d4 `
  projectiq_executioner.tests.test_phase2d_d5e_closeout -q

if ($LASTEXITCODE -ne 0) { throw 'Phase 2 regression failed; do not launch QEMU' }
```

Phase 4B includes tests for the guest lifecycle call inside both `try/finally`
blocks and malformed-bundle denial before any VM toolchain launch.
One symlink test may be skipped on Windows without privileges; inspect any other skip.

## 3. Exactly ONE operator-approved PHP/MySQL smoke test

Reuse the actual approved PHP/MySQL plan which produced `STAGED_NOT_EXECUTED`
and `GET /public/login.html`. **Do not edit `approved_snapshots` just to make a
new fixture pass.** If you have several older `phase4a_approved_*` folders,
choose the correct one deliberately.

```powershell
$base = Get-ChildItem 'outputs/executioner' -Directory |
  Where-Object { $_.Name -like 'phase4a_approved_*' } |
  Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $base) { throw 'Approved 4A evidence folder not found' }
$old = Get-Content "$base/phase4/phase4_handoff.json" -Raw -Encoding UTF8 | ConvertFrom-Json
if ($old.status -ne 'STAGED_NOT_EXECUTED' -or $old.adapter -ne 'php_mysql_v1') {
  throw 'Select your already-approved PHP/MySQL Phase 4A folder; do not alter approval'
}
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4b_php_$stamp"
python -m projectiq_executioner.phase4.vm_run `
  --plan "$base/phase3/test_plan.json" `
  --candidate "$base/phase2/phase2c_candidate.json" `
  --vm-config vm_config.json `
  --out $out
$exitCode = $LASTEXITCODE
$r = Get-Content "$out/phase4_run.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r | Select-Object status,execution,cleanup,issue_code
$r.summary | Format-List
$r.results | Format-Table test_id,result,http_status,issue_code
if ($exitCode -ne 0) { Write-Warning 'Investigate the evidence; do not push yet' }
```

Expected *if* source authorization, image/toolchain, guest readiness, test and
cleanup all work: `TESTS_PASSED`, one HTTP 200 assertion, and
`VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`. **This is a target, not a verified
claim.** A functional assertion mismatch should produce `TESTS_FAILED`
with clean cleanup; infrastructure and missing or malformed guest evidence
must not masquerade as a pass.

Keep the three output files:

- `phase4_handoff.json`: approved schema/selected test IDs (never a VM pass).
- `vm_execution.json`: Phase 2's real guest startup, resource and cleanup evidence.
- `phase4_run.json`: the reviewed functional test verdict, selected evidence and limitations.

`PHASE4_REPORT_INVALID` is an explicit Phase 2 VM status when startup succeeded
but the guest did not return valid Phase 4 test results. Cleanup still runs.

## 4. Review and safety acceptance

After a clean approved fixture run, we should run *one* appropriate legacy Phase
2 reviewed-fixture smoke test with **no** test bundle to confirm the optional
argument did not change old behavior. We should also verify a deliberately
wrong assertion produces `TESTS_FAILED` with **clean VM teardown**, but only
on the same previously reviewed exact SHA; no blanket VM test matrix needed yet.
Do not edit application code to force a pass.

Block release/push and inspect if either guest placement evidence or managed
cleanup degrades, or a malformed test bundle launches a VM.

### Status definitions

| Status | Meaning |
|---|---|
| `TESTS_PASSED` | All selected grounded assertions matched; cleanup verified |
| `TESTS_FAILED` | At least one actual HTTP assertion disagreed; cleanup verified |
| `TESTS_INCONCLUSIVE` | Loopback unreachable or body limit reached; cleanup verified |
| `BLOCKED` | Handoff denied, no VM launch |
| `NO_RUNNABLE_TESTS` | No eligible oracle; no VM launch |
| `VM_EXECUTION_NOT_VERIFIED` | No verified completed VM test execution; do not claim tests passed |
| `GUEST_RESULTS_NOT_VERIFIED` | Guest results are absent/invalid; no pass claim |
| `CLEANUP_FAILED` | Cleanup not verified; **manual inspection required** |
| `INPUT_ERROR` | Invalid input or operator configuration, no success claim |

**Remaining work:** Complete the real Windows/QEMU acceptance before calling
4B finished. Phase 4C will add controlled request inputs, authenticated
operations and richer source-/specification-backed functional oracles.
