# ProjectIQ Phase 2D — D4a: reviewed React/Vite + Express + SQLite vertical slice

**Status:** Implemented and locally regression-tested; **real Windows/WHPX VM validation is required** before accepting D4a. This is a *bounded first vertical slice*, **not** generic multi-service orchestration or security attestation. The original D1/D2/D3 adapters remain unchanged.

## 1. Supported architecture and non-goals

A **single operator-reviewed immutable public GitHub commit** with exactly:

- One React/Vite frontend service (`node_frontend`) and one Express backend service (`node`), in **different safe roots** (e.g. `frontend/`, `backend/`).
- Both roots have a separate `package.json` and npm v2/v3 `package-lock.json`. Exact SHA-256 hashes bind each lockfile to the same approved commit.
- Phase 1 must evidence `Express` and `Vite`, a frontend dependency on the backend, backend GET `/health` (or `/api/health`), and a **matching frontend GET call and backend GET declaration** for `/api/sqlite-check` or `/api/health`.
- Express has a fixed, reviewed `app.js`, `server.js` or `index.js` entrypoint, not an arbitrary package script. Vite is invoked via its known executable, not `npm run`.
- Optional **embedded SQLite** is provided by Node 24's built-in `node:sqlite` API, as exercised by the reviewed acceptance fixture. No MySQL/PostgreSQL/MongoDB service is started by D4a.
- Explicitly unsupported: external services/cloud, extra services, Next.js, monorepo workspaces, custom registries, install scripts, Docker, arbitrary commands, and unreviewed student repositories.

**A passed readiness test is not a browser rendering test, full functional test, or production sandbox attestation.** `security_attestation` remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## 2. Lifecycle and safety boundaries

1. Phase 1→2A→2B→2C→2D static planning selects `node_fullstack_offline_v1` only for a grounded, compatible two-service graph. Planning **never** grants execution authorization.
2. Operator reviews the exact fixture and allowlists `Owner/Repo@40-char-sha` in local, gitignored `vm_config.json`.
3. A **separate, explicitly network-authorized preparation VM** downloads npm tarballs for *both lockfiles* into one bounded content-addressed npm cache, with `npm ci --ignore-scripts`; no project `start`, `build`, `postinstall` or `prepare` script executes.
4. The normal disposable **execution VM denies outbound network**, verifies the Node bundle and cache SHA, installs *both* services with `npm ci --offline --ignore-scripts`, and builds Vite using a pinned executable.
5. The Express backend starts on `127.0.0.1:8766` first, then a **trusted Python static frontend server** starts on `127.0.0.1:8765`. Its `/api/*` GET proxy targets **only** the fixed backend loopback address and does not follow HTTP redirects; no external URL proxying is permitted.
6. The guest checks backend `/health`, frontend `/index.html`, a real built JS asset, then an API request **both directly to Express and via the frontend proxy**. The JSON must match. For `/api/sqlite-check`, the reviewed fixture must respond with `{"sqlite": true}` after actual guest-local database operations.
7. Child processes are terminated even on probe failure; the host stops the VM and discards its QCOW2 overlay and ephemeral credentials. The cleanup report must be checked separately.

## 3. Install the patch and run tests

From `C:\Users\varun\Code\ProjectIQ`, apply the D4 patch only after `git apply --check` succeeds. Then:

```powershell
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -q
```

The pushed D3 baseline has **308 tests**. D4a adds **33 tests**; expected **341 tests passing** before attempting the VM.

## 4. Prepare a reviewed D4 fixture on a separate branch

The accompanying **D4 fixture setup ZIP** contains custom reviewed `frontend/index.html`, `frontend/src/main.jsx`, and `backend/app.js`, plus `setup_d4_fixture.ps1`.

The script:
- Clones the **pinned `d3-express`** branch from `VarunRGB/ProjectIQ-Python-Fixtures` into a **new checkout** at `$HOME\ProjectIQ_VM\D4_Fullstack_Fixture_checkout`.
- Creates `d4-fullstack` branch, moves the existing Express `package.json` / npm lockfile into `backend/`, and replaces its entrypoint with SQLite CRUD API code.
- Downloads `frontend/package.json` and `frontend/package-lock.json` from the already-reviewed, **immutable** React/Vite sample commit `480dc9128ee90bf7c8c817e52ec063953243b240`, then verifies both exact Git blob SHA-1 values before staging them. This **does not** run or install npm on Windows.
- Stages files without committing or pushing.

Run (after reviewing the setup script):

```powershell
Expand-Archive "$HOME\Downloads\ProjectIQ_D4_Fullstack_Fixture_Setup.zip" `
    -DestinationPath "$HOME\Downloads\ProjectIQ_D4_Fixture" -Force
& "$HOME\Downloads\ProjectIQ_D4_Fixture\setup_d4_fixture.ps1"
$fixtureCheckout = "$HOME\ProjectIQ_VM\D4_Fullstack_Fixture_checkout"
git -C $fixtureCheckout diff --cached --stat
git -C $fixtureCheckout diff --cached --check
git -C $fixtureCheckout diff --cached
```

After operator review:

```powershell
git -C $fixtureCheckout commit -m "test(fixtures): add D4 fullstack SQLite pilot"
git -C $fixtureCheckout push -u origin d4-fullstack
$repo = 'https://github.com/VarunRGB/ProjectIQ-Python-Fixtures'
$sha = (git -C $fixtureCheckout rev-parse HEAD).Trim()
```

Never use a mutable branch name for authorization or execution.

## 5. Plan without launching a VM

```powershell
python -m projectiq_executioner.phase2.phase2d `
  --repo $repo --ref $sha --out outputs/executioner/d4_fullstack_plan
$c = Get-Content outputs/executioner/d4_fullstack_plan/phase2c_candidate.json -Raw | ConvertFrom-Json
$c | Select-Object status, adapter, project_root, service_roots, probe_path, backend_probe_path
```

Expected: `CANDIDATE_NEEDS_VM_VERIFICATION`, adapter `node_fullstack_offline_v1`, `service_roots = {frontend: frontend, backend: backend}`, proxy probe `/api/sqlite-check`, backend probe `/health`. `project_root` is intentionally unused for a two-service graph.

## 6. Approve only the exact SHA and prepare the combined offline npm cache

Retain existing approved snapshots and all D1–D3 profiles in local config:

```powershell
$identity = "VarunRGB/ProjectIQ-Python-Fixtures@$sha"
$configPath = Join-Path (Get-Location).Path 'vm_config.json'
$config = Get-Content $configPath -Raw | ConvertFrom-Json
if ($identity -notin @($config.approved_snapshots)) {
  $config.approved_snapshots = @($config.approved_snapshots) + @($identity)
}
[System.IO.File]::WriteAllText($configPath, (($config | ConvertTo-Json -Depth 30) + "`n"),
  (New-Object System.Text.UTF8Encoding($false)))

$env:Path += ';C:\Program Files\qemu'
$vmDir = "$HOME\ProjectIQ_VM"
$nodeTar = "$vmDir\node-v24.22.3-linux-x64.tar.xz"
$nodeHash = '4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb'
if ((Get-FileHash $nodeTar -Algorithm SHA256).Hash.ToLower() -ne $nodeHash) {
  throw 'Official Linux Node archive SHA256 mismatch'
}
```

Close heavy applications on the 16 GB host, then explicitly authorize network in the **preparation VM only**:

```powershell
python -m projectiq_executioner.phase2.phase2d.cache_prep `
  --adapter node_fullstack_offline_v1 `
  --repo $repo --ref $sha --vm-config vm_config.json `
  --node-tarball $nodeTar --node-sha256 $nodeHash --node-version v24.22.3 `
  --out "$vmDir\d4_fullstack_cache" --allow-trusted-npm-network
```

Expected: `OFFLINE_CACHE_PREPARED_NOT_SANDBOX_ATTESTED` with `node-fullstack-npm-cache.zip` and `node_fullstack_profile.json`, the **two lockfile hashes**, and cleanup `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`. The preparer refuses to overwrite an existing cache. If it fails, **inspect the error category; don't rerun blindly**.

## 7. Merge profile and execute completely offline

Only if preparation succeeded:

```powershell
$profile = Get-Content "$vmDir\d4_fullstack_cache\node_fullstack_profile.json" -Raw | ConvertFrom-Json
$config = Get-Content vm_config.json -Raw | ConvertFrom-Json
if ($null -eq $config.node_fullstack_offline) {
  $config | Add-Member -NotePropertyName node_fullstack_offline `
    -NotePropertyValue ([pscustomobject]@{}) -Force
}
foreach ($entry in $profile.node_fullstack_offline.PSObject.Properties) {
  $config.node_fullstack_offline | Add-Member -NotePropertyName $entry.Name `
    -NotePropertyValue $entry.Value -Force
}
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path 'vm_config.json'),
  (($config | ConvertTo-Json -Depth 30) + "`n"),
  (New-Object System.Text.UTF8Encoding($false)))

python -m projectiq_executioner.phase2.phase2d `
  --repo $repo --ref $sha --vm-config vm_config.json `
  --run-reviewed-fixture --out outputs/executioner/d4_fullstack_execution
```

Inspect the independent controller report and guest readiness breakdown:

```powershell
$r = Get-Content outputs/executioner/d4_fullstack_execution/pipeline_summary.json -Raw | ConvertFrom-Json
$r | Select-Object status, execution, phase2c_adapter, cleanup, security_attestation
$v = Get-Content outputs/executioner/d4_fullstack_execution/vm_execution.json -Raw | ConvertFrom-Json
$v.guest_result | Format-List
$v.guest_result.service_results | Format-List
```

**D4a acceptance** (real VM only): `STARTUP_VERIFIED`, `PERFORMED_IN_DISPOSABLE_VM`, `node_fullstack_offline_v1`, `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, frontend HTTP 200, backend HTTP 200, built JS HTTP 200, frontend-proxied API HTTP 200, and `sqlite_verified = True` from the reviewed CRUD fixture.

## 8. What D4a does not complete

- No browser rendering or human UI interactions. That belongs in later functional-testing phases.
- No independent MySQL/Postgres service or support for arbitrary service graphs; those are potential **D4b** extensions, after the pilot passes.
- No cloud/S3, arbitrary scripts, real secrets, production deployment, or sandbox attestation.
- A reviewed fixture returning `sqlite: true` is an API contract validation combined with separately reviewed fixture code, not an independent SQL tracer or proof that all student database features work.
- A successful guest report requires checking outer VM cleanup and limit statuses. D5 must separately validate resource exhaustion, network isolation, host escape resistance, and adversarial teardown before any unreviewed student code can be considered.

**Checkpoint D4a only after the real VM run and 341 regression tests pass.**
