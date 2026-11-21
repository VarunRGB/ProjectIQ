# ProjectIQ D5d Gate 4 — Independent Windows QEMU Runtime Posture

**Status:** acceptance artifact; do not mark Gate 4 passed until the *real Windows* clean-VM run reports all checks PASS and disposable VM cleanup succeeds.

**Last audited branch head:** `executioner` commit `d761d12e66c09a5e74194a1549af969fbaaab9ab` (D5d Gate 3).

## Motivation, distinct from earlier gates

- D5c already covered Windows kill-on-close Job Objects, lease recovery, and crash handling. D5d Gate 1 covered managed-root ACLs.
- D5d Gates 2–3 verified guest scope/MySQL limits, selected guest write paths, and one guest-to-host network path.
- D5d Gate 4 asks **what Windows actually launched**: the live QEMU process image/parent/owner, complete QEMU command line, loopback SSH listener owner, and inherited Windows job membership. We do **not** trust `Popen.args` or `QemuWorker.network_spec()` as the runtime observation.

## Scope and boundaries

The new acceptance module is **add-only**. It does **not** alter the QEMU launcher, security policy or any reviewed fixture. It boots *one trusted, clean, disposable Windows/WHPX VM* with a bounded 96 MiB workspace; transfers no student source; starts no dependency fetch; and reports only fixed status categories. It uses `Get-CimInstance Win32_Process`, `Invoke-CimMethod GetOwnerSid`, and `Get-NetTCPConnection -State Listen` through the built-in Windows PowerShell. The independent command-line parser is Windows `CommandLineToArgvW`.

`--qemu-sha256` is mandatory. The gate compares this operator-reviewed hash with the configured QEMU binary before launch **and** with the live executable image path inspected by Windows while the VM is running. A hash computed from the same local binary is only a *measurement consistency check*, not evidence of authentic vendor origin. Validate installer provenance/signature separately if needed.

## Acceptance checks

| Check | What it observes |
| --- | --- |
| `host_toolchain_and_pinned_base_image` | Existing trusted toolchain check and cryptographic guest base image pin |
| `operator_qemu_binary_digest_matches` | Installed QEMU bytes match the supplied 64-digit SHA-256 before launch |
| `live_windows_process_identity_and_parent` | CIM process PID, owner SID equality and direct Python-controller parent |
| `live_qemu_executable_matches_operator_binary` | Actual process image path matches configured executable |
| `live_qemu_executable_digest_matches` | Actual image-file SHA-256 matches operator pin during VM lifetime |
| `live_qemu_arguments_match_restricted_policy` | All Windows-parsed QEMU arguments, in exact order, match a second policy formulation: WHPX, fixed disks/devices, restricted SLiRP, SSH loopback, no monitor, serial, display, host shares or other flags |
| `host_ssh_listener_owned_by_qemu_loopback_only` | Windows TCP table contains exactly one listener on the chosen SSH port; bound `127.0.0.1` and owned by live QEMU PID |
| `live_qemu_in_controller_job` | Windows Job Object membership via the existing kernel API, not just inherited launch claims |
| `trusted_guest_ssh_reachable` | Clean VM reaches SSH readiness and remains alive during independent host inspection |

Expected status: `D5D_GATE4_LIVE_WINDOWS_QEMU_POSTURE_OBSERVED_NOT_ATTESTED` with **9/9 PASS** and `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`.

## Commands (PowerShell at repository root)

First apply the downloadable add-only `.patch` with `git apply --check` and `git apply`, then run:

```powershell
python -m unittest projectiq_executioner.tests.test_phase2d_d5d_host_runtime -q
if ($LASTEXITCODE -ne 0) { throw 'Gate 4 unit tests failed' }

# Use the configured QEMU binary, or the binary found on PATH.
$cfg = Get-Content vm_config.json -Raw | ConvertFrom-Json
$exe = if ($cfg.qemu_binary) { (Resolve-Path $cfg.qemu_binary).Path } else {
    (Get-Command qemu-system-x86_64.exe -ErrorAction Stop).Source
}
Get-Item -LiteralPath $exe | Select-Object FullName, Length
Get-AuthenticodeSignature -FilePath $exe | Select-Object Status, SignerCertificate
$pin = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()

$out = 'outputs/executioner/d5d_gate4_host_runtime.json'
python -m projectiq_executioner.phase2.vm_worker.d5d_host_runtime_acceptance `
    --vm-config vm_config.json --qemu-sha256 $pin --run-clean-vm --out $out
$rc = $LASTEXITCODE
$p = Get-Content $out -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, failure_code, security_attestation
$p.checks | Format-List *
$p.host_diagnostics | Format-List *
if ($p.cleanup -ne 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' -or $rc -ne 0 -or
    $p.status -ne 'D5D_GATE4_LIVE_WINDOWS_QEMU_POSTURE_OBSERVED_NOT_ATTESTED' -or
    @($p.checks.PSObject.Properties.Value | Where-Object { $_ -ne 'PASS' }).Count -ne 0) {
    throw 'Gate 4 independent live-host acceptance incomplete. Do not push.'
}
```

Do **not** repeat the FSDL/MiniCollabApp/FullStack three-fixture regression for this add-only probe: Gate 3 already ran it after the most recent production bootstrap changes. If a future Gate 4 fix changes production launch behavior, reconsider the affected regression.

## What this does NOT establish

- The operating-system API observations are real, but **do not attest the hypervisor or WHPX isolation itself**. A Windows host process reporting `-accel whpx` and booting a guest does not prove prevention of malicious VM escapes.
- No independent hardware measurement of a loaded WHPX VM is obtained. No QEMU publisher signature or secure supply-chain provenance is established by a self-recorded SHA.
- Same-Windows-operator malicious code may alter the process or its environment and is outside this check's threat model.
- There are no unrestricted/student-source builds in this acceptance. Real build descendants and all possible guest root-overlay writes remain broader security review questions.
- This check inspects **one** clean VM invocation; it is not perpetual monitoring of every future VM.

The report must retain `security_attestation = NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` regardless of check count. D5e must make the final deployment decision only after reviewing these limitations.
