# ProjectIQ Phase 4C-B — Source-Grounded Functional Oracle Evidence

## Scope and non-goals

Phase 4C-B is a **read-only, deterministic evidence extractor** for Phase 3-discovered HTTP test targets. It **does not** execute student code, provision data, generate credentials, issue HTTP requests, launch QEMU, revise the Phase 3 JSON contract, or promote any response hint to a runnable assertion. It is not a parser or semantic verifier for arbitrary programming languages.

The output is `source_evidence.json` (`schema_version: 0.1`). Each entry is bound to a Phase 3 test ID and repository SHA. The report is advisory, not an approved oracle. The Phase 4C-A oracle catalog remains the editable source of explicit operator expectations, and 4C-C will implement reviewed execution.

## Supported evidence

- **PHP file endpoints**: literal `$_POST['name']`, `$_GET['name']`, `$_REQUEST['name']`, `filter_input(INPUT_POST, 'name')`; literal fields accessed through a directly bound `json_decode(file_get_contents('php://input'), true)` array (or decoded object); `http_response_code(NNN)`; redirects, session access, candidate SQL mutations and inline JSON object keys.
- **Flask/FastAPI-style Python handlers**: explicit route decorators and `request.form`, `request.args`, `request.json` field accesses; explicit `status_code`, `abort(NNN)` or return tuple status; JSON response keys, session and possible database mutation hints.
- **Express-style JS/TS handlers**: exact route method and literal path anchors, `req.body`, `req.query`, `req.params` access, destructured fields, `res.status(NNN)`, `res.sendStatus(NNN)`, JSON key names, possible database mutations.
- **OpenAPI 3 JSON**: exact path/method matching; inline parameters, JSON/form request properties, explicitly documented response codes and JSON property names. External `$ref` is never followed; missing/complex schemas are marked unresolved.
- **Other stacks**: valid but unrecognized syntax yields a **partial or no-evidence report**. It never causes an invented oracle.

All field names, status codes and source lines are treated as **observations**, not verified runtime behavior. Observed status codes may represent *different branches of the same handler*. Status 201 observed in a source line is not proof that every request should return 201.

## Safety and scope boundaries

1. Require a valid Phase 3 schema 1.1 test plan. Only discovered targets can receive hints.
2. Source must come from an explicitly supplied checkout (`--source-root`, *unverified*) or a bounded GitHub download re-resolved to the **same pinned SHA** in the plan (`--repo`/`--ref`, *verified snapshot provenance*).
3. Analyze only literal repository-evidence files (plus a direct PHP route file), and a handful of known/indexed OpenAPI JSON files. No recursive scanning of the entire checkout, remote `$ref`, imports, execution or dependency resolution.
4. File access rejects symlinks, traversal, oversized or non-UTF-8 sources. Source inspect budget: 250 KB per file, 96 unique source paths, six code files per target, 400 test scenarios, 120 lines per route handler. Exceeded/missing information stays unresolved.
5. Multi-handler JS/Python files require an unambiguous **literal path and matching method** anchor before request/response evidence is attributed to a route. Arbitrary source files without an anchor cannot produce route-specific assertions.
6. Output stores source filenames, SHA-256 hashes, line numbers, field **names**, limited status codes and enum hints—not full source text, credentials, literal JSON values, SQL queries, redirect locations or HTTP bodies.
7. Output has `execution: NOT_PERFORMED`, `oracle_promotion: NOT_PERFORMED`, `operator_approval: NOT_EVALUATED`, no `test_bundle`, and no execution authorization. Even `snapshot_verified: true` does not confer execution safety.

## Install

From `C:\Users\varun\Code\ProjectIQ`, on `executioner` after pushing Phase 4C-A:

```powershell
$patch = "$HOME\Downloads\projectiq_phase4cb_source_grounded_evidence.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw "Patch incompatible; don't force" }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw "Patch failed" }
git diff --check
python -m unittest projectiq_executioner.tests.test_phase4cb_source_evidence -q
if ($LASTEXITCODE -ne 0) { throw "4C-B tests failed" }
```

## Run against the exact pinned PHP/MySQL repository

This uses the existing Phase 3 plan and fetches the *exact pinned snapshot*, without executing it:

```powershell
$base = Get-ChildItem "outputs/executioner" -Directory |
  Where-Object { $_.Name -like "phase4a_approved_*" } |
  Sort-Object Name -Descending |
  Select-Object -First 1 -ExpandProperty FullName

$planPath = "$base/phase3/test_plan.json"
$p3 = Get-Content $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
$repo = "https://github.com/$($p3.source.repository)"
$sha = $p3.source.commit_sha

$out = "outputs/executioner/phase4cb_php_$(Get-Date -Format yyyyMMdd_HHmmss)"
python -m projectiq_executioner.phase4.source_oracle_stage `
  --plan $planPath `
  --repo $repo `
  --ref $sha `
  --out $out
if ($LASTEXITCODE -ne 0) { throw "Source analysis failed (no execution attempted)" }

$r = Get-Content "$out/source_evidence.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$r.summary | Format-List
$r.targets | Select-Object path, status, `
  @{Name='Inputs';Expression={$_.inputs.Count}}, `
  @{Name='ResponseHints';Expression={$_.response_signals.Count}}, `
  @{Name='Requirements';Expression={$_.requirements -join ', '}} |
  Format-Table -Wrap -AutoSize

$r.targets | Where-Object { $_.path -eq '/logic/add_task.php' } |
  ConvertTo-Json -Depth 8
```

For a **pre-existing local checkout** rather than a new network download:

```powershell
python -m projectiq_executioner.phase4.source_oracle_stage `
  --plan $planPath --source-root "C:\path\to\the\repository" `
  --out "outputs/executioner/phase4cb_local_$(Get-Date -Format yyyyMMdd_HHmmss)"
```

Local-checkout results are marked `snapshot_verified: false`; they are still useful advisory evidence for development but must **not** be promoted directly to execution. Never manually edit this flag.

## Interpretation

- `EVIDENCE_FOUND`: at least one source input, explicit response signal or prerequisite was found for a target. Not a PASS, and not proof of correctness.
- `SOURCE_INSPECTED_NO_CONTRACT`: at least one bounded source was inspected but no confidently target-bound behavior was found.
- `SOURCE_UNAVAILABLE`: source file missing/rejected, unsupported evidence format or no attributable source. Route parameters might still be listed as discovered names.
- `NON_HTTP_NOT_ANALYZED`: intentionally outside 4C-B's HTTP scope.
- `DECLARED_STATUS`: literal code/status declaration; may appear only in a conditional branch.
- `JSON_KEY_POSSIBLE`: candidate JSON property name; does not assert the value or presence on all branches.
- `FORM`, `JSON`, `JSON_OR_FORM`, `QUERY`, `PATH`: observed input access modes; `UNKNOWN` means requiredness is *not* established. Explicit inline OpenAPI fields can be `REQUIRED` or `OPTIONAL`.

Example: a PHP task handler may read `$_POST['task_title']`, write to SQL and redirect. The report can identify `task_title` and note likely database state and a redirect, **but cannot claim task creation succeeded**, or that a missing title should return 422. Those assertions require trusted test data, operator review and execution in 4C-C/4C-D.

## Limits and follow-on

This is not full AST/CFG analysis, data-flow tracing, automatic schema dereferencing or a proof engine. It will miss dynamic routes, middleware-based status codes, multiline/indirect request access, Django controllers and some frameworks. Complex handlers must be represented as unresolved or reviewed manually. Support for URL-encoded POST bodies, session/cookie lifecycles and MySQL fixture operations belongs to **4C-C/4C-D**; 4C-A currently validates JSON bodies only.

Do not commit raw checked-out source, local VM credentials, generated output folders or operator-owned fixture secrets. Keep the Phase 4B VM code and 4C-A oracle catalog unchanged while validating 4C-B.

### Regression targets

Run the isolated suite and the pinned PHP source check. Expected offline tests only: `python -m unittest projectiq_executioner.tests.test_phase4cb_source_evidence -q`. Phase 3/4A/4C-A tests should continue passing; Phase 2 runtime integration files are not modified. This implementation has **not** run student code or a VM.

## PHP JSON-body evidence

For direct literal PHP JSON decoding, report field names with `location: JSON`
(e.g. `title`, `deadline`) and `required: UNKNOWN`. This is not evidence
that the values are valid, that a request can succeed without an authenticated
session, or that an HTTP status is correct. Dynamic indices or unrecognized raw
request-body parsing are marked `DYNAMIC_REQUEST_INPUTS_UNRESOLVED` instead
of inventing fields. No request body or secret value is copied into the report.
