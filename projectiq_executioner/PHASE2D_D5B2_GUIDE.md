# ProjectIQ Phase 2D — D5b.2: bounded writable workspace disk (reviewed-fixture pilot)

**Status:** Implemented and locally regression-tested. A real clean Ubuntu/WHPX VM disk-exhaustion proof and cross-adapter regressions are required before D5b.2 is accepted. No production sandbox attestation is made. D5a and D5b.1 remain uncommitted until D5b as a whole is complete.

## 1. Scope and precise security claim

Every **normal reviewed-fixture execution VM** receives a second disposable **raw virtual disk with a fixed virtual capacity** (default **1536 MiB**, operator-overridable **64–2048 MiB**). The root-owned cloud-init bootstrap formats it as ext4 and mounts it with `nosuid,nodev` at `/srv/projectiq-workspace`, then allows the trusted controller and unprivileged `projectiq_run` application UID to use it. The host QEMU controller transfers snapshot and runtime assets via SSH, then the trusted guest agent moves them into that bounded workspace **before extracting or executing source**. Source trees, offline npm installations, Node runtimes, Vite builds, Python install targets and on-disk SQLite files under the project directory use that same filesystem. Project launcher `HOME`, `TMPDIR`, `TMP` and `TEMP` point inside the workspace.

A full filesystem refuses new allocations (`ENOSPC`), rather than merely reporting high usage. **The additional disk is not a host-directory mount and carries no production credentials.** It is deleted with the per-run VM directory. There is no change to the sealed golden Ubuntu base image.

**This is a scoped workspace quota, not comprehensive write confinement or host disk capacity enforcement:**

- The guest root QCOW2 overlay still grows independently and has no hard host-side space quota.
- `/tmp`, `/var/tmp`, `/dev/shm`, system logs and other guest-writable paths are not yet comprehensively jailed by filesystem/mount namespaces. The launcher uses a bounded `TMPDIR`, but application code can explicitly choose other writable locations.
- The PHP/MySQL adapter's MySQL service stores its data under the guest's system database directory, **outside the bounded workspace**. No global MySQL/database disk quota is claimed.
- Dependency **cache-preparation VMs** retain their existing explicitly network-authorized preparation flow and are not covered by this normal-runtime workspace quota. Their independent storage caps require future work.
- A fixed-size raw disk caps *that device's* virtual capacity but **does not cap the writable root overlay or all host disk allocations**.
- A student application is **not authorized** to run without explicit immutable-SHA approval. The D5 probe below uses trusted code only. Status remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

These remain mandatory security blockers before arbitrary untrusted student execution. D5b.3 will address guest process/memory/I/O controls; later hardening must address global disk/mount confinement and host overlay growth explicitly. Do not rename this workspace-only control as whole-VM disk quota.

### D5b.2 Windows acceptance path correction

In the first real clean-VM run, the mounted ext4 disk passed its size/mount
check, but the unprivileged proof failed with `PermissionError` under
`/home/projectiq/workspace`. The application execution path grants
traversal of `/home/projectiq` later, while the clean-VM probe deliberately
runs before that step. The shared mount now lives at `/srv/projectiq-workspace`,
**outside the controller's private home directory**. Its root-owned bootstrap
still sets ownership `projectiq:projectiq_exec`, mode `2770`, and filesystem
options `nosuid,nodev`. The controller home is not made world-traversable as a
quota workaround. A successful real VM ENOSPC proof remains mandatory.

## 2. Installation — stack on top of the local D5a + D5b.1 + Windows test fix

Do **not** commit, push, reset or restore previous changes. Apply the accompanying D5b.2 patch from the repository root:

```powershell
cd C:\Users\varun\Code\ProjectIQ
Expand-Archive "$HOME\Downloads\ProjectIQ_Phase2D_D5b2_Workspace_Disk_Quota_Patch.zip" `
  -DestinationPath "$HOME\Downloads\ProjectIQ_D5b2" -Force
$patch = "$HOME\Downloads\ProjectIQ_D5b2\ProjectIQ_Phase2D_D5b2_Workspace_Disk_Quota.patch"
git apply --check $patch
git apply $patch
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -q
```

Expected after applying D5b.2: **393 tests, OK** (373 prior + 20 new), on Windows Python 3.13. No `vm_config.json` or VM disk is staged into Git.

## 3. Optional operator-owned configuration

The normal runtime default is a **1536 MiB workspace disk**. You can override it in your ignored `vm_config.json` with a JSON integer:

```json
"workspace_disk_mib": 1536
```

Only integers in `[64, 2048]` are accepted. Boolean, string, zero, and out-of-range values are rejected. The selected value is part of the **trusted** guest request, seed and QEMU launch; it does not come from a student repository. On a 16-GB WHPX laptop, leave the default initially. The disk is an on-demand sparse raw image (up to 1536 MiB if completely filled), **not** a 1536-MiB RAM reservation.

## 4. Static host inspection (does not boot QEMU)

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5_disk_acceptance `
  --vm-config vm_config.json `
  --out outputs/executioner/d5b2_disk_static.json
```

Expected: `INSPECTION_ONLY_NOT_ATTESTED`. This is **not** a disk quota PASS.

## 5. Real clean-VM quota acceptance — required

The D5b.2 acceptance VM uses a **96-MiB** disposable disk for a fast, bounded proof; normal reviewed-fixture VMs keep their independently configured default budget. It runs no student repository or dependencies. The trusted probe:

1. Verifies ext4 is actually mounted at the expected path, with `rw,nosuid,nodev` and capacity at most 96 MiB.
2. Runs a fixed `python3` command as **`projectiq_run`**, attempting `os.posix_fallocate()` for **104 MiB** inside that disk (not a sparse `truncate` or mere usage observation).
3. Requires the allocation to be rejected with **`ENOSPC`** (or `EDQUOT`), then requires a small write to succeed after deleting the failed allocation file.
4. Requires QEMU to stop and the guest root overlay, seed/SSH keys, and bounded disk to be removed.

```powershell
$env:Path += ";C:\Program Files\qemu"
python -m projectiq_executioner.phase2.vm_worker.d5_disk_acceptance `
  --vm-config vm_config.json `
  --run-clean-vm `
  --out outputs/executioner/d5b2_disk_clean_vm.json
$p = Get-Content outputs/executioner/d5b2_disk_clean_vm.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, security_attestation, cleanup, error_code
$p.checks | Format-List
$p.remaining_security_gates
```

**Expected acceptance target (not yet observed on Windows/WHPX):**

- `status = WORKSPACE_QUOTA_OBSERVED_NOT_ATTESTED`
- `execution = TRUSTED_DISK_PROBE_EXECUTED_IN_CLEAN_VM`
- `workspace_mount_bounded = PASS`
- `app_write_over_quota_denied = PASS`
- `app_write_after_denial = PASS`
- `cleanup = VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`
- `security_attestation = NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`

If the VM fails to boot, ext4 device is missing, mount is absent, allocation succeeds unexpectedly, guest probe times out, or QEMU fails to terminate, **stop**, inspect the bounded error status, and do not claim D5b.2 acceptance. Do not grant sudo to the app user or relax network restrictions.

## 6. Mandatory real application regressions

Only after the clean-VM disk probe passes, repeat existing **operator-approved pinned SHAs** with `--run-reviewed-fixture`:

- **PHP + MySQL** Mini Task (`c4f7965d16807fa05c3d7ffeee54f86fee4d0a37`) — prove PHP/PDO readiness remains intact; note its MySQL data directory is not workspace-quota confined.
- **React + Express + SQLite** D4 full-stack pinned fixture — test build, backend, frontend, proxy, SQLite and cleanup; use the existing reviewed `d4-fullstack` commit hash, *not a branch name*.
- D1/D2/D3 (React/Vite, Flask/FastAPI, Express) as available — check asset caches and application starts still work with the bounded workspace.

Successful application startup is *compatibility* evidence, not a disk-exhaustion security proof.

### Known limitations / next milestones

D5b.2 covers a **bounded workspace** and its actual exhaustion behavior, not global guest write confinement. D5b.3 adds process/memory/I/O controls; later D5 security acceptance must explicitly test mount namespace/write path escape, MySQL DB growth, trusted cache-preparation worker storage and host QCOW2 overlay growth. **No D5 security attestation may be claimed while those gates remain unresolved.**

## D5b.2 WHPX dual-disk boot differential (diagnostic improvement)

If the 96-MiB clean-VM disk probe reports `Worker VM SSH readiness timed out`,
first rerun D5a clean-VM acceptance on the same machine/config to check whether
the existing single-disk boot still works. Do not assume a blank serial log proves
a kernel hang: the template may not route its console to the serial port.

The dual-disk QEMU machine now assigns `bootindex=1` to the sealed Ubuntu root
image and `bootindex=2` to the blank workspace disk. This removes ambiguous
firmware boot priority without changing the existing single-disk launcher.
It does **not** assert a resolution of WHPX interrupt-handling faults; a real
Windows boot and ENOSPC probe must still pass. If a time-out persists, keep
the security status `NOT_VERIFIED` and investigate the WHPX/device configuration.
