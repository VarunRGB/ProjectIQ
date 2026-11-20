# ProjectIQ Phase 2D / D5b / B3b / Gate 2B.4 — controlled failure recovery

**Scope:** opt-in failure-recovery acceptance for a trusted clean-VM pilot, **not** a complete D5c orphan/cancellation attestation or authorization to run arbitrary student code.

## What gets verified

1. The normal root-owned `projectiq-eval.slice` still has `memory.max=2147483648`, `pids.max=80` and 16 MiB/s workspace-device `io.max`, and the ext4 workspace is genuinely mounted and bounded.
2. A fixed `projectiq_run` process is verified using `/proc` UID, exact argv and its real cgroup, deliberately exits with status **42**, and its scope drains.
3. A second verified fixed worker observes a trusted-controller-owned, 0644 cancellation marker under the controller's 0711-traversable home, exits **0**, and its scope drains. The application UID cannot create the marker.
4. A **third verified worker remains alive** under the evaluation cgroup while a unique sentinel is written and synced to the second, disposable workspace disk. The trusted Windows controller then deliberately **kills the entire QEMU VM** instead of requesting cooperative guest shutdown.
5. The killed VM's QEMU process has exited. Its overlay, raw workspace disk, seed ISO, SSH keys and temporary directory are removed **before** starting another VM.
6. The controller boots a **new VM** from the same pinned template with a new overlay, raw disk, SSH key and NoCloud identity. The guest verifies that its workspace sentinel is absent, its evaluation cgroup is empty, and the kernel resource policy is intact. Its VM and temporary data are then removed.
7. Ten individually validated checks must pass; a missing cgroup, unverified UID/command, unexpected exit code, unreadable bootstrap, missing marker, surviving VM process, missing cleanup, or malformed report cannot count as success.

The test uses two sequential **1536 MiB** VMs and separate **96 MiB** disposable workspace disks. It doesn't run student code or download packages. It is deliberately destructive **only to the test VMs created by this command**. The normal application launchers and their resource limits are not changed.

## Why this is not full security attestation

It demonstrates recovery from a controlled application exit, cooperative cancel, and deliberate QEMU hard kill, plus lack of persistent state in a fresh guest. It does **not** simulate unexpected Windows host power loss, a killed parent Python process leaving QEMU running, arbitrary guest escape, user-controlled cancellation races, all descendant graphs, all writable guest paths, MySQL resource confinement, or network/host-key independent verification. Those remain **D5c/D5d/D5e** work. Final attestation stays `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## How to run

Apply the source-pinned add-only patch generated for Git commit `fdc264c45ec434d879760bd8faaa1d3f58dcc446`, then run the complete offline suite. Inspection without any VM:

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5_recovery_acceptance `
  --vm-config vm_config.json `
  --out outputs/executioner/b3b_gate2b4_recovery_inspection.json
```

Explicit live test. Do not have other QEMU probes running simultaneously:

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5_recovery_acceptance `
  --vm-config vm_config.json `
  --run-clean-vm --acknowledge-hard-vm-kill `
  --out outputs/executioner/b3b_gate2b4_recovery_live.json
```

Inspect `status`, `checks`, `observations`, `cleanup`, `error_code`, `failure_stage` in the JSON. Bounded `arm_guest_probe_stage`, `arm_crash_exit_code` and `arm_cancel_exit_code` diagnostics distinguish early exit/cleanup problems without exposing raw process output or changing the fail-closed acceptance decision. Success only if ten checks are **PASS**, status is `EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED` and cleanup is `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`.

The saved local FSDL/MiniCollabApp/FullStack three-repository regression harness still needs to pass independently before committing and pushing this checkpoint. The clean-VM recovery experiment alone is **not** a comprehensive adapter regression.
