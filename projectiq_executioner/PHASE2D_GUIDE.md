# Phase 2D — reusable stack planning and integrated execution (increment D0)

**Status:** First increment implemented. It is not a claim of general student-repository execution or sandbox attestation.

## What this increment adds

1. A single CLI that can take a public GitHub repository (resolving and pinning one SHA), analyze it in Phase 1, then produce Phase 2A, 2B, 2C, and 2D reports together.
2. A **nonexecuting**, source-grounded service capability inventory for the next runtime families: Node SPA (React/Vite-like), Node SSR (Next.js), Node API, Python web, database resources, and external services. This inventory explicitly distinguishes planned profiles from implemented adapters.
3. Optional real execution of an **already approved pinned fixture** by calling the unchanged Phase 2C disposable VM worker. A review flag, exact 40-character SHA, local reviewed-SHA allowlist, pinned template hash, QEMU checks, and VM cleanup remain mandatory.
4. Offline tests for provenance, denial of unapproved execution, unsupported stacks, and forwarding the approved candidate through the existing VM worker.

**No Node/React/Python adapter is enabled yet.** Cloud/S3 remains unsupported; real credentials are never requested. No `npm install`, `pip install`, student scripts, or README commands run on Windows.

## Requirements

Run from ProjectIQ's root in your activated `.venv`. The existing Phase 2C dependencies and optional local `vm_config.json` are unchanged. The local config and VM image must remain untracked; the provided `.gitignore` already excludes `vm_config.json` and `outputs/`.

## Planning only (safe, no VM or Docker)

Using an existing Phase 1 artifact (does not download a repo):

```powershell
python -m projectiq_executioner.phase2.phase2d `
  --manifest outputs/executioner/mini_collab_phase1/manifest.json `
  --out outputs/executioner/phase2d_mini
```

Analyzing a GitHub repo from scratch (Phase 1 through Phase 2D plans in one command):

```powershell
python -m projectiq_executioner.phase2.phase2d `
  --repo https://github.com/VarunRGB/Mini-Task-Collaboration-App `
  --ref c4f7965d16807fa05c3d7ffeee54f86fee4d0a37 `
  --out outputs/executioner/phase2d_mini_full
```

Plans are **never authorization**. Read `phase2d_capabilities.json` for known runtime gaps and `phase2c_candidate.json` for the currently implemented pilot adapter.

## One-command approved execution for the existing Phase 2C adapters

Requires your original SHA-pinned, locally reviewed `vm_config.json` and tested QCOW2 template:

```powershell
$env:Path += ';C:\Program Files\qemu'
python -m projectiq_executioner.phase2.phase2d `
  --repo https://github.com/VarunRGB/Mini-Task-Collaboration-App `
  --ref c4f7965d16807fa05c3d7ffeee54f86fee4d0a37 `
  --vm-config vm_config.json `
  --run-reviewed-fixture `
  --out outputs/executioner/phase2d_mini_approved
```

It produces `phase1/manifest.json` and `manifest.md`, `phase2a_plan.json`, `phase2b_preflight.json`, `phase2c_candidate.json`, `phase2d_capabilities.json`, `pipeline_summary.json`, plus `vm_execution.json` if a run was attempted. The existing Phase 2C runner deliberately re-downloads the pinned immutable GitHub snapshot and independently checks it before guest execution.

`STARTUP_VERIFIED` means the HTTP readiness probe passed inside the VM; it does **not** prove every feature works or that an arbitrary student repo is safe to execute. VM pilot security status remains `NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS`.

## Expansion order (not implemented by D0)

- **D1:** Frozen dependency-manifest verification, lockfiles, hashes, offline npm/wheel caches, and a new trusted runtime template version (do not mutate the validated Phase 2C image).
- **D2:** First approved React/Vite or equivalent Node SPA in a no-egress disposable VM, with build logs and bounded readiness verification.
- **D3:** Python Flask/FastAPI and SQLite, and curated Node/Express backends.
- **D4:** Multi-process service graph with ordered startup, port/health contracts, synthetic config and reliable teardown.
- **D5:** Empirical isolation/security acceptance, resource quotas, failure handling, scheduling, and CI.

**Principle:** New runtime support should be a versioned capability with explicit dependency and execution policies, not a student-provided command string interpreted by the Windows host. Cloud mocks/emulators are deferred.
