# ProjectIQ Phase 4A — Trusted test handoff and guest HTTP contract

## Scope and threat boundary

Phase 4A consumes **Phase 3 `test_plan.json` schema 1.1** and a matching Phase 2C VM candidate. It produces a **Phase 4 `phase4_handoff.json` schema 0.1**, ready for future trusted integration into the existing reviewed-fixture QEMU runner.

**Phase 4A does not launch a VM, install dependencies, evaluate student code or execute generated test cases.** The standalone `guest_http.py` contains a trusted HTTP interpreter, currently tested **only against a harmless in-process HTTP fixture**. Phase 4B will copy that reviewed file to the Linux guest and invoke it while the reviewed application's HTTP server is alive.

The existing Phase 2 exact SHA approval, pinned base image, cgroup enforcement, no-egress guest isolation, cleanup/recovery and security attestation restrictions are unchanged. A handoff status of `STAGED_NOT_EXECUTED` is **NOT** permission to bypass Phase 2's checks. Arbitrary untrusted submissions remain unsupported.

## Implementation

- `phase4/handoff.py`: validates the whole Phase 3 contract, pins source and adapter to Phase 2C, checks exact `owner/repo@sha` operator review, selects up to 16 supported GET tests, tracks excluded unresolved scenarios, generates a canonical request bundle and hashes the inputs. Does not update `vm_config.json`.
- `phase4/guest_http.py`: self-contained guest-only declarative GET runner (no dynamic Python/shell/JS, npm scripts, external URLs, redirects, cookies, form bodies or arbitrary headers). Contacts **only `127.0.0.1:<guest-assigned-port>`**, uses 3-second per-request socket timeout by default, limits response bodies to 1 MiB, and never persists response text.
- `phase4/report_contracts.py`: validates the guest's strict JSON report against the exact request, detecting missing/duplicate test IDs, unknown fields, inconsistent verdicts, false status/hash assertions and incorrect summaries. **It does not independently attest VM execution.**
- `phase4/__main__.py`: CLI; returns non-overwriting `phase4_handoff.json` and safe `INPUT_ERROR` report for malformed inputs.
- `tests/test_phase4a.py`: offline, deterministic tests and a trusted loopback HTTP fixture.

## Test command

From the ProjectIQ repository root in PowerShell:

```powershell
python -m unittest projectiq_executioner.tests.test_phase4a -q
if ($LASTEXITCODE -ne 0) { throw 'Phase 4A tests failed; do not push' }
```

## Stage a previously reviewed, SHA-pinned repository

Use the existing Phase 3 and Phase 2 reports for **the exact same GitHub source SHA**:

```powershell
python -m projectiq_executioner.phase4 `
  --plan 'outputs/executioner/phase3d_static_01/test_plan.json' `
  --candidate 'outputs/executioner/batch_20261121_205242/StaticSite/phase2c_candidate.json' `
  --vm-config 'vm_config.json' `
  --out 'outputs/executioner/phase4a_static_01'

$r = Get-Content 'outputs/executioner/phase4a_static_01/phase4_handoff.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$r | Select-Object status, execution, adapter, phase3_plan_sha256, phase2_candidate_sha256
$r.summary | Format-List
$r.issues | Format-Table
```

`STAGED_NOT_EXECUTED` appears **only if** the exact SHA is already operator-approved and Phase 3/2C agree. If the example repository is *not* in your operator-owned allowlist, expect `BLOCKED` with `PINNED_SHA_NOT_OPERATOR_APPROVED`. **Do not automatically allowlist new repositories to make this test green.** Use only reviewed fixtures and their already approved commits. No VM is launched by this command.

For unrelated/unsupported projects, Phase 4A returns `NO_RUNNABLE_TESTS` when Phase 3 identified no supported executable assertions. This is a normal outcome, not an application failure. For malformed input, an `INPUT_ERROR` report is emitted with a fixed code and exit status 2.

## JSON states

`phase4_handoff.json` contains `schema_version`, `phase`, `stage`, `status`, `source`, `adapter`, input hashes, `execution`, `security_attestation`, `launch_authorization`, `test_bundle`, `selected_test_ids`, `excluded`, `summary`, `issues`, `limitations`.

The `test_bundle` is intentionally tiny and declarative:

```json
{
  "schema_version": "0.1",
  "source": {"kind": "github", "repository": "Fixture/Reviewed", "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "adapter": "static_html_v1",
  "tests": [
    {"id": "TC-0000000000000001", "method": "GET", "path": "/index.html",
     "assertions": [{"kind": "HTTP_STATUS_EQUALS", "expected": 200}]}
  ]
}
```

The interpreter supports `HTTP_STATUS_EQUALS` (200 only) and `HTTP_BODY_SHA256_EQUALS` (requires a simultaneous HTTP 200 assertion). It uses **exact response bytes**, not the DOM. `NEEDS_ORACLE`, unsupported and overflow cases are excluded from execution, not falsely marked FAIL. Each executed test will be PASS, FAIL, or INCONCLUSIVE; those are **HTTP assertions only**, not marks for functional/business correctness.

## Phase 4B integration plan (next step)

1. Freeze and review this contract, then modify `phase2/vm_worker/orchestrator.py` to accept a separately validated, operator-authorized test bundle **only for the same source SHA and adapter**.
2. Copy the trusted `guest_http.py` and the bounded bundle to the disposable VM; do not let student code overwrite the trusted controller file or modify the request.
3. In **both** `guest_agent.run()` and `run_fullstack()`, execute guest HTTP assertions **after readiness and before the server is terminated**, subject to the existing wall-time watchdog, non-root process boundaries and guest loopback-only network.
4. Collect guest test results while preserving its existing cgroup/build placement checks. Run `validate_guest_report()` on the host. Invalid guest output must be `INCONCLUSIVE`/infrastructure error, not PASS.
5. Preserve **unconditional VM teardown** and all existing Phase 2D/D5 acceptance behavior. Start with one previously approved static fixture; then PHP/MySQL and full-stack. Do not repeat every expensive VM regression unless the changed execution boundary requires it.

Phase 4C later handles stronger stateful/API oracles; Phase 4D optional browser tests; Phase 4E release benchmark.

## Tested locally before delivery

- 31 offline Phase 4A tests passed, using reconstructed pushed Phase 3 code and a local fake server. No student repository was run.
- Six synthetic architecture inputs were replayed: static, Flask and PHP -> test bundles staged under synthetic operator config; Express, Next.js and GraphQL -> no runnable tests, as expected.
- Python modules compiled successfully.
- The patch is add-only; no Phase 2 guest/security/VM source code was touched.

**Not yet tested**: installation into your Windows checkout, live SSH/QEMU guest HTTP assertion execution, full Phase 3+4 regression suite in your environment, or malicious-student VM security attestation. Do not present 4A as full Phase 4 completion.
