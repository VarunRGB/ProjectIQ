# Phase 4C-D4 — reviewed VM-per-scenario isolation and reset

## Intent and honest scope

**D4 pilot implements hard per-scenario VM isolation** for the pinned, approved
MiniCollabApp PHP/MySQL fixture. It does **not** implement a SQL `TRUNCATE`, an
in-place DB rollback, a fake session reset, or general-purpose student execution.
The reset primitive is the teardown of the entire disposable QEMU QCOW2 overlay
and runtime directory, followed by creation of a distinct signed runtime lease
and clean SQL import for the next scenario.

Each of exactly two approved scenario IDs runs the already-reviewed D3 registration
persistence workflow independently. D4 checks source/digest approvals before the
first VM, executes strictly sequentially, verifies D3's guest-auth and DB witness
before/after evidence, independently verifies QEMU backend, signed runtime cleanup,
and distinct managed runtime IDs. It aborts before the next VM on any failure.

**Limit:** D3's `before_matches=0` is for its freshly generated synthetic email.
This does not compare whole-table DB baselines or prove all unrelated rows are
reset. The VM overlay isolation and validated cleanup are the reset mechanism.
For full CRUD/whole-database baseline checks, implement separately rather than
pretend D3's email check covers them.

The existing D1, D2, D3, Phase 2 and VM transport files are **unchanged**.
Security remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## 1. Apply additive patch / local offline tests

From ProjectIQ repo root, on `executioner` branch after D3 commit:

```powershell
$patch = "$HOME\Downloads\projectiq_phase4cd4_scenario_isolation.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'D4 patch does not apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'D4 patch application failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'D4 whitespace failure' }
python -m unittest projectiq_executioner.tests.test_phase4cd4_scenario_isolation -v
if ($LASTEXITCODE -ne 0) { throw 'D4 offline acceptance failed: no VM launch' }
```

## 2. Operator-readable manifest — exactly two scenarios

This assumes the previously approved `$base`, `$authCatalogPath`,
`$protectedOraclesPath`, `$catalogPath` and local `vm_config.json` from D3
are in the PowerShell session. Do not copy the VM configuration into a Git
repository artifact.

```powershell
foreach ($inputPath in @("$base/phase3/test_plan.json", "$base/phase2/phase2c_candidate.json",
                          "$authCatalogPath", "$protectedOraclesPath", "$catalogPath", 'vm_config.json')) {
    if (-not (Test-Path $inputPath)) { throw "Missing D3 input: $inputPath" }
}
$plan = Get-Content "$base/phase3/test_plan.json" -Raw -Encoding UTF8 | ConvertFrom-Json
if ($plan.source.repository -ne 'VarunRGB/Mini-Task-Collaboration-App' -or
    $plan.source.commit_sha -ne 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37') {
    throw 'Unexpected D4 pilot source'
}
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$d4Dir = "outputs/executioner/phase4cd4_inputs_$stamp"
New-Item $d4Dir -ItemType Directory -ErrorAction Stop | Out-Null
$manifestPath = "$d4Dir/isolation_manifest.json"
$manifest = @{
    schema_version = '0.1'
    source = $plan.source
    strategy = 'FRESH_DISPOSABLE_QCOW2_VM_PER_SCENARIO'
    scenarios = @(
        @{id='registration-first';workflow='REVIEWED_D3_REGISTRATION_PERSISTENCE'},
        @{id='registration-after-reset';workflow='REVIEWED_D3_REGISTRATION_PERSISTENCE'}
    )
}
$json = $manifest | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path $manifestPath), $json,
    [System.Text.UTF8Encoding]::new($false))
Get-Content $manifestPath -Raw
```

Review the two scenario IDs, pinned SHA and reset strategy before approval.

## 3. Separate exact-digest approval, Windows BOM-free

```powershell
$d4Hash = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]), 32768)))" "$manifestPath"
if ($LASTEXITCODE -ne 0 -or $d4Hash -notmatch '^[0-9a-f]{64}$') { throw 'D4 digest invalid' }
$d4Approval = "$($plan.source.repository)@$($plan.source.commit_sha)#$d4Hash"
Write-Host "Review and approve: $d4Approval"
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $cfg.PSObject.Properties['approved_scenario_isolation']) {
    $cfg | Add-Member -NotePropertyName approved_scenario_isolation -NotePropertyValue @()
}
$cfg.approved_scenario_isolation = @(@($cfg.approved_scenario_isolation) + $d4Approval | Select-Object -Unique)
$json = $cfg | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText((Resolve-Path 'vm_config.json').Path, $json,
    [System.Text.UTF8Encoding]::new($false))
python -c "from pathlib import Path; from projectiq_executioner.phase2.vm_worker.orchestrator import load_config; load_config(Path('vm_config.json')); print('CONFIG PASS')"
if ($LASTEXITCODE -ne 0) { throw 'D4 VM config load failed' }
```

Never commit `vm_config.json`, approval inputs, VM outputs, emails or secrets.

## 4. Offline preflight — no VM

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cd4_preflight_$stamp"
python -m projectiq_executioner.phase4.scenario_isolation_vm_run `
    --plan "$base/phase3/test_plan.json" --auth "$authCatalogPath" `
    --oracles "$protectedOraclesPath" --postconditions "$catalogPath" `
    --manifest "$manifestPath" --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json --out $out --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'D4 preflight failed: no VM launch' }
$p = Get-Content "$out/d4_preflight.json" -Raw | ConvertFrom-Json
if ($p.status -ne 'APPROVED_NOT_EXECUTED' -or $p.scenario_count -ne 2) {
    throw 'D4 preflight report unexpected'
}
```

## 5. Real D4 QEMU acceptance — exactly two sequential disposable VMs

```powershell
& {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $out = "outputs/executioner/phase4cd4_vm_$stamp"
    python -m projectiq_executioner.phase4.scenario_isolation_vm_run `
        --plan "$base/phase3/test_plan.json" --auth "$authCatalogPath" `
        --oracles "$protectedOraclesPath" --postconditions "$catalogPath" `
        --manifest "$manifestPath" --candidate "$base/phase2/phase2c_candidate.json" `
        --vm-config vm_config.json --out $out `
        --confirm-reviewed-scenario-isolation
    $exit = $LASTEXITCODE
    $r = Get-Content "$out/d4_run.json" -Raw | ConvertFrom-Json
    $r | Select-Object status,execution,issue_code
    $r.summary | Format-List
    $r.scenarios | Format-Table id,status,cleanup,issue_code -AutoSize
    if ($exit -ne 0 -or $r.status -ne 'SCENARIO_ISOLATION_VERIFIED' -or
        $r.execution -ne 'PERFORMED_IN_DISTINCT_DISPOSABLE_VMS' -or
        $r.summary.total -ne 2 -or $r.summary.passed -ne 2 -or
        $r.summary.failed -ne 0 -or $r.summary.not_executed -ne 0 -or
        @($r.scenarios | Where-Object {$_.status -ne 'RESET_VERIFIED' -or
            $_.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'}).Count -ne 0) {
        throw 'D4 REAL VM ACCEPTANCE FAILED. Do not push.'
    }
    Write-Host 'D4 VM SCENARIO ISOLATION: PASS'
}
```

Expected D4 summary: total=2, passed=2, failed=0, not_executed=0;
both entries `RESET_VERIFIED` and cleanup verified. The JSON artifacts in
`$out/scenarios/<scenario-id>/` hold the original full D3 and VM evidence.
**If one fails, don't launch a new QEMU run; inspect the existing evidence.**

## 6. Final offline and source review

```powershell
python -m unittest projectiq_executioner.tests.test_phase4cd4_scenario_isolation `
    projectiq_executioner.tests.test_phase4cd3_db_postconditions -q
if ($LASTEXITCODE -ne 0) { throw 'D4 final regression failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'D4 whitespace failure' }
git status --short
git add projectiq_executioner/phase4/scenario_isolation_contracts.py `
    projectiq_executioner/phase4/scenario_isolation_vm_run.py `
    projectiq_executioner/tests/test_phase4cd4_scenario_isolation.py `
    projectiq_executioner/PHASE4C_D4_SCENARIO_ISOLATION_GUIDE.md
git diff --cached --check
git diff --cached --stat
```

Stage exactly these four files. No old worker, QEMU, D1-D3 modules, config,
evidence or approvals should be staged. Since D4 doesn't modify Phase 2 or
existing VM entrypoints, and D3 already passed legacy compatibility, no
additional legacy QEMU run is required merely to test this additive controller.

After reviewing the staged diff:

```powershell
git commit -m "Verify D4 fresh-VM isolation for reviewed scenarios"
if ($LASTEXITCODE -ne 0) { throw 'D4 commit failed' }
git push origin executioner
if ($LASTEXITCODE -ne 0) { throw 'D4 push failed' }
```
