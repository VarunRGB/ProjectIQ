# ProjectIQ — D5c Gate 4: registered real-runtime workspace and cleanup

**Trust state:** `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`. The only approved execution remains operator-reviewed, immutable GitHub SHA fixtures. This gate does not approve arbitrary student repositories.

## Behavior

The existing `execute_vm_candidate` real Windows/WHPX execution path now **requires** a managed, signed, OS-leased run workspace instead of creating a disposable untracked `TemporaryDirectory`. The default root is `outputs/executioner/projectiq_d5c_managed_runtime` (outside Git), with a preexisting parent directory; alternatively specify an **absolute, operator-controlled** `managed_run_root` in VM config. The registry is reused from Gate 3; no global TEMP scan is performed.

Each invocation: (1) checks the managed root and sweeps only independently authenticated stale runs, refusing unresolved manual-review states; (2) creates a random-ID signed run and acquires its exclusive lease before fetching the immutable approved snapshot; (3) signs LAUNCHING before QEMU startup; (4) checks Job Object membership and records QEMU's exact Windows process creation time, binary path, and PID as LAUNCHED; (5) signs BOOTED after SSH readiness; (6) stops QEMU and signs STOPPED only after successful stop; (7) unlocks the lease, invokes conservative sweep, and reports successful cleanup **only after the registered workspace is actually removed**.

Host-side inspected source files and known fixed dependency archive names are now inside the registered workspace. Recovery verifies the bounded `inspection/` tree without following symlinks, junctions, Windows reparse points, or hardlinks. Unknown root artifacts or unsafe nested types force manual review. Failed deletion leaves signed state for retry; cleanup never kills a process based on a stale PID.

If the controller is killed, the verified Windows Job Object terminates QEMU. On the next runtime startup, the registry checks the previous lease and QEMU identity before removing abandoned storage. A controller crash in the gap between start and trusted process-record publication remains `LAUNCHING` and **requires manual review**. This is deliberate; do not auto-clear it.

A verified runtime result additionally includes `managed_runtime: SIGNED_LEASED_WORKSPACE_REMOVED` and a random `managed_run_id`. A cleanup failure forces `status: CLEANUP_FAILED`, `cleanup: CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED`, and a bounded failure code/workspace path for operator review.

## Execute

After applying the source-pinned patch and passing the complete offline suite:

```powershell
& '.\outputs\executioner\tools\projectiq_gate2a3_regression.ps1'
if ($LASTEXITCODE -ne 0) { throw 'Regression harness failed' }
```

Use the **new** saved regression summary path shown by the harness, not a report from before Gate 4:

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5c_gate4_acceptance `
  --vm-config vm_config.json `
  --regression-summary 'outputs/executioner/gate2a3_regression_<NEW_RUN>/regression_summary.json' `
  --out outputs/executioner/d5c_gate4_runtime_acceptance.json
if ($LASTEXITCODE -ne 0) { throw 'D5c Gate 4 acceptance failed' }
$p = Get-Content 'outputs/executioner/d5c_gate4_runtime_acceptance.json' -Raw | ConvertFrom-Json
$p.status
$p.checks | Format-List *
```

Target: `D5C_GATE4_REAL_RUNTIME_INTEGRATED_NOT_ATTESTED` and **8/8 PASS** from genuine FSDL, MiniCollabApp and FullStack runs. Keep outputs, managed root key and VM evidence local; commit only `projectiq_executioner/` files after review.

## Open limitations

- **Windows-specific:** Linux/KVM controller-death supervision has not been independently implemented and the new managed runtime blocks unsupported hosts.
- **Host power loss:** Windows Job Object teardown on reboot is not a substitute for independent crash/power-loss isolation assurance. Restart recovery is verified only for authenticated, safely identifiable stale runs.
- **Phase 2D planning scratch:** the earlier `phase2d.pipeline.run_pipeline` source-analysis stage still uses a separate `TemporaryDirectory` for static inspection. This patch covers the *real VM candidate execution path*, not crash-cleanup of that pre-analysis scratch. Treat this as open before a full Phase 2 security signoff.
- **LAUNCHING identity gap:** no PID-based guessing or blanket deletion. Operator inspection is required.
- **Same-user host attacker and filesystem races:** the root's secret and filesystem ACLs are in the host security boundary; host-side same-user hostile races are not attested. Do not claim same-user ACL hardening from signed records alone.
- **Guest isolation:** complete build-descendant confinement, MySQL daemon resource accounting, root QCOW2 write ceiling, and independent network/VM escape evaluation remain D5d/D5e blockers.
- **Arbitrary repositories:** still **disabled**. The accepted regression runs only three operator-reviewed fixture SHAs.
