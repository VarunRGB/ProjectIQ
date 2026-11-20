# ProjectIQ Phase 2D — D3: reviewed offline Express/Node API pilot

**Status:** Implemented and locally unit-tested. **Real Windows/WHPX VM test is required before D3 is accepted.** This is not security attestation for arbitrary student repositories.

## Scope

- Exactly one evidenced Express backend and no frontend, second backend, database, cloud or other external service.
- Root or one safe nested backend directory with `package.json`, npm `package-lock.json` version 2/3 and a simple `server.js`, `app.js`, or `index.js` entrypoint. `package.json.main` can name one of those exact files. If omitted, exactly one of them must exist.
- Requires an independently detected literal **GET `/health`, `/api/health`, or `/`** route. The HTTP probe checks nonempty JSON and status 200; it does **not** test API business correctness.
- Direct package version specs are bounded standard numeric semver (`4.19.2`, `^4.19.2`, `~4.19.2`). No source distributions, git/file/url dependencies, custom registries, `.npmrc`, workspaces, package-lock links, or uncontrolled package scripts.
- Reuses the **exact Linux Node v24.22.3** tarball already pinned for D1. npm-cache preparation occurs in a separate operator-authorized disposable Linux VM using `npm ci --ignore-scripts`; no npm command or student code executes on Windows.
- Execution VM has denied outbound egress. It performs `npm ci --offline --ignore-scripts` and starts only the reviewed fixed JS entrypoint. `PORT=8765`, `HOST=127.0.0.1`, `NODE_ENV=production`. VM storage and API data are disposable.
- Execution is restricted to an operator-reviewed, immutable, individually allowlisted GitHub SHA **and** a matching SHA-pinned offline npm profile. No blanket permission for arbitrary submissions.
- D1 Vite assets under `node_vite_offline` and D2 Python assets under `python_web_offline` remain untouched. D3 uses `node_express_offline`.

## Before testing

Verify local D2 branch has no unexpected changes, then apply D3 patch and run:

```powershell
cd C:\Users\varun\Code\ProjectIQ
python -m unittest discover -s projectiq_executioner/tests -p 'test_*.py' -q
```

The baseline has 265 tests. D3 adds 43; expected **308 tests passing**.

## A. Create a reviewed Express fixture without running Node/npm on Windows

Use the separate Express fixture bundle (provided alongside the D3 patch). Do not include those files in your ProjectIQ executioner Git branch. The fixture's `app.js` and `package.json` are in the fixture ZIP; its exact npm v3 lockfile is downloaded from an **immutable public GitHub commit** with a verified Git blob SHA.

1. Extract the fixture ZIP in Downloads.
2. Open and review the fixture's `app.js` and `package.json` and the included setup PowerShell script.
3. Run the included setup script: it clones your existing `ProjectIQ-Python-Fixtures` repository into a fresh **separate checkout**, creates `d3-express` branch, copies fixture files and fetches the pinned lockfile. The script verifies the lockfile's Git SHA before staging it; it **does not** commit or push for you.
4. Inspect `git diff --cached` in that checkout, then explicitly `git commit` and `git push -u origin d3-express`.
5. Set `$repo` and `$sha` from that checkout's immutable new commit.

## B. Verify planning (no VM)

```powershell
$repo = 'https://github.com/VarunRGB/ProjectIQ-Python-Fixtures'
$sha = (git -C "$HOME\ProjectIQ_VM\D3_Express_Fixture_checkout" rev-parse HEAD).Trim()
python -m projectiq_executioner.phase2.phase2d `
  --repo $repo `
  --ref $sha `
  --out outputs/executioner/d3_express_plan
$c = Get-Content outputs/executioner/d3_express_plan/phase2c_candidate.json -Raw | ConvertFrom-Json
$c | Select-Object status, adapter, project_root, probe_path
```

Expected adapter `node_express_offline_v1`, root `.`, probe `/health`, `CANDIDATE_NEEDS_VM_VERIFICATION`. **Planning never authorizes execution.** If these values differ, stop; do not override the planner.

## C. Allowlist only the reviewed fixture commit

```powershell
$configPath = (Join-Path (Get-Location).Path 'vm_config.json')
$config = Get-Content $configPath -Raw | ConvertFrom-Json
$identity = "VarunRGB/ProjectIQ-Python-Fixtures@$sha"
if ($identity -notin @($config.approved_snapshots)) {
  $config.approved_snapshots = @($config.approved_snapshots) + @($identity)
}
[System.IO.File]::WriteAllText($configPath,
  (($config | ConvertTo-Json -Depth 30) + "`n"),
  (New-Object System.Text.UTF8Encoding($false)))
```

`vm_config.json` must stay ignored by Git. This is operator approval for a specific pilot fixture only, not a general sandbox-security guarantee.

## D. Prepare Express-specific offline npm cache in separate authorized Linux VM

Reuse the same verified Linux Node runtime from D1. Do **not** reinstall Node on Windows or re-bake the Ubuntu template.

```powershell
$vmDir = "$HOME\ProjectIQ_VM"
$nodeTar = "$vmDir\node-v24.22.3-linux-x64.tar.xz"
$nodeHash = '4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb'
if ((Get-FileHash $nodeTar -Algorithm SHA256).Hash.ToLower() -ne $nodeHash) { throw 'Node tarball hash mismatch' }
$env:Path += ';C:\Program Files\qemu'

python -m projectiq_executioner.phase2.phase2d.cache_prep `
  --adapter node_express_offline_v1 `
  --repo $repo `
  --ref $sha `
  --vm-config vm_config.json `
  --node-tarball $nodeTar `
  --node-sha256 $nodeHash `
  --node-version v24.22.3 `
  --out "$vmDir\d3_express_cache" `
  --allow-trusted-npm-network
```

This stage deliberately permits temporary network access **only to the disposable preparation VM**. Dependency install/lifecycle scripts are disabled. Requires adequate host free RAM; close heavy apps first. On success the tool produces `node-express-npm-cache.zip` and `node_express_profile.json`. It refuses to overwrite existing artifacts.

## E. Register D3 profile while preserving D1 and D2

```powershell
$profile = Get-Content "$vmDir\d3_express_cache\node_express_profile.json" -Raw | ConvertFrom-Json
$config = Get-Content vm_config.json -Raw | ConvertFrom-Json
if ($null -eq $config.node_express_offline) {
  $config | Add-Member -NotePropertyName node_express_offline -NotePropertyValue ([pscustomobject]@{}) -Force
}
foreach ($entry in $profile.node_express_offline.PSObject.Properties) {
  $config.node_express_offline | Add-Member -NotePropertyName $entry.Name -NotePropertyValue $entry.Value -Force
}
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path 'vm_config.json'),
  (($config | ConvertTo-Json -Depth 30) + "`n"),
  (New-Object System.Text.UTF8Encoding($false)))
```

For other approved Express commits, prepare separate caches and merge their entries instead of replacing older profiles.

## F. Run the reviewed Express API in an offline VM

```powershell
python -m projectiq_executioner.phase2.phase2d `
  --repo $repo `
  --ref $sha `
  --vm-config vm_config.json `
  --run-reviewed-fixture `
  --out outputs/executioner/d3_express_execution

$r = Get-Content outputs/executioner/d3_express_execution/pipeline_summary.json -Raw | ConvertFrom-Json
$r | Select-Object status, execution, phase2c_adapter, cleanup, security_attestation
$v = Get-Content outputs/executioner/d3_express_execution/vm_execution.json -Raw | ConvertFrom-Json
$v.guest_result | Format-List
```

Acceptance: `STARTUP_VERIFIED`, `PERFORMED_IN_DISPOSABLE_VM`, `node_express_offline_v1`, `HTTP 200 /health`, `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, with attestation `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`. An HTTP 200 JSON response is **startup-only** evidence, not proof of functional correctness.

If preparation or execution fails, preserve the fixed error category and investigate before changing any security policy. Do **not** turn on execution-VM networking, use `npm install` on Windows, disable TLS, run package scripts, or automatically approve new commits.

## D3 completion gates

1. Full 308+ unit tests on Windows, no regressions.
2. Verified Express planning on a real immutable GitHub commit.
3. Real network-authorized **preparation VM** creates a SHA-verified offline npm cache.
4. Real **offline execution VM** installs and starts Express and returns JSON HTTP 200.
5. Guest and disposable storage clean up correctly. Negative controls reject wrong hashes and unapproved SHAs.
6. D1 Vite, D2 Flask/FastAPI, PHP/MySQL and static adapters remain usable.

Only then checkpoint D3 on the `executioner` branch. D4 multi-service and D5 security acceptance are separate milestones.
