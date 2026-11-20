# ProjectIQ B3b Gate 2A.2: independent live-server placement evidence

**Status: offline-tested candidate; real WHPX/QEMU guest verification pending.**

The trusted `projectiq` guest controller reads the live Linux cgroup files and
`/proc/<pid>/{status,cgroup,cmdline}` while a reviewed fixture server is running.
It requires the exact transient `piq-app-<16-hex>.scope`, the separate
`projectiq_run` real/effective UID, and the intended server command line.
A missing or mismatched observation fails closed before startup can be accepted.
The `vm_execution.json` report carries only a bounded `guest_result.cgroup_placement`
summary, without PIDs or application arguments. Expect one verified server for
FSDL/PHP and two for the D4 frontend/backend fixture.

This is **not a complete process graph attestation**: short-lived `npm ci`, pip,
Vite build commands, grandchildren not sampled, and the separate MySQL system
service remain outside the independent live-server evidence. The existing
pre-exec cgroup check still applies to those project-owned commands. Do not
approve arbitrary untrusted projects or assert general sandbox isolation.

Real VM acceptance: repeat the three previously approved SHA-pinned Phase 2D
runs, using fresh output folders. Require `STARTUP_VERIFIED`, cleanup
`VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, plus:

```powershell
$v = Get-Content outputs/executioner/b3b_scope_fsdl/vm_execution.json -Raw | ConvertFrom-Json
$v.guest_result.cgroup_placement | Format-List *
# status SERVER_PIDS_VERIFIED, server_count 1, builds_verified False,
# database_service_verified False.
```

Next: capture actual short-lived build/install placement, then memory/PID/IO
exhaustion evidence, and address MySQL's separately managed service budget.
