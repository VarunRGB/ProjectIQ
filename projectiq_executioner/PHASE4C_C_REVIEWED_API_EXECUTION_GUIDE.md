# Phase 4C-C — Operator-reviewed API assertions in disposable QEMU VMs

## Scope

This is an opt-in, exact-catalog-digest-approved API runner layered over the **existing** Phase 2 VM controller. Phase 4B's GET readiness runner, reviewed-SHA approval, managed VM lease, in-guest cgroup/workspace limits, wall-time watchdog, and verified cleanup remain independently enforced. This release does **not** authorize arbitrary student submissions; security attestation remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

| Component | Responsibility |
|---|---|
| 4C-A oracle catalog | Operator-authored proposed requests/expected results, bound to discovered Phase 3 test IDs |
| 4C-B source evidence | Advisory per-handler fields, response signals and prerequisites; no oracle auto-approval |
| 4C-C approval compiler | Revalidate plan/candidate, exact approved SHA and exact canonical approved catalog hash, reject unresolved fixtures |
| 4C-C guest API interpreter | Only localhost HTTP; GET/POST/PUT/PATCH/DELETE, JSON or form body, exact status and JSON-subset assertions |
| Phase 2 worker | The **only** real student-app VM launcher, cleanup and security enforcement path |
| 4C-D (future) | Secret fixture provisioning, authenticated cookie sessions, seeded database state/mutation verification |

## Operator approval is separate

The operator-owned `vm_config.json` must contain an **explicit entry** in `approved_oracle_catalogs`, in addition to its existing approved snapshot identity. Each entry has the exact form:

```
Owner/Repo@40-char-commit-sha#64-char-canonical-catalog-sha256
```

The hash is of canonical JSON (`projectiq_executioner.phase4.oracle_contracts.digest`), **not** the raw file bytes. Changes to any request input or expected outcome invalidate the approval. The operator must manually inspect the catalog and relevant verified source before adding the entry. A CLI flag alone cannot approve an oracle. The approval is an operator-controlled local policy, not a cryptographic signature.

Any `{$fixture_ref: ...}` placeholder is rejected for execution in 4C-C, even if the catalog digest is approved. No auth headers, cookies, arbitrary URLs, shell commands, body/response contents or raw application logs are persisted in the API results. JSON/form payloads are transferred to the VM in the bounded trusted request and deleted with the disposable managed runtime. No external URL or redirect is followed by the trusted test interpreter.

## Apply, offline tests

```powershell
$patch = "$HOME\Downloads\projectiq_phase4cc_reviewed_api_execution.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw "Patch check failed" }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw "Patch application failed" }
git diff --check

python -m unittest `
    projectiq_executioner.tests.test_phase4cc_api `
    projectiq_executioner.tests.test_phase4c_oracle_contracts `
    projectiq_executioner.tests.test_phase4cb_source_evidence `
    projectiq_executioner.tests.test_phase4b -q
if ($LASTEXITCODE -ne 0) { throw "Phase 4C-C regression failed" }
```

**Do not push until a real reviewed VM test and backward-compatibility checks pass.** Full Phase 2 boundary tests in the user's checkout are not available in the isolated patch workspace.

## First controlled scenario: MiniCollabApp missing task ID

This scenario is explicitly reviewed and intentionally provides **no task ID**. At the pinned source, `logic/delete_task.php` returns `{"success": false, "error": "Task ID missing"}` before executing the SQL deletion branch. The caller must inspect that exact approved source and confirm the branch before authorizing the catalog. This is a negative-validation test, **not a test of successful deletion**, authorization, or database integrity.

Use your previously approved PHP/MySQL Phase 3 plan and Phase 2 candidate:

```powershell
$base = Get-ChildItem "outputs/executioner" -Directory |
    Where-Object { $_.Name -like 'phase4a_approved_*' } |
    Sort-Object Name -Descending |
    Select-Object -First 1 -ExpandProperty FullName

$p3 = Get-Content "$base/phase3/test_plan.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$t = @($p3.test_cases | Where-Object {
    $_.target.method -eq 'POST' -and $_.target.path -eq '/logic/delete_task.php'
})
if ($t.Count -ne 1) { throw 'Expected one discovered delete_task.php POST target' }

$catalog = @{
    schema_version = '0.1'
    source = $p3.source
    cases = @(@{
        name = 'Reject task deletion without an identifier'
        test_id = $t[0].id
        request = @{ path_params = @{}; query = @{}; json_body = @{} }
        expected = @{ status = 200; json_subset = @{ success = $false; error = 'Task ID missing' } }
    })
}
$catalogPath = "outputs/executioner/phase4cc_oracle_catalog.json"
$catalog | ConvertTo-Json -Depth 16 | Set-Content -Encoding UTF8 $catalogPath

python -c "import sys; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(sys.argv[1], 256000)))" $catalogPath
```

Verify the SHA/identity and the source. Then manually add one string `Owner/Repo@SHA#HASH` under `approved_oracle_catalogs` in your **local, uncommitted** `vm_config.json`. Preserve `approved_snapshots` as before. This deliberately requires an operator decision; do not automate the approval based only on a discovered endpoint.

### Validate without VM (preflight)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cc_preflight_$stamp"
python -m projectiq_executioner.phase4.api_vm_run `
    --plan "$base/phase3/test_plan.json" `
    --oracles $catalogPath `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json `
    --out $out --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'Oracle preflight rejected review/contract' }

Get-Content "$out/api_preflight.json" -Raw -Encoding UTF8
```

Expected `APPROVED_NOT_EXECUTED`, one POST scenario, and `NOT_PERFORMED`.

### Real approved VM execution

Only after preflight and operator review succeed:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cc_php_$stamp"
python -m projectiq_executioner.phase4.api_vm_run `
    --plan "$base/phase3/test_plan.json" `
    --oracles $catalogPath `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config vm_config.json `
    --out $out --confirm-reviewed-api-execution
$code = $LASTEXITCODE
$r = Get-Content "$out/api_run.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r | Select-Object status, execution, cleanup, issue_code
$r.summary | Format-List
$r.results | Format-Table test_id, result, http_status, issue_code
if ($code -ne 0) { Write-Warning 'Inspect VM/guest evidence; do not push' }
```

Target is `TESTS_PASSED`, `PERFORMED_IN_DISPOSABLE_VM`, `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, and one JSON-subset assertion PASS. **This is not a verified result until it runs on your VM.**

## Release gates

1. Offline 4C-C tests and focused Phase 2 compatibility tests pass; inspect any expected platform skips.
2. Missing `approved_oracle_catalogs` or changed catalog digest causes denial *before* VM launch.
3. Positive and negative trusted guest JSON/form assertion tests pass offline.
4. Approved PHP/MySQL JSON validation run returns one real result with verified cleanup.
5. Legacy Phase 2 and Phase 4B VM paths remain operational (existing signatures unchanged, new bundle optional).
6. No secrets/fixture refs or unreviewed business operations are silently promoted.

**Limitations:** A reviewed oracle may still be wrong; it proves correspondence with operator expectations, not correctness in an absolute sense. This release doesn't support credentials, sessions, seeded database state, response-body content persistence, or arbitrary API headers. Meaningful stateful create/update/delete workflows require 4C-D controlled fixtures and postconditions.
