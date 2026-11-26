# Phase 8D — Generalized reviewed runtime pilot

## Scope and security boundary

Additive to the pushed Phase 8C consolidated pilot. This is not an unrestricted sandbox and does not remove `approved_snapshots`. A repository's configuration, previous reports, dependency hashes and inferred stack cannot authorize execution. `runtime_permission` and `automatic_untrusted_execution` remain false (no transferable permission). Exact reviewed GitHub SHA, host and image verification, existing ACL and managed lifecycle, restricted QEMU networking, guest workspace and cgroups, pre-execution handoffs, a gateway negative control, and sampled storage monitoring are required. No automatic retry, no host-side execution of submitted code, no secret/environment provisioning. Whole-host storage and independent worker qualification remain outstanding. Keep the legacy path unchanged. This implementation has not been validated in a real Windows QEMU run by the package author.

## Runtime layer

| Adapter group | New reviewed execution recipe | Conditions |
|---|---|---|
| Static / PHP | `STATIC_HTTP`, `PHP_HTTP` | Fixed public entry; PHP without unresolved Composer/database requirements |
| Node web / CLI | `NODE_HTTP`, `NODE_CLI` | Single unambiguous JS entry; declared simple node start; literal nonprivileged port or PORT |
| Node builds | `NODE_VITE`, `NODE_NEXT`, `NODE_CRA`, `NODE_ASTRO`, `NODE_NUXT`, `NODE_SVELTEKIT`, `NODE_ANGULAR` | npm v3 lock, fixed build/start commands and supported output conventions; SvelteKit requires adapter-node, Angular single static app, Astro static |
| Python web / CLI | `PY_FLASK`, `PY_ASGI`, `PY_DJANGO`, `PY_STREAMLIT`, `PY_CLI` | Unique conventional entry, Flask/FastAPI/Starlette top-level app constructor; Django manage.py; Streamlit unique app; CLI main guard |
| Optional native | `NATIVE_C`, `NATIVE_CPP`, `JAVA_SINGLE`, `GO_SINGLE`, `RUST_SINGLE` | One source file with conventional name, no project build system/modules; trusted binaries must already exist in guest; explicit opt-in |

These are 21 adapter implementations/candidate types, not 21 live-validated framework configurations. HTTP 2xx is readiness, and CLI zero is completion of the selected smoke command only. No functional test, correctness or grading credit is claimed. No arbitrary lifecycle scripts or command templates, build system autodetection that runs arbitrary shell, service composition, Docker, credentials, or network enabled application execution.

Node: select the highest compatible operator-provisioned version from a catalog of up to 8 SHA-pinned Linux x64 archives. Check common engine semver syntax, ancestor `.nvmrc`/`.node-version`, mandatory locked package engines, and guest bundled npm constraints. No automatic runtime download. A catalog has `{"runtimes":[{"version":"v24.22.3","tarball":"absolute-or-catalog-relative-path","sha256":"64 lowercase hex characters"}]}`. Python: 3.12.x already baked; match actual patch release, PEP 621 requires-python and `.python-version`. Python ranges and markers are bounded; unknown marker semantics and alternate dependency managers block. Import inference uses explicit known import-to-distribution mappings, excludes local/stdlib modules, and refuses unknown/dynamic imports. `--allow-inferred` is required for inferred acquisition. No extras, editable installs, source distributions, dynamic setup.py, lock v2, pnpm/yarn/bun, Poetry/uv/Conda, Composer/Maven/Gradle or native module resolver implemented.

## Commands (PowerShell, ProjectIQ root)

Planning (remote, zero VMs; exit 3 is expected):

```powershell
python -m projectiq_executioner.phase8d dispatch --repo $repo --ref $sha --out outputs/executioner/8d-plan
```

Local source planning only (no execution permission):

```powershell
python -m projectiq_executioner.phase8d dispatch --source-dir C:/path/to/source --out outputs/executioner/8d-local
```

Reviewed execution (single component, one VM; requires existing approval):

```powershell
python -m projectiq_executioner.phase8d dispatch --repo $repo --ref $sha --out $out --run-reviewed --vm-config .\vm_config.json --reuse-npm-bundle $bundle --node-tarball $node --node-sha256 $nodeSha
```

Python uses `--reuse-python-dir $directory --pip-wheel $pip --pip-sha256 $pipSha`. The directory contains `python-lock.json` and `python-wheelhouse.zip`. To explicitly acquire new packages for an already reviewed source, replace the reuse option with `--fetch`. The broker downloads bytes only from restricted registries. No npm/pip install on the host. npm installs from verified tarballs using offline ci and disabled scripts; Python re-resolves with pinned constraints and verifies wheel hashes in guest. Each chosen component then builds/runs in that same disposable VM. No separate preparation VM. Reuse is checked against fresh source requirements/manifests; an incompatible bundle refuses instead of fetching silently.

`--component <id-or-root>` chooses exactly one component. `--all-independent --max-vms 2` permits sequential independent components (budget 1–4, stop at first failure). This does not establish that components communicate or work together. `--enable-optional-toolchains` allows the single-file native adapters if already baked. `--require-bounded-volume` refuses when the separate bounded volume is not observed. `--legacy-reviewed` explicitly routes existing PHP/MySQL, Vite/Express and other supported legacy cases through the earlier reviewed orchestrator, using the existing profiles. It is a distinct path and does not claim the new storage watchdog. `--static-report` generates the existing static Executioner report alongside the new plan; the 8D smoke receipt is separate, not imported as Phase 6 or rubric evidence.

## Evidence and acceptance

- `execution_plan.json`: existing 8A evidence.
- `runtime_plan.json`: component candidate, blockers, toolchain, dependency origin, entrypoint, probe, plan checksum.
- `dispatch_result.json`: stage, source, per-component report references, attempts, outcome; no submitted source archive retained in the output.
- `<component-id>/runtime_result.json`: fixed bounded guest evidence, cleanup, storage watchdog, reviewed scope, dependency acquisition stats. No raw stdout, HTTP body, or arbitrary logs.
- A plan uses exit 3, blocked/failure exit 2, verified reviewed smoke exit 0. Smoke success requires both guest evidence and clean managed teardown.

Run the included `run_8d_acceptance.ps1` first without `-Live` if desired, or once with `-Live` for the integrated acceptance. It runs combined targeted tests, fresh pinned plans for Vite/Flask/Moss, the MDN unapproved refusal, then reuses the two existing dependency bundles for two reviewed runtime launches. No new package downloads by default, no automatic approvals. Stop on first failure; inspect reports rather than retry. Optional `-ExtraRepo` and `-ExtraRef` can exercise a separately reviewed single-component new framework source with explicit restricted acquisition; exactly one additional reviewed VM maximum, no approval editing. Existing 8C successful live runs are not repeated.

This package establishes a broad generalization foundation, not exhaustive runtime support. Live acceptance of added frameworks, generalized dependency resolution across ecosystems, coupled multi-service provisioning, dedicated isolation and independent qualification, and the unified functional-testing/report evidence integration remain outstanding. Native tools are not automatically added to the current guest image. A public repo requiring an external backend, such as Moss & Meadows, remains blocked even if Next.js is recognized. Do not treat a candidate as proven runnable.
