# Phase 8E — diverse repository acceptance

This is an additive acceptance/coverage layer over the pushed Phase 8D engine. It does not change the runtime worker, admissions, guest image, approved snapshots, or rubric. No external repository is automatically reviewed or executed. A passing smoke check is not functional correctness, and no untrusted-runtime permission is granted.

## Corpus layers

- 35 isolated local controls: 25 positive planning cases covering all 21 adapter types, including distinct HTTP framework hints, plus 10 expected-block controls. The Node framework planning fixtures contain deliberately synthetic archive digests: they are never acquired or executed.
- An independent multi-component source fixture containing 14 roots, with actual simple applications (no invented lockfiles). Local planning verifies all expected components. The optional export command writes these real smoke sources to a new folder only. To run them, publish to a separate operator-owned GitHub repository, review the entire exact commit and use the existing operator approval process. The runner verifies every exported file byte and refuses extra files. Do not initialize the new repository with a README/license/.gitignore or otherwise alter the generated tree. The fixtures are not external student projects.
- Seven independently pinned public repositories, identified via GitHub commit/tree inspection. Their compatibility expectations are hypotheses until the local campaign acquires and inspects the whole snapshots. Positive candidate hypotheses: MDN scripted HTML, Render SvelteKit, Streamlit example. Expected policy/compatibility blocks: Render Express (Yarn), Render Next (Yarn), Render FastAPI (extras), Render Django (PostgreSQL/deployment config). A different blocker or unavailable source is reported as a gap, not silently counted as a pass. None is pre-approved. Public registry bytes and wheel size budgets may block candidates during live preparation.

## Commands

From ProjectIQ root, after running the supplied guarded installer:

```powershell
# Full combined regression + local corpus + pinned public plans; zero VM launches.
& "$acceptance"
# Only offline local controls (no GitHub or config):
& "$acceptance" -LocalOnly
# List exact public commits:
python -m projectiq_executioner.phase8e list-public
# Export operator-created fixture sources (no upload, no approval):
$fixtures = "$HOME\Code\ProjectIQ-8E-Fixtures"
python -m projectiq_executioner.phase8e export-fixtures --out $fixtures
```

Review the exported source before publishing it to an empty repository. Set `$fixtureRepo` to its GitHub URL and `$fixtureSha` to the exact remote commit. The existing operator VM configuration must already approve that exact source after review; the campaign never edits it. Do not approve unknown code to force a pass. Review external repositories independently before selecting live cases. The original `vm_config.json` stays unchanged by the runner. Avoid committing private config, cache or generated outputs.

One selected live campaign (at most seven VMs here), after the regression and source review:

```powershell
& "$acceptance" -Live -SkipRegression -SkipPublic `
  -FixtureRepo $fixtureRepo -FixtureRef $fixtureSha `
  -Cases @('fixture-static','fixture-php','fixture-node-http','fixture-node-cli','fixture-python-cli','fixture-asgi','fixture-django') `
  -MaxVMs 7 -Fetch
```

`-SkipRegression` is only appropriate after the same code passed the regression; the transcript explicitly records the skip, not a pass. `-SkipPublic` avoids repeating public planning when only the operator fixture repository is being tested. Remove it to plan the public sources in the same run and optionally select one approved public candidate (e.g. `public-svelte`) within `-MaxVMs 8`. Live IDs require a positive fresh plan, existing exact-SHA approval, trusted Node/pip assets, and explicit `-Fetch` for packages. No default package downloads; no default VMs. If a selected component requires dependencies, restricted acquisition uses 8D's existing npm/Python broker. No host install or guest Internet is introduced. `-EnableOptionalToolchains` is required for native fixtures (`fixture-c`, `fixture-cpp`, `fixture-java`, `fixture-go`, `fixture-rust`); it is not a toolchain installer. The current guest may lack compilers. `fixture-streamlit` is optional and may exceed wheel or workspace limits. Do not raise security/resource limits just to obtain a pass. `-RequireBoundedVolume` deliberately refuses the current shared-volume setup if it has not been independently qualified.

## Evidence and gates

1. Combined Phase 8 regression, then local fixture plan controls. Any failed control stops the campaign.
2. Fresh exact GitHub pins and public plans; plans independently cross-check the 8A and 8D digests. When operator config is supplied, a new unapproved public candidate can be sent through the refusal path with a deliberately nonexistent component as a second zero-launch backstop. If no such candidate exists, that negative observation is reported as not run.
3. Optional fixture repo byte verification and root discovery. Preflight all selected live cases and approvals before executing any. Budget 1–8 attempts; sequential one-component 8D dispatch; no retries. Stop at the first unexpected live outcome or evidence error. A QEMU process inventory check precedes and follows successful runs. If cleanup is uncertain, stop and inspect the worker before any later run.
4. Validate saved receipt checksums and links, exact source/root/adapter, runtime permission false, snapshot and dependency hash binding, actual dependency archive bytes (when present on the operator host during live audit), cleanup, watchdog, network denial, handoff counts, listener/HTTP or CLI evidence. Uses the existing 8D receipt validator plus separate cross-report checks. Saved report digests are consistency checks, not signatures or independent sandbox attestation.
5. Emit `campaign_result.json`, `coverage.md`, and all relevant nested plan/runtime JSON. Script bundles these plus pytest transcript into a review ZIP, excluding VM config, package archives, snapshots and raw process output. Runtime failures are grouped by review policy, dependency, toolchain, build, startup/smoke, worker boundaries, or unknown evidence failure. Attempt count is explicitly uncertain if no valid dispatcher receipt exists. The ledger distinguishes synthetic planning, real public candidate planning, operator-created fixture planning, and fresh reviewed smoke. Previous 8D Flask/Vite smoke results are not counted as fresh coverage. No rubric credit is claimed.

## Closeout conditions

A planning campaign passing is not a completed diverse live acceptance campaign. `*_WITH_GAPS` makes public planning discrepancies visible. `REVIEWED_SELECTED_CAMPAIGN_COMPLETE` means only the explicitly selected reviewed tests passed. `STOPPED_ON_GATE` requires inspection, not blind retry. No claims of unseen unrestricted execution, automated review, generalized npm/Yarn/pnpm lock generation, Python extras, all framework build configurations, service composition, database provisioning, native toolchain installation, functional verification, independent host isolation, or hard global disk bounds. Treat these as follow-up gaps. Phase 9 must bind actual functional oracle evidence before assigning scores.
