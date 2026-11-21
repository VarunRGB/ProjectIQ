# ProjectIQ Phase 3D — Higher-Quality Deterministic Test Plans

**Scope:** add-on to the already installed Phase 3A–3C package. Generates fewer,
better-grounded test scenarios; separates descriptive metadata from executable
claims; adds source-backed assertions when there is sufficient evidence.
**No LLM, installation, student code execution, or VM launch.**

## What's changed

- Output schema **1.1**, generator **0.2.0**. Existing Phase 1 manifest stays
  schema **1.0**. New `inventory` is an explicit collection for page source
  paths, existing tests, entrypoints, DB schemas, features, leftover Phase 1
  proposals, and source contracts. These do **not** inflate `test_cases` totals.
- Merge matching route and Phase 2 readiness evidence into **one test**.
  Route/proposal and feature/proposal duplicates are linked as provenance,
  not counted as extra tests. Distinct operation methods remain distinct.
- Only real source program entrypoints (not Dockerfiles/npm script names) become
  CLI scenarios, and only if no more specific API behavior exists.
- For the approved *static_html_v1* architecture, root-level HTML files
  detected in Phase 1 can have a `GET`/`200` status oracle. A verified exact-SHA
  checkout can additionally generate `HTTP_BODY_SHA256_EQUALS` to check raw
  response content, without recording text or credentials in the report.
- A bounded reader recognizes local **OpenAPI v3 JSON**, using literal `GET`
  routes with **explicit** `200` responses. Unsafe inputs, path parameters,
  authentication, external servers, method mutations, references and ambiguous
  context do **not** receive a runnable assertion.
- `contracts.validate_test_plan()` fails closed for duplicate IDs/HTTP
  targets, unsupported assertions, fake execution success, missing oracles and
  inconsistent summary counts. Phase 4 can reuse this schema gate, but must
  independently revalidate SHA, operator approval and VM policy.

**Terminology:** `READY_FOR_PHASE4` means *a declarative test specification*
contains a justified oracle and supported adapter. It does **not** mean its
future runner is implemented, its app has been executed, or its source has
been authorized as generally untrusted. `UNSUPPORTED_BY_RUNNER` also covers
cases with an oracle but without verified source provenance.

## Apply on top of your installed Phase 3A–3C

```powershell
cd C:\Users\varun\Code\ProjectIQ
$patch = "$HOME\Downloads\projectiq_phase3d_quality_upgrade.patch"

git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw "Patch check failed" }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw "Patch apply failed" }
git diff --check
if ($LASTEXITCODE -ne 0) { throw "Whitespace validation failed" }

python -m unittest `
    projectiq_executioner.tests.test_phase3_generator `
    projectiq_executioner.tests.test_phase3d_quality -q
if ($LASTEXITCODE -ne 0) { throw "Phase 3D regression failed" }
```

## Reuse existing saved Phase 1 manifests — no network or VM

```powershell
$base = "outputs/executioner/batch_20261121_205242"
python -m projectiq_executioner.phase3 `
    --manifest "$base/StaticSite/phase1/manifest.json" `
    --candidate "$base/StaticSite/phase2c_candidate.json" `
    --out outputs/executioner/phase3d_static_01

$r = Get-Content "outputs/executioner/phase3d_static_01/test_plan.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r.summary | Format-List
$r.test_cases | Select-Object id,category,status,title | Format-Table -Wrap -AutoSize
$r.inventory | ConvertTo-Json -Depth 8
```

For Express, omit Phase 2 candidate. The generator should still report API
cases, with lack of an execution adapter noted separately:

```powershell
python -m projectiq_executioner.phase3 `
    --repo "https://github.com/krnpatel88/nodejs-sample-api" `
    --ref "b6d84feb52e605a788ccafbddfb6d2dc3508bcae" `
    --out outputs/executioner/phase3d_express_01
```

Repository mode reuses the bounded GitHub ZIP and safe archive reader, and
performs *inert* source scanning while the pinned checkout exists. It can
produce new source-backed oracles if it finds suitable specs.

### Optional local checkout for extra source context

```powershell
python -m projectiq_executioner.phase3 `
    --manifest "path\to\manifest.json" `
    --candidate "path\to\phase2c_candidate.json" `
    --source-root "C:\path\to\repo" `
    --out outputs/executioner/phase3d_local_source_01
```

`--source-root` is not automatically tied to the pinned GitHub SHA. Its
OpenAPI information is included, but cannot become `READY_FOR_PHASE4` until
run through a verified pinned ingestion path. The API has a `source_verified`
argument for trusted callers that performed this verification; it must not
be set merely because a path was provided. Phase 4 still validates provenance.

## Output JSON

Top-level schema 1.1 includes the original `test_cases` and `summary` plus
`inventory`. Each test retains `id`, `title`, `category`, `target`, `status`,
`assertions`, `oracle`, `evidence`, `evidence_kinds`, `required_adapter`,
`blockers`, `execution`, and now `related_proposals`.

`summary.inventory_counts` lists the separate artifacts. In a repo with
feature lists but no grounded behavior, status is `DISCOVERY_ONLY`, not
an artificial collection of executable "tests".

New assertion kind: `HTTP_BODY_SHA256_EQUALS` (hex digest of exact static
file bytes). A future Phase 4 executor must **explicitly implement it**,
compare the decoded response entity bytes under a fixed size cap, and never
trust a student's test script or source content to execute.

**No grading claim:** `/index.html` HTTP 200, an OpenAPI GET 200 and static
content integrity do not prove business correctness. HTTP status expectations
based on OpenAPI are the project's *declared behavior*, not observed behavior.

## Quality and security checks

- Regressions include stability under input reordering, duplicate merges,
  schema validation, missing adapters, source provenance, oversized JSON,
  path traversal, symlinks, auth/required parameters/external-server OpenAPI
  exclusions, static HTML hashes and no raw page text leakage.
- Deliberately do not guess valid user IDs, login credentials, request bodies,
  expected database state, CSRF tokens or success responses from route names.
- **Current restrictions remain:** operator-approved exact SHA for live VM
  execution, no arbitrary untrusted execution attestation, and no Phase 4
  test runner yet. This patch changes only Phase 3 planning.
