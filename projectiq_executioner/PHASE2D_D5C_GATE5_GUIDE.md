# ProjectIQ — D5c Gate 5: controller-safe managed workspace recovery

**Base checkpoint:** `70f0044b86fb465e96f6143f2e5f367a6dd08622` on `executioner` (Gate 4). This patch is additive to the existing reviewed-fixture runner; it does not authorize student submissions. **Security attestation stays `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.**

## Why this gate exists

Gate 4 connected the real QEMU runner to signed per-run workspaces, but (a) new run registration preceded per-run lease acquisition, and (b) recovery unlocked the per-run lease before deleting the run directory. A second trusted controller could intervene at those boundaries. Moreover, deletion of `run.json` and `.lease` left no independent evidence for a safe retry if the controller died before `rmdir`.

## Changes (no new VM engine)

1. **Permanent managed-root lease.** Each registered root now owns a `.lease` file, alongside `.projectiq-d5c3-root-key`. All cooperative creation, sweeping and cleanup are serialized by a bounded OS-exclusive root lease. Existing Gate 4 roots are upgraded by exclusive file creation; the lock inode is never removed or replaced. The per-run lease is still held for the full VM lifetime. A missing, inaccessible or tampered root lock fails closed.
2. **Atomic runtime registration.** `create_leased_run()` creates and signs a PREPARING run and acquires its per-run lease before releasing the root lease. `ManagedRuntimeRun.open()` uses this API. The existing `create_run()` remains an explicitly *unleased* fixture/helper operation, and must not be used for live VM launch.
3. **Durable signed sweep journal.** Before deletion, the root lease holder verifies the signed run manifest, active lease and exact QEMU process identity, marks SWEEPING and writes an HMAC-authenticated root journal. The journal survives removal of `run.json` and `.lease`, permitting a second controller to finish interrupted cleanup. A pending journal is recovered first, never overwritten. Every retry checks process identity again and revalidates the allowlisted files/inspection tree. Tampering, unknown artifacts, a running or ambiguous VM, and inaccessible locks retain data for manual review.
4. **Backward compatibility.** Existing Gate 3 and Gate 4 acceptance checks recognize the new permanent root `.lease`. Reports still require actual verified removal, not merely `worker.stop()`.
5. **Windows-specific acceptance.** The new Gate 5 acceptance starts separate *trusted Python controllers*, tests root-lock contention and a live per-run lease, kills its own controller, forces a real hard process exit after the per-run identity files were removed, proves authenticated retry, rejects a tampered journal, and requires clean idempotent final state. It starts **no QEMU VM and no student code**. It is bound to a *fresh post-patch* three-fixture regression through the Gate 4 validator.

## Exact PowerShell workflow (from repository root)

```powershell
# 1. Inspect and apply the supplied patch; do not force it.
git branch --show-current
git status --short
git rev-parse HEAD
# Expected HEAD: 70f0044b86fb465e96f6143f2e5f367a6dd08622
$patch = "$env:USERPROFILE\Downloads\d5c_gate5_concurrency_recovery.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch does not match; stop.' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch application failed; stop.' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed; stop.' }

# 2. Targeted unit tests; these are fast and do not launch VMs.
python -m unittest projectiq_executioner.tests.test_phase2d_d5c_gate3 `
    projectiq_executioner.tests.test_phase2d_d5c_gate4 `
    projectiq_executioner.tests.test_phase2d_d5c_gate5 -q
if ($LASTEXITCODE -ne 0) { throw 'Targeted tests failed; no VM regression.' }

# 3. Entire offline suite ONCE after targeted tests pass.
python -m unittest discover -s projectiq_executioner/tests -p 'test_*.py' -q
if ($LASTEXITCODE -ne 0) { throw 'Offline suite failed; no VM regression.' }

# 4. Fresh three-adapter real Windows/QEMU VM regression ONCE.
& '.\projectiq_executioner\tools\d5_b4_regression.ps1'
if (-not $?) { throw 'VM fixture regression failed; do not push.' }
$summary = Get-ChildItem 'outputs/executioner/gate2a3_regression_*/regression_summary.json' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $summary) { throw 'Missing fresh regression summary.' }

# 5. Validate the real fixture evidence under the new root-lock layout.
python -m projectiq_executioner.phase2.vm_worker.d5c_gate4_acceptance `
    --vm-config vm_config.json --regression-summary $summary `
    --out outputs/executioner/d5c_gate4_post_gate5_acceptance.json
if ($LASTEXITCODE -ne 0) { throw 'Post-patch Gate 4 evidence failed.' }

# 6. Independent Windows controller-crash and concurrent-cleanup acceptance.
python -m projectiq_executioner.phase2.vm_worker.d5c_gate5_acceptance `
    --vm-config vm_config.json --regression-summary $summary `
    --out outputs/executioner/d5c_gate5_acceptance.json
if ($LASTEXITCODE -ne 0) { throw 'Gate 5 acceptance failed; do not push.' }
$p = Get-Content 'outputs/executioner/d5c_gate5_acceptance.json' -Raw | ConvertFrom-Json
$p | Select-Object status, failure_code, security_attestation
$p.checks | Format-List *
```

**Expected:** `D5C_GATE5_CONTROLLER_RACES_AND_RECOVERY_VERIFIED_NOT_ATTESTED` and **8/8 PASS**. Record the regression summary and acceptance report locally under `outputs/executioner`; never commit VM disks, local root keys, or acceptance evidence by default.

## Non-goals and residual risks

- Root locking protects **cooperative ProjectIQ controllers**, not malicious code running as the same Windows host account. Filesystem ACL/ownership and handle-level hostile-race review remain D5d work.
- A run interrupted between starting QEMU and signing its immutable process identity remains in `LAUNCHING` and **manual review**, not guessed cleanup. This patch intentionally does not kill processes by stored PID.
- A file deleted or altered outside a trusted registered root, guest root QCOW2 write ceiling, full build descendants, MySQL daemon placement, SSH host identity, network escape and hypervisor assurance remain outside Gate 5.
- Linux controller-death supervision is not attested. The real runtime remains Windows/WHPX-only and operator-reviewed-fixture-only.
- A host crash during an incomplete journal write may retain a signed run with a stale `.writing` file; the next root-locked sweep safely discards only the recognized bounded temporary journal and retries. A corrupt final journal is **manual review**, never silent deletion.
