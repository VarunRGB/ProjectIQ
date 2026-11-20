# ProjectIQ — 2 / D5 / B3b: Aggregate cgroup v2 limits

**Status: B3b acceptance pilot implemented; not yet applied to student/project launches.**

This is a staged hardening gate, not another language adapter. The same design
will eventually cover Vite/React, PHP/MySQL, Flask/FastAPI, Express and
multiple services inside one evaluation VM. D5a/B1/B2/B3a remain uncommitted
locally until the user completes all of D5b.

## Verified baseline before this increment

- B1: application account (`projectiq_run`) is distinct from trusted controller;
  sudo-to-root denied, MiniCollab PHP/MySQL still starts successfully.
- B2: bounded `/srv/projectiq-workspace` ext4 disk; real `ENOSPC:RECOVERED`
  test and VM cleanup passed. Root/host overlays are not fully bounded.
- B3a: inherited hard per-UID `RLIMIT_NPROC=96`, `RLIMIT_NOFILE=512`,
  `RLIMIT_CORE=0`. Real VM checks passed. cgroup v2 `memory`, `pids`, `io`
  were observed available — availability alone does not prove enforcement.

## B3b design and rollout

### B3b Gate 1 (this patch): clean-VM systemd capabilities / PID enforcement

- A separate clean VM receives the same bounded 96 MiB disposable workspace,
  no student source code, and an extra **operator-owned** cloud-init script.
- Cloud-init installs a **root-owned systemd user slice** named
  `projectiq-probe.slice` and a delegation drop-in for **only** the
  `projectiq` controller's user manager, then enables linger.
- The fixed acceptance slice has `MemoryMax=192M`, `TasksMax=32`, and
  `IOWriteBandwidthMax=/srv/projectiq-workspace 4M`.
- The trusted controller uses `systemd-run --user --scope` to run one bounded
  Python probe **as `projectiq_run`**. The probe verifies actual `memory.max`,
  `pids.max`, `io.max` values and the expected workspace block device; that
  the student UID cannot open `memory.max` for writing; and that bounded thread
  creation is rejected by the PID ceiling **with an increment in
  `pids.events`**, rather than attributing any failed thread spawn to cgroups.
- Fail-closed reporting uses `FAIL` / `NOT_VERIFIED`; the main VM and its
  disposable disks still stop and are deleted after the test.
- A kernel `memory.max` value is **not** itself a memory-OOM experiment.
  An `io.max` value is **not** a throughput experiment. These are separate
  B3b Gate 2 acceptance requirements.

**No application adapter has been switched to this user slice yet.** This
avoids destabilizing the D1–D4 startup paths before verifying on real WHPX.
Do not claim B3b complete or mark any unreviewed student job as authorized.

### B3b Gate 2 (next): shared application slice + destructive tests

- Install a distinct **root-owned evaluation slice** for actual applications
  with budget values sized for the 3 GiB VM and current offline build tools.
- Route all project-owned commands via `systemd-run --user --scope` into that
  slice *after* user switch. Frontend/backend share a parent slice, so all
  descendants count toward one aggregate memory/PID/IO policy.
- Verify via `/proc/self/cgroup` for *every* launch, and fail closed if
  systemd cannot establish the target slice.
- Run clean-VM memory-exhaustion and physical I/O rate tests; inspect
  `memory.events`, `pids.events` and `io.stat` counters. Do not over-interpret
  flaky timings or cached writes as throttling proof.
- Re-run PHP/MySQL and React+Express+SQLite real fixtures and then B4's full
  adapter regression. Keep cleanup and `NOT_ATTESTED` status mandatory.

## Installation on Windows (apply over uncommitted B3a)

From `C:\Users\varun\Code\ProjectIQ`:

```powershell
Expand-Archive "$HOME\Downloads\ProjectIQ_Phase2D_D5b3b_Cgroup_Probe_Patch.zip" `
  -DestinationPath "$HOME\Downloads\ProjectIQ_D5B3B" -Force
$patch = "$HOME\Downloads\ProjectIQ_D5B3B\ProjectIQ_Phase2D_D5b3b_Cgroup_Probe.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'B3b patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'B3b patch application failed' }
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -q
```

Expected: **427 tests, OK**, including the original 417 tests. Do not commit
or push until the D5b checkpoint.

## Static inspection

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5_cgroup_acceptance `
  --vm-config vm_config.json `
  --out outputs/executioner/d5b3b_cgroup_static.json
```

Expected `INSPECTION_ONLY_NOT_ATTESTED`; no VM is started.

## Real clean-VM B3b probe

Close memory-heavy apps. Keep the previously validated WHPX 1-vCPU setup:

```powershell
$env:Path += ";C:\Program Files\qemu"
python -m projectiq_executioner.phase2.vm_worker.d5_cgroup_acceptance `
  --vm-config vm_config.json `
  --run-clean-vm `
  --out outputs/executioner/d5b3b_cgroup_clean_vm.json
$p = Get-Content outputs/executioner/d5b3b_cgroup_clean_vm.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, error_code, error_detail
$p.checks | Format-List
$p.probe_diagnostics | Format-List
```

**Expected target** (not asserted until real guest reports):
`CGROUP_PROBE_OBSERVED_NOT_ATTESTED`, all seven checks `PASS`,
`VM_STOPPED_DISPOSABLE_STORAGE_REMOVED` and
`NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`. If any check fails or returns
`NOT_VERIFIED`, investigate the exact `probe_diagnostics` before touching
cloud-init permissions or resource limits. Do not disable restrictions.

## Open risks

The root QCOW2 growth, broader guest-writable paths, multi-tenant isolation,
SSH host-key identity, guest-to-host escape resistance, crash/orphan recovery,
and final production attestation remain unresolved, as already tracked in D5.
