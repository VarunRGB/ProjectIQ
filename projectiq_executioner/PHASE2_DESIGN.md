> **New (Phase 2C VM pivot):** The Phase 2B Docker rootless probe documented below
> is now legacy compatibility information. Phase 2C uses a native QEMU/QCOW2
> disposable VM worker with **no Docker daemon** and its own verified-image
> toolchain. See `PHASE2C_VM_GUIDE.md`. Phase 2C code is a reviewed-fixture pilot;
> real WHPX/VM acceptance and production attestation remain pending.

# Phase 2 — Secure isolated full-stack orchestration

**Implemented so far:** 2A inert execution-plan compiler, plus 2B architecture-aware
preflight, static preparation and optional read-only Docker/Podman capability
inspection. See `PHASE2B_GUIDE.md` for the CLI and output contract.
**Not yet implemented:** image building, container launches, startup,
readiness validation, real resource enforcement, teardown, network controls,
database bootstrap or cloud mocks. Generated JSON proposals are **not** proof
that an application is runnable or safe to execute.

## Required gate before launching any untrusted repository

1. **Immutable provenance:** use the Phase 1 pinned SHA, not a floating branch.
2. **Verified isolated runner:** rootless and unprivileged containers in a
   restricted worker/VM, with deny-by-default outbound network, bounded internal
   service-to-service networking, a read-only source tree, disposable data
   volumes, no host Docker socket and no privileged mounts.
3. **Runtime allowlist:** choose pinned compatible images according to project
   manifests and operator policy; never automatically run README commands.
4. **Environment:** classify config hints, inject synthetic secrets, provision
   disposable MySQL/Postgres/SQLite resources, and generate safe database
   fixtures. Do not reuse production database credentials or storage buckets.
5. **External dependencies:** hosted APIs and cloud services (e.g., S3) require
   explicit mocks/emulators or operator-authorized test resources. Never send
   arbitrary requests or mutate live resources by default.
6. **Bounded lifecycle:** image build, health probes, memory/CPU/PID/time limits,
   log limits and **unconditional teardown**; enforce all constraints at runtime.
7. **Evidence:** statuses are `READY`, `BLOCKED`, `STARTUP_FAILURE`,
   `TIMEOUT`, `UNVERIFIED`; report what actually ran and why, without leaking
   secrets. Phases 3-4 supply functional assertions and their own outcomes.

## Phase 2A command

From the ProjectIQ repository root (Windows PowerShell):

```powershell
python -m projectiq_executioner.phase2 outputs/executioner/clouddrop_phase1/manifest.json --out outputs/executioner/clouddrop_phase2_plan.json
```

The output is a JSON **proposal**, with `execution = NOT_PERFORMED` and
`readiness = NOT_VERIFIED`. `status = BLOCKED` means an integration or config
requirement is not yet satisfied; `PLAN_ONLY` means no *known* blocker appears
from Phase 1, **not** that it is safe to run.

## Stack-specific expected plans

- **Mini Task Collaboration:** PHP web container plus isolated MySQL and web
  UI, with disposable schema initialization still needing implementation.
- **Moss and Meadows:** isolated Node/Next frontend, but remote HTTP backend
  must be mocked or replaced with an explicitly provided test backend.
- **CloudDrop:** isolated Python/FastAPI backend, React/Vite frontend, embedded
  temporary SQLite and mock S3 dependency. Pydantic `JWT_SECRET` and S3 bucket
  name must use synthetic test values. No real S3 bucket should be contacted.

## Next implementation milestones

- **2B**: IMPLEMENTED static preflight, runtime candidate checks and policy
  requirements; enforcement has NOT been established.
- **2C**: bounded isolated startup for a minimal known example, with
  stdout/stderr collection and teardown verified under failure conditions.
- **2D**: multi-service health, disposable databases and test-only dependency
  mocks, followed by real cross-stack end-to-end readiness benchmarks.
