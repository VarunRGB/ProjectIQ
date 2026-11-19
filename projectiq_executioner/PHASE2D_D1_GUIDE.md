# ProjectIQ Phase 2D D1 — Offline Node/Vite adapter (reviewed-fixture pilot)

## Scope and safety

D1 adds `node_vite_offline_v1` for **one standalone Vite frontend** (React/Vite and compatible single-service Vite projects) using npm's versioned `package-lock.json`. Phase 1 must statically detect `Vite`, one `node_frontend` service, a safe root and the package files. Next.js, multi-service/Node API projects, pnpm, Yarn, Bun, missing lockfiles, unsupported registries, real cloud dependencies, and repositories without explicit SHA approval remain blocked. This is startup and asset-serving verification, not behavioral correctness or production isolation attestation.

The **runtime VM has no external egress**. `npm ci --offline --ignore-scripts` installs from an operator-approved SHA-256-pinned cache *inside the disposable guest*. It invokes Vite's own `vite.js build` directly inside the guest rather than arbitrary npm scripts; any Vite config and application code still executes, so only reviewed pinned fixture SHAs are permitted.

A **separate cache-preparation VM** has **temporarily enabled outbound network**, but **only when the operator specifies `--allow-trusted-npm-network`**. The preparer runs `npm ci --ignore-scripts` with a restricted non-root guest user, no host filesystem mounts, no secrets, and no student build scripts. It transfers only a bounded cache ZIP to Windows. This stage does not change or re-bake the validated Phase 2C QCOW2 image.

Both guest modes are **non-production development pilots**; network isolation, egress escape, process limits and adversarial code handling still require security acceptance testing.

## Official Node runtime

Use the official **Node.js 24.22.3 LTS Linux x64** bundle, **not the Windows installer**:

- URL: `https://nodejs.org/dist/v24.22.3/node-v24.22.3-linux-x64.tar.xz`
- SHA-256 from Node's official signed release SHASUMS: `4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb`
- Keep it under `$HOME\ProjectIQ_VM\node-v24.22.3-linux-x64.tar.xz` (outside Git).
- Verify with PowerShell `Get-FileHash -Algorithm SHA256` before supplying it to D1.

D1's runtime extraction checks the hash again inside the guest and verifies `node --version` matches exactly. Only Node 24 Linux-x64 tarballs are accepted in the pilot.

## Optional reviewed React/Vite fixture

Example public pinned fixture (the operator must first review and explicitly approve it):

- GitHub: `https://github.com/SafdarJamal/vite-template-react`
- Pin: `480dc9128ee90bf7c8c817e52ec063953243b240`
- Approval key: `SafdarJamal/vite-template-react@480dc9128ee90bf7c8c817e52ec063953243b240`
- The repository has a root `package.json`, npm v3 lockfile, `vite.config.js`, React source and `index.html`.

You may choose another reviewed, single-service Vite repository, but never use a mutable branch name for execution. Do **not** add new repository SHAs automatically in code. The operator updates the local, ignored `vm_config.json` approved-snapshots list.

## Operator commands (Windows PowerShell)

### 1. Verify the package and tests

```powershell
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -q
```

### 2. Download official Linux Node (one-time)

```powershell
$vmDir = "$HOME\ProjectIQ_VM"
$nodeVersion = "v24.22.3"
$nodeHash = "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb"
$nodeTar = Join-Path $vmDir "node-v24.22.3-linux-x64.tar.xz"
Invoke-WebRequest "https://nodejs.org/dist/v24.22.3/node-v24.22.3-linux-x64.tar.xz" -OutFile $nodeTar
if ((Get-FileHash $nodeTar -Algorithm SHA256).Hash.ToLower() -ne $nodeHash) {
    throw "Official Node SHA-256 mismatch"
}
```

### 3. Plan the fixture (no VM)

```powershell
$repo = 'https://github.com/SafdarJamal/vite-template-react'
$sha = '480dc9128ee90bf7c8c817e52ec063953243b240'
python -m projectiq_executioner.phase2.phase2d --repo $repo --ref $sha --out outputs/executioner/d1_vite_plan
```

Check `phase2c_candidate.json` has adapter `node_vite_offline_v1` and status `CANDIDATE_NEEDS_VM_VERIFICATION`. If not, stop and inspect the manifest; do not force execution.

### 4. Explicitly approve pinned fixture (local config only)

After reviewing the fixture, add this string to `vm_config.json`'s existing `approved_snapshots` array:

`SafdarJamal/vite-template-react@480dc9128ee90bf7c8c817e52ec063953243b240`

Keep `vm_config.json` ignored by Git; do not publish local paths or cache metadata.

### 5. Prepare Linux offline npm cache using disposable VM with temporary network

Close memory-heavy apps. The QEMU/SSH toolchain must be on PATH, and 5-6 GiB of host RAM should be free for the 3-GiB VM.

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.phase2d.cache_prep `
    --repo $repo `
    --ref $sha `
    --vm-config vm_config.json `
    --node-tarball $nodeTar `
    --node-sha256 $nodeHash `
    --node-version v24.22.3 `
    --out "$vmDir\d1_vite_cache" `
    --allow-trusted-npm-network
```

Expected `OFFLINE_CACHE_PREPARED_NOT_SANDBOX_ATTESTED`, not a claim of security attestation. Output files `node-vite-npm-cache.zip` and `node_vite_profile.json` are operator-owned and stay outside Git.

### 6. Register the cache profile in the local config

The generated `node_vite_profile.json` contains a `node_vite_offline` mapping from the full approved repo SHA to pinned runtime, npm cache and lockfile hashes. Add/merge that mapping into your local `vm_config.json` (without replacing its existing `base_image`, `base_sha256`, or `approved_snapshots`). Do not commit either file.

PowerShell for a first, single fixture:

```powershell
$profile = Get-Content "$vmDir\d1_vite_cache\node_vite_profile.json" -Raw | ConvertFrom-Json
$config = Get-Content vm_config.json -Raw | ConvertFrom-Json
$config | Add-Member -NotePropertyName 'node_vite_offline' -NotePropertyValue $profile.node_vite_offline -Force
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path 'vm_config.json'),
    (($config | ConvertTo-Json -Depth 20) + "`n"),
    (New-Object System.Text.UTF8Encoding($false)))
```

For multiple fixtures, merge entries instead of replacing the entire `node_vite_offline` map.

### 7. Fully offline reviewed-fixture execution

Keep heavy applications closed; 3 GiB of guest RAM is allocated. No npm internet access is possible in this worker QEMU network mode.

```powershell
python -m projectiq_executioner.phase2.phase2d `
    --repo $repo `
    --ref $sha `
    --vm-config vm_config.json `
    --run-reviewed-fixture `
    --out outputs/executioner/d1_vite_execution
```

Inspect `pipeline_summary.json` and `vm_execution.json`. Successful startup requires `STARTUP_VERIFIED`, `HTTP_200_HTML_PROBE_PASSED`, and `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`. The guest notes should say Vite built, and a generated JavaScript asset returned HTTP 200.

## Failure and non-goals

- Missing Node tar, wrong Node version, wrong cache SHA or wrong lockfile SHA: **DENIED** or explicit guest failure, no fallback to online dependency resolution.
- Missing Linux-only optional dependencies: offline `npm ci` fails; recreate cache inside the Linux preparation VM, not Windows.
- Binary/native packages needing install scripts: may fail because `--ignore-scripts` is mandatory.
- No committed VM images/cache, no uncontrolled `npm install`, no Docker, no real AWS/S3 credentials.
- Production trust boundary/security attestation and arbitrary student submissions remain out of scope.
- The HTTP probes check startup and static asset serving, not React behavior, visual correctness, API correctness or route coverage.
