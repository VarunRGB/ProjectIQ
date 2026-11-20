# ProjectIQ D5b.3a — Resource ceilings and cgroup capability baseline

**Status:** implementation + 417 local unit tests passed; real Ubuntu WHPX acceptance **pending**. This is the *first increment* of D5b.3, not completion of the process/memory/I/O hardening milestone or security attestation.

## What is actually enforced

The shared `guest_agent._project_command` now runs *every* project-owned command through a fixed Python 3 isolated-mode launcher **after** `sudo -u projectiq_run` and the sanitised environment. This covers application startup, npm offline installs, Vite builds, Python dependency installs/imports, and PHP/Express processes. The launcher uses only `os.execvpe`, no shell, and never executes as root.

Linux hard resource limits inherited by that application process and descendants:

| Resource | Limit | Exact scope |
| --- | --- | --- |
| `RLIMIT_NPROC` | **96** Linux tasks | Aggregate for the **real application UID**, not a cgroup service tree. Does not restrict root; app UID is non-root. |
| `RLIMIT_NOFILE` | **512** open descriptors | Per process, inherited. |
| `RLIMIT_CORE` | **0** core-dump bytes | Per process, inherited. |

Hard limits are lowered or kept stricter; never raised. If the launcher cannot establish them, it fails closed **before running any project code**. No changes are made to the controller's privilege permissions, golden image, SSH policy, VM network, or offline assets.

**These DO NOT replace:** `memory.max`, cgroup-wide `pids.max`, `io.max`, root-QCOW2 write confinement, and per-job aggregate resource quotas. VM RAM/vCPU arguments still apply, but they do *not* prove memory containment of an individual process tree. The next D5b.3b step should add **cgroup v2-based** memory/PID/I/O enforcement after inspecting real guest capabilities, without granting root or cgroup-controller access to student code.

## Apply over uncommitted D5b.2

You should already have the D5a, D5b.1, D5b.2, disk-boot and Windows test fixes locally, **uncommitted**. The patch was generated against that precise baseline. From the ProjectIQ repo root:

```powershell
Expand-Archive "$HOME\Downloads\ProjectIQ_Phase2D_D5b3a_Resource_Ceilings_Patch.zip" `
  -DestinationPath "$HOME\Downloads\ProjectIQ_D5b3a" -Force
$patch = "$HOME\Downloads\ProjectIQ_D5b3a\ProjectIQ_Phase2D_D5b3a_Resource_Ceilings.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'D5b.3a patch incompatible with local changes' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'D5b.3a patch failed' }
python -m unittest discover -s projectiq_executioner/tests -p 'test_*.py' -q
```

Expected **417 tests OK** (on Windows, the direct Linux launcher test is skipped).

## Inspect without booting, then run a clean VM

No student archive, npm cache or executable is transferred.

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5_resource_acceptance `
  --vm-config vm_config.json `
  --out outputs/executioner/d5b3a_resource_static.json

python -m projectiq_executioner.phase2.vm_worker.d5_resource_acceptance `
  --vm-config vm_config.json `
  --run-clean-vm `
  --out outputs/executioner/d5b3a_resource_clean_vm.json

$p = Get-Content outputs/executioner/d5b3a_resource_clean_vm.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, security_attestation, error_code
$p.checks | Format-List
$p.cgroup_capabilities | Format-List
$p.remaining_security_gates
```

Target **only for D5b.3a**:

- `PER_UID_RESOURCE_CEILINGS_OBSERVED_NOT_ATTESTED`
- All 5 clean app-identity / inherited-hard-limit checks `PASS`
- `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`
- `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`
- Capability observations `YES`/`NO`/`NOT_VERIFIED` for cgroup v2, memory, PIDs, I/O. **Availability is not enforcement.**

This clean probe does not exhaust 96 tasks; it checks the inherited **hard limits** and proves the app UID cannot raise them. It is not an independent audit of arbitrary process escape or aggregate memory/I/O use.

## After the clean probe

Only after successful VM acceptance, re-run a reviewed D4 full-stack fixture and PHP/MySQL fixture under the new launcher. A passed clean probe alone does not show React/Vite or MySQL compatibility. Report both startup and cleanup. Do not change limits to force green without examining the actual failure.

**Do not commit/push until all D5b increments and cross-adapter regression checks are complete.**
