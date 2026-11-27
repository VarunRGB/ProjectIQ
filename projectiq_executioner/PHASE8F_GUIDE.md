# Phase 8F — trusted local demo and deferred production qualification

## Current scope (8F-B)

8F-A remains the non-authorizing compatibility/qualification diagnostic. The original production plan below is deferred, not satisfied. For the local MVP, `demo` is a separate **operator-trusted** route. Use only on repositories and dependencies you trust. Do not expose it to student submissions, HTTP APIs, background jobs, or a shared/unattended service. Run as your normal Windows user, not administrator. The trust switch is an operator assertion, not a security assessment. No dedicated worker or hard global disk quota has been established.

- Resolve the supplied GitHub URL and optional ref (default branch when omitted), acquire an exact commit and compile fresh 8D plans. The 8F-A eight-adapter policy does not restrict this trusted demo route: all compatible 8D adapters are eligible. Optional native toolchains require explicit opt-in and actual provisioned guest compilers.
- Refuse blocked plans, missing/ambiguous components, missing or mismatched trusted toolchain assets, and a busy or unverified QEMU host. Use `--component ROOT` for a multi-component project; `--all-independent --max-vms N` permits at most four compatible components sequentially. It does not run services together. No retry after a failed or uncertain worker result.
- Create a temporary local config containing only this exact source approval. The original file is never written; the temporary file is removed on ordinary completion or exceptions. A force-kill or power loss can leave a temporary directory; do not reuse it. Inspect worker leases/cleanup before retrying. Temporary config copies must never be committed or included in report bundles. It is not a production authorization token.
- Call the unchanged 8D dispatcher with the pinned SHA, automatically acquire dependencies only where needed, and validate saved plan/dispatch/runtime receipts using the 8E auditor. The existing exact-SHA checks, offline runtime, resource restrictions, QEMU networking, and cleanup remain. GitHub is acquired once for preflight and again at the exact SHA by the existing dispatcher per selected component. This intentionally avoids moving the dispatcher trust boundary.
- A successful `demo_result.json` has `DEMO_REVIEWED_SMOKE_COMPLETE`, `temporary_approval_cleanup: REMOVED`, `original_config_unchanged: true`, and `automatic_untrusted_execution: false`. `vm_attempt_count_uncertain: true` means inspect the worker before retrying. Smoke readiness is not functional verification.

### Commands (regular PowerShell, ProjectIQ root)

```powershell
python -m pytest -q tests/test_executioner_phase8f.py tests/test_executioner_phase8f_demo.py
$demoOut = "outputs\executioner\phase8f_demo_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase8f demo --repo "https://github.com/mdn/beginner-html-site-scripted" --ref 7e7e6527e10072ff0cacda499145ffba63af057e --trust-demo-source --vm-config vm_config.json --out $demoOut
Get-Content "$demoOut\demo_result.json"
```

Only run this example if you have personally selected and trust the exact source. For subsequent submissions, omit `--ref` to resolve the current default branch. For Node, supply the existing trusted `--node-tarball` and `--node-sha256` (or `--node-catalog`). For Python dependencies, supply the existing trusted `--pip-wheel` and `--pip-sha256`. Reuse the operator-pinned hashes from Phase 8E; do not derive expected hashes from newly obtained files. `--allow-inferred` opts into the existing bounded Python dependency inference, not arbitrary package installation. No extra assets are necessary for static/PHP/stdlib Python CLI. All original compatibility limits remain, including unsupported package managers, external services, private repositories, ambiguous entrypoints, and archive limits. The main configuration and normal reviewed execution remain unchanged.

8F-C: live demo acceptance with previously unapproved trusted source, followed by a refusal in ordinary 8D using the original config. 8F-D: integration and documentation. Production qualification and authenticated admission remain a separate future effort. The original 8F-A production roadmap is recorded below for continuity.

---

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
