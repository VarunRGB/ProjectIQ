# ProjectIQ Phase 4D-A — dependent API workflow using a live response ID

**Baseline:** `executioner` branch, pushed 4C-D5 commit `c585b28337`.
**Scope:** a bounded, operator-reviewed, one-row PHP/MySQL pilot. This is not
arbitrary workflow scripting, broad CRUD coverage, a browser runner, or an
attested untrusted-student execution environment.

## What is new and why it is not redundant

Phase 4C-D1 proved controlled MySQL seeding. D3 proved registration persistence;
D4 proved fresh-VM reset, and D5 proved two different reviewed repo workflows.
4D-A performs *actual cross-request data passing*: seed one harmless task;
GET a JSON list, locate the unique task by reviewed title, capture its **actual
runtime ID** (must equal the reviewed seeded ID), POST that captured ID to the
reviewed deletion endpoint, then GET the list again and prove that task is gone.
If discovery fails, **no delete is attempted**. Neither the task title nor ID
is written to the report. One disposable VM per acceptance, one signed lease,
verified VM/storage cleanup. No host-side student execution, arbitrary URLs,
headers, cookies, SQL scripts, or raw response logs.

The pinned fixture used here is:
`VarunRGB/Mini-Task-Collaboration-App@c4f7965d16807fa05c3d7ffeee54f86fee4d0a37`.
The source-defined endpoints are:

- `GET /logic/get_tasks.php`: returns task objects with `id`, `title`, etc.
- `POST /logic/delete_task.php`: accepts JSON `id`, returns `{ "success": true }`
  after the SQL deletion branch. The HTTP status alone does not prove deletion.
- `GET /logic/get_tasks.php`: the captured task must be absent.

The source uses an **unauthenticated delete route**. This pilot exercises it
ONLY in the disposable guest against an explicitly seeded synthetic task. It
is **not** evidence of proper authorization; test that separately in later 4D.

## 1. Apply only after pushed D5

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current) -ne 'executioner') { throw 'Wrong branch' }
$patch = "$HOME\Downloads\projectiq_phase4da_dependent_api.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw '4D-A patch check failed; do not apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw '4D-A patch apply failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw '4D-A whitespace check failed' }
Write-Host 'D4A PATCH APPLIED'
```

## 2. Offline regressions, NO VM

```powershell
python -m unittest `
  projectiq_executioner.tests.test_phase4da_dependent_api `
  projectiq_executioner.tests.test_phase4cd1_db_fixtures `
  projectiq_executioner.tests.test_phase4cd3_db_postconditions `
  projectiq_executioner.tests.test_phase4cd5_cross_repository -q
if ($LASTEXITCODE -ne 0) { throw '4D-A offline regression failed; do not launch VM' }
```

The new guest tests prove ID capture, unique-row requirement, response binding,
no deletion after failed capture, negative mutation, post-delete persistence,
forged report rejection, and boundary wiring. A Windows QEMU run is still
required; mock tests cannot establish actual VM acceptance.

## 3. Select existing approved MiniCollabApp inputs

The previously approved `$base` folder contains `phase3/test_plan.json` and
`phase2/phase2c_candidate.json`. If `$base` is not defined in your current
PowerShell session, recover it by checking the existing directories:

```powershell
Get-ChildItem outputs/executioner -Directory -Filter 'phase4a_approved_*' |
  Sort-Object Name -Descending | Select-Object FullName
# Choose the directory that actually contains the pinned MiniCollabApp plan
# and candidate. Do not choose only by timestamp.
$base = '<ACTUAL_EXISTING_PHASE4A_APPROVED_DIRECTORY>'
if (-not (Test-Path "$base/phase3/test_plan.json") -or
    -not (Test-Path "$base/phase2/phase2c_candidate.json")) {
  throw 'Original approved MiniCollabApp plan/candidate not found'
}
$p3 = Get-Content "$base/phase3/test_plan.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$pc = Get-Content "$base/phase2/phase2c_candidate.json" -Raw -Encoding UTF8 | ConvertFrom-Json
if ($p3.source.repository -ne 'VarunRGB/Mini-Task-Collaboration-App' -or
    $p3.source.commit_sha -ne 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37' -or
    $pc.source.commit_sha -ne $p3.source.commit_sha -or $pc.adapter -ne 'php_mysql_v1') {
  throw 'Unexpected source SHA or adapter; do not approve'
}
```

## 4. Create one synthetic D1 fixture (BOM-free JSON)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$d4aDir = "outputs/executioner/phase4da_inputs_$stamp"
New-Item -ItemType Directory -Path $d4aDir -ErrorAction Stop | Out-Null
$fixturePath = "$d4aDir/fixtures.json"
$fixture = @{
  schema_version = '0.1'
  source = $p3.source
  tables = @(@{ name = 'tasks'; rows = @(@{
    id = 901
    title = 'ProjectIQ fixture task'
    deadline = '2027-03-15'
    priority = 'Low'
    user_id = 2
    status = 'Pending'
  }) })
}
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path $fixturePath),
  ($fixture | ConvertTo-Json -Depth 20), [System.Text.UTF8Encoding]::new($false))
Get-Content $fixturePath -Raw -Encoding UTF8
```

This fixture is disposable. `user_id=2` comes from the pinned SQL seed dump
and is not a production user. Review the SQL schema and row before approval.
If you prefer to reuse a previously approved D1 catalog, you may, but it must
contain **exactly one row** and the same source SHA, and you must pass its
actual path consistently in the remaining commands.

## 5. Review and approve the D1 fixture digest

```powershell
$ident = "$($p3.source.repository)@$($p3.source.commit_sha)"
$fixtureHash = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]),64000)))" $fixturePath
if ($LASTEXITCODE -ne 0 -or $fixtureHash -notmatch '^[a-f0-9]{64}$') { throw 'Invalid fixture digest' }
$fixtureApproval = "$ident#$fixtureHash"
Write-Host "REVIEW D1 APPROVAL: $fixtureApproval"
```

**Only after examining the pinned source and synthetic fixture**, add the
exact `$fixtureApproval` string under `approved_database_fixtures` in your local
`vm_config.json`. Do not modify `approved_snapshots` or other approvals.
The successful existing D1 configuration may already contain this exact entry.

## 6. Stage the response-dependent workflow (NO VM and NO APPROVAL)

```powershell
$workflowPath = "$d4aDir/workflow.json"
python -m projectiq_executioner.phase4.dependent_workflow_stage `
  --plan "$base/phase3/test_plan.json" --fixtures $fixturePath `
  --read-path /logic/get_tasks.php `
  --delete-path /logic/delete_task.php `
  --out $workflowPath
if ($LASTEXITCODE -ne 0) { throw '4D-A stage failed; inspect Phase 3 discovery' }
Get-Content $workflowPath -Raw -Encoding UTF8
```

The helper selects **actual Phase 3 test IDs** and requires unique discovered
GET and POST routes. It does not invent IDs, approve the workflow or run SQL.
Review the following pinned handlers *before* approving their expected
status/JSON behavior:

- https://github.com/VarunRGB/Mini-Task-Collaboration-App/blob/c4f7965d16807fa05c3d7ffeee54f86fee4d0a37/logic/get_tasks.php
- https://github.com/VarunRGB/Mini-Task-Collaboration-App/blob/c4f7965d16807fa05c3d7ffeee54f86fee4d0a37/logic/delete_task.php

The staged expectation `{ "success": true }` is an **operator-review proposal**, not an automatically trusted oracle.

## 7. Approve exact 4D-A digest

```powershell
$wfHash = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]),32768)))" $workflowPath
if ($LASTEXITCODE -ne 0 -or $wfHash -notmatch '^[a-f0-9]{64}$') { throw 'Invalid workflow digest' }
$wfApproval = "$ident#$wfHash"
Write-Host "REVIEW D4A APPROVAL: $wfApproval"
```

After reviewing the workflow, add its exact entry to the **new** local operator
config field `approved_dependent_workflows`. For example, with both reviewed
approval strings computed and confirmed:

```powershell
Copy-Item vm_config.json "$d4aDir/vm_config_before_d4a.json"
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if ($ident -cnotin @($cfg.approved_snapshots)) { throw 'Source SHA not approved' }
foreach ($field in @('approved_database_fixtures','approved_dependent_workflows')) {
  if (-not $cfg.PSObject.Properties[$field]) {
    $cfg | Add-Member -NotePropertyName $field -NotePropertyValue @()
  }
}
$cfg.approved_database_fixtures = @(@($cfg.approved_database_fixtures) + $fixtureApproval | Select-Object -Unique)
$cfg.approved_dependent_workflows = @(@($cfg.approved_dependent_workflows) + $wfApproval | Select-Object -Unique)
[System.IO.File]::WriteAllText((Resolve-Path vm_config.json).Path,
  ($cfg | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
```

This is an **explicit operator approval**, not a step to automate in student-facing software. `vm_config.json` and its backups belong in ignored local storage.

## 8. Strict offline preflight, NO VM

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$pf = "outputs/executioner/phase4da_preflight_$stamp"
python -m projectiq_executioner.phase4.dependent_workflow_vm_run `
  --plan "$base/phase3/test_plan.json" --candidate "$base/phase2/phase2c_candidate.json" `
  --fixtures $fixturePath --workflow $workflowPath --vm-config vm_config.json `
  --out $pf --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'D4A offline preflight failed; do not launch QEMU' }
$p = Get-Content "$pf/d4a_preflight.json" -Raw | ConvertFrom-Json
$p | Select-Object status,execution
$p.steps | Format-Table role,method,path
if ($p.status -ne 'APPROVED_NOT_EXECUTED' -or $p.execution -ne 'NOT_PERFORMED' -or
    @($p.steps).Count -ne 3) { throw 'D4A preflight evidence invalid' }
Write-Host 'D4A PREFLIGHT: PASS'
```

## 9. One real QEMU acceptance (only after Step 8)

```powershell
& {
  $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
  $out = "outputs/executioner/phase4da_vm_$stamp"
  python -m projectiq_executioner.phase4.dependent_workflow_vm_run `
    --plan "$base/phase3/test_plan.json" --candidate "$base/phase2/phase2c_candidate.json" `
    --fixtures $fixturePath --workflow $workflowPath --vm-config vm_config.json `
    --out $out --confirm-reviewed-dependent-workflow
  $rc = $LASTEXITCODE
  if (-not (Test-Path "$out/d4a_run.json")) { throw 'Missing 4D-A evidence' }
  $r = Get-Content "$out/d4a_run.json" -Raw | ConvertFrom-Json
  $r | Select-Object status,execution,cleanup,issue_code
  $r.observations | Format-List
  if ($rc -ne 0 -or $r.status -ne 'WORKFLOW_PASSED' -or
      $r.execution -ne 'PERFORMED_IN_DISPOSABLE_VM' -or
      $r.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' -or
      $r.observations.discovery_matches -ne 1 -or
      $r.observations.captured_matches_seed -ne $true -or
      $r.observations.mutation_subset_match -ne $true -or
      $r.observations.remaining_matches -ne 0) {
    throw 'D4A REAL VM ACCEPTANCE FAILED. Inspect evidence; do not push.'
  }
  Write-Host 'D4A DEPENDENT API WORKFLOW: PASS'
}
```

A positive VM run must show the three sequential HTTP 200 observations, exactly
one discovered synthetic task, its ID matching the D1 seed, deletion returning
`success=true`, and no matching task on the final GET. **Any missing evidence
or cleanup failure is not a PASS.** Do not rerun QEMU blindly after failure.

## 10. Final compatibility and Git closeout

Because this patch modifies `orchestrator.py`, `guest_agent.py` and `qemu.py`,
run **one** previously successful legacy Phase 2 QEMU compatibility check, not
the whole historical VM matrix:

```powershell
$helper = "$HOME\Downloads\projectiq_phase4b_two_acceptance_checks.py"
if (-not (Test-Path $helper)) { throw 'Legacy acceptance helper missing' }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$legacyOut = "outputs/executioner/phase4da_legacy_$stamp"
python $helper --mode legacy `
  --candidate "$base/phase2/phase2c_candidate.json" --vm-config vm_config.json `
  --out $legacyOut --confirm-reviewed-vm-run
if ($LASTEXITCODE -ne 0) { throw '4D-A legacy compatibility FAILED. Do not push.' }
```

Review staging:

```powershell
if ((git branch --show-current) -ne 'executioner') { throw 'Wrong branch' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Source whitespace errors' }
git add projectiq_executioner/PHASE4D_A_DEPENDENT_API_GUIDE.md `
  projectiq_executioner/phase4/guest_workflow.py `
  projectiq_executioner/phase4/dependent_workflow_contracts.py `
  projectiq_executioner/phase4/dependent_workflow_vm_run.py `
  projectiq_executioner/phase4/dependent_workflow_stage.py `
  projectiq_executioner/phase2/vm_worker/orchestrator.py `
  projectiq_executioner/phase2/vm_worker/guest_agent.py `
  projectiq_executioner/phase2/vm_worker/qemu.py `
  projectiq_executioner/tests/test_phase4da_dependent_api.py
git diff --cached --check
git diff --cached --stat
git status --short
```

Stage exactly **nine intended files**. Never commit `vm_config.json`, SQL
secrets, staged fixture/workflow catalogs, raw VM evidence or guest data.
After passing 4D-A real VM acceptance and legacy compatibility:

```powershell
git commit -m 'Verify 4D-A dependent API workflows with captured IDs'
if ($LASTEXITCODE -ne 0) { throw '4D-A commit failed' }
git push origin executioner
if ($LASTEXITCODE -ne 0) { throw '4D-A push failed' }
```

**Security remains:** `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`. More powerful
multi-step workflows, authenticated CRUD, cross-app data binding and browser
interaction are 4D-B/4D-C tasks; this pilot makes no claim to cover them.
