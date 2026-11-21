# ProjectIQ — D5d Gate 2: independent process-boundary baseline

**Base:** `aa8811f78547ca9f8c7ef4e7cbe44c81669e9e7e`, branch `executioner` (D5d Gate 1).
**Trust:** `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` at all times.

## Why this checkpoint exists

D5b/B3b verified the application server scopes, resource policy and paused
build-entry handoff. It did **not** independently show a descendant of a build
inside the same kernel cgroup or establish bounded resource settings for the
MySQL system service. Claiming that a PostgreSQL/MySQL daemon lives in
`projectiq-eval.slice` would be wrong: it is an independently launched system
service. It must have its **own** verified security boundary.

This Gate 2 patch is an **evidence-first baseline**, not the final containment
fix. A probe must first reveal the actual MySQL kernel/service policy before we
modify a trusted Ubuntu systemd service or guess resource settings. If the
reported MySQL caps are missing, we will add bounded, operator-owned limits in
the next focused Gate 2 change and retest. No new broad gate sequence is needed.

## What the patch does

- Adds a **trusted, source-free** clean-VM probe and a bounded Windows host
  acceptance CLI; no reviewed or unreviewed GitHub repo is transferred.
- Boots **one** disposable QEMU VM with the existing pinned image, restricted
  network, 96 MiB workspace, evaluation slice bootstrap and existing MySQL
  bootstrap. Does **not** alter any production runtime file, guest image,
  Windows ACL, application adapter, or dependency cache.
- Starts a fixed synthetic `projectiq_run` Python parent which starts one
  `/usr/bin/sleep` child in the same systemd evaluation scope. Independently
  reads Linux `/proc/<pid>` UIDs, PPID, cgroup membership and `cgroup.procs`.
  Stops its *exact controller-owned scope* and checks `cgroup.events`
  `populated=0` (includes descendant cgroups) or complete scope removal.
- Inspects the actual MySQL systemd `MainPID`, non-root UID, kernel cgroup
  membership and independent system-service scope. Reads actual `memory.max`,
  `pids.max` and `io.max` on its MySQL datadir device, without changing them.
- Host accepts only a tiny, fully typed, no-PID, no-path boolean evidence
  structure; missing information is `NOT_VERIFIED`, never assumed PASS.

## Apply (PowerShell, ProjectIQ root)

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current).Trim() -ne 'executioner') { throw 'Wrong branch' }
if ((git rev-parse HEAD).Trim() -ne 'aa8811f78547ca9f8c7ef4e7cbe44c81669e9e7e') { throw 'Wrong checkpoint' }
if (git status --porcelain) { throw 'Uncommitted changes; review before applying' }
$patch = "$env:USERPROFILE\Downloads\d5d_gate2_process_baseline.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch application failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Diff whitespace error' }
```

## Targeted offline tests (no VM)

```powershell
python -m unittest projectiq_executioner.tests.test_phase2d_d5d_process -q
if ($LASTEXITCODE -ne 0) { throw 'Gate 2 offline tests failed' }
```

## Evidence-only toolchain inspection (no VM)

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5d_process_acceptance `
    --vm-config vm_config.json `
    --out outputs/executioner/d5d_gate2_inspection.json
```

Expected status if pinned QEMU image and tools are available:
`INSPECTION_ONLY_NOT_ATTESTED`. No VM is launched without `--run-clean-vm`.

## ONE clean VM: kernel/process/MySQL evidence

```powershell
python -m projectiq_executioner.phase2.vm_worker.d5d_process_acceptance `
    --vm-config vm_config.json `
    --run-clean-vm `
    --out outputs/executioner/d5d_gate2_process_baseline.json
$rc = $LASTEXITCODE
$p = Get-Content outputs/executioner/d5d_gate2_process_baseline.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, failure_code, security_attestation
$p.checks | Format-List *
if ($p.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED') {
    throw 'Cleanup not verified; do not start more VMs'
}
if ($rc -ne 0) { throw 'Gate 2 probe failed; inspect the report before rerun' }
```

**Expected outcome:** Synthetic process-placement and scope-drain checks
`PASS`; MySQL service identity and independent cgroup `PASS` if the template
runs `mysql.service`. MySQL memory/PID/write ceilings may legitimately be
`NOT_VERIFIED`, because the existing Phase 2 code never installed explicit
per-MySQL systemd resource caps. The corresponding status is deliberately
`D5D_GATE2_MYSQL_RESOURCE_LIMITS_UNVERIFIED` — **not a Gate 2 security PASS**.

If every bounded kernel observation is verified (e.g., the operator template
already has suitable MySQL caps) the status is
`D5D_GATE2_SYNTHETIC_PROCESS_BOUNDARIES_OBSERVED_NOT_ATTESTED`.
Neither status authorizes arbitrary untrusted student work. Any core
`NOT_VERIFIED` yields `D5D_GATE2_KERNEL_EVIDENCE_INCOMPLETE`; investigate before
making enforcement changes.

## After the report

Share `status`, `cleanup` and `checks`. When the MySQL unit has missing
ceilings, implement and empirically test **root-owned MySQL systemd unit
limits** and verify the real reviewed PHP/MySQL adapter. Separately verify the
real npm/pip/build scope lifecycle, rather than promoting synthetic evidence
to coverage of all builds. Do not rerun the complete 655+ offline suite or the
three reviewed fixtures for this *add-only, clean-VM observation patch*.

## Explicit limitations

1. One synthetic parent/child does not establish whole-lifecycle confinement
   for arbitrary real npm, Vite or Python build descendants.
2. This patch does not impose MySQL limits, and measures write bandwidth caps,
   not the entire database write surface or root QCOW2 disk quota.
3. Missing PIDs, inaccessible kernel files, wrong UIDs or ambiguous scopes
   are never promoted to PASS.
4. SSH uses the existing per-run TOFU guest host-key bootstrap; independent
   SSH/hypervisor/network/host escape assurance remains D5d/D5e work.
5. A clean VM is not a student's untrusted code execution and is not production
   security attestation.
