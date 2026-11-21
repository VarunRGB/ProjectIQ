# ProjectIQ D5d — Gate 1: independent Windows host ACL precheck

**Base checkpoint:** `d64e48de9025eab377d09a440ad386f209956d70` (`executioner`, D5c Gate 5).
**Scope:** Read-only host-side observation. **Not an authorization to run arbitrary student repositories.** Status remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## Why this gate is meaningful

D5c verifies authenticated, lease-protected recovery under cooperative controllers. However, HMAC signatures and byte-range locks do **not** protect their own files from a malicious host process that can write the root registration key or replace the lock. Before any final D5e review, verify the actual Windows discretionary ACLs on the operator's managed root parent directory, managed root, `.projectiq-d5c3-root-key`, and root `.lease`. This is a different control from repeating VM startup tests.

The audit uses the fixed System32 Windows PowerShell executable and .NET ACL APIs, evaluates the owner's SID and each allow/deny ACE as numeric rights, and **never** modifies a filesystem permission. It permits operator/SYSTEM/Administrators writes, rejects broad writable grants to Everyone, Authenticated Users, Builtin Users/Guests/Power Users, and requires manual review for unknown writable trustees or owners. An overlapping DENY cannot turn a broad writable ALLOW into a pass. All read-only / unknown SID identities stay out of the persisted report.

The audit explicitly rejects a missing/null DACL and is deliberately conservative: rights can be inherited, complex account/group membership and DENY semantics may require manual review. It does not evaluate attacker-controlled processes running **as the same operator SID**. It is not a hypervisor penetration test. Do not weaken host ACLs or delete a live registry merely to force PASS.

## Apply (Windows PowerShell, repository root)

```powershell
# Pin the source. This patch is tested against the pushed Gate 5 checkpoint.
$expected = 'd64e48de9025eab377d09a440ad386f209956d70'
if ((git branch --show-current).Trim() -ne 'executioner') { throw 'Wrong branch' }
if ((git rev-parse HEAD).Trim() -ne $expected) { throw 'Wrong base commit; stop' }
if (git status --porcelain) { throw 'Working tree not clean; stop' }
$patch = "$env:USERPROFILE\Downloads\d5d_gate1_host_acl_precheck.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'D5d Gate 1 patch did not match' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Failed to apply D5d Gate 1 patch' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed' }

# First: only the new targeted offline tests (no VMs).
python -m unittest projectiq_executioner.tests.test_phase2d_d5d_host_acl -q
if ($LASTEXITCODE -ne 0) { throw 'D5d offline tests failed' }

# Second: inspect actual Windows ACLs without launching a VM.
python -m projectiq_executioner.phase2.vm_worker.d5d_host_acl_audit `
  --vm-config vm_config.json `
  --out outputs/executioner/d5d_host_acl_precheck.json
$p = Get-Content outputs/executioner/d5d_host_acl_precheck.json -Raw | ConvertFrom-Json
$p | Select-Object status, failure_code, security_attestation
$p.checks | Format-List *
```

Expected **only if Windows reports a safe observed ACL shape**: `D5D_HOST_ACL_PRECHECK_PASSED_NOT_ATTESTED` with five checks `PASS`. If report is `D5D_HOST_ACL_REQUIRES_REVIEW`, stop and share the concise report (no credentials or raw ACL dumps). This gate must not auto-repair ACLs or automatically sweep unresolved managed runs.

**No three-fixture rerun is needed**: the patch only adds a read-only operator acceptance command, targeted tests and documentation; it does not modify the runtime, QEMU, registry or guest code.

## Remaining D5d work (independent verification, not repeated smoke tests)

1. **Guest workload isolation:** Prove complete npm/Vite/pip *post-exec descendant* placement under enforceable cgroup limits, not merely the pre-exec handoff; verify MySQL's service process accounting and privileges independently.
2. **Guest filesystem and host escape boundaries:** Confirm root QCOW2 writable-space ceiling or establish an enforceable dedicated-worker storage budget; validate no host folder/device exposure and independent network policy incl. host-reachable services.
3. **SSH/QEMU trust and operational boundary:** Replace TOFU (`StrictHostKeyChecking=accept-new`) with independently pinned per-VM host identity, review the QEMU/Ubuntu patch policy and a dedicated worker threat model.
4. **Evidence consolidation:** Carry only verified properties into D5e. Where a critical boundary remains unproven, D5e is NO-GO for arbitrary student submissions.

D5d Gate 1 is **not** the whole D5d; it closes one missing host-permissions evidence item without unnecessary VM launches.
