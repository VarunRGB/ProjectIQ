# ProjectIQ Phase 4C-D3 — Approved database postconditions (registration slice)

**Baseline:** `executioner` branch, D2 commit `55a30a1e4354b3b2ddaeae8fc25f7fb686445d66`.
**Scope:** For reviewed PHP/MySQL projects, verify a disposable user's persistence with **read-only** MySQL queries immediately before and after D2's application-level registration. Neither HTTP 302 nor successful cookie-based login alone counts as database proof.

## Capability / boundaries

- D3 reuses the **existing** Phase 2 QEMU worker, MySQL database-scoped user and D2 registration/login flow. It adds no second VM launcher, arbitrary SQL, host-side app execution, network egress or raw database export.
- Three existing independent approvals remain required: exact `approved_snapshots`, approved D2 `approved_auth_flows`, approved 4C `approved_oracle_catalogs`. New **fourth** approval is `approved_database_postconditions: ["Owner/Repo@SHA#POSTCONDITION_CANONICAL_SHA256"]`.
- Operator reviews a strict JSON `checks` catalog. Each check selects rows using the **guest-generated email** as the sole prepared-statement value and verifies non-sensitive literal fields that were present in the reviewed registration input. Identifiers are bounded and validated on host and guest; no SQL clause can be provided in the catalog.
- Guest executes trusted, fixed PHP PDO SELECT code using **only** `projectiq_run` scoped credentials. The guest sees raw rows transiently; the host sees only `before_matches` (capped at 2), `after_matches` (capped at 2) and `expected_fields_match` (bool). No email/password, row text, SQL, cookie, session token, or database credential in reports.
- PASS requires: D2's four steps pass, **before 0**, **after exactly 1**, matching expected literal fields; VM runtime verified, cgroup limits verified, and disposable VM/storage cleanup verified. Unexpected database state is a functional FAIL; missing DB observation is infrastructure/inconclusive, never PASS.
- Existing D1 database fixtures and D2 standalone authentication continue to work unchanged. D3 is a **mutually exclusive mode**, and must not be combined with D1 fixture seeding or standalone API test bundles.
- **Not yet D3 CRUD-wide coverage:** the current slice proves registration INSERT persistence. Upcoming D3 extensions can inspect task create/update/delete postconditions using separately reviewed mutation requests; D4 handles per-scenario clean resets. Security remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## 1. Apply to the committed D2 branch

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current) -ne 'executioner') { throw 'Wrong branch' }
git status --short
$patch = "$HOME\Downloads\projectiq_phase4cd3_db_postconditions.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch does not apply to committed D2' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'D3 patch apply failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'D3 whitespace check failed' }
```

## 2. Targeted offline regressions — do not launch QEMU yet

```powershell
python -m unittest `
    projectiq_executioner.tests.test_phase4cd3_db_postconditions `
    projectiq_executioner.tests.test_phase4cd2_auth `
    projectiq_executioner.tests.test_phase4cd1_db_fixtures -q
if ($LASTEXITCODE -ne 0) { throw 'Offline D3 regression failed' }
```

## 3. Prepare an operator-reviewable D3 postcondition manifest

Reuse the **same** `$base`, `$authCatalogPath`, `$protectedOraclesPath` from successful D2. No credential or database password is used in this manifest.

```powershell
if (-not $base -or -not $authCatalogPath -or -not $protectedOraclesPath) {
    throw 'D2 approved inputs not in current PowerShell session'
}
$p3 = Get-Content "$base/phase3/test_plan.json" -Raw -Encoding UTF8 | ConvertFrom-Json
if ($p3.source.repository -ne 'VarunRGB/Mini-Task-Collaboration-App' -or
    $p3.source.commit_sha -ne 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37') {
    throw 'This acceptance example is only for the reviewed pinned MiniCollabApp'
}
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$d3Dir = "outputs/executioner/phase4cd3_catalogs_$stamp"
New-Item $d3Dir -ItemType Directory -ErrorAction Stop | Out-Null
$catalogPath = "$d3Dir/postconditions.json"
@{
    schema_version = '0.1'
    source = $p3.source
    checks = @(@{
        table = 'users'
        lookup_column = 'email'
        expected_fields = @{ name = 'ProjectIQ Test'; role = 'user' }
    })
} | ConvertTo-Json -Depth 20 | Set-Content $catalogPath -Encoding UTF8
Get-Content $catalogPath -Raw -Encoding UTF8
```

The checker will query the existing `users` table at `email = <guest-generated-email>`, then compare only `name=ProjectIQ Test` and `role=user`. These are non-sensitive fields submitted by the approved D2 registration request. It does **not** extract or compare password hashes.

## 4. Add exact D3 approval (after reviewing JSON and pinned source)

Do not auto-approve arbitrary discovered source. Compute this catalog's canonical hash, then explicitly review and append its exact approval. Protect local VM config and do not commit it.

```powershell
$d3Hash = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]), 32768)))" $catalogPath
if ($LASTEXITCODE -ne 0 -or $d3Hash -notmatch '^[a-f0-9]{64}$') { throw 'Bad D3 hash' }
$d3Approval = "$($p3.source.repository)@$($p3.source.commit_sha)#$d3Hash"
Write-Host "Review D3 approval: $d3Approval"
Copy-Item vm_config.json "$d3Dir/vm_config_before_d3.json"
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $cfg.PSObject.Properties['approved_database_postconditions']) {
    $cfg | Add-Member -NotePropertyName approved_database_postconditions -NotePropertyValue @()
}
$cfg.approved_database_postconditions = @(@($cfg.approved_database_postconditions) + $d3Approval | Select-Object -Unique)
$json = $cfg | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText((Resolve-Path vm_config.json).Path, $json,
    [System.Text.UTF8Encoding]::new($false))
```

## 5. D3 preflight (no VM)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cd3_preflight_$stamp"
python -m projectiq_executioner.phase4.db_postcondition_vm_run `
    --plan "$base/phase3/test_plan.json" `
    --auth "$authCatalogPath" `
    --oracles "$protectedOraclesPath" `
    --postconditions "$catalogPath" `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json --out $out --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'D3 preflight failed; do not launch' }
```

Expected `APPROVED_NOT_EXECUTED`, `NOT_PERFORMED`, `check_count=1`. If blocked, review SHA, exact approval digest and registration literals; **never weaken a gate** to force a PASS.

## 6. First real QEMU D3 acceptance

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cd3_vm_$stamp"
python -m projectiq_executioner.phase4.db_postcondition_vm_run `
    --plan "$base/phase3/test_plan.json" `
    --auth "$authCatalogPath" `
    --oracles "$protectedOraclesPath" `
    --postconditions "$catalogPath" `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json --out $out --confirm-reviewed-db-postconditions
$exit = $LASTEXITCODE
$r = Get-Content "$out/d3_run.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r | Select-Object status,execution,cleanup,session_cookie_observed,issue_code
$r.auth_summary | Format-List
$r.summary | Format-List
$r.results | Format-Table id,result,before_matches,after_matches,expected_fields_match,issue_code -AutoSize
if ($exit -ne 0 -or $r.status -ne 'POSTCONDITIONS_PASSED' -or
    $r.execution -ne 'PERFORMED_IN_DISPOSABLE_VM' -or
    $r.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' -or
    $r.session_cookie_observed -ne $true -or
    $r.summary.passed -ne 1 -or $r.summary.failed -ne 0 -or
    $r.auth_summary.passed -ne 4) { throw 'D3 VM acceptance failed; do not push' }
```

Expected single DB check: `before_matches=0`, `after_matches=1`, `expected_fields_match=True`, `PASS`. All four D2 steps must pass in the same VM. Cleanup must be verified independently.

## 7. Negative oracle + legacy acceptance (closeout, not before the positive run)

To prove we aren't trusting HTTP 200/302 alone, use a **separately inspected, intentionally false** postcondition check with `lookup_column=name` (the generated email won't equal the literal name). It must pass structural preflight **with its own exact digest approval in a separate temporary VM config**, then return `POSTCONDITIONS_FAILED` after HTTP registration/login pass and VM cleanup. Do not change the main approved catalog or approve this negative catalog in your permanent VM config. If using a temporary config, preserve all existing settings and update only its `approved_database_postconditions` list.

Also run one previously approved `--mode legacy` acceptance (PHP/MySQL) with `projectiq_phase4b_two_acceptance_checks.py`, **not** every past QEMU regression. The legacy run must print `ACCEPTANCE: PASS` and verify disposable cleanup.

## 8. Commit only after acceptance

```powershell
git diff --check
python -m unittest projectiq_executioner.tests.test_phase4cd3_db_postconditions `
    projectiq_executioner.tests.test_phase4cd2_auth `
    projectiq_executioner.tests.test_phase4cd1_db_fixtures -q
if ($LASTEXITCODE -ne 0) { throw 'Final D3 regression failed' }
git status --short
git add projectiq_executioner/PHASE4C_D3_DB_POSTCONDITIONS_GUIDE.md `
    projectiq_executioner/phase2/vm_worker/guest_agent.py `
    projectiq_executioner/phase2/vm_worker/orchestrator.py `
    projectiq_executioner/phase4/guest_api.py `
    projectiq_executioner/phase4/guest_db_postconditions.py `
    projectiq_executioner/phase4/db_postcondition_contracts.py `
    projectiq_executioner/phase4/db_postcondition_vm_run.py `
    projectiq_executioner/tests/test_phase4cd3_db_postconditions.py
git diff --cached --check
git diff --cached --stat
# Confirm no vm_config.json, outputs, approval catalogs or VM evidence staged.
git commit -m "Verify reviewed D3 registration database postconditions"
git push origin executioner
```

**Trust note:** Review the patch and inputs before accepting. Only the approved SHA-constrained internal pilot is supported; the general untrusted-student security gate remains NO_GO.
