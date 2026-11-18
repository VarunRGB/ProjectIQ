# ProjectIQ Executioner Phase 2B — sandbox preflight (planning only)

Phase 1 is **feature-frozen** at `schema_version=1.0`. Phase 2B consumes the
**same** pinned Phase 1 manifest together with its Phase 2A plan, checks that
they agree, and produces a more detailed, architecture-aware **candidate**
container/service blueprint. It **never builds or starts a project**.

## Running it on Windows 11 PowerShell

From the ProjectIQ repository root (after installing the package patch):

```powershell
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -v
```

Phase 2A still works unchanged:

```powershell
python -m projectiq_executioner.phase2 outputs/executioner/mini_collab_phase1/manifest.json --out outputs/executioner/mini_collab_phase2_plan.json
```

Phase 2B without contacting a container runtime:

```powershell
python -m projectiq_executioner.phase2.preflight --manifest outputs/executioner/mini_collab_phase1/manifest.json --plan outputs/executioner/mini_collab_phase2_plan.json --out outputs/executioner/mini_collab_phase2b.json --md outputs/executioner/mini_collab_phase2b.md
```

Optional **read-only** Docker/Podman capability probe (may query a local Docker
or Podman engine; never creates containers, pulls images, or executes student
code):

```powershell
python -m projectiq_executioner.phase2.preflight --manifest outputs/executioner/clouddrop_phase1/manifest.json --plan outputs/executioner/clouddrop_phase2_plan.json --out outputs/executioner/clouddrop_phase2b.json --md outputs/executioner/clouddrop_phase2b.md --probe-runtime --runtime auto
```

`--runtime docker` or `--runtime podman` limits discovery to the selected
engine. When `--probe-runtime` is omitted, the tool does not call either CLI.
If the executable/engine is absent, the tool records `UNAVAILABLE_OR_UNVERIFIED`.

### Understanding statuses

- `planning_status=SUCCESS`: both input files passed validation and a safe
  *static preparation document* was generated.
- `status=PREPARED_NOT_VERIFIED`: no **known compatibility blocker** was found;
  the result is still not runnable or secure.
- `status=BLOCKED`: a known issue needs attention (external service, missing
  adapter, unreviewed SQL, hardcoded credentials, dependency cycle, or a
  reported runtime policy gap).
- `execution=NOT_PERFORMED`, `readiness=NOT_VERIFIED`, and
  `launch_authorization=DENIED` **always** apply to Phase 2B.
- `runtime_capability_probe.capability_status=CANDIDATE_REQUIRES_ATTESTATION`
  is NOT a safety approval. An isolated dedicated worker must pass separate
  enforcement and escape-boundary checks in 2C.

### Current examples

| Repository | Candidate containers | Non-container dependencies | Expected blockers |
|---|---:|---|---|
| Mini Task Collaboration | 2 (PHP web + MySQL) | HTML/JS frontend cohosted with PHP | DB credentials rebind; SQL schema review |
| Moss and Meadows | 1 (Next.js) | Missing external HTTP API implementation | Mock or isolated test backend required |
| CloudDrop | 2 (FastAPI + React/Vite) | SQLite embedded, AWS S3 external | Isolated S3 emulator/adapter required |

Candidate counts reflect proposed **execution units**, not numbers of
architectural modules. SQLite is not a standalone server. A native PHP project
can serve its static HTML/JS and PHP routes in the same container when their
source roots and dependency relationships support that interpretation.

### What the report includes

`logical_services`, `provisioning_order_logical`, `candidate_containers`,
`container_startup_order_candidate`, `interservice_network_intents`,
`noncontainer_dependencies`, `database_initialization_candidates`,
`configuration_contract`, `source_safety_findings`,
`readiness_probe_candidates`, `sandbox_enforcement_requirements`,
`runtime_capability_probe`, `blocking_conditions`, `verification_gates`,
and `phase2c_handoff`.

Service roots, SQL schema files, and configuration declaration paths are
lexically validated; **they have not been resolved against a checkout**.
2C must verify the pinned source snapshot and reject traversal/symlink escapes.
The report does not copy raw package scripts, environment values, credentials,
or executable repository Dockerfiles. Runtime image families are **hints**,
not approved images; immutable image digests are intentionally absent.

## Security boundaries and necessary Phase 2C work

Phase 2B's generated plan does **not** certify Docker as a sandbox. On a
Windows personal laptop, Docker Desktop commonly runs a Linux engine inside a
VM, which is useful for development but is not by itself proof of safe
multi-tenant execution. Docker Engine running rootless, cgroup v2 and systemd
for enforceable resource limits are separate questions. Even reported support
is not an enforcement test.

Before allowing 2C to execute untrusted submissions:

1. Use a disposable **dedicated worker VM** (not the user's host or normal
   Docker Desktop environment as a multi-tenant execution boundary).
2. Choose operator-allowlisted images by **immutable SHA-256 digest**. Never
   execute untrusted Dockerfiles or README/package commands without a
   separate, enforced build policy inside the worker.
3. Prepare immutable pinned source snapshots. Validate archive paths and
   symlinks after materialization; expose only narrow, disposable writes.
4. Enforce unprivileged rootless containers, seccomp, Linux capability drop,
   read-only root filesystem, PID limits, CPU/memory/disk/time limits and
   independent teardown; **test** those constraints against hostile fixtures.
5. Explicitly construct private interservice networks with no external egress.
   A declared isolated network alone is not proof; verify DNS, proxy, routes,
   container interfaces, and build-time egress independently.
6. Inject synthetic credentials only into the worker. Never copy students'
   production `.env`, database passwords or cloud keys. Review PHP hardcoded
   connection settings before any attempted test connection.
7. Execute SQL bootstrap only after inspecting SQL and targeting a disposable
   database; do not automatically run arbitrary checked-in SQL.
8. S3 and other cloud API code must be wired to a verified offline emulator or
   an explicitly authorized disposable resource. CloudDrop's existing boto3
   client does not, by itself, prove it accepts an emulator endpoint override.
9. Discover service-specific startup ports, readiness probes and warmup times;
   implement bounded log capture, failure diagnosis and teardown on success,
   failure, timeout or cancellation.

## Coverage statement

Built-in **candidate** profiles recognize common Python, Node, PHP, Java,
.NET, Go, Ruby and Rust application services; Next.js, Nuxt, SvelteKit,
Angular, React/Vite and static web frontends; SQLite, MySQL, MariaDB,
PostgreSQL, MongoDB and Redis. Unknown stacks must produce a clear
`UNSUPPORTED_RUNTIME_OR_ARCHITECTURE` blocker; no system can infer arbitrary
custom build commands or undocumented behavior perfectly from metadata.
Additional adapters may be registered in later iterations without changing
Phase 1's evidence contract.

## Tests

`tests/test_phase2b.py` includes runtime probe mocks, hostile input tests,
architecture-adapter tests and small **sanitized** fixtures derived from the
three validated repos. The fixtures contain metadata only, no live credentials,
project code, or untrusted startup commands. All Phase 1 / 2A tests are retained.
