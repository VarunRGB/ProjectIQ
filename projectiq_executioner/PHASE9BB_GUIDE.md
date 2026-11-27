# Phase 9B-B — reviewed independent HTTP functional evidence

Local operator-selected trusted repositories only. Never expose to an untrusted student submission API, unattended job or shared worker. Does not qualify the Windows host for untrusted projects. Existing Phase 8D approval, restricted networking, guest UID/cgroup, disk watchdog and disposable cleanup remain mandatory. No grade is calculated. Phase 9B-A remains planning-only.

## Invocation (PowerShell, ProjectIQ root, activated venv)

```
$nodeCompiled = 'outputs\executioner\phase9ba_node_20261127_125910927\compiled_plan.json'
$nodeOut9BB = "outputs\executioner\phase9bb_node_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase9b.run_http --repo 'https://github.com/bhs100/CRUD-Express' --ref 09364d67e2b30b8da663240a9cb40723c239959c --from-compiled $nodeCompiled --trust-demo-source --vm-config vm_config.json --out $nodeOut9BB
Get-Content "$nodeOut9BB\functional_result.json"
```

```
$pythonCompiled = 'outputs\executioner\phase9ba_python_20261127_125952614\compiled_plan.json'
$pythonOut9BB = "outputs\executioner\phase9bb_python_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase9b.run_http --repo 'https://github.com/render-examples/flask-hello-world' --ref facfc73c44b35d00bb1f72e8986f0ea47cab0755 --from-compiled $pythonCompiled --trust-demo-source --vm-config vm_config.json --out $pythonOut9BB
Get-Content "$pythonOut9BB\functional_result.json"
```

Run sequentially. Existing `phase8f_demo_assets.local.json` beside VM config supplies pinned Node and pip assets. An explicit alternate profile can be supplied with `--profile`. Single selected component only; `--component` accepts its ID or root. `--require-bounded-volume` retains existing optional stronger local volume gate. No automatic retries. New dependencies are acquired by the existing reviewed dispatch path; no dependency changes to host Python. No extra VM for individual test cases. No source or config modifications.

## Contract and accounting

The saved compiled plan must match fresh Phase 1, 3, 8D, 9A and 9B-A results from the exact same dispatch checkout before dependency fetching or VM startup. The test subset has a fixed loopback target (actual component port), GET only, no body, cookie, query, user header, redirect or retry. Max 32 selected tests, 5 assertions per test, 256 KiB response bytes, 5-second Linux main-thread wall timer per attempted test. Oversized total guest request is rejected before VM startup. No raw response content or headers exported. Non-identity content encoding, truncated responses, transport failures, decoding failures and parsing failures are explicit; they are not silently converted to pass. HTML uses normalized parser text, not browser visibility/layout. JSON comparison uses canonical serialization (type-sensitive). HTTP status assertion is verified independently from the guest row's status code by host; content comparison takes place in the trusted guest controller. Worker evidence is not cryptographic remote attestation.

Tests execute after readiness and application UID listener verification, before application shutdown, in the same managed offline runtime. If application/listener unavailable before a test, it is NOT_RUN. A false assertion is FAIL; unavailable assertion is INCONCLUSIVE unless another assertion already definitively fails. All matched is PASS. `functional_tests_executed` counts attempted test requests (including transport failures); `summary.NOT_RUN` counts unattempted cases. `FUNCTIONAL_EVIDENCE_COMPLETE` means a validated result receipt, NOT necessarily all tests passed. CLI exit 0 means evidence collection completed; inspect summary for test outcomes. Failed readiness, worker cleanup, invalid receipt, or config integrity means INCOMPLETE; counts may be uncertain, and no functional result is promoted. No false grade credit. Deferred and other-component counts are explicit. No adaptive mutation, auth, DB, browser, CLI or cross-component workflow evaluation yet.

## Offline regression

```
python -m pytest -q tests/test_executioner_phase9a.py tests/test_executioner_phase9b_a.py tests/test_executioner_phase9b_b.py
```

39 new tests in this patch, one real Linux timer control skips on Windows; other local loopback tests use socket timeouts on Windows (no mock of application responses). The actual guest is Linux. Controls include incorrect body/status, redirects, malformed/duplicate-key JSON, HTML hidden text, body hash, large/encoded/truncated response, invalid UTF-8, transport deadline, unsafe paths, duplicate/budget rejection, tampered receipt, source rederivation, dispatcher fail-closed before fetching, smoke-only compatibility, same-VM ordering and synthetic worker failure evidence. No QEMU launched by these tests. Live positive acceptance is still required.

## Expected live acceptance

Express: PASS 3, FAIL 0, INCONCLUSIVE 0, NOT_RUN 0, deferred 5 cases and one workflow. Flask: PASS 1, all other outcomes 0, no deferred cases. Each exactly one VM, cleanup `VM_STOPPED_DISPOSABLE_STORAGE_REMOVED`, original config unchanged, temporary approval removed, automatic untrusted execution false. The regression negative control is not a live VM negative-control attestation; no extra VM launch is required for the agreed positive acceptance. Do not change an expected oracle in a saved plan to produce a failure; rederivation must reject that.

Keep output private; dependency bundles and source-derived artifacts may be large or sensitive. Review archives should contain the two run directories, never vm_config.json or local asset profile. Do not commit generated outputs. For any INCOMPLETE result, inspect nested dispatch_result.json and runtime_result.json before retrying. Phase 9B-C will handle adaptive binding and mutations; 9B-D will integrate coverage and faculty-facing evidence.
