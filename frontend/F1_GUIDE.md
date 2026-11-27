# F1 — Local bridge integration and reliability

F1 extends the existing faculty UI and bridge. It does not modify the Executioner,
other agents, the rubric engine, dependency provisioning or VM policy. It calls the
already implemented Phase 9B-D evaluator for trusted demos, retaining the legacy
REVIEWED/RESUME composition and STATIC/report-only paths.

## Start locally

In the combined ProjectIQ checkout, with `.venv` active:

```powershell
$env:PROJECTIQ_BRIDGE_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(32))").Trim()
Write-Host "Local operator token: $env:PROJECTIQ_BRIDGE_TOKEN"
python frontend\bridge\server.py --root outputs\frontend_acceptance --vm-config vm_config.json --review-root outputs\executioner --enable-trusted-demo
```

A fresh token is generated for this bridge session. Keep it private; do not commit
it or paste it into chat. Do not start two bridge processes. The bridge accepts
only loopback host names, the configured browser origin, and the session token
(except an allowed-origin CORS preflight). There is no institutional authentication.
The token stays in the browser tab's session storage, not the Vite build. A stale
403 clears it; refresh and enter the new token. The dev server is bound to 127.0.0.1
with strict port 5173. Do not expose it using `--host 0.0.0.0`.

In a separate terminal:

```powershell
cd frontend
npm ci
npm test
npm run build
$env:VITE_BACKEND = "http"
$env:VITE_API_BASE = "http://127.0.0.1:8765"
npm run dev
```

Open `http://127.0.0.1:5173`, enter the token from the bridge terminal.
Use `http://127.0.0.1:5173` rather than `localhost` (different browser origin).
The mock frontend still works with `VITE_BACKEND=mock` and no token.
A bridge with no `--enable-trusted-demo` does not advertise automatic mode.
A bridge with no `--vm-config` allows static/report-only and approved imports.

## No-VM verification

```powershell
python -m unittest discover -s frontend\bridge\tests -v
python -m pytest -q tests/test_projectiq_phase9bd_bridge.py tests/test_executioner_phase9b_d.py
```

The first command covers admission policy and real HTTP/bridge integration with
mocked backend calls. The second reuses Phase 9 regression coverage. Run npm tests
and build from `frontend`. These commands do not require a VM.

## Live F1 acceptance: one Express evaluation

Create a synthetic text report if no existing Express report is available:

```powershell
$report = Join-Path $env:TEMP "ProjectIQ_F1_Express_Demo.txt"
@'
ProjectIQ trusted demonstration: Express CRUD posts application.
The application renders posts with Express and EJS. Its in-memory records are
identified by UUIDs. A user can create a post, inspect the list and the individual
record, update the content, and delete the record. The demonstration evaluates
only source-derived behavior and synthetic disposable state, not all requirements.
No research bibliography is required for this synthetic integration fixture.
'@ | Set-Content -LiteralPath $report -Encoding UTF8
```

New evaluation:

- Title: Express CRUD F1 Demo; submission ID: F1-EXPRESS-01
- Repository: https://github.com/bhs100/CRUD-Express
- Exact commit: 09364d67e2b30b8da663240a9cb40723c239959c
- Report: the synthetic report above; no references; research disabled
- Automatic evaluation (TRUSTED_DEMO); confirm operator trust and disposable state
- Objective only for this integration run (automatic rubric was already exercised
  in MiniCollab; this isolates the bridge and avoids unnecessary model calls)

No catalogs, import acknowledgment or manual VM budget should be required. Expect
one direct `projectiq_core evaluate` call, at most one VM, selected path ADAPTIVE,
3 independent passing tests and 9 workflow requests for the known source, if the
runtime is available. Confirm cleanup and temporary approval removal. These are
expected results, not guaranteed until live execution is verified.

The dashboard/report should show automatic mode and separate evidence. Detailed
coverage and operation visualization are F2. The original STATIC matrix is
preserved and must not be presented as the automatic test coverage. Research is
optional; generated observations are not independently reviewed grading credit.

## Stop semantics

"Stop after current command" sets a flag and does not kill a process. In composed
REVIEWED/RESUME, the current command completes before the next command is blocked.
In STATIC/TRUSTED_DEMO the whole evaluation is one unified command, which may
finish and save results despite the stop request. This is intentional. Do not
close the bridge or terminate its child processes while a run is active. No forced
process-tree teardown or immediate interruption is claimed. Cancellation tests use
mocked children; do not launch another VM just to test cancel.

One admission lock serializes evaluation launches, teacher writes and reassessments
within one bridge process. Concurrent operations receive HTTP 409. It is not a
cross-process/distributed lock or production service. The old native receipts and
rubric engine remain the authority. Live dashboard uses the saved engine assessment;
mock fixtures keep the existing TS scoring implementation. Legacy receipt provenance
is not promoted: bridge metadata only distinguishes work performed in this job.

## Acceptance artifacts

After the F1 Express evaluation, inspect (from root):

```powershell
$run = Get-ChildItem outputs\frontend_acceptance -Directory |
  Where-Object { $_.Name -like 'F1-EXPRESS-01_*' -and (Test-Path (Join-Path $_.FullName 'result.json')) } |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
$r = Get-Content (Join-Path $run.FullName 'result.json') -Raw | ConvertFrom-Json
$r.agents.executioner.details.trusted_demo | Select-Object status,selected_path,vm_start_attempts,vm_attempt_count_uncertain,functional_tests_executed,workflow_requests_attempted,temporary_approval_cleanup,original_config_unchanged
$r.agents.executioner.details.trusted_demo.functional_inventory.summary
$r.assessment | Select-Object status,proposed_points,reviewed_points,unresolved_criterion_ids
```

Keep the original result and artifacts private. For this synthetic report only,
share the result and executioner artifacts in a review ZIP (no token or local VM
config). The saved MiniCollab result can be used for grade/persistence regression
without launching another VM. Do not overwrite or erase its original receipts.

## Limits

This remains a trusted local operator MVP, not production execution for arbitrary
student code. Unsupported automatic stacks can return static or readiness-only
results; PHP/MySQL reviewed suites remain the richer path for MiniCollab. F2 adds
full evidence views; F3 handles layout, feedback and report polish. The installer
is guarded against unknown source modifications and does not commit or push.
