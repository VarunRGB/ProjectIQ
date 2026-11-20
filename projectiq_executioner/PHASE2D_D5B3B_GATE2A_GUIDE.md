# ProjectIQ D5/B3b Gate 2A — bounded shared evaluation slice

**Source baseline:** `executioner` commit `2b58b02d222f0d18cd447a4dd55ff082f14ba467`.
**Status:** offline-wired only; clean-VM fixture regression and destructive memory/I/O probes **not yet run**.

Gate 1 was observed in a clean VM with 7/7 cgroup checks and disposable cleanup.
Gate 2A is the smallest meaningful step toward a real application-bound group.

- Reviewed runtime VMs now request `app_cgroup=True` at VM provisioning.
  A root-owned cloud-init script installs `projectiq-eval.slice` under the
  trusted `projectiq` user manager and then marks bootstrap readiness.
- Budget: `MemoryMax=2147483648` (2 GiB), `TasksMax=80`, and workspace-disk
  `IOWriteBandwidthMax=16777216` bytes/sec (16 MiB/sec). These are initial acceptance
  budgets, with a cgroup task ceiling below the existing per-UID
  RLIMIT_NPROC=96. The 2 GiB memory ceiling exceeds smaller VM profiles
  (e.g. static 1 GiB); this stage proves placement and policy presence,
  not memory-OOM enforcement across every adapter. Tune after VM evidence.
- The trusted guest controller verifies the bootstrap marker and actual
  `memory.max`, `pids.max` and device-specific `io.max` files. It never falls
  back to unsliced evaluation when these requirements fail.
- Every project-owned build/install/server launch passes through
  `systemd-run --user --scope` and a unique `piq-app-*` transient scope
  *before* sudo drops to `projectiq_run`. Inside the app UID the isolated
  Python launcher verifies membership before exec. Trusted runtime-version
  probes and database-scoped MySQL client operations remain separate.
- Both HTTP server adapters and the D4 frontend proxy use this launch path.
  Offline dependency-preparation VMs retain their separate reviewed policy.
- MySQL is system-managed and **outside** this app-user slice. It must be
  independently constrained and measured before full-stack attestation.
- Disposing of the VM remains the ultimate cleanup; scope/orphan handling,
  memory OOM, IO throughput, multi-adapter real VM tests and overall host
  safety still need independent acceptance.

## Offline tests

Run `python -m unittest discover -s projectiq_executioner/tests -p 'test_*.py' -q`.

## Next checks on the user's Windows/QEMU machine

1. Re-run known pinned reviewed FSDL, MiniCollab/PHP-MySQL and D4
   React+Express+SQLite fixtures using existing `phase2.vm_worker` CLI inputs.
2. For each reviewed fixture, verify the normal startup evidence and clean VM
   disposal. A failed app slice must surface `APP_CGROUP_ENFORCEMENT_UNAVAILABLE`.
3. Gate 2B: add memory OOM, PID stress and uncached physical I/O experiments,
   and inspect kernel `memory.events`, `pids.events` and `io.stat`.
4. Gate 2C/B4: full adapter regression and MySQL/system-service resource policy.

**Security attestation remains NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS.**
