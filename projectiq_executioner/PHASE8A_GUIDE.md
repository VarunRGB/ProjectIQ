# ProjectIQ Executioner — Phase 8A: generalized advisory execution planning

## Scope

Additive planning module, Python 3.11+ (standard library; no LangChain or new runtime dependency). The previous STATIC / REVIEWED / RESUME evaluator and its approval, adapter, VM and test-oracle contracts are unchanged. Phase 8A is a separate CLI/API using the **same** bounded pinned-checkout acquisition helper as the existing Executioner. It neither invokes the legacy evaluator nor launches any VM. It is a discovery/plan interface for the future 8B and 8C stages, not a replacement for the current reviewed execution path. No plan is executable or grants permission to prepare dependencies.

### Workflow

1. For GitHub input, validate the request as STATIC with zero VM budget; use existing `pinned_checkout`; verify the resolved source identity.
2. For a trusted local directory, use `local_unverified` identity; no claim of an immutable Git commit.
3. Scan bounded source/manifest data (12,000 entries, 3,000 directories, 18 levels, 512 KiB per read, 24 MiB total read). Ignore generated/cache trees, actual `.env` files and common private key/credential filenames. Refuse linked root paths and skip links/reparse points and non-regular files. Do not interpret shell commands or import target source. Inspect files only; never print raw command bodies or environment values.
4. Identify component roots from manifests and entrypoint conventions; avoid duplicating manifest-owned source folders; distinguish workspace roots and multiple ecosystems.
5. Infer framework, role, toolchain constraints, dependency name hints, package manager candidates, entrypoint candidates, environment variable names, database/external-service hints, literal port hints, deployment declarations and existing adapter candidates.
6. Propose **symbolic** ordered operations (dependency resolution, build/start candidates, health-probe discovery). Retain ambiguities and blockers. Combined Vite/Express is a hypothesis requiring existing adapter validation, not a confirmed topology. Docker/Compose hints are lexical only, not parsed or executable deployment instructions.
7. Emit `execution_plan.json`, schema `8A.1`, with deterministic plan digest and inspected-content/inventory digests, zero execution counters, explicit non-admission state, and evidence paths. Shape/checksum validation is not authentication; a future worker must independently verify source, dependencies, operations and policy.

## Discovery matrix (not runnable support)

| Ecosystem | Examples | Existing adapter candidates |
|---|---|---|
| Static web | self-contained HTML | static_html_v1 |
| Node | React/Vite, Express, Fastify, Koa, Hono, NestJS, Next.js, Nuxt, SvelteKit, Angular, CRA, React Native/Expo, Electron, Gatsby, Astro, Remix, Vue, SolidJS | node_vite_offline_v1, node_express_offline_v1, combined node_fullstack_offline_v1 only for candidate shapes |
| Python | Flask, FastAPI, Django, Streamlit, Gradio, Dash, Starlette, aiohttp, Tornado, Bottle, Litestar, Celery, notebooks | python_web_offline_v1 for bounded Flask/FastAPI candidates |
| PHP | native PHP, Laravel, Symfony | php_mysql_v1 candidate for native PHP only; MySQL and shape must be confirmed |
| JVM | Java/Kotlin, Maven/Gradle, Spring Boot, Ktor, Android, Quarkus, Micronaut | expansion required |
| Go | Gin, Echo, Fiber, net/http | expansion required |
| Rust | Cargo, Axum, Actix Web, Rocket, Bevy | expansion required |
| Ruby | Rails, Sinatra, Bundler | expansion required |
| .NET | ASP.NET Core, csproj/fsproj | expansion required |
| C/C++ | CMake, Make, Meson | expansion required |
| Dart | Flutter, Pub | expansion required |
| Other | SwiftPM, Elixir/Phoenix, Haskell, Deno, Godot, R, Julia, notebooks | expansion required |

The `ADAPTER_CANDIDATE` label does **not** mean the existing runner will accept the project. Exact lock formats, source shape, independent compatibility and approval must still be validated. `PLANNED` means discovery produced a plan without a detected planning issue, not that an app will build. `PLANNED_WITH_LIMITATIONS` is a useful result, not a test failure.

## Commands (PowerShell)

After installing into the latest main-derived branch, from the repository root:

```powershell
python -m pytest tests/test_executioner_phase8a.py -q --tb=short
python -m pytest tests -q --tb=short -ra
```

Pinned real-repository planning (no QEMU, no model inference, no installs):

```powershell
$run = "outputs\executioner\phase8a_$(Get-Date -Format 'yyyyMMdd_HHmmssfff')"
python -m projectiq_executioner.phase8a --repo https://github.com/VarunRGB/Mini-Task-Collaboration-App --ref c4f7965d16807fa05c3d7ffeee54f86fee4d0a37 --out "$run\minicollab"
python -m projectiq_executioner.phase8a --repo https://github.com/VarunRGB/FSDL --ref f358b6edda071da6666e587a8f1db054965ba106 --out "$run\fsdl"
python -m projectiq_executioner.phase8a --repo https://github.com/SafdarJamal/vite-template-react --ref 480dc9128ee90bf7c8c817e52ec063953243b240 --out "$run\vite"
python -m projectiq_executioner.phase8a --repo https://github.com/VarunRGB/ProjectIQ-Python-Fixtures --ref c0858c957f9f79f7459f57c24cb9d0c07c43ad84 --out "$run\flask"
```

Also choose at least two unfamiliar public repositories (prefer a Next.js/DB app and a mixed frontend/backend project), pin their commit if possible, and use the same command. Do not change any approval registry. Exit 0 = planned; 3 = planned with limitations; 2 = input/planning failure. Only 0 and 3 can have a completed plan. Existing output directories cannot be overwritten. A directory can also be analyzed without network:

```powershell
python -m projectiq_executioner.phase8a --source-dir "C:\path\to\trusted\snapshot" --out "$run\local"
```

Inspect:

```powershell
$p = Get-Content "$run\minicollab\execution_plan.json" -Raw | ConvertFrom-Json
$p.source
$p.components | Select-Object root,ecosystem,role_hint,framework_hints,adapter_candidate,status,blockers | Format-List
$p.admission
$p | Select-Object vm_launches,commands_executed,functional_tests_executed,execution_authorized
```

Python API:

```python
from projectiq_executioner.phase8a import plan_repository, plan_directory, validate_plan
from pathlib import Path
plan = plan_repository('https://github.com/VarunRGB/FSDL', 'f358b6edda071da6666e587a8f1db054965ba106')
validate_plan(plan)
# local = plan_directory(Path('trusted-snapshot'))
```

## Validation and remaining limitations

The delivered isolated suite has 59 passing test cases covering 34 parameterized stack fixtures, multi-root and mixed-language projects, workspace conflict, lock/manifest errors, bounded reads, symlinks, secret/command omission, checksum/permission constraints, deterministic output, CLI no-overwrite and mocked pinned acquisition. It does not certify the completeness of stack discovery, the old regression suite, actual GitHub download behavior, VM isolation, dependency preparation or real application startup. Existing `evaluate`/`result.json` are intentionally unchanged; use separate planning API/CLI for 8A. Future 8B must consume pinned manifests afresh and validate operations; 8C must validate isolation before admission; 8D must validate runtime adapters. No raw plan or checksum should be accepted as approval. Avoid viewing untrusted path labels as HTML. The local directory path is operator-controlled; the scanner does not provide a concurrency-safe snapshot of a changing filesystem. Heuristics can miss custom files, dynamic imports, complex monorepos, services and framework conventions; a dependency name or literal port is not evidence of runtime use. Exact versions, locks, workspace membership, actual topology and startup instructions are not solved by this discovery phase. Existing known dependency preparation utilities should be reused rather than executed by the planner.
