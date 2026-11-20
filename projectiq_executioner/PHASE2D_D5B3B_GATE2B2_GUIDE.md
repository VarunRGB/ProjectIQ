# ProjectIQ Phase 2D / D5b / B3b: Gate 2B.2 (aggregate PID enforcement)

**Status:** new bounded clean-VM acceptance pilot; **not** arbitrary student-execution attestation.

Gate 2B.1 proved `memory.max=2147483648` at the real evaluation slice. Gate 2B.2 independently proves that `pids.max=80` is enforced when **two app-UID workloads** concurrently hold tasks inside the same `projectiq-eval.slice`. Linux's pids controller counts tasks (threads included), not only process IDs.

## Trust model

- Explicit operator opt-in; no student source archives, Git downloads, dependency installs, containers or host-folder mounts.
- A disposable Ubuntu QEMU VM (`1536 MiB`, one WHPX vCPU where required), restricted networking (SSH loopback forwarding only), separate bounded 96 MiB ext4 workspace.
- Root-owned cloud-init provisions the standard 2 GiB / 80-task / 16 MiB/s evaluation slice. A trusted `projectiq` controller independently verifies the policy and starts two `projectiq_run` workers through systemd scopes.
- Each worker only creates a fixed, bounded number of Python threads; there is no `fork()` loop, no shell command, and no child processes from the workers. Worker 1 attempts 30 threads, then worker 2 attempts at most 110; the second cannot exceed the aggregate 80-task kernel limit.
- The controller publishes unique, controller-owned, readable-but-not-writable release markers **after** verifying both workers. It releases worker 1 and first requires at least 25 live tasks in that worker's scope, then releases worker 2. The workers poll fixed controller-owned stop markers for cooperative teardown.
- Never infer PID enforcement from configured limits, `RuntimeError`, wrapper exits or user-supplied output. The controller reads the parent `pids.current`, both worker-scope `pids.current`, kernel `pids.events`, and effective child `pids.max` directly. On Linux with `pids_localevents`, a scope-local denial event is accepted only when that scope has no tighter ceiling than the parent and both scopes' concurrent task counts are observed.
- Verify kernel `cgroup.events populated=0` (or absent cgroup) for both scopes, independent of wrapper exit status. The entire VM + temporary disks must also be removed. Report `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` regardless of result.

## Commands (from repo root, with activated venv)

Offline full regression:

```powershell
$env:PYTHONUTF8 = "1"
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -q
```

Read-only/preflight, **no VM**:

```powershell
$env:Path += ";C:\Program Files\qemu"
python -m projectiq_executioner.phase2.vm_worker.d5_pid_acceptance `
  --vm-config vm_config.json `
  --out outputs/executioner/b3b_gate2b2_pid_inspection.json
```

Explicitly opted-in real clean-VM stress:

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5_pid_acceptance `
  --vm-config vm_config.json --run-clean-vm --acknowledge-pid-stress `
  --out outputs/executioner/b3b_gate2b2_pid_live.json
```

Check evidence:

```powershell
$p = Get-Content outputs/executioner/b3b_gate2b2_pid_live.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, error_code
$p.checks | Format-List *
$p.kernel_observations | ConvertTo-Json -Depth 8
```

Acceptance requires **all eight checks `PASS`** and status `EVALUATION_SLICE_PIDS_ENFORCED_NOT_ATTESTED`, including positive parent or scoped kernel `pids.events.max` delta, observed parent `pids.current` 79 or 80, at least 25 tasks in the first worker scope and 30 in the second concurrently, no lower child-scope task cap, guest task count recovery, and zero remaining tasks in both scopes. If the live probe fails or is inconclusive, do **not** loosen policy or push the gate; inspect the bounded report and fix the actual failure.

Next: Gate 2B.3 workspace device I/O throughput enforcement; Gate 2B.4 crash/teardown acceptance and full fixture regression. Gate 2B does not solve MySQL resource accounting, root guest filesystem write confinement, complete descendant/lifecycle monitoring, or host/hypervisor escape resistance.
