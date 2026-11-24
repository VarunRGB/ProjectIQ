# ProjectIQ Executioner — Installation, Usage and Operator Handoff

> **Reviewed-fixture MVP, November 2026.** This README is the supported entry point for new developers, evaluators and the main ProjectIQ orchestrator. The full phase-by-phase design and historical acceptance summary live in [`../logs/version_1.4_executioner_agent_change_log.md`](../logs/version_1.4_executioner_agent_change_log.md). The old incremental milestone Markdown files were consolidated; they remain retrievable from Git history. **The disposable QEMU pilot is NOT security-attested for arbitrary untrusted student projects** (`NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`).

## 0. Quickest route: get an analysis running

You can use Executioner in **STATIC mode** on a publicly accessible GitHub repository without QEMU, Node, Docker, a Linux VM, an image, or preapproved repositories. Install Python 3.11+ and Git. Open Windows PowerShell in the folder where you want the repository:

```powershell
git clone https://github.com/VarunRGB/ProjectIQ.git
cd ProjectIQ

# One-time setup, Windows PowerShell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-executioner.txt
python scripts/check_executioner_setup.py --mode static

# Check the single-call CLI exists and no VM is required by STATIC mode
python -m projectiq_executioner.orchestration --help

# Construct a real, bounded STATIC request (does not execute the target app)
$request = @{
    submission_id = "DEMO-STATIC-001"
    repository_url = "https://github.com/VarunRGB/FSDL"
    ref = "f358b6edda071da6666e587a8f1db054965ba106"
    mode = "STATIC"
    max_vm_launches = 0
}
$requestPath = Join-Path $env:TEMP 'projectiq_executioner_static.json'
$request | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $requestPath
$out = "outputs/executioner/static_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.orchestration --request $requestPath --out $out
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 3)) { throw "Executioner failed; inspect input/output. Exit: $exitCode" }
Get-Content "$out/executioner_report.md" | Select-Object -First 70
```

`COMPLETED_WITH_LIMITATIONS` and exit **3** may be expected in STATIC mode: a repository can be analyzed and have test cases **planned** without running them. Do not reinterpret `NOT_TESTED` as `FAILED`. This demo requires access to public GitHub over HTTPS, even though it does not run a VM.

For a fresh Windows install that has Python 3.11 or 3.12 rather than 3.13, use the installed interpreter (`py -3.12`, `py -3.11`, or `python`). PowerShell execution policy may block venv activation; you can use `.\.venv\Scripts\python.exe` directly without changing system execution policy. On macOS/Linux, use `python3 -m venv .venv`, `source .venv/bin/activate` and `python3` commands. **Only static analysis is documented as cross-platform here; the tested reviewed VM path is Windows 11/WHPX.**

## 1. What Executioner does—and does not do

The single-call Executioner is `projectiq_executioner.orchestration.evaluate(...)`. It exposes **three explicit modes**:

| Mode | What happens | QEMU | Requirements |
|---|---|---|---|
| `STATIC` | Pin and inspect GitHub snapshot, Phase 1 inventory, Phase 2 architecture/capabilities, Phase 3 test planning, Phase 5 static quality, Phase 6 available evidence/limits, final report | **Never** | Python 3.11+, public GitHub access |
| `REVIEWED` | Same static work, plus select **operator-approved, exact-commit, reviewed** suites, launch disposable Linux VM(s), confirm guest evidence/cleanup, diagnose, report | Only for separately authorized suites | QEMU/WHPX, SSH, sealed guest, approved SHA, correct offline assets, approved catalogs where required |
| `RESUME` | Verify a prior result's identity and SHA-256 artifact receipts; reuse eligible saved evidence and optionally execute only genuinely new approved work | **0 new VMs possible** | Prior report + unchanged approvals; `max_vm_launches: 0` forbids launches |

Executioner does **not** execute repository-provided shell commands on the Windows host, install student packages into the host venv, infer correct arbitrary API responses from a README, assign final academic marks, or permit arbitrary unreviewed student code inside its QEMU development VM. Phase 7 is a **separate offline benchmark** for release quality, not a per-submission stage. The project-wide multi-agent orchestrator is separate from this package.

### 1.1 Recognized technology versus runnable technology versus tested functionality

These three ideas are different:

1. **Recognized:** Phase 1 can inspect text and infer Python, JS/TS, PHP, HTML/CSS, Java, Kotlin, C/C++, Go, Rust, Ruby, C#, Swift, Dart, Vue, Svelte, SQL and selected other files. It recognizes common route conventions in Flask/FastAPI, Express, Fastify, Koa/Hono, Django, NestJS, Spring, Laravel/PHP, ASP.NET, Go frameworks, Rails, Rust, Next.js, Nuxt, SvelteKit and React/Vue/Angular frontends. It also recognizes basic OpenAPI, GraphQL and gRPC contract candidates. Recognition **does not** run these platforms or verify that inferred routes exist at runtime.
2. **Runnable:** exactly six **bounded guest adapter shapes** exist. They may return `NEEDS_ADAPTER` on different layouts using the same language.
3. **Actually tested:** only a Phase 4 check with a matching test ID, approved oracle and verified VM observation may be called `PASSED` or `FAILED`. The four one-call real-Windows acceptance runs demonstrated cross-stack **readiness**, not exhaustive application feature behavior.

| Guest adapter | Supported reviewed fixture shape | Readiness (not full end-to-end correctness) | Main constraints |
|---|---|---|---|
| `static_html_v1` | One self-contained static HTML site | Guest serves `/index.html` | No arbitrary SPA build/backend |
| `php_mysql_v1` | Native PHP app + disposable local MySQL, optional static UI, reviewed SQL input | PHP/static HTTP startup | Requires baked PHP/MySQL guest and supported PDO/schema pattern; not generic Laravel |
| `node_vite_offline_v1` | One Vite-based SPA such as React/Vite | Offline Vite build and guest HTTP asset readiness | SHA-pinned Node bundle, npm v2/v3 lockfile, approved npm cache; not Next.js/Yarn/pnpm/Bun |
| `python_web_offline_v1` | One Flask **or** FastAPI service, optional embedded SQLite/static UI | Flask `GET /` or FastAPI `GET /openapi.json` | Simple pinned `requirements.txt`, binary-only prepared wheelhouse, supported fixed app entrypoint |
| `node_express_offline_v1` | One Express JSON API | Approved literal health GET and nonempty JSON 200 | Fixed `app.js`/`server.js`/`index.js`, lockfile, pinned npm cache; no cloud DB |
| `node_fullstack_offline_v1` | One Vite UI + one Express backend in separate safe roots, optional Node `node:sqlite` | Built asset, loopback backend + frontend proxy/API checks | Both npm locks, fixed routes and source-backed client/backend match, no arbitrary microservices |

Other recognized technologies, cloud services, remote databases, multiple backends, custom install hooks or package managers require separate approved adapters. A successful run does not imply the VM has been attested safe for unreviewed code.

### 1.2 Reviewed suite types

The one-call orchestrator can select these fixed Phase 4 suites, but most require separate **operator-owned, exact-SHA reviewed catalogs** before execution:

| `kind` | Catalog keys required | Scope |
|---|---|---|
| `readiness` | none (adapter-provided, if suitable) | Guest startup/HTTP test |
| `api` | `oracle` | Reviewed API request/assertions |
| `auth` | `auth`, `oracle` | Narrow reviewed authentication/session |
| `db_fixture` | `fixture` | Controlled, disposable DB setup |
| `db_postconditions` | `auth`, `oracle`, `postconditions` | Reviewed read-only DB witness |
| `scenario_isolation` | `auth`, `oracle`, `postconditions`, `isolation` | Two independent VM scenarios; budget **2** |
| `dependent_workflow` | `fixture`, `workflow` | Source-supported dependent API workflow |
| `crud` | `auth`, `oracle`, `crud` | Narrow reviewed authenticated CRUD flow |
| `browser_ui` | `browser` | Reviewed real-browser DOM evidence |
| `browser_login` | `login` | Narrow reviewed browser login/session |

The fixed catalogs are not generic autogenerated tests. The separate legacy Phase 4 cross-repository acceptance harness is **not** an extra one-call suite kind.

## 2. Install dependencies: choose one of two paths

**Recommended for Executioner development, STATIC analysis and the Python orchestrator:**

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-executioner.txt
```

`requirements-executioner.txt` is intentionally small. Current Executioner source uses Python's standard library for source analysis, planning, quality, diagnosis, benchmarking, and the orchestration API. The additional **host controller** library `pycdlib` creates the cloud-init ISO for the reviewed VM. It is not a guest app dependency. The lightweight install intentionally avoids downloading the full ProjectIQ NLP/PyTorch stack.

**For all ProjectIQ agents on the same Python environment:**

```powershell
python -m pip install -r requirements.txt
```

The root file now includes `-r requirements-executioner.txt` **as well as** the existing Summarizer, Research and Integrity dependencies. Full-project packages include large torch/transformers and SpaCy components. In particular, the existing `numpy==2.5.4` pin requires **Python 3.12+**, even though Executioner-only code supports Python 3.11+. Use Python 3.12 or 3.13 for a shared all-agent environment; CPU/GPU wheel availability and transitive package versions still vary. These requirements are *constraints*, not a resolved, reproducible cross-platform lockfile. For an evaluator who only needs Executioner, prefer the lightweight file. Never install **submitted** project dependencies into this venv.

System dependencies are **not pip-installable**: QEMU executables, Windows Hypervisor Platform, OpenSSH, the Ubuntu guest disk, Linux Node tarball, npm caches, Python wheels, PHP/MySQL guest packages and optional Chrome are covered below.

### 2.1 Offline regression tests (no VM)

```powershell
python -m compileall -q projectiq_executioner
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -q
python -m unittest discover -s tests -p "test_executioner_setup.py" -q
```

The Executioner tests contain **synthetic source strings that mention Python web frameworks**; these are not requirements to install FastAPI or HTTPX on the controller. The above suite is the correct standard-library `unittest` discovery path. Running the **entire ProjectIQ** `python -m pytest -q` suite requires the root `requirements.txt` and whatever independent model/mock fixtures individual agents use. Passing offline tests cannot independently attest guest safety or WHPX behavior.

### 2.2 No-surprises setup diagnostics

```powershell
# No VM, no GitHub download, no writes, no student code
python scripts/check_executioner_setup.py --mode static
python scripts/check_executioner_setup.py --mode reviewed --vm-config .\vm_config.json
# Optional: additionally verify SHA-256 of large trusted image and UEFI ROM
python scripts/check_executioner_setup.py --mode reviewed --vm-config .\vm_config.json --check-image-sha
```

The checker verifies Python, importability of `pycdlib` in reviewed mode, tool locations, config structure, trust-asset presence and optionally their hashes. **It is preflight guidance, not a hypervisor security attestation, and it never starts a VM.** It prints bounded reasons and never displays operator secrets.

## 3. Windows 11 setup for reviewed disposable Linux VMs

> **Stop here if you only need STATIC.** This setup is for a trusted operator running **personally reviewed fixture commits**, not a public, multi-tenant grading server. The user's previously verified environment used Windows 11, QEMU/WHPX, OpenSSH and a SHA-pinned disposable Ubuntu guest. Do not assume identical results on an untested machine.

### 3.1 Host prerequisites

1. Enable hardware virtualization in firmware and **Windows Hypervisor Platform** in Windows optional features; reboot if prompted. WHPX availability may differ across editions/firmware and other hypervisors.
2. Install reputable QEMU x86-64 for Windows. Find `qemu-system-x86_64.exe` and `qemu-img.exe`. Add their directory to your terminal `PATH` or specify absolute paths in your local `vm_config.json` using `qemu_binary` and `qemu_img_binary`.
3. Enable/install **Windows OpenSSH Client** so `ssh.exe` and `ssh-keygen.exe` work. No Docker Desktop, WSL2 shell, or host-mounted student checkout is required for this VM pilot.
4. Install `pycdlib` using the lightweight Python requirements (above).
5. Keep at least several GiB of spare host RAM and disk space; the typical approved fixture VM used about 3 GiB RAM. Close memory-heavy applications. Never try to fix a failed capacity limit by silently weakening VM safety policy.

Check tools:

```powershell
qemu-system-x86_64 --version
qemu-img --version
ssh -V
Get-Command ssh-keygen | Select-Object -ExpandProperty Source
python -c "import pycdlib; print('pycdlib import OK')"
```

If QEMU's directory is not on `PATH` yet, only for the current PowerShell session:

```powershell
$env:Path = "C:\Program Files\qemu;$env:Path"
```

Do **not** uninstall Docker to make this work; it is simply unused by the current reviewed QEMU worker.

### 3.2 Build and pin the trusted Ubuntu guest template (one time per operator image)

**Do not commit image files.** Fetch an official **Ubuntu 24.04 AMD64 cloud QCOW2 image** from https://cloud-images.ubuntu.com/releases/24.04/release/ and its matching official `SHA256SUMS`, validate the official digest, and store both outside the Git checkout. The original `.img` download is QCOW2-formatted even though its filename ends in `.img`. The base image is **not** a published ready-to-run asset in this repository.

The included trusted image baker uses an **explicitly permitted temporary setup network** to install approved guest packages, seal a template and perform a throwaway offline guest check. The normal evaluation VM must not have internet egress.

```powershell
$base = "$HOME\ProjectIQ_VM\ubuntu-24.04-server-cloudimg-amd64.img"
$officialSha = "<64-character SHA-256 from Ubuntu official SHA256SUMS>"
$template = "$HOME\ProjectIQ_VM\ubuntu-24.04-projectiq-v1.qcow2"

# Confirm hash against the official checksum BEFORE invoking the baker
if ((Get-FileHash -Algorithm SHA256 $base).Hash.ToLower() -ne $officialSha.ToLower()) {
    throw "Ubuntu cloud-image SHA mismatch"
}

python -m projectiq_executioner.phase2.vm_worker.template_bake `
    --ubuntu-image $base `
    --ubuntu-sha256 $officialSha `
    --out-template $template `
    --accelerator whpx `
    --allow-trusted-template-network
```

Inspect the baker's actual output and confirm `SEALED_TEMPLATE_BOOT_VALIDATED_NOT_PRODUCTION_ATTESTED` and the resulting template SHA. The **operator** must review the image provenance and guest contents. If official Ubuntu requires UEFI for your configuration, supply a separately reviewed `--uefi-firmware` and exact `--uefi-sha256`, then keep the matching entries in the VM config. **Do not mutate/rebake an already-accepted golden image in place.** An optional official Chrome DEB can be added at bake time **only** when explicitly reviewed and SHA-pinned (`--chrome-deb`, `--chrome-sha256`) for browser suites.

### 3.3 Configure your private, SHA-bound operator policy

```powershell
Copy-Item .\projectiq_executioner\vm_config.example.json .\vm_config.json
```

Edit `vm_config.json` locally with the **actual absolute** trusted template path and the real `base_sha256` computed and approved from the baker. Set `accelerator` to `whpx` on the tested Windows host. Keep `approved_snapshots` limited to **specific reviewed `Owner/Repo@40-character-sha` identities**. Its sample list is illustrative; **do not auto-approve new commits**. Optional explicit tool paths and UEFI hashes are supported. Do not commit `vm_config.json`, VM overlays, caches, approval catalogs, secret material or outputs. The root `.gitignore` excludes the local config and output directory.

Example **schema**, not a ready-to-run approved config:

```json
{
  "base_image": "C:/Users/OPERATOR/ProjectIQ_VM/ubuntu-24.04-projectiq-v1.qcow2",
  "base_sha256": "REPLACE_WITH_ACTUAL_SEALED_TEMPLATE_SHA256",
  "accelerator": "whpx",
  "approved_snapshots": [
    "VarunRGB/FSDL@f358b6edda071da6666e587a8f1db054965ba106"
  ]
}
```

**Security note:** A URL/ref provided in a student's submission is not operator approval. The request mode `REVIEWED` cannot create permission. There are separate independently checked allowlists for source SHA and reviewed test catalogs; the VM worker and original suite compilers revalidate both. An unapproved repository must return a review requirement, not trigger weakened safeguards.

Now run preflight only, without a VM:

```powershell
python scripts/check_executioner_setup.py --mode reviewed --vm-config .\vm_config.json --check-image-sha
```

If preflight fails, correct the **host** configuration; do not edit the submitted repository to work around a blocked execution path.

### 3.4 Prepare offline assets for non-static adapters (operator action, per reviewed SHA)

**Normal evaluation VMs are offline.** For app families needing npm or pip, first prepare exact Linux-compatible assets using a deliberately **network-authorized preparation VM**. This differs from `pip install -r requirements-executioner.txt` on the host. Exact source/lockfile/pip-wheel/cache digests must be checked during preparation and evaluation.

**Node/Vite, standalone Express, Vite+Express:** use official Linux-x64 Node **v24.22.3**, the matching official release hash and reviewed `package-lock.json` files. The historical fixture guide recorded:

- Archive `https://nodejs.org/dist/v24.22.3/node-v24.22.3-linux-x64.tar.xz`.
- SHA-256 `4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb` (verify against Node's signed official SHASUMS; this README is not the cryptographic trust root).

```powershell
$repo = "https://github.com/OWNER/REVIEWED_REPOSITORY"
$sha = "<reviewed full 40-character commit SHA>"
$nodeTar = "$HOME\ProjectIQ_VM\node-v24.22.3-linux-x64.tar.xz"
$nodeSha = "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb"
if ((Get-FileHash -Algorithm SHA256 $nodeTar).Hash.ToLower() -ne $nodeSha) { throw "Node SHA mismatch" }

python -m projectiq_executioner.phase2.phase2d.cache_prep `
    --adapter node_vite_offline_v1 `
    --repo $repo --ref $sha `
    --vm-config .\vm_config.json `
    --node-tarball $nodeTar --node-sha256 $nodeSha `
    --node-version v24.22.3 `
    --out "$HOME\ProjectIQ_VM\npm_cache_reviewed" `
    --allow-trusted-npm-network
```

For a separately approved Express project use `--adapter node_express_offline_v1`; for Vite + Express use `node_fullstack_offline_v1`. Each supported project shape requires its own validated source and lockfile evidence. The preparer outputs a SHA-pinned npm-cache ZIP and an adapter-specific profile JSON. **Carefully merge** its adapter mapping into your private `vm_config.json` without dropping `base_image`, `base_sha256`, other profiles or `approved_snapshots`. `npm ci --ignore-scripts` and offline evaluation rules reject arbitrary lifecycle scripts, custom registries and nonmatching locks.

**Flask/FastAPI:** the historical D2 setup uses a trusted **pip 25.2** universal wheel, SHA-256 `6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717`; **confirm this against official PyPI release metadata**. Only reviewed Python repositories with simple exact-version-pinned requirements (plus `uvicorn` for FastAPI) can receive an offline wheelhouse.

```powershell
$repo = "https://github.com/OWNER/REVIEWED_PYTHON_REPOSITORY"
$sha = "<reviewed full 40-character commit SHA>"
$pipWheel = "$HOME\ProjectIQ_VM\pip-25.2-py3-none-any.whl"
$pipSha = "6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717"
if ((Get-FileHash -Algorithm SHA256 $pipWheel).Hash.ToLower() -ne $pipSha) { throw "pip wheel SHA mismatch" }

python -m projectiq_executioner.phase2.phase2d.python_cache_prep `
    --repo $repo --ref $sha `
    --vm-config .\vm_config.json `
    --pip-wheel $pipWheel --pip-sha256 $pipSha `
    --out "$HOME\ProjectIQ_VM\python_wheels_reviewed" `
    --allow-trusted-pypi-network
```

The preparer outputs `python-wheelhouse.zip` and a `python_web_profile.json`. Merge the **exact `Owner/Repo@SHA` `python_web_offline` mapping** into your private VM config. Never run student `pip install` on Windows. Binary wheels must be prepared for guest OS/architecture using the trusted guest workflow; availability of an offline cache is not permission to run code without independent source approval.

**PHP/MySQL:** the curated stack expects guest PHP CLI/PDO and MySQL 8 installed in the baked image; schema and credentials remain guest-local and disposable. There is no generic host MySQL setup step.

**Browser suites:** these require an explicitly approved browser-capable template, browser catalog and reviewed guest/CDP. Not every browser runtime works under the ordinary 128-task profile. Earlier CDP diagnostics required investigation and temporary, **non-default** resource experiments. Never silently elevate resource caps or relabel an inconclusive result PASS.

### 3.5 Run a reviewed one-call readiness demo

**Only after** exact SHA approval, image/tool preflight and any required offline asset profile:

```powershell
$request = @{
    submission_id = "DEMO-REVIEWED-001"
    repository_url = "https://github.com/VarunRGB/FSDL"
    ref = "f358b6edda071da6666e587a8f1db054965ba106"
    mode = "REVIEWED"
    run_readiness = "YES"
    max_vm_launches = 1
}
$requestPath = Join-Path $env:TEMP "projectiq_executioner_reviewed.json"
$request | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $requestPath
$out = "outputs/executioner/reviewed_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.orchestration `
    --request $requestPath `
    --vm-config .\vm_config.json `
    --out $out
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 3)) { throw "Inspect Security Hold / Failed result; exit $exitCode" }
```

On a supported approved fixture, confirm `vm_verified: true`, cleanup `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, and a matching actual test observation before a case becomes `PASSED`. An HTTP 200 doesn't establish all project features. If host preflight fails, don't repeatedly launch guests; inspect the host issue first.

## 4. CLI reference, API contract and result interpretation

### 4.1 CLI syntax

```text
python -m projectiq_executioner.orchestration --request REQUEST.json --out NEW_OUTPUT_DIRECTORY
    [--vm-config vm_config.json]
    [--review-root TRUSTED_CATALOG_ROOT]
    [--review-registry TRUSTED_REVIEW_REGISTRY.json]
    [--work-root TRUSTED_VM_WORKSPACE]
```

The bracketed options indicate **optional flags**, not literal PowerShell syntax. Use a **fresh, nonexistent `--out` directory** per assessment to prevent evidence overwrites. `work_root`, review catalogs and registry must be operator-controlled resources, never repository-supplied content.

Supported request keys (unknown keys are rejected):

| Key | Meaning |
|---|---|
| `submission_id` | Required bounded ID, letters/digits and up to 79 additional `A-Za-z0-9_.-` characters |
| `repository_url` | Required public `https://github.com/OWNER/REPO` root |
| `ref` | Optional branch/tag/SHA in STATIC; full lowercase 40-char SHA in reviewed/resume |
| `mode` | `STATIC`, `REVIEWED`, `RESUME` |
| `run_readiness` | `AUTO` (default), `YES`, `NO` |
| `max_vm_launches` | Integer **0–12**, default 4 |
| `suites` | At most ten explicitly selected reviewed suite objects `{"kind":..., "catalogs":{...}}`; forbidden in STATIC |
| `resume_target` | Required with RESUME: `STATIC` or `REVIEWED` |
| `resume_from` | Required with RESUME: prior `executioner_result.json`, whose identity and receipts will be validated |

Use `--review-root` and optionally `--review-registry` only when an **operator** has separately approved exact-SHA catalogs. An example of a suite request (deliberately not authorized):

```json
{
  "submission_id": "APPROVED-API-001",
  "repository_url": "https://github.com/OWNER/REPO",
  "ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "mode": "REVIEWED",
  "run_readiness": "NO",
  "max_vm_launches": 2,
  "suites": [
    {"kind": "api", "catalogs": {"oracle": "approved_api_catalog.json"}}
  ]
}
```

The URL, SHA and catalog name are placeholders, not independently reviewed resources. The registry selects candidates but **does not itself approve** a catalog; Phase 4 compilers revalidate approvals and source evidence.

### 4.2 RESUME without another VM

```powershell
# Replace the example path with a genuine, verified earlier evaluation result.
$prior = (Resolve-Path "outputs/executioner/previous_approved_run/executioner_result.json").Path
$request = @{
    submission_id = "DEMO-REVIEWED-001"
    repository_url = "https://github.com/VarunRGB/FSDL"
    ref = "f358b6edda071da6666e587a8f1db054965ba106"
    mode = "RESUME"
    resume_target = "REVIEWED"
    resume_from = $prior
    run_readiness = "YES"
    max_vm_launches = 0
}
$requestPath = Join-Path $env:TEMP "projectiq_executioner_resume.json"
$request | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $requestPath
$out = "outputs/executioner/resume_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.orchestration `
    --request $requestPath --vm-config .\vm_config.json --out $out
```

RESUME only reuses a suite if saved hashes, source identity, adapter, approval/catalog fingerprint, VM witness and cleanup still validate. Otherwise the suite remains blocked or needs review. With budget **0**, it may **never launch a new VM**. Inspect `reused_from_prior` and `vm_attempts_scheduled` rather than trusting the existence of an old JSON file.

### 4.3 Read individual test cases and statuses

```powershell
$r = Get-Content "$out/executioner_result.json" -Raw | ConvertFrom-Json
$r | Select-Object status, mode, effective_mode, vm_attempts_scheduled
$r.test_case_matrix.counts | Format-List
$r.test_case_matrix.discovered_cases |
    Select-Object test_id, title, category, planning_status, outcome, reason |
    Format-Table -AutoSize -Wrap
$r.suites |
    Select-Object kind, state, result_status, vm_verified, reused_from_prior, cleanup |
    Format-Table -AutoSize
```

| Case outcome | Interpretation |
|---|---|
| `PASSED` | Exact planned test ID matched a **verified** passing Phase 4 observation |
| `FAILED` | Exact planned ID matched a verified failing reviewed assertion; **not automatically proven student fault** |
| `INCONCLUSIVE` | Valid indeterminate / conflicting observations |
| `BLOCKED` | An execution could not be verified or a gate prevented it |
| `NOT_TESTED` | Discovered but not observed, usually missing oracle, unsupported feature or STATIC mode |

Additional reviewed checks without a Phase 3 ID appear separately and never inflate discovered-case coverage. A suite-level `PASS` cannot promote unrelated source features to PASS.

Top-level states include `COMPLETED`, `COMPLETED_WITH_LIMITATIONS`, `NEEDS_REVIEW`, `SECURITY_HOLD` and `FAILED`. CLI returns **0** for `COMPLETED`, **3** for limited or needs-review work, and **2** for failed/hold/invalid input. Always inspect the JSON `status`; exit 3 is often an expected conservative evaluation. If cleanup or provenance cannot be verified, **stop further guest execution** until an operator investigates.

### 4.4 Main ProjectIQ orchestrator Python API

```python
from pathlib import Path
from projectiq_executioner.orchestration import OperatorContext, evaluate

# Trusted backend-owned configuration, NOT a student-supplied file:
operator = OperatorContext(vm_config=Path("vm_config.json"))

result = evaluate(
    {
        "submission_id": "SUB-001",
        "repository_url": "https://github.com/VarunRGB/FSDL",
        "ref": "f358b6edda071da6666e587a8f1db054965ba106",
        "mode": "STATIC",  # switch to REVIEWED only after separate operator authorization
        "max_vm_launches": 0,
    },
    output_dir=Path("outputs/executioner/SUB-001-unique-run"),
    operator=operator,
)
print(result["status"])
print(result["test_case_matrix"]["counts"])
print(result["review_requests"])
```

`evaluate` is **synchronous**. The main ProjectIQ orchestrator should own job scheduling, submission isolation, budgets, error handling, model scheduling for other agents, and the faculty UI. Do not interpret `NOT_TESTED`, provisional maintainability indicators or Phase 6 hypotheses as a final academic grade. The Version 1.4 log describes the full phased architecture and report semantics.

### 4.5 Output tree

```text
outputs/executioner/<unique-run>/
    phase1/manifest.json, manifest.md
    phase2/phase2a_plan.json, phase2b_preflight.json,
           phase2c_candidate.json, phase2d_capabilities.json
    phase3/test_plan.json
    phase4/<approved-suite>/...  # only if approved and executed/reused
    phase5/code_quality.json, code_quality.md
    phase6/phase6_diagnosis.json, phase6_diagnosis.md
    executioner_result.json
    executioner_report.md
```

The JSON is the canonical machine-readable handoff. Markdown includes project intelligence, planned tests, *actually* verified outcomes, code-quality findings, diagnoses, review requests, evidence receipts and limitations. Raw cookies, student credentials, complete response bodies and secret values must not be emitted in consolidated reports.

## 5. Tests, acceptance and troubleshooting

A new maintainer should follow this **minimal acceptance sequence** before attempting QEMU:

1. Clone `main` (or checkout preserved `executioner`), create venv, install `requirements-executioner.txt` only.
2. Run `python scripts/check_executioner_setup.py --mode static` and import the public API.
3. Run local `unittest` discovery in `projectiq_executioner/tests`.
4. Run one pinned FSDL STATIC request. Verify `phase1`, `phase3`, `phase5`, `test_case_matrix`, report, and **zero VM attempts**.
5. **Only as a reviewed operator:** install toolchain/image, run `--mode reviewed --check-image-sha`, approve a fixture and launch **one** bounded readiness VM. Validate observed HTTP result, `vm_verified` and cleanup.
6. Optionally RESUME that verified case with `max_vm_launches: 0`. Only then consider independently reviewed functional catalogs; don't repeat every historical exploratory VM run.

| Symptom | Action |
|---|---|
| `ModuleNotFoundError: pycdlib` | Install `requirements-executioner.txt` into the **active** controller venv; verify Python executable |
| `ModuleNotFoundError: projectiq_executioner` | Run from repository root; use `python -m ...` rather than launching internal files directly |
| `qemu-system-x86_64` or `qemu-img` not found | Install Windows QEMU; set `PATH` or operator config; this is a **host infrastructure** error |
| SSH tools missing | Enable Windows OpenSSH Client; check `ssh` and `ssh-keygen` |
| Template SHA mismatch | Recheck provenance and official source/trusted template; don't bypass hash checks |
| WHPX errors | Confirm firmware virtualization, Windows Hypervisor Platform, QEMU build, image/UEFI setup; don't silently fall back to Docker |
| Node/npm cache refused | Confirm exact approved SHA, lockfile, Linux Node archive/version and npm cache hashes; prepare in trusted Linux VM |
| Python wheelhouse missing | Confirm pinned simple requirements and exact-source, binary-only guest wheelhouse; no host-side student package install |
| `NEEDS_ADAPTER` | Detected source stack has no compatible reviewed guest adapter; STATIC remains useful |
| Missing oracle/catalog | Review source, expectations and catalog separately; only operator can add digest approvals |
| `COMPLETED_WITH_LIMITATIONS` / exit 3 | Read coverage/oracle findings; not necessarily a VM or student app failure |
| Browser CDP `INCONCLUSIVE` | Preserve forensics; never report DOM/rendering PASS or alter resource caps without review |
| `SECURITY_HOLD` / cleanup unverified | Stop further guests on the worker; inspect processes, storage, evidence and security boundaries |
| Unicode `â€”` in PowerShell | Terminal display issue; trust UTF-8 Markdown and JSON rather than the console glyph |
| Output folder exists | Generate a new unique folder; don't erase old evidence to make a rerun succeed |

The host pip install does **not** provide Ubuntu, QEMU/WHPX, SSH, approved source SHAs, offline npm/pip artifacts, an independently reviewed oracle or an authorized browser catalog. The setup checker is deliberately a **read-only preflight**, not a one-button unsafe VM bootstrap.

## 6. Architecture, historical safety decisions and document consolidation

**Actual per-submission order:** Phase 1 pins source → Phase 5 static-quality runs in a `ThreadPoolExecutor(max_workers=2)` while Phase 2 capability planning and Phase 3 test-plan building proceed → REVIEWED/RESUME-only Phase 4 original compilers preflight and launch approved suites **sequentially**, each in its own disposable VM, within budget → Phase 6 diagnoses saved, validated evidence **without another VM** → an exact-test-ID result matrix, JSON and Markdown are generated. Phase 7's benchmark is **not** executed per student. The separate main ProjectIQ orchestrator calls this **one API**.

**Important details retained from the retired phase guides:**

- The legacy Docker capability probe is not the native QEMU trust gate. The Windows pilot executes supported reviewed student fixture code only inside its pinned Ubuntu guest.
- QEMU/WHPX success plus process/disk/cgroup/host observations **do not establish** independent VM-escape or hostile-student-code safety. The reviewed-pilot security decision remained **NO_GO for arbitrary untrusted students**.
- The guest uses loopback-only SSH with ephemeral identity; app processes and dependency installers run under restricted guest identities, with approved guest-local SQL and other witnesses. Status **and** cleanup must be verified; missing evidence cannot become PASS.
- D5c added Windows Job Object kill-on-controller-death and signed/leased scratch recovery. The sweeper **never kills a process using a stale stored PID** and refuses ambiguous or in-use workspaces. This does not attest host crashes, same-user adversaries, or Linux/KVM parity.
- D5d independently inspected the live Windows QEMU executable/arguments, owner/parent, Job membership, SSH listener and selected storage/network/resource properties. Those are bounded observations, not general hypervisor security attestation.
- Phase 5 quality uses Python AST and bounded lexical heuristics; provisional measured-only weights are lint 30%, Python AST complexity 30%, duplication 20% and structure 20%. Unknown time complexity is not a penalty; a nominal score of 100 is not a grade. Sensitive values are not emitted.
- Phase 6 diagnoses from evidence and keeps root-cause explanations as unverified hypotheses. **Student defect remains NOT_ESTABLISHED** without stronger independent truth. The separate Phase 7 empirical benchmark still has real-defect/human-rating limitations.

The old incremental Markdown guides are preserved **as committed Git objects**. To inspect historical audit detail without restoring retired files to today's source tree:

```powershell
$baseline = "655fb5b1b874758a4383955d6e11e06410aa7d91"
git show "${baseline}:projectiq_executioner/PHASE2D_D5E_CLOSEOUT_GUIDE.md"
git show "${baseline}:projectiq_executioner/PHASE4D_C_REAL_BROWSER_GUIDE.md"
```

Those legacy documents were patch-by-patch instructions containing obsolete "apply on top of checkpoint" steps, inconsistent pending statuses and repeated tests. New users should use **this README**. The full phase-by-phase technical development record remains [`../logs/version_1.4_executioner_agent_change_log.md`](../logs/version_1.4_executioner_agent_change_log.md). Git history preserves unique fine-grained historical details if needed for an audit, without leaving dozens of outdated entry points in the current tree.

**Before publishing the handoff:** run the no-VM tests, confirm only docs/dependencies/setup tools changed, run `git diff --check`, inspect every deleted Markdown path and run a real static smoke check on a machine with GitHub access. Do not commit private approvals, local VM images, student checkouts, caches or `outputs/`.
