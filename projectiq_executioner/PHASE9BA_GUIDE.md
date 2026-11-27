# Phase 9B-A — Source-bound functional test compilation

This is a planning-only compiler, not a runner or an admission path. It changes no existing executioner production files. All current trusted-demo and ordinary reviewed execution restrictions remain unchanged. A GET method is not a security guarantee: the future runner must use isolated disposable state and explicit reviewed admission. Do not expose this local workflow as a service for arbitrary student submissions.

## Run (Windows PowerShell, ProjectIQ root)

Use the existing 9A `functional_plan.json`, the operator-selected GitHub URL and an exact 40-character SHA. Do not take the URL or ref from an untrusted plan automatically. The output must be a new directory.

```powershell
$nodeOut9 = "outputs\executioner\phase9a_node_20261127_124547718"
$nodeOut9BA = "outputs\executioner\phase9ba_node_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase9b --repo "https://github.com/bhs100/CRUD-Express" --ref 09364d67e2b30b8da663240a9cb40723c239959c --from-plan "$nodeOut9\functional_plan.json" --out $nodeOut9BA
```

Expected: `COMPILED_TESTS_AVAILABLE`, 8 proposed cases, 3 compiled tests, 5 deferred cases, 1 deferred workflow, 0 VM attempts, 0 executed tests.

```powershell
$pythonOut9 = "outputs\executioner\phase9a_python_20261127_124636060"
$pythonOut9BA = "outputs\executioner\phase9ba_python_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase9b --repo "https://github.com/render-examples/flask-hello-world" --ref facfc73c44b35d00bb1f72e8986f0ea47cab0755 --from-plan "$pythonOut9\functional_plan.json" --out $pythonOut9BA
```

Expected: 1 proposed and compiled test, zero deferred cases/workflows, 0 VM attempts. GitHub acquisition is required; repository code and dependencies are not executed. The compiler first checks the saved plan's checksum and operator-selected source identity, then performs a fresh pinned checkout and regenerates the complete 9A plan via the existing planner. Exact canonical equality (not just hash validity or Python object equality) is required. A stale plan must be regenerated and reviewed, not edited or re-signed. If the compiler refuses, no `compiled_plan.json` is published; the fresh output directory may contain rederived diagnostic reports. Use a new directory for any subsequent attempt. The original 9A output remains unchanged.

## Contract (`9BA.1`)

`compiled_plan.json` binds source, original functional plan digest, Phase 3 digest, runtime plan digest and its own canonical digest. `rederived/` contains the fresh 9A, runtime, Phase 3 and Phase 1 artifacts. The rederived runtime plan supplies component ID, adapter and guest service port. Groups are independent; they are not a composed service or authorization to launch multiple VMs. Supported adapters: `NODE_HTTP`, `PY_FLASK`, `PY_ASGI`, `STATIC_HTTP`. All eligible cases require GET with a static local path, no request body, no dynamic parameters, no caller-specified headers/cookies, no query strings, no blockers, and no reported planner issues. Each request is guest-loopback only (the eventual runner must use its own verified service identity, not a report-provided host), no redirect following, no retries, sequential, 5-second timeout and 256 KiB response limit. 32 tests per component, 64 total; overflow is deferred and never silently counted as pass. These are proposed bounds for 9B-B; 9B-A does not execute or enforce HTTP at runtime.

The assertion vocabulary is restricted to:

- `HTTP_STATUS_EQUALS`: exact status integer; every compiled test has one status assertion.
- `BODY_TEXT_EQUALS`: exact UTF-8 text, no stripping or whitespace normalization.
- `JSON_EQUALS`: bounded strict JSON (no duplicate keys or nonfinite numbers); compare canonical JSON values, preserving type distinctions.
- `HTML_TEXT_CONTAINS`: text marker, not raw markup search. Future runner must parse text with normalized whitespace, exclude non-content such as script/style and comments, and distinguish this from browser visibility or interaction.
- `HTTP_BODY_SHA256_EQUALS`: exact response entity bytes using identity encoding; no implicit rewriting or decompression that changes meaning.

Implementation-derived cases require a content assertion in addition to status. Repository-declared OpenAPI GET/200 contracts can be status-only and remain explicitly labeled as repository declarations, not independent teacher requirements. Conflicts, dynamic bindings, missing oracles, unsupported stacks, mutation requests and workflows are deferred with reasons. Nothing is counted as executed or graded. The compiler does not bind generated CRUD IDs or values; 9B-C must implement those requirements. Saved reports are consistency evidence, never permission or security attestation. 9B-B must revalidate the plan and source at its actual execution boundary, independently authorize and verify the worker, enforce limits and report observed results. No full application correctness inference from these narrow assertions.

## Tests

```powershell
python -m pytest -q tests/test_executioner_phase9a.py tests/test_executioner_phase9b_a.py
```

The new tests use fixture source checkouts (no network, no VM) and the real Phase 9A planner. They include re-signed tampering, stale source, malformed JSON, exact-ref requirement, Flask, FastAPI, static HTTP, Express, OpenAPI conflicts, oracle type validation, test budgets, determinism and deferred workflows. The two public acceptance compilations are still required on the operator's machine. No new dependencies. Keep output reports local.
