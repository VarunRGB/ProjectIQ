# ProjectIQ D5c Gate 3 — registered orphaned-VM storage recovery

**Checkpoint:** `7925880e3e46c22dc0b7e124069f6393a2033291`, `executioner` branch.

**Scope:** Opt-in **trusted** clean-QEMU recovery acceptance on Windows 11/WHPX. The ordinary student/project launchers are **not automatically enrolled** in the managed-run registry by this additive pilot. Runtime integration, full crash-race/partial-cleanup behavior, independent filesystem ACL review, and production authorization remain pending. Never run arbitrary untrusted student code based on this report.

## Trust model and safety boundaries

- The operator explicitly creates a **dedicated new managed root**. The recovery routine never scans Windows TEMP or an arbitrary existing project folder. A 32-byte owner-registration key authenticates fixed-format per-run manifests (HMAC-SHA256). The managed root must remain inaccessible to student code. The authentication mechanism is accidental/unauthorized-record resistance within the trusted operator environment, **not** protection against a malicious program running as the same Windows account.
- Each run gets an unguessable `piq-d5c3-<20 hex>` folder, an OS-exclusive `.lease` held throughout controller ownership, and a strict atomic `run.json` state transition. A locked lease prevents deletion. A run interrupted during `LAUNCHING` before recording the immutable QEMU creation time is **manual review**, never assumed safe.
- A launched run is recoverable only when a stored, signed QEMU executable path matches the current pinned provider and a Windows process-handle check finds the recorded process has exited or is absent. A live process, a reused/ambiguous PID, inaccessible process, wrong binary, missing root key, invalid signature, symlink/reparse point, hardlink, nested/unknown artifact, or incomplete lease is left untouched and reported for manual review.
- The sweeping function **never kills a process using a stored PID**. Normal Windows Job Object lifetime control (verified in D5c Gate 1) is the primary process-kill safety mechanism. The sweeper only deletes files under a verified registered folder after proving the original VM no longer lives. A per-run file lock and post-lock filesystem validation provide concurrency safety for cooperative ProjectIQ controllers; adversarial same-user file races require stronger filesystem ACLs/handle-based deletion, reviewed in D5d.
- The registered folder is explicitly allowlisted: `.lease`, `run.json`, `run.json.writing`, `run.qcow2`, `workspace.raw`, `seed.iso`, `controller_ed25519`, `controller_ed25519.pub`, `qemu.log`, `known_hosts`, `known_hosts.old`. Nothing else is removed.

## Real Windows acceptance experiment

1. Verify the pinned toolchain/QEMU template, create a fresh test-only managed root and a signed run record.
2. Launch one verified fixed 1536 MiB / 96 MiB workspace clean QEMU VM using the existing Windows Job Object guard. Verify SSH readiness and the exact live process handle/creation time.
3. Attempt a sweep **while the controller is alive**. The live lease must block cleanup, leaving the VM and files intact.
4. Hard-kill only the test Python controller, verify the exact QEMU process handle exits, then sweep the now-stale signed run. All allowlisted files and the abandoned directory must be removed.
5. Create a deliberately tampered manifest and an unrelated sibling. The sweeper must refuse the tampered run and preserve unrelated data. Repeat the sweep to confirm no extra deletion occurs.
6. Start a second independent clean VM using a fresh run ID, verify readiness, stop it normally, sweep its exited VM files, and remove the test-only root.

**Pass:** all ten named checks `PASS`, status `D5C_ORPHAN_STORAGE_RECOVERY_VERIFIED_NOT_ATTESTED`, cleanup `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`. Expected two sequential disposable VMs; no student source, dependencies, or host shared mounts. If a VM remains alive, fallback termination never counts as a pass; ambiguous storage is left for manual inspection.

## Apply

The source-pinned generator checks that you are on exactly the pushed Gate 2 checkpoint and that the Executioner tree is clean. It runs the **entire offline suite in an isolated temporary checkout** before writing an add-only patch. Check `git apply --check`, apply, then run the full suite again. Do **not** commit until live acceptance and the existing three-fixture VM regression both pass.

## Real clean-VM test (PowerShell)

```powershell
$env:PYTHONUTF8 = '1'
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.vm_worker.d5c_orphan_acceptance `
    --vm-config vm_config.json `
    --run-clean-vm --acknowledge-orphan-recovery `
    --out outputs/executioner/d5c_gate3_orphan_recovery.json
$p = Get-Content outputs/executioner/d5c_gate3_orphan_recovery.json -Raw | ConvertFrom-Json
$p | Select-Object status, execution, cleanup, failure_code
$p.checks | Format-List *
```

The command uses only its own disposable test root. It intentionally kills its *own* first controller; do not run other disposable acceptance probes concurrently.

## Remaining work

D5c Gate 4 must integrate the registry into normal reviewed-fixture runtime startup, address partial cleanup, concurrent recovery, orphan retention/age policy, and rerun the full adapter regression. D5d independently reviews OS filesystem ownership/ACLs, process identity, build descendants, MySQL isolation, network and guest root write boundaries. D5e makes the final go/no-go decision. `security_attestation` remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS` throughout this gate.
