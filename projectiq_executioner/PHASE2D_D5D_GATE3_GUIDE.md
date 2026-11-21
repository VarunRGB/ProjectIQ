# ProjectIQ — D5d Gate 3: storage/network boundary observation

**Reviewed baseline:** `f93d4bc8bcee5ba32e38ba1e5c8f986ccc24513e` on `executioner` (D5d Gate 2).

**Scope:** This is an add-only, read-only/low-impact *diagnostic acceptance* checkpoint, not the final Gate 3 storage enforcement. No student source, dependency cache, external Internet destination or host filesystem mount is involved. The single clean disposable VM executes trusted Python sent through SSH stdin. **Security attestation remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.**

## Why this test exists

Gate 2 verified the synthetic build parent/child, drained cgroup and bounded the MySQL system service. Separately, production QEMU uses `user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:<port>-:22`, with the application workspace on a separately provisioned bounded ext4 raw disk. That does **not** prove that app UID cannot write `/tmp` and `/var/tmp` outside the bounded disk, nor does a QEMU command-line string by itself demonstrate runtime network denial.

This acceptance tests two independent surfaces:

1. **Storage:** root-owned bootstrap marker, bounded mounted ext4 disk, distinct root/workspace devices, successful harmless exclusive create+unlink by `projectiq_run` on the workspace; separate create+unlink attempts in `/tmp` and `/var/tmp`. The status reports any successful non-workspace writes as a gap; it never silently labels them confined. The guest script runs as `sudo -u projectiq_run` without a shell or student input. This is a UID permission baseline, **not** a verification of any future application-specific launch sandbox.
2. **Networking:** the host starts a TCP listener bound only to `127.0.0.1` on an ephemeral port and proves the listener detects a host-side positive control. The guest must have a valid QEMU slirp default route through `10.0.2.2`, then tries only `10.0.2.2:<listener-port>`; its connection must be denied and the listener must observe no guest connection. The host also confirms `QemuWorker.network_spec()` is the production restricted contract. No public external IP or DNS query is used. A negative result here tests that **one host-gateway path**, not all Internet or hypervisor escape paths.

Unsuccessful or unexpected guest output, unreadable mounts, missing route, or unverified cleanup fail closed; no plain `PASS` text from the guest is trusted.

## Applying the patch — PowerShell

From your project root, with an otherwise clean `executioner` working tree:

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current).Trim() -ne 'executioner') { throw 'Wrong branch' }
if ((git rev-parse HEAD).Trim() -ne 'f93d4bc8bcee5ba32e38ba1e5c8f986ccc24513e') { throw 'Unexpected HEAD' }
git status --short

$patch = "$env:USERPROFILE\Downloads\d5d_gate3_storage_network_baseline.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'Gate 3 patch does not apply' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Gate 3 patch application failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed' }
```

## Fast offline tests (no VM)

```powershell
python -m unittest projectiq_executioner.tests.test_phase2d_d5d_boundary -q
if ($LASTEXITCODE -ne 0) { throw 'Gate 3 offline tests failed; do not boot VM' }
```

Expected: **19 tests, OK** (test count may differ if you subsequently add tests). No full 700+ suite or three-fixture VM regression is warranted for this add-only diagnostic patch.

## One clean VM — initial evidence

```powershell
$out = 'outputs/executioner/d5d_gate3_storage_network_baseline.json'
python -m projectiq_executioner.phase2.vm_worker.d5d_boundary_acceptance `
    --vm-config vm_config.json `
    --run-clean-vm `
    --out $out
$rc = $LASTEXITCODE
$p = Get-Content $out -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, failure_code, security_attestation
$p.checks | Format-List *
$p.guest_diagnostics | Format-List *
if ($p.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED') {
    throw 'VM cleanup not verified; STOP before rerunning'
}
# Important: a nonzero exit is EXPECTED if the probe establishes a real
# non-workspace write gap. Share the report before choosing enforcement.
Write-Host "Gate 3 observer exit code: $rc"
```

## Outcomes

- `D5D_GATE3_NONWORKSPACE_GUEST_WRITES_OBSERVED` — the trusted app UID was able to create+remove a file in `/tmp` or `/var/tmp`. **This is a storage containment finding, not an acceptance pass.** Kernel-backed filesystem restriction is needed before final Gate 3 acceptance, and then the production launcher and actual fixture behavior must be verified.
- `D5D_GATE3_BOUNDARY_EVIDENCE_INCOMPLETE` — missing or ambiguous storage/network evidence. Inspect the fixed-category diagnostics; do not infer a safe network from a socket timeout without a route or a verified host sentinel.
- `D5D_GATE3_STORAGE_NETWORK_BOUNDARIES_OBSERVED_NOT_ATTESTED` — all ten checks pass *for the tested paths only*, and the guest root QCOW2 and other VM write surfaces remain unverified. This still is **not** arbitrary-student safety attestation.
- `D5D_GATE3_PROBE_ERROR` or `CLEANUP_FAILED` — fail closed, investigate before any new VM launch.

## Follow-up after the baseline

Do not commit the diagnostic as a completed Gate 3 security boundary. Use the observations to choose a production-enforced filesystem write policy that restricts app code to the dedicated workspace (for example a verified guest kernel policy) **without** modifying or locking down root-owned MySQL/systemd paths. Require an independent test of the actual production app launcher, negative write tests, and at least affected real-fixture regression before closing Gate 3. Network host-gateway denial should remain a separate observed result; broad egress and hypervisor escape attestations remain open for Gate 4/D5e.
