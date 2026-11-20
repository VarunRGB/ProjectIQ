# ProjectIQ Phase 2D — D5a: clean VM security and reliability acceptance baseline

**Status:** Implemented and offline-tested only. The **real Windows/WHPX clean-guest acceptance run is pending**. A report from this tool is **not a security attestation**, does **not** authorize unreviewed student repositories, and does not claim that any hypervisor can safely execute arbitrary hostile code.

This starts **D5**, not finishes it. D5a aims to establish repeatable, adapter-independent evidence from a clean guest before testing failure, crash, storage, privilege, or adversarial conditions in later D5 increments.

## Why this is adaptable

`d5_acceptance.py` uses the shared QEMU provider and the immutable Ubuntu template; it never imports a Vite/Express/Python/PHP student application. Later adapters can reuse the same worker baseline without D5-specific hardcoding. `QemuWorker.network_spec()` is shared between the **actual launcher** and the D5 test. Normal execution behavior of the existing six adapters is not otherwise altered.

## Scope

- Inspect the QEMU/SSH toolchain, pinned QCOW2 image SHA-256, optional UEFI SHA-256, runtime `restrict=on`, and loopback-bound SSH forwarding. Inspection does **not** start a VM.
- With explicit `--run-clean-vm`, create an ephemeral Ubuntu overlay, per-run cloud-init SSH identity and a 1 GiB runtime-mode VM. Do not transfer GitHub snapshots, student sources, npm/pip caches, or application code.
- Use trusted controller-owned Python sent over SSH **stdin**, not code from a repository. Inside the guest, observe non-root identity, passwordless root sudo, host-shared mounts, and attempted external IPv4 TCP, IPv6 TCP and DNS access.
- Record a known limited MySQL sudo exception for **manual security review**. The clean guest may have `NOPASSWD: /usr/bin/mysql` to support the existing PHP pilot. This is **not suitable to grant to arbitrary untrusted student code** without independent security review and a safer privilege boundary.
- Terminate QEMU, remove the temporary QCOW2 overlay and ephemeral credentials, and report cleanup. Unit tests simulate launch failure, probe failure and cleanup failure.
- Observe a few known external destinations. **A failed connection attempt does not prove a complete network boundary**, e.g. it cannot rule out alternate routes, IPv6 behavior, host gateway access, tunnel interfaces or a hypervisor escape.

## Step 1 — Apply D5a and run unit tests (no VM)

From `C:\Users\varun\Code\ProjectIQ` on the pushed, clean `executioner` D4a branch:

```powershell
# First extract the accompanying ZIP and check the patch; do not force it.
$patch = "$HOME\Downloads\ProjectIQ_D5a\ProjectIQ_Phase2D_D5a_Clean_VM_Acceptance.patch"
git apply --check $patch
git apply $patch
python -m unittest discover -s projectiq_executioner/tests -p 'test_*.py' -q
```

Expected **362 tests OK** (341 existing + 21 D5a tests). No QEMU is started during this test suite.

## Step 2 — Static inspection (no VM)

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5_acceptance `
  --vm-config vm_config.json `
  --out outputs/executioner/d5a_static_inspection.json

$p = Get-Content outputs/executioner/d5a_static_inspection.json -Raw | ConvertFrom-Json
$p | Select-Object status, security_attestation, cleanup
$p.checks | Format-List
```

Expected **only if the pinned operator template and tools are available**: `INSPECTION_ONLY_NOT_ATTESTED`. Missing/invalid toolchains or image hashes produce `HOST_GATES_BLOCKED`. Static results show `NOT_VERIFIED` for controls that need empirical proof; do not relabel them `PASS`.

## Step 3 — Clean-VM live observation (no student repository)

Close memory-heavy apps. The QEMU execution VM network policy remains restricted; this tool has **no option** to authorize a networked preparation VM.

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5_acceptance `
  --vm-config vm_config.json `
  --run-clean-vm `
  --out outputs/executioner/d5a_clean_vm.json
```

The tool may return exit code **1** if it observes a control failure **or** the current restricted MySQL sudo exception that needs independent review. This is a deliberate fail-closed result, **not** a request to weaken controls. Do not automatically change sudo policy, QEMU networking, or the verified template to make the test green. Stop and inspect the JSON report.

```powershell
$p = Get-Content outputs/executioner/d5a_clean_vm.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, security_attestation, cleanup, error_code
$p.checks | Format-List
$p.remaining_security_gates
```

Possible statuses include `RESTRICTED_SUDO_EXCEPTION_REQUIRES_REVIEW`, `SECURITY_OBSERVATION_FAILED`, `OBSERVATIONS_PARTIAL_NOT_ATTESTED`, `CLEAN_VM_OBSERVATIONS_PASSED_NOT_ATTESTED`, `HOST_GATES_BLOCKED`, and `CLEANUP_FAILED`. **All** retain `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## What D5a does NOT verify

- No CPU/RAM/disk exhaustion or per-process quota measurements. Requested QEMU memory/vCPU is not a guest PID, I/O or host-disk write quota.
- No adversarial filesystem, network or hypervisor escape attempt; no proof of isolation from the Windows host.
- No crash/cancellation/orphan VM recovery or actual host reboot test; unit-test mocks are not equivalent to real host failures.
- No independent SSH host-key identity verification. The existing pilot uses `StrictHostKeyChecking=accept-new` on a unique loopback forward.
- No independent proof that MySQL's restricted `sudo` exception is safe for untrusted code.
- No proof of multi-tenant safety, safe parallel scheduling, secret handling under malicious code, or production threat-model closure.

## D5 remaining increments — in order

1. **D5b — privilege and disk/PID/IO containment:** replace/rationalize MySQL privilege exception, enforce actual storage and process quotas, test under bounded controlled guest workloads (never on Windows). Consider a dedicated Linux worker for enforceable quota mechanisms.
2. **D5c — fault-injection and cleanup:** controlled guest wall timeouts, network attempts, VM failure, aborted jobs, crashes and host reboot recovery with filesystem/process orphan checks. Restore a clean VM baseline after every run.
3. **D5d — independent adversarial and identity acceptance:** network/host-device/host-mount attack surface, robust SSH host-key identity, hypervisor patch policy, isolation audit and repeatable acceptance in a **dedicated worker environment**. Add concurrency/scheduler controls before untrusted student execution.
4. **D5e — final go/no-go review:** require independently collected results for every security requirement. If any critical gate is unverified, remain **reviewed-fixture-only**, regardless of how many ordinary applications started successfully.

Only after evidence-based security hardening should we consider lifting the reviewed-fixture restriction. Phases 3/4 can still be developed meanwhile against reviewed fixtures.

## D5b.1 status (applied on top of D5a)

See [PHASE2D_D5B1_GUIDE.md](PHASE2D_D5B1_GUIDE.md). The former sudo MySQL exception is removed by using a separate application UID and trusted one-time MySQL cloud-init bootstrap. Real WHPX security observations and real PHP/MySQL plus D4 regressions are mandatory before declaring D5b.1 accepted. The original D5a outputs remain historic baseline evidence, not a current runtime-policy report. No student submissions are authorized.
