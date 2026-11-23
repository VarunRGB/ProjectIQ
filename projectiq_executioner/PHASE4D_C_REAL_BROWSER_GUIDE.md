# ProjectIQ Phase 4D-C — Real browser-rendered React UI (reviewed pilot)

**Patch baseline:** `executioner` commit `87f19d492d3536865f272a34dd1594cf99922ad9` (passed 4D-B).

**Scope:** exact-SHA operator-reviewed FullStack React/Vite + Express fixture; actual
headless Chrome/Chromium runs **inside** the existing offline QEMU Linux VM as
APP_USER inside the evaluation cgroup. JavaScript executes and the React UI's
post-fetch DOM must contain the reviewed heading **and** `SQLite verified` text.
No browser process or student code runs on Windows. No remote URLs, injected JS,
custom user script, sandbox-disable flags, arbitrary clicks/forms, screenshots,
raw DOM or credentials are exported. This is **DOM-render pilot**, not complete
browser interaction testing or sandbox security attestation.

**Browser provision:** the existing golden VM image probably has **no browser**.
The patch adds optional `--chrome-deb`/`--chrome-sha256` to trusted **one-time**
`template_bake`. It validates the DEB SHA, installs it with apt in the *trusted
setup-only networked VM*, then seals a **new** image and verifies the installed
Chrome CLI on a separate offline boot. This is *not* runtime student npm/package
installation. Never modify/reseal the old image in place or inject Chrome into
a running student VM. Keep `security_attestation=NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## 1. Build a source-matched patch (PowerShell)

Download `projectiq_phase4dc_package.zip` into Downloads.

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current) -ne 'executioner') { throw 'Wrong branch' }
if ((git rev-parse HEAD).Trim() -ne '87f19d492d3536865f272a34dd1594cf99922ad9') {
    throw 'Unexpected 4D-B HEAD; inspect before applying'
}
if (-not (Test-Path "$HOME\Downloads\projectiq_phase4dc_package.zip")) { throw 'Package missing' }
$package = "$HOME\Downloads\projectiq_phase4dc_package"
if (Test-Path $package) { throw 'Package extraction directory already exists; inspect before retry' }
Expand-Archive -LiteralPath "$HOME\Downloads\projectiq_phase4dc_package.zip" -DestinationPath $package -ErrorAction Stop
$stamp = Get-Date -Format yyyyMMdd_HHmmss
$patch = "outputs/executioner/phase4dc_source_matched_$stamp.patch"
python "$package/build_phase4dc_patch.py" --out $patch
if ($LASTEXITCODE -ne 0) { throw '4D-C patch builder rejected actual source' }
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw '4D-C patch does not apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw '4D-C patch apply failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace errors' }
```

Expect **10** files: 4 additive modifications (`qemu`, `orchestrator`,
`guest_agent`, `template_bake`) and 6 new ones (browser contract/runner/stage,
guest interpreter, tests, guide). No D4-B source code is replaced.

## 2. Offline regression (no browser or QEMU)

```powershell
python -m unittest `
  projectiq_executioner.tests.test_phase4dc_browser_ui `
  projectiq_executioner.tests.test_phase4db_crud `
  projectiq_executioner.tests.test_phase4da_dependent_api `
  projectiq_executioner.tests.test_phase4cd5_cross_repository `
  projectiq_executioner.tests.test_phase2c -q
if ($LASTEXITCODE -ne 0) { throw 'Offline regression failed; do not bake or launch' }
```

The new 4D-C focused suite has **30 tests**. Browser success here uses mocked
subprocess output; it is NOT real Chromium/WHPX validation.

## 3. One-time, opt-in browser-enabled *trusted* image bake

**Do this before real browser acceptance.** Reuse your *official source* Ubuntu
24.04 cloud image and its independently published Ubuntu SHA-256 (`SHA256SUMS`).
Do not use the SHA of an already-sealed ProjectIQ base image as `--ubuntu-sha256`.
Keep old golden images untouched; `--out-template` must not already exist.

Download the official Google Chrome stable Linux AMD64 DEB through Google's
website; source provenance and redistribution policy are your responsibility.
Example official download URI:
`https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb`.
Review that it is a genuine **Google** package, not a student-repository
artifact. Its local hash is a *transfer-integrity pin*, **not** an independent
vendor signature. The browser DEB can update over time; compute the actual hash
of the file you inspected and provide the same hash to the trusted baker.

```powershell
$vmDir = "$HOME\ProjectIQ_VM"
$chromeDeb = "$vmDir\google-chrome-stable_current_amd64.deb"
# Run only if the file is not already downloaded and reviewed:
# Invoke-WebRequest -Uri 'https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb' -OutFile $chromeDeb
if (-not (Test-Path $chromeDeb)) { throw 'Review and download official Chrome DEB first' }
$chromeHash = (Get-FileHash $chromeDeb -Algorithm SHA256).Hash.ToLower()
Write-Host "Operator-review Chrome SHA256: $chromeHash"

# Replace with the exact paths/checksum of your original official Ubuntu cloud image.
$ubuntuImage = "$vmDir\ubuntu-24.04-server-cloudimg-amd64.img"
$officialUbuntuSha = '<COPY_EXACT_SHA_FROM_OFFICIAL_UBUNTU_SHA256SUMS>'
if (-not (Test-Path $ubuntuImage) -or $officialUbuntuSha -notmatch '^[a-f0-9]{64}$') {
    throw 'Original official Ubuntu image/checksum not provided'
}
$newImage = "$vmDir\ubuntu-24.04-projectiq-browser-v1.qcow2"
if (Test-Path $newImage) { throw 'Refusing to overwrite golden image' }
python -m projectiq_executioner.phase2.vm_worker.template_bake `
    --ubuntu-image $ubuntuImage --ubuntu-sha256 $officialUbuntuSha `
    --chrome-deb $chromeDeb --chrome-sha256 $chromeHash `
    --out-template $newImage --allow-trusted-template-network
if ($LASTEXITCODE -ne 0) { throw 'Trusted Chrome image bake/seal failed' }
$newImageHash = (Get-FileHash $newImage -Algorithm SHA256).Hash.ToLower()
Write-Host "Review new SEALED browser image: $newImageHash"
```

If your existing image needs UEFI or explicit QEMU binaries/accelerator, pass the
**same** `--uefi-firmware`, `--uefi-sha256`, `--qemu-binary`, etc. used during its
previous trusted bake. Do not omit a previously required firmware pin.

Only after successful seal/reboot and operator review, update local
`vm_config.json` (never commit it):

```powershell
$stamp = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item vm_config.json "outputs/executioner/vm_config_before_4dc_$stamp.json"
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
$cfg.base_image = $newImage
$cfg.base_sha256 = $newImageHash
$runtime = [pscustomobject]@{
    binary = '/usr/bin/google-chrome-stable'
    base_sha256 = $newImageHash
}
if ($cfg.PSObject.Properties['browser_runtime']) { $cfg.browser_runtime = $runtime }
else { $cfg | Add-Member -NotePropertyName browser_runtime -NotePropertyValue $runtime }
[System.IO.File]::WriteAllText((Resolve-Path vm_config.json).Path,
    ($cfg | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
python -c "from pathlib import Path; from projectiq_executioner.phase2.vm_worker.orchestrator import load_config; load_config(Path('vm_config.json')); print('NEW SEALED IMAGE CONFIG: PASS')"
if ($LASTEXITCODE -ne 0) { throw 'VM config invalid' }
```

If you cannot yet bake Chrome into a new sealed image, **stop at Steps 1–2**.
Preflight may still succeed once a policy hash is entered, but that *does not
establish the browser exists*. Never set the runtime to an unsealed executable.

## 4. Reuse the pinned FullStack Phase 3 plan/candidate

The existing D5 FullStack inputs should be under
`outputs/executioner/phase4cd5_fullstack_<timestamp>/phase3/test_plan.json`.
Find the latest reviewed one; do not assume it has matching source without
checking:

```powershell
$fsBase = Get-ChildItem 'outputs/executioner' -Directory -Filter 'phase4cd5_fullstack_*' |
    Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $fsBase) { throw 'D5 FullStack input folder not found' }
$fsPlan = "$fsBase/phase3/test_plan.json"
$fsCandidate = "$fsBase/phase2/phase2c_candidate.json"
if (-not (Test-Path $fsPlan) -or -not (Test-Path $fsCandidate)) { throw 'FullStack files missing' }
$p = Get-Content $fsPlan -Raw | ConvertFrom-Json
$c = Get-Content $fsCandidate -Raw | ConvertFrom-Json
if ($p.source.repository -ne 'VarunRGB/ProjectIQ-Python-Fixtures' -or
    $p.source.commit_sha -ne '790d5e974fc0119dd6c258aeed118348eb971295' -or
    $c.source.commit_sha -ne $p.source.commit_sha -or
    $c.adapter -ne 'node_fullstack_offline_v1') {
    throw 'FullStack source/adapter not the reviewed fixture; do not approve'
}
Write-Host 'FULLSTACK REVIEWED INPUTS: PASS'
```

Confirm source basis independently on GitHub at **that exact SHA**:
- `frontend/src/main.jsx` renders `<h1>ProjectIQ D4 pilot</h1>` and displays
  `SQLite verified` only after the JSON fetch reports `sqlite === true`.
- `backend/app.js` exposes reviewed `GET /api/sqlite-check` with SQLite effects.
- Frontend's existing trusted proxy targets only loopback backend.

## 5. Stage and approve the exact browser DOM catalog

```powershell
$stamp=Get-Date -Format yyyyMMdd_HHmmss
$d4cInputs="outputs/executioner/phase4dc_inputs_$stamp"
New-Item $d4cInputs -ItemType Directory -ErrorAction Stop | Out-Null
$browserCatalog="$d4cInputs/browser_catalog.json"
python -m projectiq_executioner.phase4.browser_ui_stage `
   --plan $fsPlan --heading 'ProjectIQ D4 pilot' `
   --loaded-text 'SQLite verified' --out $browserCatalog
if ($LASTEXITCODE -ne 0) { throw 'Browser staging failed' }
Get-Content $browserCatalog -Raw -Encoding UTF8
$h = python -c "import sys; from pathlib import Path; from projectiq_executioner.phase4.oracle_stage import _load_strict_json; from projectiq_executioner.phase4.oracle_contracts import digest; print(digest(_load_strict_json(Path(sys.argv[1]),32768)))" "$browserCatalog"
if ($LASTEXITCODE -ne 0 -or $h -notmatch '^[a-f0-9]{64}$') { throw 'Bad browser catalog digest' }
$identity="$($p.source.repository)@$($p.source.commit_sha)"
$approval="$identity#$h"
Write-Host "Review 4D-C approval: $approval"
$cfg = Get-Content vm_config.json -Raw -Encoding UTF8 | ConvertFrom-Json
if ($identity -cnotin @($cfg.approved_snapshots)) { throw 'FullStack SHA not approved' }
if (-not $cfg.PSObject.Properties['approved_browser_ui']) {
    $cfg | Add-Member -NotePropertyName approved_browser_ui -NotePropertyValue @()
}
$cfg.approved_browser_ui = @(@($cfg.approved_browser_ui) + $approval | Select-Object -Unique)
[System.IO.File]::WriteAllText((Resolve-Path vm_config.json).Path,
    ($cfg | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
```

Do not approve any other source, script or browser command. Existing `approved_snapshots`
and the pinned Node cache profile remain unchanged.

## 6. Offline preflight — NO VM

```powershell
$stamp=Get-Date -Format yyyyMMdd_HHmmss
$pf="outputs/executioner/phase4dc_preflight_$stamp"
python -m projectiq_executioner.phase4.browser_ui_vm_run `
    --plan $fsPlan --candidate $fsCandidate --catalog $browserCatalog `
    --vm-config vm_config.json --out $pf --preflight-only
if ($LASTEXITCODE -ne 0) { throw '4D-C preflight failed; no VM launch' }
$r=Get-Content "$pf/d4c_preflight.json" -Raw | ConvertFrom-Json
$r | Select-Object status,execution,browser_binary,browser_installed_in_guest
if ($r.status -ne 'APPROVED_NOT_EXECUTED' -or $r.execution -ne 'NOT_PERFORMED') {
    throw 'Unexpected preflight evidence'
}
```

**Preflight does not verify actual Chrome installation**; it proves only policy
and review inputs. Actual installed-browser and JavaScript/DOM proof requires the
next real VM step.

## 7. Real browser QEMU acceptance — once after the sealed image is ready

```powershell
$stamp=Get-Date -Format yyyyMMdd_HHmmss
$out="outputs/executioner/phase4dc_vm_$stamp"
python -m projectiq_executioner.phase4.browser_ui_vm_run `
    --plan $fsPlan --candidate $fsCandidate --catalog $browserCatalog `
    --vm-config vm_config.json --out $out --confirm-reviewed-browser
$rc=$LASTEXITCODE
if (-not (Test-Path "$out/d4c_run.json")) { throw 'No browser run report' }
$r=Get-Content "$out/d4c_run.json" -Raw | ConvertFrom-Json
$r | Select-Object status,execution,cleanup,issue_code
$r.checks | Format-Table tag,matched -AutoSize
if ($rc -ne 0 -or $r.status -ne 'BROWSER_PASSED' -or
    $r.execution -ne 'PERFORMED_IN_DISPOSABLE_VM' -or
    $r.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' -or
    @($r.checks).Count -ne 2 -or
    @($r.checks | Where-Object {$_.matched -ne $true}).Count -ne 0) {
    throw 'Browser acceptance incomplete: inspect evidence before any rerun'
}
Write-Host '4D-C REAL CHROME DOM ACCEPTANCE: PASS'
```

Expected **two matched DOM assertions**, `BROWSER_PASSED` and verified cleanup.
If `BROWSER_INCONCLUSIVE` with `BROWSER_FAILED_OR_OUTPUT_TOO_LARGE`, inspect
sandbox/guest system/browser compatibility; **never** add `--no-sandbox` or
transfer executable from student repo. Do not claim UI success from API 200.
Chrome subprocess runs under the existing APP_USER+cgroup limits; the program
prints only bounded booleans/statuses to the host (not raw DOM or screenshots).

## 8. Single legacy compatibility smoke, final tests, commit

Because the same sealed image and worker code will be used for other reviewed
adapters, run **one** previously approved legacy PHP/MySQL startup test using
the same `vm_config.json` and the existing legacy helper. Do not repeat the
full Phase 2 benchmark. Confirm `STARTUP_VERIFIED` + verified VM cleanup.

Then:

```powershell
python -m unittest `
  projectiq_executioner.tests.test_phase4dc_browser_ui `
  projectiq_executioner.tests.test_phase4db_crud `
  projectiq_executioner.tests.test_phase4da_dependent_api `
  projectiq_executioner.tests.test_phase4cd5_cross_repository `
  projectiq_executioner.tests.test_phase2c -q
if ($LASTEXITCODE -ne 0) { throw '4D-C final regressions failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace failure' }
git add `
  projectiq_executioner/phase2/vm_worker/qemu.py `
  projectiq_executioner/phase2/vm_worker/orchestrator.py `
  projectiq_executioner/phase2/vm_worker/guest_agent.py `
  projectiq_executioner/phase2/vm_worker/template_bake.py `
  projectiq_executioner/phase4/guest_browser_ui.py `
  projectiq_executioner/phase4/browser_ui_contracts.py `
  projectiq_executioner/phase4/browser_ui_vm_run.py `
  projectiq_executioner/phase4/browser_ui_stage.py `
  projectiq_executioner/tests/test_phase4dc_browser_ui.py `
  projectiq_executioner/PHASE4D_C_REAL_BROWSER_GUIDE.md
git diff --cached --check
git diff --cached --stat
git status --short
# Verify no vm_config.json, output, Chrome DEB, golden image, keys or credentials staged.
git commit -m 'Verify 4D-C real browser rendered UI in pinned disposable VM'
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
git push origin executioner
if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
```

**Exact acceptance scope:** reviewed React DOM rendering including the dynamic
backend fetch. This does **not** click buttons, fill forms, verify accessibility,
record video/screenshots, or generalize to every student SPA. Those belong to
later reviewed browser interactions / 4D-D E2E testing. Full untrusted student
execution stays `NO_GO` and not attested.
