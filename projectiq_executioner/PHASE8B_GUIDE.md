# Phase 8B — Dependency planning and reviewed preparation bridge

## Scope and acceptance boundary

This additive implementation builds on the repaired 8A plan schema (8A.1). It adds **dependency preflight and automatic routing into existing reviewed preparation workers**. It does not introduce a general-purpose dependency resolver, automatic security admission, new VM backend, or application execution. The two existing preparers independently acquire the pinned source again, validate the legacy runtime adapter, verify trusted tool assets, and prepare offline dependencies using their existing VM policies. Their own checks can refuse a recipe that passed 8B preflight. No approval list or VM configuration is modified. Unfamiliar, unapproved repositories cannot run these workers until a separate security policy and worker are validated (8C). Other package managers and arbitrary layouts require additional resolver work; their discovery is not preparation.

## Install

From a clean `executioner-generalized` branch containing pushed 8A, run the kit's `apply_phase8b.py --repo .` for dry run and add `--apply` to install. The installer checks integration function anchors and payload hashes, refuses different existing files and adds six files only. No new requirements. Run:

```powershell
python -m pytest tests/test_executioner_phase8a.py tests/test_executioner_phase8a_acceptance_repairs.py tests/test_executioner_phase8b.py -q --tb=short
```

The isolated development harness passed 123 tests (66 prior 8A + 57 new 8B). Actual GitHub acquisition, worker integration, and full regression must be validated on the operator machine; worker tests in the harness use mocks. No VM or actual package download was performed by the kit's development tests.

## Dependency planning (default; no preparation VM)

```powershell
python -m projectiq_executioner.phase8b --repo https://github.com/VarunRGB/ProjectIQ-Python-Fixtures --ref c0858c957f9f79f7459f57c24cb9d0c07c43ad84 --out outputs/executioner/8b_flask_fresh
```

Alternatively `--source-dir PATH` performs local bounded inspection (not verified GitHub identity). `--expected-plan PATH` checks equality to a newly regenerated 8A plan and refuses changed snapshots. Output directory must not exist and must be outside source. Network access to GitHub is used only by repository acquisition. No host package installation. Exit 0 means a preparation recipe or no package job is indicated, exit 3 means blocked, exit 2 means error/refusal. None signifies successful app startup.

`execution_plan.json` holds the 8A plan. `dependency_plan.json` includes source and plan digests, component manifests and hashes, package manager candidates, dependency metadata, preparation intent, runtime blockers, job routing, and recipe checksum. The checksum is consistency evidence, not authorization. File and read limits inherit 8A; exceeding them is a block, not an implicit success. npm lockfile v2/v3 public registry SHA-512 records are checked for shape and root dependency consistency; npm itself validates graph resolution and downloaded integrity in the legacy worker. Python simple direct pins are checked, not resolved by the host. No semantic solver or full lockfile validation is claimed. Nonstandard requirements, conflicts, workspaces, custom configs, unknown resolvers, unreadable locks and mixed layouts can be blocked. Static sites require no package job; PHP without Composer still needs baked runtime/service provisioning.

## Live reviewed preparation

**Only use already-reviewed immutable snapshots.** No new approvals, no automatic acceptance, no untrusted worker runs. The operator must have existing verified QEMU configuration, trusted image and official tool assets. The exact existing helper is selected automatically (Vite, Express, combined Vite+Express, Flask/FastAPI). The helper may reject unsupported layouts/requirements even after the advisory preflight. Existing trusted package-network permission is explicit. No application VM launched. Use a fresh output path and one job at a time. If a helper fails or cleanup is not confirmed, stop and investigate; do not retry automatically. Official toolchain digests must be verified from independent trusted sources, not derived from an untrusted file and treated as approval.

```powershell
# Only if the exact fixture is already approved and these trusted local assets exist.
$nodeTar = "$HOME\ProjectIQ_VM\node-v24.22.3-linux-x64.tar.xz"
$nodeSha = "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb"
python -m projectiq_executioner.phase8b --repo https://github.com/SafdarJamal/vite-template-react --ref 480dc9128ee90bf7c8c817e52ec063953243b240 --out outputs/executioner/8b_npm_prepared_fresh --prepare-reviewed --allow-preparation-network --vm-config vm_config.json --node-tarball $nodeTar --node-sha256 $nodeSha

$pipWheel = "$HOME\ProjectIQ_VM\pip-25.2-py3-none-any.whl"
$pipSha = "6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717"
python -m projectiq_executioner.phase8b --repo https://github.com/VarunRGB/ProjectIQ-Python-Fixtures --ref c0858c957f9f79f7459f57c24cb9d0c07c43ad84 --out outputs/executioner/8b_pip_prepared_fresh --prepare-reviewed --allow-preparation-network --vm-config vm_config.json --pip-wheel $pipWheel --pip-sha256 $pipSha
```

Tool asset names/digests above are the existing reviewed fixture values documented in Executioner README; confirm against the operator's previously verified assets. If paths differ use actual trusted asset paths. Do not download or replace them merely to pass a test.

The preparation output includes `dependency_plan.json`, `preparation_result.json` and `prepared/` containing the existing helper's cache ZIP and private profile JSON. A successful result means exact source, manifest hash(es), toolchain profile, reported cleanup and cache digest match. It does not mean package completeness independently tested, app works, or security attestation. The helper's checks and restrictions remain in force. No profiles are installed/merged automatically. On failure the report withholds artifact eligibility, cleanup remains unverified and no automatic retry is made. Some preflight refusals return exit 2 before creating output. Existing config is never mutated. Do not share private profile JSON, caches, VM config or host paths. For review, share only dependency_plan.json and preparation_result.json plus pytest output; no need to copy large caches.

## Remaining work

8B is a staged foundation: actual preparation requires existing reviewed policy. To achieve generalized Phase 8 goals, 8C must establish validated worker admission (including package-network isolation and resource limits), and 8D must generalize the resolver/adapter coupling, worker profiles, dependency acquisition for more package managers, and runtime startup. Keep compatibility, dependency availability, preparation success and runtime execution separate. Never automatically populate the old approved snapshot registry.


### Acceptance finalization

Single-service npm preparation must share the extracted, operator-pinned Node runtime with the app group using `_share_tree(runtime, writable=False)` before launching npm as the application UID. The source-order regression checks this invariant; live guest permissions require separate VM acceptance.

Failed bridge receipts now include `failure_stage` and `error_code` from a fixed allowlist. A reported preflight or guest failure is a classification, not independent proof of launch or shutdown. Cleanup remains `UNVERIFIED` on failures; no automatic retry, admission or raw guest log export is introduced. The successful receipt format is unchanged.

The npm resolver uses the exact npm lock records and disables lifecycle scripts. Python uses direct exact-version requirements and resolves transitive binary wheels in the reviewed preparation VM. Direct pins do not constitute a full transitive lock; the saved wheelhouse digest identifies the specific prepared artifact.
