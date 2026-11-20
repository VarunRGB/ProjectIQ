# ProjectIQ B3b Gate 2A.3 — short-lived build/install pre-exec evidence

Status: **candidate; real WHPX/QEMU acceptance pending**. The previous gate
observed 1 FSDL server, 1 PHP server and 2 full-stack servers inside the
operator-owned evaluation cgroup. This gate adds an independent observation of
short-lived project-controlled npm, Vite and pip work **before exec**.

How it works:
- The trusted app launcher validates the parent evaluation slice and moves
  into a unique transient `piq-app-*.scope` as `projectiq_run`.
- For selected `npm ci`, Vite build and offline pip install commands only,
  trusted Python waits cooperatively for a controller-owned release file before `exec`.
- The controller reads kernel `cgroup.procs`, `/proc/<pid>/status`,
  `/proc/<pid>/cgroup` and `/proc/<pid>/cmdline`, verifying all four UIDs,
  waiting state, exact script/arguments, and the target scope.
- After verified placement, the controller atomically publishes a read-only release
  file beneath its protected 0711 home. The app UID may read but cannot forge it.
  No SIGSTOP/SIGCONT is used, avoiding sudo monitor/job-control deadlocks.
  Any mismatch or timeout fails closed, stops the scope and tears down the VM.
- The report shows `guest_result.build_launch_evidence` with the exact number
  of successfully completed kernel-observed handoffs. No PIDs, credentials or
  command arguments are persisted.

Expected for existing reviewed fixtures:

| Adapter | Evidence | npm_ci | vite_build | pip_install |
|---|---|---:|---:|---:|
| FSDL/static | NOT_APPLICABLE | 0 | 0 | 0 |
| PHP/MySQL | NOT_APPLICABLE | 0 | 0 | 0 |
| Vite SPA | PREEXEC_KERNEL_HANDOFF_VERIFIED | 1 | 1 | 0 |
| Express API | PREEXEC_KERNEL_HANDOFF_VERIFIED | 1 | 0 | 0 |
| Python Flask/FastAPI | PREEXEC_KERNEL_HANDOFF_VERIFIED | 0 | 0 | 1 |
| React+Express+SQLite | PREEXEC_KERNEL_HANDOFF_VERIFIED | 2 | 1 | 0 |

This is not independent proof of post-exec process lifetime, complete child
process graph confinement, memory OOM or physical I/O throttling. The old
`cgroup_placement.builds_verified=False` remains accurate. MySQL runs as a
separately managed system service. Unreviewed student projects remain blocked.

Validation:

1. Generate the patch on the exact pinned `executioner` commit; the generator
   first runs the entire offline unit suite in a temporary copy and refuses
   to create a patch on any failure.
2. Apply with `git apply --check` / `git apply`; rerun offline unit tests.
3. Run an operator-approved, SHA-pinned D4 full-stack evaluation in a clean VM
   using the existing Phase 2D reviewed-fixture CLI. Inspect:

```powershell
$v = Get-Content outputs/executioner/b3b_gate2a3_fullstack/vm_execution.json -Raw | ConvertFrom-Json
$v | Select-Object status, cleanup, guest_error_code
$v.guest_result.build_launch_evidence | Format-List *
$v.guest_result.cgroup_placement | Format-List *
```

Require `STARTUP_VERIFIED`, all prior HTTP/SQLite proofs, VM cleanup, and
`npm_ci=2`, `vite_build=1`, `status=PREEXEC_KERNEL_HANDOFF_VERIFIED`.
If it fails, retain fail-closed behavior and inspect the error category. Don't
loosen cgroup policy to make the test pass. If build handoffs work in the full
stack, repeat on reviewed Vite, Express and Python adapters.

## Gate 2A.3 integration correction

The original SIGSTOP handshake deadlocked with sudo monitoring the child.
The revised file gate was locally reproduced across sudo and a separate UID.
WHPX/QEMU acceptance is still required; no unreviewed student execution.
