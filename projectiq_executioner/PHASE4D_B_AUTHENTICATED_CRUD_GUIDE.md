# Phase 4D-B — Authenticated CRUD + independent read-only MySQL state witnesses

**Baseline:** pushed `executioner` commit `511df6b4e2fdc53cdc28f1e8daf34b38850d4a2a` (4D-A).

**Pilot:** only reviewed `VarunRGB/Mini-Task-Collaboration-App@c4f7965d16807fa05c3d7ffeee54f86fee4d0a37`, adapter `php_mysql_v1`. 4D-B adds one real authenticated **CREATE → READ → UPDATE → READ → DELETE → READ** flow, with fresh D2-generated guest-only identity/session and scoped PDO SELECT state checks at BEFORE, CREATED, UPDATED and DELETED. Nothing runs on the Windows host except trusted Python, Git and QEMU/SSH tooling. It does not authorize unreviewed repositories or certify general sandbox isolation.

**Unlike 4D-A**, task creation/update/deletion occur through app APIs under an authenticated, newly registered user; there is no preseeded task. The guest sends the captured integer ID from the first read in both update and delete, checks API responses **and** independent SQL read-only observations of `(tasks.id, users.email)` ownership. Positive acceptance cannot rely on a successful HTTP 200 alone. Password, cookie, generated email, raw responses and task ID remain entirely inside the disposable guest. Reports contain only small integers and booleans.

The exact **pinned source** contains:

- `POST /logic/add_task.php` — inserts a task for `$_SESSION['user_id']`, returns `{"success":true}`.
- `GET /logic/get_tasks.php` — lists task records including `id,title,deadline,priority,status`.
- `POST /logic/edit_task.php` — updates a task using `task_id` and session user.
- `POST /logic/delete_task_user.php` — deletes using `task_id` and session user.

The trusted guest uses the D2 registration/login/authenticated flow **first**, with a new callback after all four existing auth assertions pass. It never exports session credentials. The existing 4D-A workflow, D1 fixture mode, D3 postconditions, cross-repo D5 and legacy VM mode remain mutually exclusive and are not weakened.

## 1. Build the source-matched patch, then apply

Unlike earlier patches built against abbreviated local baseline copies, this package contains a **patch builder that validates your exact pushed files and produces a normal Git patch locally**. It does not modify source. This guards against a mismatched `orchestrator.py`/`guest_agent.py` baseline and checks Python syntax before patch creation.

Download `projectiq_phase4db_package.zip` and expand it to Downloads. At ProjectIQ root:

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current) -ne 'executioner') {throw 'Wrong branch'}
if ((git rev-parse HEAD) -ne '511df6b4e2fdc53cdc28f1e8daf34b38850d4a2a') {throw 'Unexpected HEAD: review changes first'}
$package = "$HOME\Downloads\projectiq_phase4db_package"
Expand-Archive -LiteralPath "$HOME\Downloads\projectiq_phase4db_package.zip" -DestinationPath $package -ErrorAction Stop
$patch = "outputs/executioner/phase4db_source_matched.patch"
python "$package/build_phase4db_patch.py" --out $patch
if ($LASTEXITCODE -ne 0) {throw 'Patch builder rejected current source; do not force'}
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) {throw '4D-B patch does not apply; do not force'}
git apply $patch
if ($LASTEXITCODE -ne 0) {throw '4D-B patch apply failed'}
git diff --check
if ($LASTEXITCODE -ne 0) {throw 'Whitespace validation failed'}
```

Expected: **10 files** (4 existing VM integration files + 6 new sources/tests/guide). If `--out` already exists, choose a fresh patch filename; never overwrite or bypass a failed check.

## 2. Offline regression first — NO QEMU

```powershell
python -m unittest `
    projectiq_executioner.tests.test_phase4db_crud `
    projectiq_executioner.tests.test_phase4da_dependent_api `
    projectiq_executioner.tests.test_phase4cd2_auth `
    projectiq_executioner.tests.test_phase4cd3_db_postconditions `
    projectiq_executioner.tests.test_phase4cd5_cross_repository -q
if ($LASTEXITCODE -ne 0) {throw '4D-B regression failed; DO NOT launch QEMU'}
Write-Host '4D-B OFFLINE REGRESSION: PASS'
```

The local construction tests include synthetic HTTP/DB injections and review tampering; they do **not** prove real guest integration. Note any skipped tests and verify they are expected Windows privilege-related cases.

## 3. Reuse reviewed MiniCollabApp Phase 3 plan and approved D2 catalogs

Use the **corrected** 4D-A source-reviewed plan, not the original plan with `GET /logic/get_tasks.php` recorded as `UNKNOWN`:

```powershell
$d4aDir = 'outputs/executioner/phase4da_inputs_20261122_180904'
$workflowPlanPath = "$d4aDir/phase3_reviewed/test_plan.json"
if (-not (Test-Path $workflowPlanPath)) {throw 'Reviewed 4D-A Phase 3 plan unavailable'}
if (-not $base -or -not (Test-Path "$base/phase2/phase2c_candidate.json")) {
    throw 'Restore the original approved MiniCollabApp $base directory from earlier work'
}
if (-not $authCatalogPath -or -not (Test-Path $authCatalogPath)) {
    throw 'Restore the actual approved D2 $authCatalogPath; do not invent one'
}
if (-not $protectedOraclesPath -or -not (Test-Path $protectedOraclesPath)) {
    throw 'Restore the actual approved D2 $protectedOraclesPath'
}
$p3 = Get-Content $workflowPlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($p3.source.repository -ne 'VarunRGB/Mini-Task-Collaboration-App' -or
    $p3.source.commit_sha -ne 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37') {
    throw 'Incorrect pinned MiniCollabApp plan'
}
```

The reviewed D2 catalogs and their SHA-bound approvals must still exist in `vm_config.json`. No new database fixture or fullstack runtime profile is needed.

## 4. Create the inert reviewed CRUD proposal

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$d4bDir = "outputs/executioner/phase4db_inputs_$stamp"
New-Item $d4bDir -ItemType Directory -ErrorAction Stop | Out-Null
$crudPath = "$d4bDir/crud.json"
python -m projectiq_executioner.phase4.crud_workflow_stage `
    --plan $workflowPlanPath --out $crudPath
if ($LASTEXITCODE -ne 0) {throw 'Phase 3 does not establish the required CRUD routes'}
Get-Content $crudPath -Raw -Encoding UTF8
```

Verify all **six real Phase 3 test IDs** and the following reviewed literals before approval:

- CREATE: `ProjectIQ CRUD Task`, `2027-03-15`, `Low`.
- UPDATE: `ProjectIQ CRUD Updated`, `2027-03-16`, `High`.
- Endpoint roles: `POST add_task`, `GET get_tasks`, `POST edit_task`, `GET get_tasks`, `POST delete_task_user`, `GET get_tasks`.

The source-reviewed `get_tasks.php` GET ID should match 4D-A's `TC-DBBD1D1C5869CFA1`. No arbitrary SQL or scripts appear in the JSON.

## 5. Add a *separate* exact workflow approval

This is an independent operator decision. Review the pinned PHP handlers, approved D2 catalogs and freshly staged JSON. Then:

```powershell
$hash = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]),32768)))" $crudPath
if ($LASTEXITCODE -ne 0 -or $hash -notmatch '^[a-f0-9]{64}$') {throw 'CRUD digest invalid'}
$ident = "$($p3.source.repository)@$($p3.source.commit_sha)"
$approval = "$ident#$hash"
Write-Host "REVIEW EXACT 4D-B APPROVAL: $approval"
Copy-Item vm_config.json "$d4bDir/vm_config_before_4db.json" -ErrorAction Stop
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if ($ident -cnotin @($cfg.approved_snapshots)) {throw 'Source SHA not approved'}
if (-not $cfg.PSObject.Properties['approved_crud_workflows']) {
    $cfg | Add-Member -NotePropertyName approved_crud_workflows -NotePropertyValue @()
}
$cfg.approved_crud_workflows = @(@($cfg.approved_crud_workflows) + $approval | Select-Object -Unique)
[System.IO.File]::WriteAllText((Resolve-Path vm_config.json).Path,
    ($cfg | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
```

Keep `vm_config.json` and every approval/output file local and untracked. This workflow also requires the *previous* exact `approved_auth_flows` and `approved_oracle_catalogs` entries; never bypass them.

## 6. Preflight — no VM

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$pf = "outputs/executioner/phase4db_preflight_$stamp"
python -m projectiq_executioner.phase4.crud_workflow_vm_run `
    --plan $workflowPlanPath `
    --auth $authCatalogPath --oracles $protectedOraclesPath `
    --crud $crudPath `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json --out $pf --preflight-only
if ($LASTEXITCODE -ne 0) {throw '4D-B preflight rejected; DO NOT launch VM'}
$p = Get-Content "$pf/d4b_preflight.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$p.steps | Format-Table role,method,path -AutoSize
if ($p.status -ne 'APPROVED_NOT_EXECUTED' -or $p.execution -ne 'NOT_PERFORMED' -or
    @($p.steps).Count -ne 6) {throw 'Invalid preflight evidence'}
Write-Host '4D-B PREFLIGHT: PASS'
```

## 7. One positive real QEMU CRUD acceptance

Only launch once preflight passes. The controlled VM registers a fresh identity, logs in, creates one task, reads and captures the ID, checks its database ownership, edits the task, verifies both API and DB fields, deletes the same ID and verifies API + DB absence.

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4db_vm_$stamp"
python -m projectiq_executioner.phase4.crud_workflow_vm_run `
    --plan $workflowPlanPath `
    --auth $authCatalogPath --oracles $protectedOraclesPath `
    --crud $crudPath `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json --out $out `
    --confirm-reviewed-crud
$rc = $LASTEXITCODE
if (-not (Test-Path "$out/d4b_run.json")) {throw 'Missing 4D-B VM evidence'}
$r = Get-Content "$out/d4b_run.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r | Select-Object status,execution,cleanup,issue_code
$r.auth_summary | Format-List
$r.observations | Format-List
if ($rc -ne 0 -or $r.status -ne 'CRUD_PASSED' -or
    $r.execution -ne 'PERFORMED_IN_DISPOSABLE_VM' -or
    $r.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' -or
    $r.auth_summary.passed -ne 4 -or
    $r.observations.before_count -ne 0 -or
    $r.observations.created_db_count -ne 1 -or
    $r.observations.created_db_fields_match -ne $true -or
    $r.observations.updated_db_count -ne 1 -or
    $r.observations.updated_db_fields_match -ne $true -or
    $r.observations.deleted_db_count -ne 0 -or
    $r.observations.deleted_matches -ne 0) {
    throw '4D-B REAL VM ACCEPTANCE FAILED. DO NOT PUSH.'
}
Write-Host '4D-B AUTHENTICATED CRUD: PASS'
```

Failure classification: `CRUD_FAILED` means functional API/DB assertions failed after valid VM evidence; `CRUD_INCONCLUSIVE` means the D2/CRUD prerequisites or observations were unavailable; `CRUD_VM_NOT_VERIFIED` means worker/evidence failure; `CLEANUP_FAILED` is a hard stop. Do not rerun until reviewing bounded evidence in `$out/d4b_vm_execution.json` and `$out/d4b_run.json`.

## 8. One legacy Phase 2 VM compatibility check

Since `guest_agent.py`, `orchestrator.py`, `qemu.py` and the D2 helper changed, run **one** legacy PHP/MySQL VM smoke check after CRUD passes; do not repeat every past D1–D5 VM matrix.

```powershell
$helper = "$HOME\Downloads\projectiq_phase4b_two_acceptance_checks.py"
if (-not (Test-Path $helper)) {throw 'Previously approved legacy acceptance helper missing'}
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$legacyOut = "outputs/executioner/phase4db_legacy_$stamp"
python $helper --mode legacy `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json --out $legacyOut `
    --confirm-reviewed-vm-run
if ($LASTEXITCODE -ne 0) {throw '4D-B legacy regression FAILED. DO NOT PUSH'}
$legacy = Get-Content "$legacyOut/legacy_vm_execution.json" -Raw -Encoding UTF8 | ConvertFrom-Json
if ($legacy.status -ne 'STARTUP_VERIFIED' -or
    $legacy.execution -ne 'PERFORMED_IN_DISPOSABLE_VM' -or
    $legacy.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED') {throw 'Legacy evidence invalid'}
Write-Host '4D-B LEGACY: PASS'
```

## 9. Git closeout

Only after positive real QEMU acceptance, legacy compatibility and final offline regression:

```powershell
git diff --check
git status --short
git add projectiq_executioner/phase4/guest_crud.py `
        projectiq_executioner/phase4/crud_workflow_contracts.py `
        projectiq_executioner/phase4/crud_workflow_stage.py `
        projectiq_executioner/phase4/crud_workflow_vm_run.py `
        projectiq_executioner/phase4/guest_api.py `
        projectiq_executioner/phase2/vm_worker/orchestrator.py `
        projectiq_executioner/phase2/vm_worker/guest_agent.py `
        projectiq_executioner/phase2/vm_worker/qemu.py `
        projectiq_executioner/tests/test_phase4db_crud.py `
        projectiq_executioner/PHASE4D_B_AUTHENTICATED_CRUD_GUIDE.md
if ($LASTEXITCODE -ne 0) {throw 'Git add failed'}
git diff --cached --check
git diff --cached --stat
# Inspect; the staged diff must not contain vm_config.json, outputs, IDs, cookies, passwords or runtime files.
git commit -m 'Verify 4D-B authenticated CRUD and MySQL postconditions'
if ($LASTEXITCODE -ne 0) {throw 'Commit failed'}
git push origin executioner
if ($LASTEXITCODE -ne 0) {throw 'Push failed'}
```

**Exit boundary:** this verifies a single operator-reviewed PHP/MySQL task flow, not an arbitrary CRUD engine, browser UI, authorization-attack resistance or comprehensive project coverage. Phase 4D-C browser testing remains subsequent scope. Security stays `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.
