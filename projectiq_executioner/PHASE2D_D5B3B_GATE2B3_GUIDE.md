# ProjectIQ Phase 2D / D5b / B3b / Gate 2B.3 — aggregate workspace I/O bandwidth

**Status:** opt-in clean-VM acceptance implementation; not a production security attestation.

Gate 2B.1 demonstrated an actual 2 GiB evaluation-slice OOM kill; Gate 2B.2 demonstrated a real denial at 80 aggregate tasks. Gate 2B.3 tests whether the **normal application evaluation slice** actually throttles *block-device writes* to the existing **16,777,216 bytes/second (16 MiB/s)** workspace-device policy. This does not attest root-QCOW2 writes, `/tmp`, database storage, or all guest write paths.

## What this test does

1. Preflights the pinned guest image, QEMU toolchain, and the existing kernel `io.max` policy for the **actual `/srv/projectiq-workspace` ext4 block device**.
2. Boots a **1536 MiB** network-restricted disposable QEMU VM with a **256 MiB** bounded ext4 workspace. Never downloads a repository or executes student code.
3. Runs a fixed **64 MiB unthrottled control** as the separate unprivileged `projectiq_run` user inside a verified transient scope under `projectiq.slice`. Its kernel `io.stat` and measured throughput must exceed **24 MiB/s**. If the environment cannot supply that rate, the test remains inconclusive rather than claiming cgroup throttling caused slow I/O.
4. Starts **two independently verified** `projectiq_run` worker scopes under `projectiq-eval.slice`. Only after each UID, argv, and cgroup is observed does the trusted controller publish a rootless, controller-owned 0644 release marker.
5. Each fixed worker attempts **64 MiB** of page-aligned **`O_DIRECT`**, 1 MiB block writes, followed by `fdatasync`; the kernel's *actual* workspace-device `io.stat` `wbytes` counters, not worker stdout or buffered file timestamps, are the evidence.
6. Requires at least **128 MiB** of aggregate kernel-counted evaluation-slice writes and **56 MiB per child scope**, with both contributing concurrently. The sustained, controller-observed rate over the full workload must be **at most 20 MiB/s**, while the control exceeds 24 MiB/s. The tolerance covers kernel throttler bursts and measurement overhead; instantaneous BPS is not treated as a fixed rate ceiling.
7. Publishes cooperative stop markers; independently verifies `cgroup.events populated=0` or scope absence, no remaining tasks, wrapper exits, and removal of the fixed disposable files. The entire VM and scratch disk are then deleted.

**Limits:** This is bounded evidence for this actual disposable VM/provider/device and configuration. It neither proves no unrestricted guest write paths nor validates database and build-process descendants. A slow baseline, unavailable `O_DIRECT`, missing I/O accounting, invalid cgroup, early worker exit, failed cleanup, or timeout must not produce a `PASS`.

## Apply (operator host PowerShell)

The downloaded generator must match the exact reviewed `executioner` Git SHA and the pinned blobs. It runs the full offline test suite in a temporary copy, and emits an **add-only patch** in ignored `outputs/executioner/` without changing tracked source files. Use the output patch name printed by the generator, validate with `git apply --check`, apply, and rerun the full offline suite before starting the VM.

## Static inspection (no VM)

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5_io_acceptance `
    --vm-config vm_config.json `
    --out outputs/executioner/b3b_gate2b3_io_inspection.json
```

Target `INSPECTION_ONLY_NOT_ATTESTED` with pinned toolchain/template checks reported.

## Explicit live clean-VM acceptance

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5_io_acceptance `
    --vm-config vm_config.json `
    --run-clean-vm --acknowledge-io-stress `
    --out outputs/executioner/b3b_gate2b3_io_live.json
```

Inspect `status`, `execution`, `cleanup`, `error_code`, `checks`, and `kernel_observations`. Expected **only with all ten checks PASS**: `EVALUATION_SLICE_IO_THROTTLED_NOT_ATTESTED`, cleanup `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, positive per-worker workspace `io.stat` counters, a fast control, a measured evaluation rate at or below 20 MiB/s, and both worker scopes drained.

## Final checkpoint

Do **not** commit/push after a failed or inconclusive live probe. After live success, run the saved FSDL/MiniCollabApp/FullStack regression harness, examine generated diagnostics, and stage only reviewed `projectiq_executioner/` source/tests/guide. Keep `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`; Gate 2B.4 still needs failure/orphan recovery acceptance.

References: Linux cgroup v2 `io.max`/`io.stat`, and `systemd.resource-control` IOWriteBandwidthMax documentation.


### Baseline-counter preflight diagnostics

`io.stat` may contain additional controller-specific fields. Gate 2B.3 reads
only the required numeric `wbytes` and `wios` counters for the exact workspace
device, rejects malformed required counters/duplicate device entries, and
ignores well-formed optional extensions. A baseline counter preflight error is
reported as `BASELINE_IOSTAT_INVALID` (with bounded reason) or
`BASELINE_IOSTAT_UNREADABLE`. No baseline scope was launched, so its cleanup
check remains `NOT_VERIFIED`, not misleadingly `FAIL`. A later failure can be
isolated as `BASELINE_WORKER_LAUNCHING`. Real-VM evidence is still mandatory.

### Baseline `io.stat` zero-write preflight

A device may already have an `io.stat` row with read/discard or other
statistics before the trusted baseline issues a write. The controller now
accepts **both absent write counters** (`wbytes`, `wios`) as a *zero* starting
point only; this is **not** evidence of any writes. An incomplete pair (only
one write counter), malformed counter, or duplicate device row still fails
closed. The existing independent acceptance checks continue to require at
least 56 MiB of kernel-accounted actual write bytes in the baseline and each
worker, at least 128 MiB in the evaluation parent, the faster unthrottled
control, an observed aggregate rate, and complete scope cleanup.

### Device-only preflight and classified io.stat parser diagnostics

A device-only `io.stat` row (e.g., `8:0` with no key-value tokens) is now a
valid **zero-write starting state**, not proof of I/O throttling. After the
workload, the acceptance gate still requires actual parent- and scope-level
kernel-accounted write bytes, a fast unthrottled baseline, observed rate and
complete cleanup. Unrecognized populated fields are never silently dropped:
the probe returns fixed `UNEXPECTED_BARE_TOKEN`, `DUPLICATE_FIELD`,
`INVALID_OPTIONAL_KEY` or `INVALID_OPTIONAL_VALUE` categories without
copying raw kernel text into the report. These categories will isolate a
remaining io.stat compatibility issue if the live VM still cannot pass preflight.
