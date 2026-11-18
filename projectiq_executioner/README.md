# ProjectIQ Executioner — Repository Intelligence and VM Execution Pilot

**Status:** Phase 1 locked; Phase 2A/2B planning implemented; Phase 2C **QEMU disposable-VM reviewed-fixture pilot implemented** (real Windows VM execution still needs hardware validation). **Phase 1 never executes source code**. Only the explicit Phase 2C reviewed-fixture command can execute a pinned, operator-approved source snapshot **inside a separate Linux guest**; arbitrary student submissions are not enabled. Python 3.11+; core static analysis uses the standard library. The optional Phase 2C operator tool needs `pycdlib`, QEMU and OpenSSH.

Phase 1 accepts an HTTPS *public GitHub repository URL*, resolves the default branch (or an explicit `--ref`) to a **full immutable commit SHA**, downloads its ZIP from GitHub, validates and extracts it in a disposable directory, inspects repository files **as inert data**, and produces a versioned `manifest.json` and readable `manifest.md`.

## Phase 2C native-VM execution pilot

See [`PHASE2C_VM_GUIDE.md`](PHASE2C_VM_GUIDE.md) for the VM-only architecture, approved Ubuntu template baking, source SHA pinning, offline application startup, guest smoke tests, and the Windows 11 CLI. **Docker Desktop is not used by Phase 2C**. The old Phase 2B Docker/Podman runtime probe remains as a historical, non-authoritative compatibility tool; it does not gate this VM path.

## Install into ProjectIQ

Place the **whole `projectiq_executioner/` folder** alongside `projectiq_research/`, `projectiq_integrity/`, and `projectiq_summarizer/` at your ProjectIQ root. The ZIP includes `projectiq_executioner/tests/test_phase1.py` within the package; you do not need to move any test file.

```powershell
cd C:\Users\varun\Code\ProjectIQ
python --version
python -m projectiq_executioner "https://github.com/VarunRGB/Mini-Task-Collaboration-App.git" --out outputs/executioner/mini_collab_phase1
```

Read `outputs/executioner/mini_collab_phase1/manifest.md` and `manifest.json`.

### Select a branch, tag, or SHA

```powershell
python -m projectiq_executioner "https://github.com/owner/any-repo" --ref main --out outputs/executioner/custom
```

### Inspect a local folder without a network connection

```powershell
python -m projectiq_executioner "C:\path\to\any\project" --out outputs/executioner/local
```

This **also does not execute project code**. On local scans, commit provenance is unavailable and is explicitly marked unknown.

### Test Phase 1 itself

```powershell
python -m unittest discover -s projectiq_executioner/tests -p "test_phase1.py" -v
```

Or, within the full project, use `python -m pytest projectiq_executioner/tests/ -q`.

For the **full nomenclature regression suite**, replace the discovery pattern with:

```powershell
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -v
```

This includes both the earlier Phase 1 tests and the new cross-stack convention tests.

## What is inspected

- **Languages** by source-file inventory (file counts, not LOC or proficiency).
- **Declared stacks** by `package.json`, `composer.json`, `requirements.txt`, `pyproject.toml`, Java/JVM, Go, Rust, .NET, Ruby and Flutter manifests, plus bounded source markers.
- **Service hints** for frontend/backend/database; architecture (`fullstack_web`, `frontend_web`, `backend_service`, `cli_or_library`, or `unknown`) with a confidence label. These are **logical components**, not necessarily distinct processes or containers.
- **Routes** through recognized PHP file-backed endpoints, Express/router, Flask/FastAPI, Spring and Next.js patterns, plus static browser `fetch()` calls, local TypeScript/JavaScript `fetch` wrappers (including common nested default arguments),
  basic axios usage, and source-based Next.js App/Pages router URL candidates. A
  route like `/orders/:id` is a *candidate URL pattern*, not a verified HTTP
  response. The bounded wrapper recognizer supports typed TypeScript calls like
  `request<Result>("/login", ...)`, simple dynamic path parameters, and an
  optional query-string template such as `` `/plants${category ? `?category=${category}` : ""}` ``.
  In that last case, the manifest records `/plants` because the empty-query
  branch demonstrably exists; it does not claim to know every query combination.
  More complex dynamic expressions remain unresolved rather than being
  fabricated. **Server records and client calls are separate evidence records**; their sum is not a count of unique working endpoints. Frontend method hints never prove the server accepts that method.
- **External API references** inferred only from observed source usage, without
  logging base URL values. A remotely hosted backend is represented as an
  `external_dependency`, not falsely counted as backend source in the repo.
  Phase 2 must NEVER send mutating requests to an external production service
  without explicit authorization and an isolated test target.
- **Pages and declared features** using known page layouts and Markdown README headings.
  For Next.js projects with boilerplate READMEs, URL/page presence is reported
  as a low-confidence, `source_inferred` capability, not a functional claim. Role bullet headings (for example, “Admins can:”) are not considered user-facing features; non-functional project-structure claims are excluded from proposed functional test targets.
- **Existing test artifacts** and framework declarations; Phase 1 never runs or claims to pass tests.
- **Setup metadata** including entrypoints, DB schema paths, declared package-script **names only** (command bodies intentionally withheld), Docker Compose service hints, configuration environment variable **names only**, and unsafe-looking tracked configuration patterns. PHP `$_SERVER["REQUEST_METHOD"]` is request metadata, not a deployment environment variable.
- **Follow-up targets** for Phase 3 test generation, not executable tests or fabricated expected results.

Every important discovery carries a source file and line number where supported. Model output does not copy source snippets or literal credentials.


### Expanded filename/API nomenclature recognition

See **[`NOMENCLATURE_COVERAGE.md`](NOMENCLATURE_COVERAGE.md)** for the supported patterns, evidence rules, and explicitly unsupported dynamic cases. The expansion adds:

- Multi-file literal Express router imports/mount prefixes and some imported FastAPI routers.
- NestJS, Django URL patterns, Laravel, ASP.NET Core, Go, Rust, SvelteKit, Nuxt, and React/Vue/Angular page routing conventions.
- Literal in-repository `/api` prefixes on proved browser HTTP wrappers, plus bounded finite URL ternary choices; cloud SDKs such as `boto3` are distinguished from verified S3 usage.
- API client conventions for shared axios instances, `ky`, Angular HttpClient, Nuxt `$fetch`, Python `requests` and `httpx`.
- Literal root-relative HTML form actions, API specifications from OpenAPI/Swagger JSON or simple YAML, and distinct non-REST GraphQL/gRPC schema operations.
- `inventory.file_roles` / `inventory.file_role_counts`: conventions for controllers, handlers, pages, app bootstrap files, model/schema files, database connectors, authentication utilities, API clients, tests, configuration, deployment files, documentation and assets.
- `inventory.unresolved_api_sites`: bounded file+line diagnostics when a dynamic `fetch(url)` or similar expression cannot be statically resolved. **No URL value, request or code execution is inferred.**

The JSON manifest retains schema **1.0** with *additive inventory metadata*; existing top-level fields remain compatible. This is **broad heuristic coverage, not perfect source comprehension**. Runtime-only routes, reflection, generated code, extensive macro systems and unusual project layouts may still be missed.

## Security and reliability boundaries

- Input URLs must be `https://github.com/owner/repo[.git]`; other hosts, embedded credentials, query strings, and nested GitHub URLs are rejected.
- Public repositories only. GitHub API requests may use `GITHUB_TOKEN` set **outside the repository** to alleviate rate limits. Token values are never logged, and direct GitHub archive downloads are anonymous. Private repositories are unsupported.
- Pinned GitHub ZIP archives have compressed/expanded/per-file/count limits; absolute/traversal paths, symlinks, and duplicate names are rejected. The ZIP is unpacked into a disposable temporary directory and deleted afterward.
- Local scanning never follows symlinks, excludes common dependency/build/cache folders and does not read the contents of `.env`, `.env.local`, `.env.production`, etc.
- **Phase 1 does not use Git checkout, install scripts, package managers, Docker, Python imports from student repositories, or subprocess execution.**
- Static patterns are fallible: custom folders, dynamic routing, unusual manifests, generated APIs, obscure frameworks and binary assets may be missed. Declared database use does *not* prove the DB starts.
- Source strings in `package.json` and README are untrusted data, not commands. No detected `npm`/`composer`/`pip` command should be run outside a sandbox.
- GitHub read access and snapshot download require internet; offline local scanning is available.

## What the sample repo is expected to reveal

The provided Mini Task Collaboration App and Moss and Meadows are **validation targets, not hard-coded special cases**. Its repository presently contains native PHP backend endpoints, HTML/JS/CSS pages, a MySQL SQL dump, login/registration/task-management behavior documented in its README, and no obvious dedicated test suite. There may be a hard-coded DB credential in a PHP DB connector; this is reported by *location only*, never value. These facts are inferred from its inspected source, not from a successful app run.

We must also test unlike projects: Python API + JS frontend, React/Node monorepo, Java Spring, and static sites. The regression tests supply representative fixtures for several distinct structures, but are not a full empirical stack benchmark.

## Output schema and downstream contracts

`manifest.json` is the machine-readable version **1.0** record. Its top-level structure includes:

```text
schema_version, phase, created_utc, source (repository/ref/commit),
project_name, architecture, architecture_confidence, languages,
technologies, services, routes, pages, entrypoints, features, tests,
setup_files, package_scripts, environment_variables, database_schema_files,
findings, unknowns, proposed_test_targets, inventory, limitations
```

Evidence consists of `{path, line, reason}` (without source snippets, secrets or claims of execution). Confidence labels are categorical heuristics, **not calibrated probabilities**. Phase 2 will add environment readiness and real execution evidence without rewriting the Phase 1 raw findings.

## CLI troubleshooting

- `GitHub request was rate-limited`: set a `GITHUB_TOKEN` environment variable before the command, or retry after API limits reset.
- `Repository/ref not found`: verify public visibility and branch name. Default branch is detected rather than assumed.
- `Archive exceeds ...`: choose a small test repository or raise limits only after assessing available disk space.
- `No supported static HTTP route patterns`: check `manifest.md` for unrecognized framework/dynamic-route notes, not an assertion that no API exists.
- `No repository-owned automated test suite was detected`: this is a cautious observation, not a proof of no tests.

See [`ROADMAP.md`](ROADMAP.md) for the locked eight phases and [`MANIFEST_CONTRACT.md`](MANIFEST_CONTRACT.md) for the Phase 1 → Phase 2/3 contract.

## Phase 1 stable baseline + Phase 2A start

The Phase 1 1.0 manifest contract is frozen after validation across PHP/MySQL,
Next.js with a separately hosted API, and FastAPI + Vite/React with SQLite/S3.
No static scanner recognizes all arbitrary projects perfectly. Only a small,
explicit compatibility layer should be added when a new pattern is confirmed by
independent evidence and regression tests.

Additional Phase 1 inventory fields:

- `environment_variable_details` gives Pydantic BaseSettings field *name*,
  inferred required/default status and line evidence—**never secret values**.
- `api_links` lists conservative frontend-to-local-backend static matches,
  including parameter-name normalization and provenance. This reduces duplicate
  proposed tests without deleting the independent route observations.

A common JS optional-query template such as `` `/files/${id}/download${suffix}` ``
can be recognized when `suffix` is an immutable, locally declared expression
whose only possible alternatives are a query string and an empty string.
Unresolved expressions remain explicitly unknown.

### Phase 2A — sandbox plan only

```powershell
python -m projectiq_executioner.phase2 outputs/executioner/clouddrop_phase1/manifest.json --out outputs/executioner/clouddrop_phase2_plan.json
```

This creates an inert plan that identifies service candidates, external
integration blockers, synthetic configuration requirements and health-check
candidates. **It does not run Docker, install packages, execute source files,
or connect to AWS or hosted APIs.** A real isolated launcher, verified network
and filesystem controls, readiness checks and unconditional teardown are still
needed to complete Phase 2. See `PHASE2_DESIGN.md`.


## Phase 2B — architecture-aware sandbox preflight (NEW)

This stage consumes **both** Phase 1 and Phase 2A outputs. It proposes
container execution units, database/schema bootstrap requirements, synthetic
configuration needs, inter-service network boundaries and Phase 2C execution
gates. It does **not** install, build or run a submitted project.

```powershell
python -m projectiq_executioner.phase2.preflight --manifest outputs/executioner/clouddrop_phase1/manifest.json --plan outputs/executioner/clouddrop_phase2_plan.json --out outputs/executioner/clouddrop_phase2b.json --md outputs/executioner/clouddrop_phase2b.md
```

Add `--probe-runtime` for an optional read-only local Docker/Podman capability
query. Even a successful probe never authorizes untrusted application execution.
See `PHASE2B_GUIDE.md` for capabilities, limitations and validation examples.
