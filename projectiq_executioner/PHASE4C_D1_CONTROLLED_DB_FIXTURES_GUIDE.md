# ProjectIQ Phase 4C-D1 — Controlled database fixtures

**Scope:** reviewed, bounded MySQL inserts into the approved disposable PHP/MySQL VM. This is not yet authenticated API testing (D2), database postcondition assertions (D3), per-scenario reset (D4), or authorization for arbitrary untrusted submissions.

## Security boundary

1. The manifest is JSON data only: `schema_version`, exact pinned `source`, and bounded `tables` of typed `rows`. No SQL text, migrations, commands, scripts, filesystem paths or application secrets.
2. `approved_snapshots` must authorize the exact repository SHA **and** `approved_database_fixtures` must separately authorize the **exact canonical SHA-256 digest** of the fixture catalog.
3. The existing `php_mysql_v1` adapter first imports its reviewed SQL dump into a **fresh disposable**, database-scoped MySQL instance. Only then does the trusted guest insert reviewed rows via static PHP code and PDO prepared statements. No MySQL root credentials are used.
4. The application server starts only after the seed transaction has committed. SQL errors, missing schema, FK conflicts or row count mismatch fail closed; the guest does not print exception details or row values.
5. Only verified counts and fixture digest are sent to the host. A missing/forged guest fixture report cannot be counted as successful startup; VM cleanup remains mandatory.
6. Non-MySQL/unsupported adapters are explicitly denied. Do not weaken this check to claim SQLite/Postgres support.

## Example fixture for the pinned MiniCollabApp PHP snapshot

The approved SQL dump already defines `users` and `tasks`. This example adds exactly one synthetic task linked to the existing demo `user_id=2`. No account passwords or hashes are copied into this fixture.

```json
{
  "schema_version": "0.1",
  "source": {
    "kind": "github",
    "repository": "VarunRGB/Mini-Task-Collaboration-App",
    "commit_sha": "c4f7965d16807fa05c3d7ffeee54f86fee4d0a37"
  },
  "tables": [
    {
      "name": "tasks",
      "rows": [
        {
          "id": 901,
          "title": "ProjectIQ fixture task",
          "deadline": "2027-03-15",
          "priority": "Low",
          "user_id": 2,
          "status": "Pending"
        }
      ]
    }
  ]
}
```

> This is **new disposable VM data**, not a persistent edit to MiniCollabApp or the host database. The fixture does not attempt to authenticate a user or verify CRUD application behavior.

## Applying and testing

From `C:\Users\varun\Code\ProjectIQ` on the `executioner` branch:

```powershell
$patch = "$HOME\Downloads\projectiq_phase4cd1_controlled_database_fixtures.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw "Patch failed -- do not continue" }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw "Apply failed" }
git diff --check
python -m unittest projectiq_executioner.tests.test_phase4cd1_db_fixtures -q
if ($LASTEXITCODE -ne 0) { throw "D1 tests failed" }
```

### Create a manifest from the existing approved plan

```powershell
$base = Get-ChildItem "outputs/executioner" -Directory |
    Where-Object {$_.Name -like 'phase4a_approved_*'} |
    Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
$p3 = Get-Content "$base/phase3/test_plan.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$manifestPath = "outputs/executioner/phase4cd1_mini_fixture.json"
@{
    schema_version = '0.1'
    source = $p3.source
    tables = @(@{
        name = 'tasks'
        rows = @(@{
            id = 901; title = 'ProjectIQ fixture task';
            deadline = '2027-03-15'; priority = 'Low';
            user_id = 2; status = 'Pending'
        })
    })
} | ConvertTo-Json -Depth 12 | Set-Content $manifestPath -Encoding UTF8
```

### Compute approval entry (not a raw-file hash)

```powershell
$hash = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]), 64000)))" $manifestPath
if ($LASTEXITCODE -ne 0 -or $hash -notmatch '^[a-f0-9]{64}$') { throw 'Hash error' }
$approval = "$($p3.source.repository)@$($p3.source.commit_sha)#$hash"
Write-Host $approval
```

After inspecting the manifest, add the **full** approval as a string in `approved_database_fixtures` in your local `vm_config.json`. Keep `approved_snapshots`, `approved_oracle_catalogs` and all other settings. Do not commit `vm_config.json`.

### Offline preflight

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cd1_preflight_$stamp"
python -m projectiq_executioner.phase4.db_fixture_vm_run `
  --plan "$base/phase3/test_plan.json" `
  --fixtures "$manifestPath" `
  --candidate "$base/phase2/phase2c_candidate.json" `
  --vm-config 'vm_config.json' `
  --out $out --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'D1 preflight failed' }
```

Expected: `APPROVED_NOT_EXECUTED`, `Tables: 1`, `Rows: 1`, `Execution: NOT_PERFORMED`.

### First real VM seed acceptance (after preflight)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cd1_seed_$stamp"
python -m projectiq_executioner.phase4.db_fixture_vm_run `
  --plan "$base/phase3/test_plan.json" `
  --fixtures "$manifestPath" `
  --candidate "$base/phase2/phase2c_candidate.json" `
  --vm-config 'vm_config.json' `
  --out $out --confirm-reviewed-db-seeding
if ($LASTEXITCODE -ne 0) { throw 'D1 VM acceptance failed, inspect report' }
$r = Get-Content "$out/db_fixture_run.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r | Select-Object status,execution,cleanup,table_count,seeded_rows,issue_code
```

Expected: `FIXTURES_SEEDED`, `PERFORMED_IN_DISPOSABLE_VM`, `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, one table and one inserted row. **Do not push D1** until this and a targeted legacy compatibility check succeed.

## Non-goals and limitations

- D1 deliberately rejects seeding password, token or credential columns. D2 will use a separate, guarded credential provisioner for authenticated test identities.
- D1 confirms an inserted row count committed by the guest, but does **not** yet compare all persisted row values or test create/update/delete endpoints. That belongs to D3.
- Fixture manifests are explicit, reviewed records. This module does not infer synthetic test data or determine whether a seeded row is semantically correct.
- Separate-VM isolation exists today. Per-scenario resets within one VM and cross-scenario dependency control belong to D4.
- Approved reviewed fixtures do not constitute security attestation for arbitrary untrusted student projects.
