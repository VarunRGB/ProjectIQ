# ProjectIQ Phase 2D / D5b B4 — Consolidated reviewed-fixture checkpoint

**Scope: a fresh, source-pinned consolidation of four proven resource and recovery
acceptance gates plus three immutable operator-approved real project fixtures.**
This is not all-adapter or security attestation, and unreviewed submissions stay blocked.

The new runner is **additive**. Existing resource policies, QEMU bootstrap,
application adapters and gate implementations are unchanged. It only invokes
existing vetted modules, checks their typed output and verifies consistency.

## Sequence

- Require the exact reviewed `executioner` commit `b500900f3deb9a310cefb9b4099370b73936cea4`
  and pinned Git blobs of the previous four gates and QEMU worker. Refuse tracked
  source modifications. The B4 source itself is untracked until review/commit.
- Require two explicit destructive-test acknowledgments. Without both, no VMs
  start. Run the complete offline unit suite first.
- Start **one** disposable VM at a time. Rerun the memory, PID, I/O and two-VM
  recovery acceptance modules, with unique fresh JSON reports. A nonzero exit,
  timeout, missing report, missing kernel evidence, incomplete cleanup or false
  attestation fails closed. Don't reuse earlier report filenames.
- Invoke the **bundled and SHA-256-verified** three-fixture PowerShell harness.
  It independently rechecks pinned approved Git commits from the local config,
  HTTP/SQL responses, server placement, build pre-exec observations and cleanup
  for FSDL, MiniCollabApp and React/Express/SQLite FullStack.
- Independently validate each of the three real-VM output records, hash the
  newly created evidence, and recheck the unchanged Git HEAD before accepting.
  All result files and logs are stored beneath a unique ignored output folder.
- A subprocess timeout triggers best-effort termination of its **own** child
  process tree; timeout is **never** a successful cleanup proof. Host-crash
  cancellation/orphans remain unverified and are D5c blockers.

## On Windows PowerShell, from the ProjectIQ root

Download the source-pinned generator, create its reviewed additive patch,
validate `git apply`, apply it, and rerun all offline tests. Then:

### Static inspection (never launches a VM)

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5_b4_acceptance `
  --vm-config vm_config.json `
  --out outputs/executioner/d5b_b4_inspection.json
```

Expected: `INSPECTION_ONLY_NOT_ATTESTED`. The checkout and SHA validation
must pass; no kernel-security claim is made.

### Full opt-in rerun (multiple sequential clean VMs)

```powershell
$env:PYTHONUTF8 = '1'
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5_b4_acceptance `
  --vm-config vm_config.json `
  --run-consolidated-acceptance `
  --acknowledge-resource-tests `
  --acknowledge-hard-vm-kill `
  --out outputs/executioner/d5b_b4_consolidated.json

$p = Get-Content outputs/executioner/d5b_b4_consolidated.json -Raw | ConvertFrom-Json
$p | Select-Object status, failure_code, all_supported_adapters_verified, security_attestation, run_root
$p.checks | Format-List *
$p.evidence | Format-List *
```

**Pass only when every check is `PASS` and the result is:**

`D5B_B4_REVIEWED_FIXTURE_CHECKPOINT_PASSED_NOT_ATTESTED`

The bundled harness is source-controlled, unlike the older ignored local
`outputs/executioner/tools/...` copy, and its normalized content hash is
verified before any subprocess is launched.

## Evidence/attestation limits

Successful B4 means *four reproducible clean-VM resource gates plus three
reviewed-fixture VMs and offline tests* pass together on the pinned source.
It does **not** mean every supported adapter is covered by a live fixture.
The sample harness's `builds_verified=false` and
`database_service_verified=false` are preserved, not promoted to PASS.

Remaining security gates are explicit in the output: live adapter coverage,
full build descendant accounting, separately managed MySQL, writable guest root
paths, D5c unexpected cancellation/orphan recovery, D5d independent isolation
verification, and D5e final risk-based security go/no-go.

Until those are resolved, use `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` and don't
run arbitrary unreviewed student repos. Don't push B4 on a non-PASS result.
