# ProjectIQ — D5e Phase 2 Executioner Pilot Closeout

**Reviewed branch:** `executioner`, commit `79eb5b10a419890cf65f886b8b5d70aba70e74d6` (D5d Gate 4).
**Trust state:** `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` remains mandatory.
**Purpose:** independently reconcile existing evidence and issue **two separate** GO/NO-GO decisions. **No QEMU VM, student code, dependency installation, filesystem cleanup, or host security-policy mutation** occurs in this closeout.

## Final decisions

| Decision | Possible outcome | Meaning |
| --- | --- | --- |
| `reviewed_pinned_fixture_pilot` | `GO` only if all 9 checks pass | The narrowly scoped, operator-reviewed, exact-SHA FSDL / MiniCollabApp / FullStack pilot may be treated as closed for the tested integration scope |
| `arbitrary_untrusted_student_execution` | **Always `NO_GO`** | These observations do not establish a general student-code sandbox |
| `phase3_untrusted_execution_integration` | **Always `BLOCKED`** | Do not expose submission-controlled execution to real students via Phase 3 |

Phase 3 may integrate **non-executing** report/rubric/orchestration functionality and may use the explicitly operator-approved reviewed fixtures in an internal pilot. It must preserve the existing `reviewed_fixture=True` + approved SHA restrictions; no bypass or broader source admission follows from D5e.

## Why not launch more VMs?

- D5c already demonstrated three reviewed fixtures with HTTP 200, expected server/cgroup evidence and verified VM/managed-run cleanup.
- D5d Gate 2 independently observed the synthetic build parent/child and MySQL kernel resource caps.
- D5d Gate 3 observed root-disk write denial at three paths and one host network sentinel path.
- D5d Gate 4 independently inspected the actual Windows QEMU executable, arguments, owner/parent, job membership and loopback SSH port.

D5e's additional contribution is *evidence reconciliation*, strict typed report validation, **fresh read-only Windows managed-root ACL re-audit**, regression report binding through the existing `d5c_gate4_acceptance.validate()` validator, and an explicit no-go register. There is no value in booting three unchanged fixtures again solely to repeat the same evidence.

## Safety properties of the validator

1. Accepts Gate 2, 3 and 4 JSON only from bounded, ordinary files under the operator-designated evidence directory. Rejects symlinked, missing, duplicate-key, oversized, malformed and non-object JSON.
2. Requires exact accepted status, execution category, `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`, `student_code_executed = false`, successful cleanup and *exact* all-PASS check-name sets per gate. Also cross-checks bounded diagnostic values (kernel child/drain, ACL-denied writes, host QEMU runtime identity).
3. Calls the **existing D5c Gate 4 validator** on the selected regression summary, including the actual three individual fixture reports, pinned repository SHAs, adapter contracts, HTTP 200, server placement, managed run cleanup, and absence of promoted unverified build claims.
4. Independently reruns the **read-only** Windows managed-root ACL audit and refuses to close the pilot if the active root has unresolved entries or its ACL posture has changed.
5. Records SHA-256 for the local report files. **These hashes are traceability fingerprints, not signatures or tamper-proof provenance.**
6. Requires explicit `--confirm-post-gate3-regression`. The legacy `regression_summary.json` does **not** embed a source-code revision; the flag means the operator checked the selected run happened after the **Gate 3 production bootstrap patch**. This is a manual confirmation, not a cryptographically proven fact. Do not set it for an earlier regression run.
7. A missing or failed check yields `D5E_EVIDENCE_INCOMPLETE_NO_GO` and a nonzero exit. Even a successful closeout keeps `arbitrary_untrusted_student_execution = NO_GO`.

## Step 1 — Apply (PowerShell in `C:\Users\varun\Code\ProjectIQ`)

```powershell
cd C:\Users\varun\Code\ProjectIQ
if ((git branch --show-current).Trim() -ne 'executioner') { throw 'Wrong Git branch' }
if ((git rev-parse HEAD).Trim() -ne '79eb5b10a419890cf65f886b8b5d70aba70e74d6') {
    throw 'Unexpected HEAD: review before applying D5e'
}
$patch = "$env:USERPROFILE\Downloads\d5e_phase2_executioner_closeout.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw 'D5e patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'D5e patch application failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'D5e diff check failed' }
```

## Step 2 — Targeted offline tests (NO VM)

```powershell
python -m unittest projectiq_executioner.tests.test_phase2d_d5e_closeout -q
if ($LASTEXITCODE -ne 0) { throw 'D5e unit tests failed' }
```

Expect `Ran 19 tests ... OK`. The patch adds only validator, tests and guide; it does not change the VM launcher or any adapter, so **do not repeat the three-fixture regression**.

## Step 3 — Select the *correct* existing regression evidence

```powershell
$prior = Get-ChildItem -LiteralPath 'outputs/executioner' -Filter 'regression_summary.json' `
    -File -Recurse | Sort-Object LastWriteTime -Descending
$prior | Select-Object -First 6 FullName, LastWriteTime
```

Choose the run **you actually completed after applying the Gate 3 storage bootstrap changes**, and verify it shows all three startup/HTTP/cleanup PASS results. Do **not** blindly trust the most recently modified file or use a historical pre-Gate-3 run. The explicit operator confirmation in Step 4 acknowledges this provenance limitation.

```powershell
# Replace with the actual FullName selected from the listing above.
$summary = 'outputs/executioner/gate2a3_regression_<ACTUAL_RUN_ID>/regression_summary.json'
$s = Get-Content -LiteralPath $summary -Raw | ConvertFrom-Json
$s | Select-Object RunId, Passed, SecurityAttestation
$s.Results | Format-Table Fixture, Startup, HTTP, Cleanup, BuildEvidence
if ($s.Passed -ne $true -or @($s.Results).Count -ne 3) {
    throw 'Wrong or incomplete regression summary'
}
```

## Step 4 — Run the D5e closeout (read-only, NO VM)

The known successful local reports from the previous gates were:

- Gate 2: `outputs/executioner/d5d_gate2_child_drain_final.json`
- Gate 3: `outputs/executioner/d5d_gate3_final_readiness.json`
- Gate 4: `outputs/executioner/d5d_gate4_host_runtime.json`

If you used different filenames, pass the actual ones instead. D5e does **not** fabricate a successful report if evidence is absent.

```powershell
$out = 'outputs/executioner/d5e_phase2_closeout.json'
python -m projectiq_executioner.phase2.vm_worker.d5e_closeout `
    --vm-config vm_config.json `
    --evidence-root outputs/executioner `
    --gate2 outputs/executioner/d5d_gate2_child_drain_final.json `
    --gate3 outputs/executioner/d5d_gate3_final_readiness.json `
    --gate4 outputs/executioner/d5d_gate4_host_runtime.json `
    --regression-summary $summary `
    --confirm-post-gate3-regression `
    --out $out
$rc = $LASTEXITCODE
$p = Get-Content -LiteralPath $out -Raw | ConvertFrom-Json
$p | Select-Object status, security_attestation, failure_code, operator_regression_review
$p.decision | Format-List *
$p.checks | Format-List *
$p.outstanding_untrusted_execution_blockers | ForEach-Object { "  $_" }
if ($rc -ne 0 -or $p.status -ne 'D5E_REVIEWED_FIXTURE_PILOT_CLOSED_NOT_ATTESTED' -or
    @($p.checks.PSObject.Properties.Value | Where-Object { $_ -ne 'PASS' }).Count -ne 0 -or
    $p.decision.reviewed_pinned_fixture_pilot -ne 'GO' -or
    $p.decision.arbitrary_untrusted_student_execution -ne 'NO_GO') {
    throw 'D5e closeout incomplete. Inspect the failed check and do not push.'
}
```

Expected: **9/9 PASS**, status `D5E_REVIEWED_FIXTURE_PILOT_CLOSED_NOT_ATTESTED`, pilot `GO`, arbitrary untrusted execution **`NO_GO`**, and no VM launch. If current host ACLs or the managed run root have changed, D5e deliberately refuses the pilot closeout. Investigate; don't suppress the check.

## Step 5 — Commit and push ONLY if Step 4 passes

```powershell
if ((git branch --show-current).Trim() -ne 'executioner') { throw 'Wrong branch' }
git status --short
git add projectiq_executioner/phase2/vm_worker/d5e_closeout.py `
        projectiq_executioner/tests/test_phase2d_d5e_closeout.py `
        projectiq_executioner/PHASE2D_D5E_CLOSEOUT_GUIDE.md
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged changes invalid' }
git diff --cached --stat
git commit -m 'Close Phase 2 Executioner reviewed-fixture pilot with D5e no-go boundaries'
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
git push origin executioner
if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
git log -1 --oneline
```

Do not stage `outputs/` or any VM runtime files.

## Unresolved security risks—do not quietly promote to PASS

- Real build-descendant lifecycle coverage is not independently complete. Gate 2's passing synthetic child is deliberately narrower.
- The guest's entire root filesystem and root QCOW2 growth are not globally bounded; Gate 3 checked specific root-disk writable locations only.
- A single gateway sentinel test does not cover all network paths; Windows process inspection does not constitute WHPX/QEMU hypervisor isolation attestation.
- SSH uses per-run `accept-new` trust-on-first-use, not an independently attested trusted guest host key.
- Malicious software already executing as the same Windows operator is outside the tested host trust boundary.
- A hash of the installed QEMU executable, generated locally, cannot by itself establish vendor provenance.
- Saved JSON files and a manually selected regression report are not cryptographically signed, independent of the operator, or continuously measured.

**Correct closeout:** the *reviewed-fixture Phase 2 Executioner pilot* can be declared complete once the evidence above is reconciled; a **general sandbox for arbitrary student submissions remains NO-GO**. Phase 3 can proceed on non-executing integration and guarded internal demonstrations, but must not expose arbitrary submission-controlled execution until those stronger boundaries are implemented and separately audited.
