# Phase 4C-D5 — cross-repository reviewed functional pilot

## Scope, rationale and security

**Baseline:** `executioner` D4 commit `3ddf03987e79d932b56caa8fafbd4a63d77d1b97`.
This **additive** D5 slice coordinates two already-approved, pinned GitHub repositories
and two different adapters in two separate disposable QEMU/QCOW2 VMs:

1. An existing **PHP/MySQL D3 registration-persistence** workflow (MiniCollabApp
   is the previously accepted fixture). This includes D2 authentication, guest DB
   before/after observation, and cleanup.
2. A **different repository's JSON API** (reviewed 4C-C workflow) using an
   approved Python-web, Node Express or Node full-stack adapter. At least one
   source-backed `JSON_BODY_SUBSET_EQUALS` assertion is required; an HTML 200
   smoke check alone does not qualify.

D5 does not invent expected responses, execute tests on the Windows host, start
arbitrary repositories, or convert readiness probes into full application coverage.
The existing Phase 2 SHA gate, D2/D3 approvals, exact 4C-C oracle approval and
new D5 suite approval are all independent. The second app may be your previously
reviewed **FullStack** Node fixture, but its source SHA, API routes and expected JSON
must come from **your actual pinned Phase 3 plan and source evidence**. Nothing in
this guide presumes its repository name, route or response.

All input catalogs and `vm_config.json` are operator-owned and kept local under
`outputs/executioner`. **Never commit them or the VM evidence**. D5 neither
broadens security attestation nor permits untrusted students; status remains
`NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`. This is cross-repository *pilot coverage*
of two independently reviewed behaviors, not end-to-end coverage of both apps.

## 1. Apply patch and run targeted unit tests (NO QEMU)

From `C:\Users\varun\Code\ProjectIQ` on `executioner` after pushing D4:

```powershell
$patch = "$HOME\Downloads\projectiq_phase4cd5_cross_repository.patch"
if ((git branch --show-current) -ne 'executioner') { throw 'Wrong branch' }
git status --short
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'D5 patch does not apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'D5 patch failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace errors' }
python -m unittest projectiq_executioner.tests.test_phase4cd5_cross_repository -v
if ($LASTEXITCODE -ne 0) { throw 'D5 offline tests failed: no VM run' }
```

The patch adds only a reviewed-suite compiler, an additive controller, an offline
input stager, tests and this guide. It does not change the VM launcher,
`qemu.py`, guest scripts or earlier D1-D4 runners.

## 2. Select the second reviewed repo and review its oracle

You already have the successful D3 MiniCollabApp inputs (`$base`, `$authCatalogPath`,
`$protectedOraclesPath`, `$catalogPath`) and `vm_config.json`.
For the **second**, distinct source you need:

- an actual Phase 3 `test_plan.json` produced from its pinned commit;
- its matching Phase 2 `phase2c_candidate.json` using one of
  `node_fullstack_offline_v1`, `node_express_offline_v1`, `python_web_offline_v1`;
- a reviewed 4C-C oracle catalog with at least one `JSON_BODY_SUBSET_EQUALS`
  expectation, grounded in the source rather than guessed;
- exact entries in `approved_snapshots` and `approved_oracle_catalogs`, independently
  checked by the original 4C-C compiler; and a working offline runtime/adaptor.

Use the previously reviewed FullStack fixture if it satisfies all requirements.
The old Phase 2 FullStack startup success **does not by itself prove** there is
an approved JSON API oracle; the D5 preflight refuses to run without one. If
missing, produce and review this one 4C-C catalog first. Do **not** weaken the
JSON-assertion requirement or fabricate a response to force a PASS.

In the commands below, `$apiBase` is **your actual local output folder** for
that second repository and `$apiCatalogPath` is its **reviewed** 4C-C catalog.
Set these paths based on existing outputs. The example assumes the D3 inputs
are still available in your PowerShell session.

## 3. Stage bound inputs offline (no VM and NO auto-approval)

```powershell
foreach ($p in @("$base/phase3/test_plan.json", "$base/phase2/phase2c_candidate.json",
                "$authCatalogPath", "$protectedOraclesPath", "$catalogPath",
                "$apiBase/phase3/test_plan.json", "$apiBase/phase2/phase2c_candidate.json",
                "$apiCatalogPath", 'vm_config.json')) {
    if (-not $p -or -not (Test-Path $p)) { throw "Missing reviewed input: $p" }
}
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$d5Stage = "outputs/executioner/phase4cd5_stage_$stamp"
python -m projectiq_executioner.phase4.cross_repository_prepare `
    --db-plan "$base/phase3/test_plan.json" `
    --db-candidate "$base/phase2/phase2c_candidate.json" `
    --db-auth "$authCatalogPath" `
    --db-oracles "$protectedOraclesPath" `
    --db-postconditions "$catalogPath" `
    --api-plan "$apiBase/phase3/test_plan.json" `
    --api-candidate "$apiBase/phase2/phase2c_candidate.json" `
    --api-oracles "$apiCatalogPath" `
    --out "$d5Stage"
if ($LASTEXITCODE -ne 0) { throw 'D5 offline staging failed' }
$manifestPath = "$d5Stage/d5_suite.json"
$inputsRoot = "$d5Stage/inputs"
Get-Content $manifestPath -Raw -Encoding UTF8
```

Review the two **different** repository names, 40-character pinned SHAs,
adapters, workflow names, relative paths and `input_sha256` digests. The helper
**does not** append approvals to config, execute student code or start any VM.
Tampering with any staged JSON after approval invalidates the suite input hash.

## 4. Explicit operator suite approval (BOM-free Windows PowerShell)

Only after reviewing the staged suite and both underlying catalogs:

```powershell
$d5Hash = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]), 32768)))" "$manifestPath"
if ($LASTEXITCODE -ne 0 -or $d5Hash -notmatch '^[a-f0-9]{64}$') { throw 'Invalid D5 suite digest' }
$d5Approval = 'cross-repo#' + $d5Hash
Write-Host "Review this new, independent D5 approval: $d5Approval"
# Review before executing the next block; this is an operator decision.
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $cfg.PSObject.Properties['approved_cross_repository_suites']) {
    $cfg | Add-Member -NotePropertyName approved_cross_repository_suites -NotePropertyValue @()
}
$cfg.approved_cross_repository_suites = @(@($cfg.approved_cross_repository_suites) + $d5Approval | Select-Object -Unique)
$json = $cfg | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText((Resolve-Path 'vm_config.json').Path, $json,
    [System.Text.UTF8Encoding]::new($false))
python -c "from pathlib import Path; from projectiq_executioner.phase2.vm_worker.orchestrator import load_config; load_config(Path('vm_config.json')); print('D5 CONFIG LOAD: PASS')"
if ($LASTEXITCODE -ne 0) { throw 'VM config failed to load; no VM launch' }
```

## 5. Offline preflight (both approvals checked before the FIRST VM)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$d5PreflightOut = "outputs/executioner/phase4cd5_preflight_$stamp"
python -m projectiq_executioner.phase4.cross_repository_vm_run `
    --manifest "$manifestPath" --inputs-root "$inputsRoot" `
    --vm-config vm_config.json --out "$d5PreflightOut" --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'D5 preflight failed: do not launch' }
$p = Get-Content "$d5PreflightOut/d5_preflight.json" -Raw | ConvertFrom-Json
$p.cases | Format-Table id,workflow,adapter,test_count
if ($p.status -ne 'APPROVED_NOT_EXECUTED' -or $p.execution -ne 'NOT_PERFORMED' -or
    $p.case_count -ne 2) { throw 'D5 preflight evidence invalid' }
```

If blocked, inspect the *separate* existing SHA/oracle approvals, pinned source,
second adapter, JSON subset assertion and exact staged input digest. No VM has
run; do not bypass a gate.

## 6. Real D5 acceptance (2 distinct disposable QEMU VMs)

Only after Step 5 succeeds:

```powershell
& {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $out = "outputs/executioner/phase4cd5_vm_$stamp"
    python -m projectiq_executioner.phase4.cross_repository_vm_run `
        --manifest "$manifestPath" --inputs-root "$inputsRoot" `
        --vm-config vm_config.json --out $out `
        --confirm-reviewed-cross-repository
    $code = $LASTEXITCODE
    if (-not (Test-Path "$out/d5_run.json")) { throw 'D5 evidence missing' }
    $r = Get-Content "$out/d5_run.json" -Raw | ConvertFrom-Json
    $r | Select-Object status,execution,issue_code
    $r.summary | Format-List
    $r.cases | Format-Table id,workflow,adapter,status,cleanup,issue_code -AutoSize
    if ($code -ne 0 -or $r.status -ne 'CROSS_REPOSITORY_VERIFIED' -or
        $r.execution -ne 'PERFORMED_IN_DISTINCT_DISPOSABLE_VMS' -or
        $r.summary.total -ne 2 -or $r.summary.passed -ne 2 -or
        $r.summary.failed -ne 0 -or $r.summary.not_executed -ne 0 -or
        @($r.cases | Where-Object {$_.status -ne 'VERIFIED' -or
            $_.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'}).Count -ne 0) {
        throw 'D5 cross-repository VM acceptance failed. DO NOT PUSH.'
    }
    Write-Host 'D5 CROSS-REPOSITORY ACCEPTANCE: PASS'
}
```

Expected: `CROSS_REPOSITORY_VERIFIED`, total=2, passed=2, distinct
managed signed VM identities, verified cleanup after **each** case, and source,
Oracle/Phase 3 bindings for both repos. Inspect full original reports in
`$out/cases/registration/` and `$out/cases/json-api/` when troubleshooting.
A true assertion failure is distinguished from transport/cleanup failures.
**If any check fails, D5 stops without launching additional cases.**

## 7. Final source review, regression and push

The new tests cover wrong SHAs/paths/input digests, oracle absence, missing
cleanup, distinct leases, execution abort, and functional-vs-infrastructure
classification. The successful D3 and 4C-C runners were not modified, so do
not repeat old three-fixture QEMU benchmarking solely for this controller.

```powershell
python -m unittest projectiq_executioner.tests.test_phase4cd5_cross_repository `
    projectiq_executioner.tests.test_phase4cd4_scenario_isolation -q
if ($LASTEXITCODE -ne 0) { throw 'D5 final offline regression failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed' }
git add projectiq_executioner/phase4/cross_repository_contracts.py `
    projectiq_executioner/phase4/cross_repository_vm_run.py `
    projectiq_executioner/phase4/cross_repository_prepare.py `
    projectiq_executioner/tests/test_phase4cd5_cross_repository.py `
    projectiq_executioner/PHASE4C_D5_CROSS_REPOSITORY_GUIDE.md
git diff --cached --check
git diff --cached --stat
git status --short
```

Stage **only these five files**; do not include output, config, staged catalogs,
credentials, private test data, or prior VM artifacts. After inspecting the diff:

```powershell
git commit -m 'Verify D5 cross-repository reviewed functional pilot'
if ($LASTEXITCODE -ne 0) { throw 'D5 commit failed' }
git push origin executioner
if ($LASTEXITCODE -ne 0) { throw 'D5 push failed' }
```

## Limits and next work

This pilot proves **one reviewed registration database effect** plus at least
**one reviewed JSON response assertion** across two dissimilar stacks. It does
not test arbitrary repositories, compare whole tables, execute browser flows,
prove complete CRUD correctness, or provide a security attestation. General
Phase 4D browser interactions and 4E benchmarking remain subsequent work.
