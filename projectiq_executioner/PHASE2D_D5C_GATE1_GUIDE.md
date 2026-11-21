# ProjectIQ Phase 2D / D5c / Gate 1 — Windows parent-death recovery

**Scope:** real Windows host-controller process termination while a **trusted disposable QEMU VM** is active. This gate is not blanket security attestation, does not run student repositories, and does not authorize unreviewed source execution.

## Why this is needed

B3b Gate 2B.4 killed QEMU deliberately *from a still-living trusted Python controller*. That proves the clean-VM disposable state reset, but it does **not** establish that QEMU dies when its Python parent itself is abruptly terminated. Plain `subprocess.Popen` on Windows does not automatically kill child processes when Python exits. Python's normal `finally` blocks and `TemporaryDirectory` cleanup are bypassed by `TerminateProcess`.

## Runtime control

- Before **any** `QemuWorker` QEMU launch on Windows, the Python controller creates an unnamed Windows Job Object, enables `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, makes the handle non-inheritable, and **assigns the controller process itself** to that job. Future normal `Popen` children inherit its job atomically at creation, avoiding the start-before-assignment orphan window.
- The job handle is retained for the controller process's lifetime. On abrupt controller termination, Windows closes the handle and kills members of that job, including QEMU. `QemuWorker` independently checks QEMU's **actual process handle** belongs to that job and fails closed if the check fails.
- This applies to all Windows `QemuWorker` use, including reviewed application fixtures and operator-managed dependency VMs. On Windows hosts where nested Job Object assignment is disallowed, launches fail closed rather than silently running unprotected.
- On Linux this Windows Job Object integration does not apply. A separate Linux parent-death supervisor needs its own acceptance before Linux host attestation.

## Explicit real-VM acceptance

Run from the repository root, with WHPX configured, no concurrent ProjectIQ VM and sufficient host memory:

```powershell
$env:PYTHONUTF8 = '1'
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5c_host_interruption_acceptance `
  --vm-config vm_config.json `
  --run-clean-vm `
  --acknowledge-controller-hard-kill `
  --out outputs/executioner/d5c_gate1_host_interruption.json
$p = Get-Content outputs/executioner/d5c_gate1_host_interruption.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, failure_code
$p.checks | Format-List *
```

The runner checks a pinned toolchain/template; starts a separate trusted Python controller; verifies its job guard; boots a 1536 MiB **network-restricted**, **96 MiB disposable workspace** VM; opens and authenticates the precise QEMU Windows process handle by its executable image; deliberately kills **only the test controller** while QEMU is live; and requires the kernel handle to signal termination. If QEMU remains live, a bounded emergency termination against that **already-verified handle** is attempted but the security gate is marked **failed**. Finally, the test VM scratch directory must be removed.

Expected success **only when all seven checks PASS**:

`D5C_WINDOWS_CONTROLLER_INTERRUPT_VERIFIED_NOT_ATTESTED`

`cleanup = VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`

## Important limits and next gates

- A Windows Job Object kills **processes**, not orphaned scratch directories if the host itself crashes. Bounded recovery of authenticated abandoned workspace directories is still pending.
- Host power loss, controller crash before attaching the guard, cleanup permission failures, cancellation during disk creation/startup, and malicious unrelated Windows process interference are not covered by this single acceptance.
- This does not independently establish hypervisor security, guest filesystem/network boundaries, build-descendant accounting, database containment, or suitability for arbitrary untrusted student code.
- Subsequent D5c gates: startup/cancellation fault injection, orphan scratch recovery with safe ownership validation, repeated/idempotent and partial cleanup, then regression. D5d and D5e remain required.

**Operator policy:** keep `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` until final independent security review. Before committing the gate, rerun the saved three-fixture VM regression harness to catch changes caused by binding all Windows QEMU launches to a Job Object.
