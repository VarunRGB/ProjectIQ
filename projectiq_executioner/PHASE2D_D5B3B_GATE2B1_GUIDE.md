
## Launch diagnostics and early-exit race fix

The first real 2B.1 attempt returned `MEMORY_ENFORCEMENT_NOT_VERIFIED` even
though the evaluation policy, guest RAM headroom, and VM cleanup passed. The
first code review revealed a release-gate timeout inconsistency: each worker
waited only 12 seconds for a controller-owned release marker, but the controller
allowed 16 seconds to verify each of two sequential workers. The marker wait
is now bounded at 60 seconds; no kernel limits or identity checks are weakened.

The guest additionally emits **only bounded, typed launch diagnostics**, not
raw process command lines, PIDs, application output, or stderr. They appear in
`kernel_observations.probe_stage`, `.launch_failure_reason`, and
`.worker_launch_observations`. Each worker observation records whether its
scope was seen, maximum number of processes, app UID, kernel cgroup, exact
trusted command line, whether verification succeeded, and an exit code if the
wrapper had already exited. Exit codes 111-116 come from the fixed in-guest
worker; exit 114 means the release marker never appeared before its deadline.

The host report parser validates their types and bounded enum values. An
unverified launch **does not count as evidence of a cgroup OOM**. The original
memory enforcement acceptance criteria remain unchanged.

To inspect diagnostic evidence after a real clean-VM run:

```powershell
$p = Get-Content outputs/executioner/b3b_gate2b1_memory_live_diagnostics.json -Raw |
    ConvertFrom-Json
$p | Select-Object status,cleanup,error_code
$p.checks | Format-List *
$p.kernel_observations | ConvertTo-Json -Depth 8
```

If the new attempt is inconclusive, capture these typed fields before making
further modifications; do not repeat the high-memory test blindly.

### Gate 2B.1 release-marker visibility and actual worker lifetime

The clean VM originally lacked the controller-home traversal setup performed
by the normal application runner. As a result, a fully verified `projectiq_run`
PID could exit before memory allocations upon attempting to read its release
marker under `/home/projectiq`. The trusted clean probe now validates the
controller owns that directory and grants only 0711 (traversal, no listing or
write) before launching workers. It independently tests release readability
with the app UID after publishing the 0644 markers.

`kernel_observations` now includes `controller_home_mode_before` and `_after`,
`release_markers_visible_to_app`, `stress_monitor_outcome`, and the two wrapper
exit codes sampled **before** teardown. If visibility is denied, the stress
checks stay NOT_VERIFIED; no cgroup OOM claim is manufactured. A worker's fixed
exit 117 indicates `PermissionError` while opening the release marker.

The live VM run remains a mandatory separate acceptance gate; these changes
alone do not establish memory enforcement.

### Gate 2B.1 cgroup OOM survivor cleanup (follow-up)

Real kernel evidence reached `memory.peak=2147483648` and a positive
`memory.events` `oom_kill` delta, but `worker_scopes_cleaned` failed. This is
**not a memory-policy failure**: the kernel killed one worker while the other
remained alive. `systemctl stop` can wait for that still-running scope; killing
only its `systemd-run` wrapper is not proof that the application child exited.

The trusted clean guest now sends SIGKILL via `systemctl --user kill
--kill-whom=all --signal=SIGKILL` to each *fixed, validated* `piq-mem-<hex>.scope`,
then issues a non-blocking `stop`. It polls up to 12 seconds, accepting cleanup
only after each wrapper exits and the corresponding scope disappears or both
`cgroup.procs` is empty and `cgroup.events populated=0` (which includes nested
cgroups). No arbitrary PID lists or raw commands are accepted from application
input. Bounded per-worker cleanup observations include the two `systemctl`
return codes, final scope state/task count, and wrapper exit code.

Cleanup failure still fails Gate 2B.1 even if the external disposable VM was
successfully destroyed. Tests cannot be declared complete until the full offline
suite and a clean-VM memory acceptance run pass.

### Gate 2B.1 cooperative post-OOM teardown (follow-up)

The real VM reached the precise 2GiB memory limit, recorded an OOM kill,
and recovered RAM, but one scope remained populated even after `systemctl
--user kill` returned code 1 and asynchronous stop returned code 0.

The *fixed trusted* memory workers now poll a controller-owned `.stop` file
while awaiting release and while allocating/holding memory. During teardown,
the controller validates both exact scope and marker paths, exclusively creates
readable/non-writable stop files under its traversable 0711 home, and waits
six seconds for cooperative exit. If any cgroup remains populated, bounded
systemd kill/stop operations run as fallback, followed by the pre-existing
independent `cgroup.events populated=0` check. A killed systemd-run wrapper
never by itself counts as cleanup. The controller records only bounded
per-worker status and classified systemd failure reason, not arbitrary stderr
or PID lists. Stop markers are removed even on failures.

Neither OOM evidence requirements nor production fixture runners are changed.
All offline and real-VM acceptance tests remain mandatory before a push.
