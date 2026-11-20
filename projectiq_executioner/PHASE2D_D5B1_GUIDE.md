# ProjectIQ Phase 2D D5b.1 — Privilege separation (reviewed-fixture pilot)

**Status:** Implementation with local unit tests; **Windows/WHPX clean-guest and actual adapter regression runs remain mandatory**. This is a D5b incremental change applied **on top of uncommitted D5a**. Do NOT checkpoint until the rest of D5b is complete. This does not attest the VM for hostile submissions.

## Design and scope

- The existing SSH / guest-agent **controller** remains OS user `projectiq`.
- Cloud-init now creates OS user **`projectiq_run`**, with a separate UID, `nologin` shell, locked password, and **no sudo rights**. The controller's sudo policy is restricted to commands executed **as `projectiq_run`**, not root. Every authorized student-app server startup, npm install/build, and Python import/package installation is routed through this user. The temporary runtime and checkout directories are group-shared selectively; the SSH identity and MySQL credential files are not made writable by the runner.
- The prior `ALL=(ALL) NOPASSWD: /usr/bin/mysql` permission is **removed from every ordinary VM**. The system refuses to execute a project if the separate UID is absent or sudo to root remains available.
- For the **PHP/MySQL adapter only**, a root-owned, fixed, cloud-init boot script (no student input) waits for MySQL, creates the **ephemeral database `projectiq_run`**, and grants an application user rights **only within that database**. It generates a random per-VM secret **inside the guest** and writes it atomically to `/home/projectiq/.projectiq_mysql_credentials.json`, mode 0600. The controller imports the validated dump using that scoped user; the PHP app runs as `projectiq_run`. No MySQL root sudo is granted after boot. This retains the already-pinned MySQL 8 template; do not re-bake it.
- The **trusted** D4 frontend-facing Python proxy still runs as the controller, never interprets student Python code, and forwards only restricted loopback GETs. The untrusted Express server and Vite build run as `projectiq_run`.
- The clean-VM D5 probe now *also observes* separate app UID, controller's runas permission, and app-account root sudo denial. The existing `mysql_sudo_exception` should change from `PRESENT` to `NOT_OBSERVED`.

**Security limitations:** These are OS process privilege distinctions, not hardened container namespaces, cgroups, quotas, trusted SSH host identity, adversarial egress proof, or a hypervisor security audit. An application may still manipulate its own files and databases, and privileged cloud-init code remains in the boot path. The runtime VM egress policy stays restricted. Never execute unreviewed submissions.

## Apply onto your local D5a (do NOT push yet)

From your `executioner` branch, keep the D5a changes and the Windows `create=True` test fix. Confirm no unrelated changes:

```powershell
cd C:\Users\varun\Code\ProjectIQ
git branch --show-current
git status --short
```

Extract the accompanying D5b.1 patch ZIP (note this ZIP **does not include D5a**):

```powershell
Expand-Archive "$HOME\Downloads\ProjectIQ_Phase2D_D5b1_Privilege_Separation_Patch.zip" `
  -DestinationPath "$HOME\Downloads\ProjectIQ_D5b1" -Force
$patch = "$HOME\Downloads\ProjectIQ_D5b1\ProjectIQ_Phase2D_D5b1_Privilege_Separation.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch conflicts with local D5a; do not force apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch application failed' }
```

Then:

```powershell
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -q
```

Expected locally: **373 tests, OK** (the old 362 plus 11 new D5b.1 tests). If an old test was modified locally beyond the Windows `create=True` fix, inspect any patch conflict and stop rather than resetting your work.

## Real acceptance gate 1: clean VM, no student code

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5_acceptance `
  --vm-config vm_config.json `
  --run-clean-vm `
  --out outputs/executioner/d5b1_clean_vm.json
$p = Get-Content outputs/executioner/d5b1_clean_vm.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, security_attestation, cleanup, error_code
$p.checks | Format-List
$p.remaining_security_gates
```

Expected observations (real guest only):

- `status = CLEAN_VM_OBSERVATIONS_PASSED_NOT_ATTESTED` **if all observed controls pass**;
- `app_uid_distinct_from_controller = PASS`;
- `controller_runas_app = PASS`;
- `app_root_sudo_denied = PASS` and `root_sudo_denied = PASS`;
- `mysql_sudo_exception = NOT_OBSERVED`;
- `cleanup = VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`;
- `security_attestation = NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

If results differ, keep the report and **do not edit sudo policy or enable runtime networking** merely to make tests pass.

## Real acceptance gate 2: PHP/MySQL regression (new trusted bootstrap)

The following source and immutable SHA were the original reviewed Mini Task fixture. Use only if they are still on your local approved list:

```powershell
python -m projectiq_executioner.phase2.phase2d `
  --repo https://github.com/VarunRGB/Mini-Task-Collaboration-App `
  --ref c4f7965d16807fa05c3d7ffeee54f86fee4d0a37 `
  --vm-config vm_config.json `
  --run-reviewed-fixture `
  --out outputs/executioner/d5b1_php_mysql
$r = Get-Content outputs/executioner/d5b1_php_mysql/pipeline_summary.json -Raw | ConvertFrom-Json
$r | Select-Object status, execution, phase2c_adapter, cleanup, security_attestation
$v = Get-Content outputs/executioner/d5b1_php_mysql/vm_execution.json -Raw | ConvertFrom-Json
$v.guest_result | Format-List
```

Target: `STARTUP_VERIFIED`, `php_mysql_v1`, expected HTTP readiness, and clean VM/storage teardown. If the cloud-init bootstrap is late or fails, the agent returns `MySQL trusted cloud-init bootstrap unavailable` without granting additional sudo.

## Real acceptance gate 3: D4 multi-service regression

Reuse the already-approved exact D4 fixture SHA (from the separate local fixture checkout):

```powershell
$repo = 'https://github.com/VarunRGB/ProjectIQ-Python-Fixtures'
$sha = (git -C "$HOME\ProjectIQ_VM\D4_Fullstack_Fixture_checkout" rev-parse HEAD).Trim()
python -m projectiq_executioner.phase2.phase2d `
  --repo $repo --ref $sha --vm-config vm_config.json `
  --run-reviewed-fixture --out outputs/executioner/d5b1_fullstack
$r = Get-Content outputs/executioner/d5b1_fullstack/pipeline_summary.json -Raw | ConvertFrom-Json
$r | Select-Object status, execution, phase2c_adapter, cleanup, security_attestation
$v = Get-Content outputs/executioner/d5b1_fullstack/vm_execution.json -Raw | ConvertFrom-Json
$v.guest_result.service_results | Format-List
```

Target: frontend, built asset, backend, proxied API all 200; `sqlite_verified=True`, clean teardown and continued `NOT_ATTESTED` status.

**Additional D5b acceptance:** Rerun original FSDL, Flask, FastAPI, standalone Vite, and standalone Express reviewed fixtures at D5b.4. D5b.2 still needs disk quotas and D5b.3 process/IO limits and teardown. No push until *all of D5b* is complete.
