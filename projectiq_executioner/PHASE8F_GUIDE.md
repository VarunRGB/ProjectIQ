# Phase 8F — automatic admission, gate A

## Objective and boundary

Replace per-submission manual SHA review with a policy-enforced admission and a dedicated qualified worker, not with automatic entries in `approved_snapshots`. 8F-A is an additive, non-authorizing preflight. It never starts a VM, fetches dependencies, installs software, edits approvals, signs an authorization, or runs submitted code. The existing reviewed dispatcher and worker remain unchanged. A candidate means compatible with the initial policy, not safe to execute. Planning can acquire the exact GitHub archive using existing bounded static extraction. The initial scope is the eight Phase 8E live-smoke adapter types (not full functional correctness), a single component, no unresolved external services, no inferred dependencies, fixed supported toolchains and dependency routes.

## Gates

- A: baseline qualification inventory and automatic compatibility classification. Local tests exercise real planner fixtures, policy denial, exact pinning, invalid plan handling and diagnostic CLI. Report current observations, not attestation. No saved JSON is accepted as authorization.
- B: provision a dedicated worker separated from the personal/development host identity and credentials. Bound all writable storage at the worker/host level (not only the guest workspace or a polling watchdog). Pin trusted worker image and tools using independently maintained provenance; test egress and management-channel isolation. Choose deployment based on gate A's actual host evidence; existing WHPX reviewed worker is not presumed qualified. Avoid any automatic destructive provisioning. Qualification must bind actual enforced controls to worker identity and configuration, including source-free negative and positive controls for process/CPU/memory/storage/network containment, cleanup, crash recovery and stale evidence. Independent host-side evidence is required; guest-reported success alone is insufficient.
- C: implement a distinct authenticated automatic launch authority, bound to exact source, full snapshot bytes, fresh plan, dependency artifacts, worker identity, image/policy version, expiry, one-use launch budget; recheck at worker immediately before launch. A persisted report or source-controlled config cannot grant launch permission. Fail closed if qualification is stale, unavailable, changed, or cleanup uncertain; never auto-add approved snapshots or fall back to reviewed route. No student code on host, offline runtime, no credentials or external service by default. Keep preparation broker bounds, no host package installation. Admission decisions explain compatibility/policy/infrastructure separately.
- D: acceptance on independent unapproved public source under qualified worker, refusal controls, changed SHA/artifact/worker, replay and crash recovery, exact attempt counts and cleanup. No broad claim of unrestricted safe execution or security attestation. Functional tests are Phase 9, not 8F smoke.

## Gate A commands (PowerShell, ProjectIQ root)

```powershell
python -m pytest -q tests/test_executioner_phase8f.py
$qualOut = "outputs\executioner\phase8f_qualification_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase8f qualify --vm-config vm_config.json --out $qualOut
Get-Content "$qualOut\worker_qualification.json"
$admitOut = "outputs\executioner\phase8f_admission_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase8f assess --repo "https://github.com/mdn/beginner-html-site-scripted" --ref 7e7e6527e10072ff0cacda499145ffba63af057e --out $admitOut
Get-Content "$admitOut\admission_result.json"
```

Expected host result is `AUTOMATIC_EXECUTION_NOT_QUALIFIED`, even if every observable prerequisite is positive. MDN should be `CANDIDATE_ONLY` if fresh acquisition and planning match Phase 8E. Both must have `runtime_permission: false`, `automatic_untrusted_execution: false`, `vm_start_attempts: 0`. Exit 0 means diagnostic completed, not authorized. The qualification report does not expose config contents, local paths or account SIDs; it does include disk capacity and config hash. Do not upload private config, tokens, or credentials. A failed/missing observation does not mean an unsafe condition was proven, only that the prerequisite is not established. Non-elevated process token and a separate <=32 GiB volume are observations, not proof of a dedicated host worker or global hard storage bound. Config self-pinned image hash is not publisher provenance. All seven qualification blockers remain open in 8F-A by design. The CLI is an assessment path, not an execution route.
