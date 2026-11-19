# ProjectIQ Phase 2D — D2: offline Flask / FastAPI and SQLite pilot

**Status:** Implemented and unit-tested; **real Windows/WHPX guest execution still pending**. D1 Node/Vite, Phase 2C static HTML, and PHP/MySQL remain unchanged. This is **not production sandbox attestation** and must not be used to run arbitrary unreviewed student repositories.

## Intended scope and explicit exclusions

- Supports exactly **one** `Flask` or `FastAPI` Python backend per pinned, operator-reviewed GitHub commit.
- Optionally accepts one embedded `SQLite` resource; SQLite uses Python's standard `sqlite3` module and temporary guest-local files. There is **no** external database service, automatic SQL migrations, database seeding or state persistence.
- Must have `requirements.txt` under the detected backend root, with **1–25** simple `package==version` lines (blank lines and full-line comments are okay). No pip CLI flags, extras, VCS, URLs, recursive files, editable installs, ranges, or unpinned requirements. For FastAPI the direct requirements must include both `fastapi` and `uvicorn`.
- Fixed entrypoint: `app.py` containing `app = Flask(...)`, or `main.py` containing `app = FastAPI(...)`. Recognized project root may be `.` or one safe nested root. Other layouts return a bounded failure or `NEEDS_ADAPTER`.
- Flask readiness: nonempty HTTP 200 response at `/`. FastAPI readiness: HTTP 200 JSON with `openapi` and `paths` at `/openapi.json`. **This is startup verification, not functional or browser testing.**
- Python **3.12** from the existing verified Ubuntu 24.04 guest; a SHA-pinned official **pip 25.2 pure-Python wheel** bootstraps pip via `PYTHONPATH`, without installing pip/venv in the OS or on Windows.
- Operator-authorized **preparation VM** may contact PyPI to **download binary wheels only**. It never installs or imports student packages or executes project scripts. All pip commands run in the trusted guest working directory, never the student checkout (prevents a student `pip.py` from shadowing pip).
- Normal **execution VM stays offline**: installs from the SHA-pinned wheelhouse with `pip --isolated install --no-index --only-binary=:all: --target` in disposable storage; starts only the approved framework launcher. The application code is executed only in this reviewed pilot execution VM.
- No Docker, external cloud/S3, live secrets, arbitrary script commands, Node/Express, or multi-service runtime. Do not modify the working Ubuntu QCOW2 template.

## A. Install D2 and unit-test (safe; no VM)

```powershell
cd C:\Users\varun\Code\ProjectIQ
python -m unittest discover -s projectiq_executioner/tests -p 'test_*.py' -q
```

Expected baseline with D2 changes: **265 tests OK**. Test success on Windows does not guarantee guest integration success.

## B. Planning a candidate without executing it

Select a small, **personally reviewed**, exact pinned public GitHub commit containing the supported root layout. Example minimal Flask source shape (illustrative only; **not** a URL or automatic approval):

```
app.py                 # from flask import Flask; app = Flask(__name__)
requirements.txt       # flask==3.1.1
```

Or minimal FastAPI:

```
main.py                # from fastapi import FastAPI; app = FastAPI()
requirements.txt       # fastapi==0.115.12 and uvicorn==0.34.2 on separate lines
```

Run with a real reviewed URL and its immutable 40-digit SHA:

```powershell
$repo = 'https://github.com/OWNER/REPO'
$sha = 'PUT_EXACT_40_CHARACTER_GITHUB_COMMIT_SHA_HERE'
python -m projectiq_executioner.phase2.phase2d `
  --repo $repo `
  --ref $sha `
  --out outputs/executioner/d2_python_plan
$c = Get-Content outputs/executioner/d2_python_plan/phase2c_candidate.json -Raw | ConvertFrom-Json
$c | Select-Object status, adapter, project_root, python_framework, probe_path
```

Expected **only for a matching candidate**: `CANDIDATE_NEEDS_VM_VERIFICATION`, `python_web_offline_v1`, `flask` / `fastapi`, `/` / `/openapi.json`. Plan generation does not authorize execution.

## C. Get the official pip 25.2 wheel and verify both PyPI metadata and local SHA

Official filename `pip-25.2-py3-none-any.whl`, SHA-256:

`6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717`

```powershell
$vmDir = "$HOME\ProjectIQ_VM"
New-Item -ItemType Directory -Path $vmDir -Force | Out-Null
$pipExpected = '6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717'
$pipWheel = Join-Path $vmDir 'pip-25.2-py3-none-any.whl'
$metadata = Invoke-RestMethod 'https://pypi.org/pypi/pip/25.2/json'
$record = $metadata.urls | Where-Object { $_.filename -eq 'pip-25.2-py3-none-any.whl' } | Select-Object -First 1
if (-not $record -or $record.digests.sha256 -ne $pipExpected) { throw 'PyPI pip 25.2 metadata mismatch' }
if (-not (Test-Path $pipWheel)) { Invoke-WebRequest $record.url -OutFile $pipWheel }
if ((Get-FileHash $pipWheel -Algorithm SHA256).Hash.ToLower() -ne $pipExpected) { throw 'pip wheel SHA-256 mismatch' }
'Official pip 25.2 wheel verified'
```

This downloads a wheel as data; **do not install it on Windows**.

## D. After manual source review: authorize the specific fixture and prepare its offline wheels

Add **only the reviewed pinned `OWNER/REPO@SHA`** to the *local*, gitignored `vm_config.json` -> `approved_snapshots`. Preserve existing entries. This action is an operator approval to run reviewed application code in a **nonproduction, not security-attested pilot**, not authorization for arbitrary future commits.

Close memory-heavy applications before a VM run (on a 16 GiB Windows host, prefer roughly 5–6 GiB available). Ensure QEMU is available:

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.phase2d.python_cache_prep `
  --repo $repo `
  --ref $sha `
  --vm-config vm_config.json `
  --pip-wheel $pipWheel `
  --pip-sha256 $pipExpected `
  --out "$vmDir\d2_python_cache" `
  --allow-trusted-pypi-network
```

**Explicit temporary network authorization** is required for this **separate disposable preparation VM**. Normal execution VM networking remains denied. On success, expect `OFFLINE_PYTHON_WHEELHOUSE_PREPARED_NOT_SANDBOX_ATTESTED`, `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, plus:

- `d2_python_cache/python-wheelhouse.zip`: binary-only Python wheelhouse
- `d2_python_cache/python_web_profile.json`: approved exact `OWNER/REPO@SHA` and hashes

If this step fails, **do not** rerun blindly; examine the fixed guest failure category. Do not weaken TLS verification, hash pinning, or allow arbitrary pip project commands.

## E. Register SHA-pinned Python profile, then execute offline

```powershell
$profile = Get-Content "$vmDir\d2_python_cache\python_web_profile.json" -Raw | ConvertFrom-Json
$config = Get-Content vm_config.json -Raw | ConvertFrom-Json
$config | Add-Member -NotePropertyName python_web_offline -NotePropertyValue $profile.python_web_offline -Force
[System.IO.File]::WriteAllText((Join-Path (Get-Location).Path 'vm_config.json'),
  (($config | ConvertTo-Json -Depth 20) + "`n"), (New-Object System.Text.UTF8Encoding($false)))
python -m projectiq_executioner.phase2.phase2d `
  --repo $repo `
  --ref $sha `
  --vm-config vm_config.json `
  --run-reviewed-fixture `
  --out outputs/executioner/d2_python_execution
```

Inspect:

```powershell
$r = Get-Content outputs/executioner/d2_python_execution/pipeline_summary.json -Raw | ConvertFrom-Json
$r | Select-Object status, execution, phase2c_adapter, cleanup, security_attestation
$v = Get-Content outputs/executioner/d2_python_execution/vm_execution.json -Raw | ConvertFrom-Json
$v.guest_result | Format-List
```

Expected **only on real success**: `STARTUP_VERIFIED`, `PERFORMED_IN_DISPOSABLE_VM`, `HTTP_200_API_PROBE_PASSED`, and `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`. `security_attestation` must remain `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## F. Required acceptance / regression before committing D2

1. Pass all D2 tests on Windows. Verify D0 and D1 planner tests still pass.
2. Real reviewed **Flask** import, offline wheel installation, HTTP 200 and cleanup.
3. Real reviewed **FastAPI** import, offline wheel installation, `/openapi.json` response and cleanup.
4. Test a project with optional local SQLite; distinguish `sqlite3` import support from actual CRUD/DB behavior.
5. Reject missing wheelhouse, wrong SHA, unsupported requirements, external service, unreviewed SHA and archive path tricks.
6. Confirm execution VM has no outbound egress and verify resource-limit/cleanup failure cases separately before considering security attestation.

Even a full pilot pass does not prove complete business functionality, arbitrary-stack compatibility or production isolation.
