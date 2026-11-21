# ProjectIQ Phase 4C-A — Functional Oracle & Fixture Contract

**Status:** offline contract and review staging only. **Not a VM or HTTP runner.**

## Why a separate contract?

Phase 3 schema 1.1 discovers independent HTTP targets and produces only those
oracles justified by repository evidence. Phase 4A/4B preserves that trust
boundary and executes only the existing limited GET assertions in a reviewed VM.
Phase 4C-A adds a **separate operator-authored, explicitly unreviewed** catalog
for functional behavior that cannot be inferred reliably from the source.

An author can supply multiple scenarios for one Phase 3 test ID (for example,
valid creation returning HTTP 201 and invalid input returning HTTP 422). This
catalog is deliberately **not accepted by the 4B guest interpreter**.

## Files

- `phase4/oracle_contracts.py`: version `0.1` input validation, deterministic
  IDs, fixture placeholders, discovered-route binding, bounded expected JSON
  subtrees. No arbitrary code, URL, port, command or runtime-provided headers.
- `phase4/oracle_stage.py`: offline template and review staging CLI. Strict,
  bounded JSON input; rejects duplicate JSON keys and nonfinite numbers;
  supports PowerShell UTF-8 BOMs; writes each evidence file once.
- `tests/test_phase4c_oracle_contracts.py`: adversarial tests and cross-stack
  contract validation. No student process, network or QEMU involved.

## Exact oracle catalog format

```json
{
  "schema_version": "0.1",
  "source": {
    "kind": "github",
    "repository": "Example/ApprovedRepo",
    "commit_sha": "<40-hex-digit-SHA-from-Phase3>"
  },
  "cases": [
    {
      "name": "Create task returns created",
      "test_id": "<EXISTING-PHASE3-TEST-ID-FOR-POST-/api/tasks>",
      "request": {
        "path_params": {},
        "query": {},
        "json_body": {"title": "Fixture task"}
      },
      "expected": {
        "status": 201,
        "json_subset": {"created": true}
      }
    }
  ]
}
```

**These expected values are illustrative only.** Never paste them into a real
project without checking the application's documented behavior or an
instructor-reviewed reference specification.

For route `GET /api/tasks/:id`, use `"path_params": {"id":"123"}`; the
operator cannot override the discovered HTTP method or target path. Query
values are URL-encoded deterministically. For a login payload, use a symbolic
placeholder such as `"password":{"$fixture_ref":"test_password"}`. Do NOT
put actual passwords, API keys, bearer tokens or real personal data in a
catalog. Fixture values are *not* provisioned in 4C-A.

Supported status expectations in the initial contract: `200, 201, 202, 204,
400, 401, 403, 404, 409, 422, 429`. `json_subset` is an optional JSON
object; nested objects use subset matching and arrays will require exact
comparisons once an executor exists. HTTP 204 cannot have an expected JSON
body. GET and DELETE cannot have request bodies in this initial contract.

Each catalog is limited to 64 scenarios. JSON input, string, structure,
query, path, fixture-reference and report sizes are bounded. Unknown fields
are rejected, rather than interpreted or silently ignored.

## 1. Generate an editable template for a real saved Phase 3 plan

From the root of your ProjectIQ checkout, after applying the patch:

```powershell
$base = Get-ChildItem "outputs/executioner" -Directory |
    Where-Object { $_.Name -like "phase4a_approved_*" } |
    Sort-Object Name -Descending |
    Select-Object -First 1 -ExpandProperty FullName

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$templateOut = "outputs/executioner/phase4ca_template_$stamp"

python -m projectiq_executioner.phase4.oracle_stage `
    --plan "$base/phase3/test_plan.json" `
    --template `
    --out $templateOut

$targets = Get-Content "$templateOut/eligible_targets.json" -Raw -Encoding UTF8 |
    ConvertFrom-Json
$targets.eligible_targets | Format-Table test_id, method, path, status -AutoSize
```

A target's presence does **not** establish the expected result. Non-HTTP
operations and unresolved source semantics remain excluded honestly.

## 2. Inspect and populate the catalog

Edit `$templateOut/oracle_template.json` and add one or more `cases` referencing
IDs from `eligible_targets.json`. Preserve the `source` object byte-for-byte in
meaning, and verify every expected result against operator-owned requirements.
**Do not manufacture expected responses just to make a test runnable.**

A safe smoke check may reuse an already-grounded, existing `GET
/public/login.html → 200` assertion from the approved PHP/MySQL repository,
provided it really appears in the target list. That proves **staging** only;
it does not increase meaningful functional coverage.

Use a NEW output folder to stage:

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stageOut = "outputs/executioner/phase4ca_review_$stamp"
python -m projectiq_executioner.phase4.oracle_stage `
    --plan "$base/phase3/test_plan.json" `
    --oracles "$templateOut/oracle_template.json" `
    --candidate "$base/phase2/phase2c_candidate.json" `
    --vm-config "vm_config.json" `
    --out $stageOut

$r = Get-Content "$stageOut/oracle_review.json" -Raw -Encoding UTF8 |
    ConvertFrom-Json
$r | Select-Object status, execution, oracle_review, launch_authorization, runner_support
$r.summary | Format-List
$r.scenarios | Select-Object id, name, phase3_test_id, fixture_refs | Format-Table
```

An empty template produces `NO_SCENARIOS`. An operator-approved pinned SHA
with at least one valid scenario produces `STAGED_FOR_REVIEW`—**not**
`READY_FOR_PHASE4`, and never permission to run. A missing candidate or
config produces `REVIEW_REQUIRED`; an explicitly unapproved SHA is `BLOCKED`.

For a source without a supported Phase 2 adapter, omit `--candidate` and
`--vm-config`. The contract can still be validated as `REVIEW_REQUIRED`, but
**no execution is enabled**. This is intentional cross-repository support.

## 3. Regression tests

```powershell
python -m unittest projectiq_executioner.tests.test_phase4c_oracle_contracts -q
if ($LASTEXITCODE -ne 0) { throw "Phase 4C-A contract tests failed" }

python -m unittest `
    projectiq_executioner.tests.test_phase3_generator `
    projectiq_executioner.tests.test_phase3d_quality `
    projectiq_executioner.tests.test_phase3e_benchmark `
    projectiq_executioner.tests.test_phase4a `
    projectiq_executioner.tests.test_phase4b `
    -q
if ($LASTEXITCODE -ne 0) { throw "Phase 3/4 regression failed" }
```

No VM acceptance run is required to validate 4C-A: it makes no changes to the
VM lifecycle. The 4C-B/C stages **must** add a separately validated guest-only
functional runner (explicit POST/GET support, bounded JSON parser and response
comparison), oracle review approval, and a runtime fixture provisioning
contract before they may execute these scenarios.

## Closeout criteria

- Valid and invalid oracle catalogs are differentiated consistently.
- Route and method are bound to the exact Phase 3 discovery record and SHA.
- Duplicate scenarios, unsupported methods/statuses, embedded secrets and
  arbitrary commands are rejected.
- No oracle is silently inferred or treated as verified by the student source.
- Reviewed source snapshot approval is not confused with oracle approval.
- All generated output clearly says `NOT_PERFORMED` / `NOT_GRANTED`.
- Phase 3 and Phase 4B contracts remain unchanged.
