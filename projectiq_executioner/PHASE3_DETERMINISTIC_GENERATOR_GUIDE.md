# ProjectIQ — Phase 3A–3C deterministic test planning (first implementation)

## Scope

This add-only patch introduces `projectiq_executioner.phase3` and an offline
regression suite. It reads **any valid Phase 1 manifest**, not just an approved
Phase 2 adapter. It generates a stable, versioned `test_plan.json`. It can also
accept a public GitHub URL by reusing Phase 1's GitHub pinning, ZIP size limits,
safe archive validation and inert static analysis.

**No student application is executed. No LLM is invoked. No VM starts.**
Only the optional Phase 2 adapter **readiness probe** has a machine-justified
HTTP 200 oracle. All other routes, pages, features, tests, schemas, existing student test files, program entrypoints, Phase 1 proposed
checks and non-HTTP operations are classified as test candidates or
unsupported by the current runner, never invented as functional assertions.

**Not a full functional test suite:** Phase 3D will add sophisticated grounded
oracle extraction and prioritization; Phase 4 will implement the restricted
in-guest test runner and validate every test again.

## Apply

```powershell
cd C:\Users\varun\Code\ProjectIQ
$patch = "$HOME\Downloads\projectiq_phase3_deterministic_test_generator.patch"
git apply --check --verbose $patch
if ($LASTEXITCODE -ne 0) { throw "Patch check failed" }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw "Patch apply failed" }
git diff --check
python -m unittest projectiq_executioner.tests.test_phase3_generator -q
if ($LASTEXITCODE -ne 0) { throw "Phase 3 tests failed" }
```

## Existing repository / offline manifest (recommended)

```powershell
$base = "outputs/executioner/batch_20261121_205242/StaticSite"
python -m projectiq_executioner.phase3 `
  --manifest "$base/phase1/manifest.json" `
  --candidate "$base/phase2c_candidate.json" `
  --out "outputs/executioner/phase3_static_01"
```

Both manifest and candidate refer to the **same full SHA**. If there is no
Phase 2 candidate, omit `--candidate`. The generator still produces test
candidates; it will have zero `READY_FOR_PHASE4` assertions.

## Any public GitHub repository

```powershell
python -m projectiq_executioner.phase3 `
  --repo "https://github.com/owner/repository" `
  --ref "FULL_40_CHARACTER_COMMIT_SHA" `
  --out "outputs/executioner/phase3_newrepo_01"
```

The old ingestion size/safety limits still apply. When ingestion fails, the
output is a fixed JSON error envelope with `status=INPUT_ERROR`, an issue code,
and an empty test-case list. An oversized repository is **not** silently
accepted. The unapproved source is **not** executed.

## Output contract

Top-level keys (always present):

- `schema_version`, `phase`, `stage`, `status`, `source`
- `generator` (rule version, LLM-used flag, execution flag)
- `execution_context` (adapter, candidate status; approval is not evaluated)
- `summary` (total, ready, needs oracle, unsupported, category counts)
- `test_cases` (stable IDs, category, target, assertions, oracle, evidence,
  evidence kinds, blockers, status, required adapter, execution)
- `issues` (safe issue codes)
- `limitations` (non-execution and non-authorization notices)

Test-case statuses:

- `READY_FOR_PHASE4`: only Phase 2's evidence-matched, adapter-provided
  **readiness probe**, with an explicit expected HTTP 200. Not a functional
  grade and not launch authorization.
- `NEEDS_ORACLE`: grounded discovery, but correct behavior or input fixture is
  not sufficiently specified. No assertions and not runnable automatically.
- `UNSUPPORTED_BY_RUNNER`: e.g. existing student tests or GraphQL operations
  that require a separately designed trusted runner.

Tests are deterministic for the same manifest and candidate, independent of
route ordering, and have no generated timestamps. Never edit or pass student
commands to a host shell, and never treat generated cases as security approval.

## Phase 3 test matrix

- Static HTML: Phase 2 `/index.html` readiness test + discovered pages.
- Flask/FastAPI: API routes, templates and unresolved mutation preconditions.
- React/Vite: readiness and discovered source pages.
- PHP/MySQL: PHP endpoints, SQL schema candidate, and static HTML.
- Unsupported Next.js/remote API: routes remain in the plan with explicit
  missing-oracle and/or missing-adapter blockers.
- Generic CLI/C++/Java: source entrypoints and proposed targets become
  candidate scenarios, without guessing inputs, outputs or command lines.
- No discovered routes, pages, tests, entrypoints, features, schemas or
  proposed checks: fixed `NO_TEST_TARGETS` JSON envelope.
- Invalid or oversized input: fixed `INPUT_ERROR` envelope.

## Next steps

3D: Source-grounded oracles (OpenAPI example responses with explicit schema,
controlled fixtures, invariants), deterministic de-duplication/coverage selection,
optional LLM **suggestions only**.

3E: Broader repo corpus checks and a versioned schema compatibility gate.

Phase 4: Execute trusted declarative tests in the disposable guest *between*
ready and cleanup, with explicit operator approval and revalidated test plan.
