# Phase 9B-C — source-bound adaptive HTML CRUD lifecycle (local trusted demo)

## Scope and reuse

- Phase 9A discovers lifecycle proposals; 9B-A's canonical compiled plan remains unchanged. Phase 9B-C rederives both from a fresh pinned checkout, then compiles one supported lifecycle for the selected NODE_HTTP component before dependency acquisition or VM launch.
- The existing Phase 4D-A guest_workflow `_select` bounded row selector is reused unchanged by adapting HTML observations into its row contract. The legacy Phase 4D-A/4D-B executors remain unchanged: their reviewed PHP/MySQL fixtures, integer IDs, session and database witnesses are not compatible with the Express/EJS lifecycle. Their existing behavior is not weakened.
- 9B-B's HTTP transport has been factored into one bounded exchange shared by independent and adaptive tests. The existing 8D worker, VM admission, storage and cleanup gates remain in use. No new dependencies or application commands.
- Initial support: direct CommonJS Express routes, form-urlencoded single synthetic value, `id` parameter, in-memory array declaration, source-grounded EJS list loop with adjacent escaped `h3` id / `p` value, detail `h1` id / `p` value, direct GET, POST, PATCH/PUT and DELETE with redirect to the list. The compiler checks the source's direct render and collection binding; this is a deliberately narrow heuristic, not a full JavaScript/EJS parser. Unsupported shapes are refused before acquisition. No claims of generic CRUD, auth, DB, or browser coverage.
- Explicit local operator trust **and** confirmation that all state is disposable and there are no external side effects are mandatory. This confirmation is not independently established by the compiler. Do not expose this entrypoint to students, an HTTP API or shared unattended grading. Existing production untrusted-worker qualification remains deferred.

## Workflow

Run the independent GET suite first, then perform nine bounded workflow observations in the same live guest. Each request uses loopback, no cookies, no retries, no redirects, 5-second wall deadline and 256 KiB response cap. No captured IDs, input values or raw bodies are exported. Verify the listener UID/availability before each request. One newly generated unique value and a distinct update value are used. The ID is taken only from a uniquely correlated new record, never guessed or accepted from an operator input. Compare the observed list mapping to the initial baseline, including all visible unrelated records, after create, after update, and after delete. Stop on the first failed/unavailable workflow observation; disposable VM teardown remains mandatory. Mutation redirects must be 302/303 with exactly one local Location equal to the source-derived list path. Redirects are not followed.

| Role | Request | Required observation |
|---|---|---|
| BASELINE | GET list | Well-formed list; unique synthetic values absent |
| CREATE | POST create | Local redirect to list |
| LIST_CREATED | GET list | Exactly one new matching record, no baseline change |
| READ_CREATED | GET captured ID | Captured ID and created value match |
| UPDATE | PATCH/PUT captured ID | Local redirect to list |
| READ_UPDATED | GET captured ID | Same ID and updated value match |
| VERIFY_UPDATED_LIST | GET list | Updated mapping plus unchanged baseline |
| DELETE | DELETE captured ID | Local redirect to list |
| VERIFY_REMOVED | GET list | Exact observed baseline restored |

PASS means every observation matched; FAIL means a definite mismatch; INCONCLUSIVE means unavailable/unparseable evidence; NOT_RUN means availability was lost before the next request. The workflow receipt contains the attempted prefix only, and validates order, count, status, and contract digest. A failed workflow can still have complete evidence. It is not proof of independent functional correctness or database state. `functional_tests_executed` remains the independent GET count for compatibility; `workflow_requests_attempted` and `workflow_status` separately describe adaptive execution. Deferred standalone cases are not automatically marked passed. `grade_credit` remains null.

## Install and test

From ProjectIQ root:

```powershell
python "$HOME\Downloads\ProjectIQ_Phase9BC\apply_9bc.py" --repo (Get-Location).Path
python -m pytest -q tests/test_executioner_phase9a.py tests/test_executioner_phase9b_a.py tests/test_executioner_phase9b_b.py tests/test_executioner_phase9b_c.py
```

Expected Windows: 128 passed, 1 skipped (Linux-specific independent wall timer control). Linux: 129 passed. If tests fail, stop before launching a VM.

## Live acceptance (one VM)

Reuse the existing Express compiled plan, not the Flask plan. Review and trust the exact source and its disposable in-memory state before using the two flags. Do not edit the source, plan or expected values to make it pass.

```powershell
$nodeCompiled = "outputs\executioner\phase9ba_node_20261127_125910927\compiled_plan.json"
$nodeOut9BC = "outputs\executioner\phase9bc_node_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase9b.run_adaptive --repo "https://github.com/bhs100/CRUD-Express" --ref 09364d67e2b30b8da663240a9cb40723c239959c --from-compiled $nodeCompiled --trust-demo-source --confirm-disposable-state --vm-config vm_config.json --out $nodeOut9BC
$r = Get-Content "$nodeOut9BC\functional_result.json" -Raw | ConvertFrom-Json
$r | Select-Object status,workflow_status,workflow_requests_attempted,functional_tests_executed,vm_start_attempts,vm_attempt_count_uncertain,temporary_approval_cleanup,original_config_unchanged
$r.evidence.workflow.steps | Format-Table role,status,http_status,matched,error_code -AutoSize
```

Expected: FUNCTIONAL_EVIDENCE_COMPLETE, workflow PASS, 9 workflow requests, 3 independent GET tests, 1 VM attempt, temporary approval REMOVED, original config unchanged. All nine workflow rows PASS. If unavailable or failed, inspect the first non-PASS role and saved dispatch report; do not retry automatically. The exit code is nonzero if a workflow or independent test is not PASS, even when valid evidence is complete.

```powershell
$reviewZip9BC = Join-Path $HOME 'Downloads\Phase9BC_Review.zip'
Compress-Archive -LiteralPath $nodeOut9BC -DestinationPath $reviewZip9BC -Force
Get-Item -LiteralPath $reviewZip9BC | Select-Object FullName,Length
explorer.exe /select,"$reviewZip9BC"
```

Keep reports local. Do not commit output directories, `vm_config.json`, reusable local asset profiles, credentials, or dependencies. Phase 9B-D should reconcile workflow evidence with the existing Phase 3 matrix and Phase 6 diagnoses rather than claim standalone cases were executed or grade source-derived expectations as independently correct.
