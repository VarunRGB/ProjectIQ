# Phase 9A — source-grounded functional test and oracle planning

## Scope and safety
This additive module reuses pinned GitHub acquisition, Phase 1 discovery, Phase 3 test-plan generation and Phase 8D component classification. It never invokes the target program, starts a VM, downloads npm/PyPI packages, uses a model, modifies approvals or emits grades. The runtime plan is advisory. Run on an operator-selected public repository, and keep output private: expected literal responses may contain source text. A report digest is a consistency check, not a signature or authorization. Existing 8F runtime protections and the STATIC main collector remain unchanged.

### Invoke from repository root (PowerShell)

```powershell
$nodeOut9 = "outputs\executioner\phase9a_node_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase9a --repo "https://github.com/bhs100/CRUD-Express" --ref 09364d67e2b30b8da663240a9cb40723c239959c --out $nodeOut9
$pyOut9 = "outputs\executioner\phase9a_python_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_executioner.phase9a --repo "https://github.com/render-examples/flask-hello-world" --ref facfc73c44b35d00bb1f72e8986f0ea47cab0755 --out $pyOut9
```

`--ref` is optional; specifying the previously tested exact SHA makes the evidence join unambiguous. Output must be new. This command downloads the source only, not runtime dependencies. A compatible runtime is not required for discovery. It produces `phase1/`, `runtime_plan.json`, `phase3_test_plan.json`, and `functional_plan.json`.

### Evidence tiers
* `ORACLE_PROPOSED`: a bounded source-derived assertion is present and no known planner blocker was detected. This is **not runnable or approved**; Phase 9B must validate runner support, scope, side effects and input requirements independently.
* `NEEDS_REVIEW`: missing or conflicting expected behavior, parameter binding, unsupported context/method, or runtime blockers. Do not manufacture assertions from README claims, status-code guesses, or readiness evidence.
* `NEEDS_BINDING_AND_REVIEW`: ordered candidate CRUD workflow with route-backed field evidence, symbolic expectations and explicit unmet conditions. No executable fixture/capture has been synthesized. Do not substitute an arbitrary existing ID. Phase 9B must bind a synthetic record using a correlated observation, prove reset, verify postconditions and preserve unrelated records.

`IMPLEMENTATION_LITERAL`, `IMPLEMENTATION_TEMPLATE`, and `STATIC_RESOURCE_BYTES` are characterization hypotheses. They cannot independently prove a project implements its intended specification, since a bug can occur in the implementation from which they were extracted. `REPOSITORY_DECLARED_OPENAPI` separates an explicit GET/200 declaration from the handler; it is still not independently teacher-approved. Contradictions are blocked. Source file SHA-256 and a line (where available) accompany evidence. Tests are grouped by component, with stable IDs, symbolic workflow assertions and a plan digest. No VM evidence is consumed by 9A; reuse the same exact SHA for later evidence joining.

### Coverage
Direct top-level Flask/FastAPI literal return handlers (bounded text or JSON); direct CommonJS Express inline arrow handlers (literal `send`, simple EJS `render` with static text preceding template expressions); direct static HTML entry bytes; Phase 3 local OpenAPI GET/200 declarations. The Express CRUD heuristic uses a POST body-field declaration and redirect to a list route, a parameterized read, and related PUT/PATCH/DELETE paths. It also records matching static create form fields when found. It is intentionally limited to candidate generation: no dynamic parameter capture or concrete request values, negative behavior guessed, or hidden external calls made. Broader Phase 3 route discovery remains review-only; full inventory is saved separately. Python dynamic routes, router mounts, JS ES modules and arbitrary syntax, Django, PHP, framework UI, CLI input/output, authentication, DB fixtures, and composed services do not receive generalized functional oracles from this version. Prior reviewed Phase 4 capabilities are retained, not replaced.

### Acceptance
Use the same two public repos as 8F. For Flask, expect GET `/`, proposed status 200 and exact body `Hello, World!`. For Express, inspect page markers, CRUD family and extracted `content`/`update` fields; expect the workflow to remain `NEEDS_BINDING_AND_REVIEW`. Both must report `functional_tests_executed: 0`, `vm_start_attempts: 0`, `runtime_permission: false`, `automatic_untrusted_execution: false`, and no grade. Review the actual reports before closing 9A. The plan cannot establish that any generated test passes. Unfamiliar repositories may produce no supported proposals, or only review-only entries.

### 9B handoff
`build_plan` and `verify_rederived_plan` operate on an inert source checkout and its freshly generated manifest/runtime/Phase 3 inputs. For 9B, always acquire the pinned source again and generate those inputs independently before comparing a saved plan. Never accept a caller-provided manifest/plan or self-checksum as authorization. Reuse the reviewed VM policy and explicit local operator trust; require a bounded typed guest contract. Distinguish executed, failed, inconclusive and untested cases; no grades from source-only oracles or smoke. Keep the original 8F evidence separately.
