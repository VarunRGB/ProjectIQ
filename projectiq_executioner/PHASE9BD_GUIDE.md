# Phase 9B-D — unified local demo evaluation and core integration

## Purpose and scope

Do not replace the existing Phase 1–7 reviewed evaluator, rubric engine, main
ProjectIQ orchestration, or Phase 8/9 runners. The new entry point evaluates a
repository with the existing STATIC pipeline first, pins the source, then selects
one supported Phase 8/9 path. Operator trust is an explicit local opt-in; this is
not a qualified worker for arbitrary untrusted student submissions. Do not pass
student-controlled CLI flags, configuration paths, or options into this API.

- `ADAPTIVE`: compiled independent GETs and one source-supported EJS CRUD workflow,
  only with explicit disposable-state confirmation and successful source binding.
- `INDEPENDENT_HTTP`: eligible independent GET assertions; mutations are deferred.
- `SMOKE_ONLY`: an eligible reviewed runtime component, but no supported functional
  contracts. Does not claim functional correctness.
- `STATIC_ONLY_LIMITED`: no eligible component, source/planning unsupported, or
  explicit component selection is required. Static analysis retained.
- `RUNTIME_INCOMPLETE` / `SECURITY_HOLD`: preserve bounded failure information and
  never retry or switch to smoke execution after a functional runtime attempt.

One selected component and one application VM maximum; no retries. Multi-component
repositories need explicit selection. Optional toolchains and complex stacks remain
subject to existing adapter policy. A separate smoke run is not needed when a
functional run includes readiness. Source and compiled plan are rechecked by the
existing dispatcher. No new VM, dependency, network, authentication or database
mechanisms. The legacy Phase 4 reviewed execution path remains independent.

## Existing application entry point

In the existing local CLI, add the two operator-only flags to an ordinary fresh
`python -m projectiq_core evaluate` command:

```
--trust-demo-source --demo-vm-config vm_config.json
```

For a source you have reviewed and whose state is disposable, add
`--confirm-disposable-state`. Optionally specify `--demo-component ROOT_OR_ID`.
`--demo-require-bounded-volume` retains the existing volume prerequisite.
The normal CLI with no flags still runs STATIC only. `collect` supports the same
options. Saved evaluation reuse cannot trigger a demo. The backend must not expose
these flags as student-controlled options or an unattended/public API.

Backend Python API, local operator only:

```python
from pathlib import Path
from projectiq_executioner.phase9b.unified import DemoOptions
from projectiq_core.orchestrator import evaluate
# Supply the existing required report, title, submission_id, repository_url,
# out and rubric arguments as before; add only this optional keyword:
# demo=DemoOptions(Path('vm_config.json'), trust_demo_source=True,
#                  confirm_disposable_state=True)
```

Executioner-only CLI (no other agents):

```
python -m projectiq_executioner.phase9b.run_unified --repo URL --ref EXACT_SHA --trust-demo-source --demo-vm-config vm_config.json --out NEW_OUTPUT
```

Omit `--ref` for automatic source resolution. Pass `--confirm-disposable-state`
only when you explicitly trust mutation in the disposable guest. The pinned commit
is reused for all subsequent planning and runtime stages.

## Output compatibility and grading boundary

The existing native `executioner_evaluation` structure remains intact. Its
`mode`, `effective_mode`, legacy `test_case_matrix`, `suites`, and scheduled VM
count describe the original STATIC evaluator. Actual demo VM attempts, status,
functional and workflow evidence live in **`trusted_demo`**. The root report
links the original `static/executioner_result.json`, the planning files, and
`runtime/functional_result.json` or `runtime/demo_result.json` with hashes.
`executioner_report.md` preserves the existing static Markdown and appends the
separate functional inventory and bounded diagnoses. No raw guest responses or
synthetic identities are included in the extension. Retain full local artifacts
for an independent consistency audit. Saved hashes alone are not attestation.

The existing core adapter passes the extension to
`result.json -> agents -> executioner -> details -> trusted_demo`. The rubric
evidence builder exposes a summary as `CHARACTERIZATION_DISPLAY_ONLY` with null
grade credit. Existing independently reviewed test marks and evidence eligibility
are unchanged. An inferred implementation oracle is not an independent teacher
specification; no Phase 3 test IDs are fabricated or relabelled as passed.

Coverage is **unique discovered method/path/component identities receiving an
attempted request**, not a pass rate or requirements coverage. Independent PASS,
FAIL, INCONCLUSIVE and NOT_RUN remain distinct from workflow-related observations.
For the accepted Express fixture the expected result is eight discovered cases,
three independent passes, nine adaptive requests, seven unique cases receiving a
request, four workflow-only observed cases, and one without an attempted request.
The edit-page GET is untested. No automated marks are added. Phase 6's diagnosis
catalog is reused for bounded mismatch, incomplete and safety diagnoses, not
invented student fault. The original Phase 6 diagnosis remains intact.

## Tests and acceptance

Executioner-only local regression: `python -m pytest -q tests/test_executioner_phase9a.py tests/test_executioner_phase9b_a.py tests/test_executioner_phase9b_b.py tests/test_executioner_phase9b_c.py tests/test_executioner_phase9b_d.py`.
Full-checkout core regression: `python -m pytest -q tests/test_projectiq_phase9bd_bridge.py tests/test_projectiq_core_phase1.py tests/test_projectiq_phase2e.py`.
The four new core tests require the actual full core checkout; they were not run
in the Executioner-only development container. Run them before live acceptance.
The existing Windows-only skip in 9B-B is expected. One live integrated Express
run through the existing core `evaluate` CLI is the acceptance target; reuse the
pinned 9B-C repo and inspect the new extension. Use `--objective-only` to exercise
the real existing evidence/scoring engine without needing rubric LLM inference.
A successful run may have unresolved rubric criteria by design. Do not claim
whole-project correctness, production security attestation, or complete MVP
acceptance from this backend milestone alone.
