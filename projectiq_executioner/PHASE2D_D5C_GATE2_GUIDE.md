# ProjectIQ Phase 2D / D5c / Gate 2 — Windows VM startup cancellation

**Scope:** Opt-in, trusted clean-VM startup-cancellation experiment. Not untrusted-student attestation. Git checkpoint pin: `c4117e7ff82e7c2b8a7d9eeb634d94439b5b75ad` (D5c Gate 1).

Gate 1 proved Windows Job Object parent-death protection. Gate 2 tests four **cooperative** cancellation windows with the existing shared `QemuWorker` and Windows guard. It extends `SSHControl.wait_ssh` with an **optional** trusted cancellation callback; callers without the callback keep the previous behavior. It does not enable a general public cancel API or grant execution permission for arbitrary repositories.

## Scenarios (sequential, independent temporary environments)

1. `pre_launch`: trusted image and workspace preparation complete, but `QemuWorker.start()` not called. Inject cancellation and require no VM launch.
2. `post_launch`: QEMU has started, its Windows process image and job membership verified, but SSH readiness has not begun. Cooperatively stop and verify the same kernel process handle exits.
3. `ssh_wait`: QEMU is alive in its job. Force the **SSH readiness probe** to a separate closed loopback port, confirm at least one real unsuccessful SSH probe, then cancel through the shared callback. This deliberately tests a blocked readiness path, not actual guest SSH failure or a guest-network policy change.
4. `guest_ready`: real guest SSH readiness and a trusted `true` probe have succeeded; cancel at the boundary **before** guest application setup can begin. This does **not** demonstrate interruption of already-running arbitrary guest initialization.

For all scenarios, the parent uses a separate trusted Python controller, controller-owned exact-format cancellation markers, a bounded startup deadline, and explicit Windows QEMU process-image verification. A normal cancellation must exit the controller successfully, and QEMU must exit without emergency host termination. Emergency termination of the **already verified** QEMU process handle is a failure, not a pass. Each temporary VM, raw workspace, overlay, seed and SSH identity must be removed. Only the trusted reviewed template is booted. No student code is used.

## Windows command

Run from the ProjectIQ root, after applying the source-pinned patch and running the offline suite:

```powershell
$env:PYTHONUTF8 = '1'
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5c_startup_cancel_acceptance `
    --vm-config vm_config.json `
    --run-clean-vm `
    --acknowledge-startup-cancellation `
    --out outputs/executioner/d5c_gate2_startup_cancel.json
$p = Get-Content outputs/executioner/d5c_gate2_startup_cancel.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, failure_stage, failure_code
$p.checks | Format-List *
$p.scenarios | Format-List *
```

Success only with all **8/8 checks PASS** and **four scenario PASS**:
`D5C_STARTUP_CANCELLATION_VERIFIED_NOT_ATTESTED` with `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`.
The runner stops at the first failed scenario and retains bounded error codes. It never treats an unsuccessful cancellation or emergency kill as a pass.

After real acceptance, run the three-project regression harness (FSDL, MiniCollabApp and FullStack), because `SSHControl.wait_ssh` is shared. Only then commit the Gate 2 checkpoint.

## Explicitly still open

Host-controller kill **during the atomic Windows process-creation boundary**, orphaned temporary storage after controller hard kill or host reboot, partial-cleanup failures and repeated cancellation, Linux parent-death behavior, all project build descendants and MySQL cgroup placement, independent hypervisor/SSH/network identity assurance, and final security go/no-go. The stage marked `guest_ready` does not prove cancellation of a running build or application. Keep `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.
