# ProjectIQ Phase 4C-D2 — Reviewed authentication and session contrast

**Scope:** Test one reviewed email/password registration/login path against the **same protected endpoint** without and with a session cookie, inside the already-approved disposable VM. This subphase adds no production credentials, host-side login calls, direct database manipulation, arbitrary request headers, browser automation or role-escalation tests.

## What changed

- A separate D2 review catalog ties registration and login endpoints to exact Phase 3 test IDs and binds two *different* protected-response oracles from an operator-approved Phase 4C catalog.
- `approved_auth_flows` in operator-owned `vm_config.json` contains `owner/repo@commit#canonical_auth_catalog_sha256`. `approved_oracle_catalogs` must independently approve the protected-response oracle catalog. Existing `approved_snapshots` still authorizes the pinned source. All three approvals are required.
- The trusted controller independently recompiles D2 from the embedded review inputs **before** QEMU startup. Tampered auth steps, forged digests, unauthorized target paths or attempts to combine D2 with D1/4C-C bundles are denied before any VM launch.
- Guest code generates a fresh random email at `example.invalid` and a random password. Neither is in the reviewed catalogs, request approval, host report or persistent operator config.
- The guest performs exactly four bounded loopback HTTP steps: unauthenticated protected error, registration, login with a verified session cookie, authenticated protected error. If unauthenticated behavior is unexpected, registration stops. If registration/login fails, subsequent steps are inconclusive, not forged passes.
- Only host-only `Path=/` bounded session cookies from **login** may be sent to the final request. Redirects are never followed, cookies are never returned to the host, and no arbitrary headers or URLs are allowed.
- The guest report contains step names, HTTP status, assertion pass/fail/inconclusive, and a cookie-observed boolean. No request/response bodies, credentials, email addresses, session IDs, arbitrary logs, raw SQL or usernames are persisted.
- The old 4B HTTP, 4C-C standalone API and D1 database-seed modes are unchanged; D2 is a mutually exclusive optional mode.

## Strict limitations

D2's first approved transport is *guest-generated email/password registration*, followed by a cookie-based login and an intentionally non-mutating or empty-body protected error-path probe. This is narrower than arbitrary OAuth, JWT headers, CSRF, two-factor authentication, SSO, or full user workflow testing. Other frameworks may use the contract only where their reviewed routes and responses fit these constraints. No arbitrary student application is certified safe to execute.

The MiniCollabApp acceptance case registers a fresh synthetic `user` in disposable MySQL **through its own application**. D1's separate database fixture mode is not combined with D2. D3 will add persistence postcondition checks, and D4 can add per-scenario database resets and longer stateful workflows.

## 1. Apply (from ProjectIQ repo root, executioner branch)

```powershell
$patch = "$HOME\Downloads\projectiq_phase4cd2_auth_sessions.patch"
if ((git branch --show-current) -ne 'executioner') { throw 'Wrong branch' }
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch application failed' }
git diff --check
```

## 2. Offline regression

```powershell
python -m unittest `
  projectiq_executioner.tests.test_phase4cd2_auth `
  projectiq_executioner.tests.test_phase4cc_api `
  projectiq_executioner.tests.test_phase4cd1_db_fixtures -q
if ($LASTEXITCODE -ne 0) { throw 'Regression failed; do not launch VM' }
```

The new D2 suite includes localhost HTTP tests and controller-level tests that require the full Phase 2 checkout. Deliberate test errors from older suites are expected; the final unittest result must be `OK` (some older environment-specific tests can be skipped).

## 3. Generate reviewed MiniCollabApp catalogs (NO VM, NO PASSWORD)

Download `projectiq_phase4cd2_make_minicollab_catalogs.py` alongside the patch. **It is a helper for the known pinned fixture only**, not a generic source-to-oracle promotion system.

```powershell
$base = Get-ChildItem 'outputs/executioner' -Directory |
  Where-Object {$_.Name -like 'phase4a_approved_*'} |
  Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $base) { throw 'Approved plan not found' }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$d2CatalogDir = "outputs/executioner/phase4cd2_catalogs_$stamp"
python "$HOME\Downloads\projectiq_phase4cd2_make_minicollab_catalogs.py" `
  --plan "$base/phase3/test_plan.json" --out $d2CatalogDir
if ($LASTEXITCODE -ne 0) { throw 'Catalog construction failed' }
$authCatalogPath = "$d2CatalogDir/auth_catalog.json"
$protectedOraclesPath = "$d2CatalogDir/protected_oracles.json"
Get-Content $authCatalogPath -Raw
Get-Content $protectedOraclesPath -Raw
```

Inspect both JSON files. The expected four guest requests are:

| Step | Request | Expected |
| --- | --- | --- |
| UNAUTHENTICATED | POST `/logic/delete_task_user.php` JSON `{}` with **no cookie** | HTTP 200, `{"error":"Unauthorized"}` |
| REGISTRATION | POST `/auth/register.php` FORM with generated email/password, name `ProjectIQ Test`, role `user` | HTTP 302 (redirect **not followed**) |
| LOGIN | POST `/auth/login.php` JSON with the same generated email/password | HTTP 200, JSON subset `{"success":true,"role":"user"}`, valid host-only session cookie |
| AUTHENTICATED | POST `/logic/delete_task_user.php` JSON `{}` **with** the login session cookie | HTTP 200, `{"error":"Task ID is required"}` |

Both expected response bodies are based on the inspected **pinned** MiniCollabApp source. They remain *operator-reviewed expectations*, not automatically proven ground truth. A deliberately missing task ID avoids executing its deletion branch.

## 4. Add two exact approvals without editing other VM settings

Use these guarded commands rather than manually adding commas to JSON. They preserve other approvals and settings. Back up the existing config in ignored `outputs/executioner` before changing it.

```powershell
$p3 = Get-Content "$base/phase3/test_plan.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$identity = "$($p3.source.repository)@$($p3.source.commit_sha)"
$authDigest = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]), 64000)))" $authCatalogPath
if ($LASTEXITCODE -ne 0 -or $authDigest -notmatch '^[a-f0-9]{64}$') { throw 'Bad auth hash' }
$oracleDigest = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]), 256000)))" $protectedOraclesPath
if ($LASTEXITCODE -ne 0 -or $oracleDigest -notmatch '^[a-f0-9]{64}$') { throw 'Bad oracle hash' }
$authApproval = "$identity#$authDigest"
$oracleApproval = "$identity#$oracleDigest"
Write-Host "New auth approval: $authApproval"
Write-Host "New protected oracle approval: $oracleApproval"

Copy-Item 'vm_config.json' "outputs/executioner/vm_config_before_d2_$stamp.json"
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $cfg.PSObject.Properties['approved_auth_flows']) {
  $cfg | Add-Member -NotePropertyName approved_auth_flows -NotePropertyValue @()
}
if (-not $cfg.PSObject.Properties['approved_oracle_catalogs']) {
  $cfg | Add-Member -NotePropertyName approved_oracle_catalogs -NotePropertyValue @()
}
$cfg.approved_auth_flows = @(@($cfg.approved_auth_flows) + $authApproval | Select-Object -Unique)
$cfg.approved_oracle_catalogs = @(@($cfg.approved_oracle_catalogs) + $oracleApproval | Select-Object -Unique)
$cfg | ConvertTo-Json -Depth 100 | Set-Content vm_config.json -Encoding UTF8
$verifiedCfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if ($authApproval -cnotin $verifiedCfg.approved_auth_flows -or
    $oracleApproval -cnotin $verifiedCfg.approved_oracle_catalogs) {
  throw 'Approval verification failed'
}
```

**Do not commit** `vm_config.json`, its backup, generated catalogs or VM outputs. The exact catalog digests are approval *identifiers*, not passwords.

## 5. Offline preflight (no QEMU)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cd2_preflight_$stamp"
python -m projectiq_executioner.phase4.auth_vm_run `
  --plan "$base/phase3/test_plan.json" `
  --auth "$authCatalogPath" `
  --oracles "$protectedOraclesPath" `
  --candidate "$base/phase2/phase2c_candidate.json" `
  --vm-config vm_config.json --out $out --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'D2 preflight failed' }
```

Expected: `APPROVED_NOT_EXECUTED`, `NOT_PERFORMED`. If it blocks, stop; do not weaken checks.

## 6. First real QEMU acceptance (ONLY AFTER preflight)

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = "outputs/executioner/phase4cd2_auth_$stamp"
python -m projectiq_executioner.phase4.auth_vm_run `
  --plan "$base/phase3/test_plan.json" `
  --auth "$authCatalogPath" `
  --oracles "$protectedOraclesPath" `
  --candidate "$base/phase2/phase2c_candidate.json" `
  --vm-config vm_config.json --out $out --confirm-reviewed-auth-execution
$r = Get-Content "$out/auth_run.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r | Select-Object status,execution,cleanup,session_cookie_observed,issue_code
$r.summary | Format-List
$r.results | Format-Table step,result,http_status,issue_code -AutoSize
```

Acceptance requires **all four steps passing**, `session_cookie_observed=True`, `TESTS_PASSED`, `PERFORMED_IN_DISPOSABLE_VM` and `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`. Inspect the report even if the CLI exits nonzero; do not mistake cleanup of a failed functional test for a functional pass.

## 7. Closeout

After the positive QEMU result, run one intentional *reviewed* bad-response oracle test (the guest must report functional failure, not infrastructure failure) and one legacy Phase 2 run using the already available `projectiq_phase4b_two_acceptance_checks.py`. Don't repeat every old VM benchmark. Review staging and commit only source/test/guide files under `projectiq_executioner`.

### Current limitations and non-goals

This does **not** prove the database persisted expected mutations (4C-D3), isolate every workflow step in an independent DB snapshot (4C-D4), perform SSO/OAuth/JWT/2FA, or support arbitrary untrusted student source. A client-only login page without server-side session behavior is not proof of authentication. All runtime actions remain inside an operator-approved disposable VM.
