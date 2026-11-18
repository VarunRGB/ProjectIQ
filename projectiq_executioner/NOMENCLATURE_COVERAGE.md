# Phase 1 — Common API and Filename Convention Coverage

**Purpose:** broad, conservative recognition of real-world naming/routing conventions without executing submitted code, importing source files, contacting referenced API endpoints, or assuming endpoints work.

**Reliability rule:** an identified route is a *source-backed candidate*. A route is not a PASS, a test, or evidence of a responding server. When a literal path or mount cannot be safely derived, preserve uncertainty instead of fabricating a URL.

## Detectable styles

| Ecosystem | Examples recognized | Boundaries |
|---|---|---|
| Node Express / Koa-style routers | `app.get`, `router.post`, named `express.Router()` instances, chained `router.route('/x').get().post()`, CommonJS/ESM router imports and literal nested `app.use('/prefix', router)` mounts | Does not resolve arbitrary runtime router factories, function-generated URLs, wildcard logic, or every export form |
| Fastify | `.get('/x')`, `.post('/x')`, `fastify.route({method:'GET',url:'/x'})` | Plugin `register` prefixes and runtime route expressions may remain unresolved |
| Hono | Common `.get('/x')` style | Dynamic chains/mounts not guaranteed |
| NestJS | `@Controller('prefix')`, `@Get(':id')`, `@Post()` | Module-level global prefix and runtime metadata not proven |
| Next.js | App Router `app/**/page.tsx`, `app/**/route.ts`, exported method functions/consts; Pages Router pages and `pages/api` with `[id]` | Optional catch-all/parallel routes and dynamic rewrites remain uncertain |
| SvelteKit | `src/routes/**/+page.svelte`, `+server.ts` exported HTTP methods | Unusual route matcher details remain uncertain |
| Nuxt | `pages/index.vue`, `pages/[id].vue` (with Nuxt config); `server/api/*.get.ts` etc. | Custom route rules / transforms not evaluated |
| React/Vue/Angular client routers | Literal React Router `<Route path>` and `createBrowserRouter`, Vue `createRouter`, Angular `Routes` configs | Nested path composition and dynamic runtime route objects require later framework-aware analysis |
| Flask/FastAPI | `@app.route`, `@router.get`, `APIRouter(prefix=...)`, blueprint prefixes, local `include_router`, simple imported router modules | Arbitrary import aliases, decorators, dynamic `prefix` expressions not guaranteed |
| Django | `path('...')`, including `<int:id>` converters | `include()` composition, dynamic `re_path()` and REST framework router expansion not guaranteed |
| Java/Kotlin Spring | `@RequestMapping` class prefix with `@GetMapping` etc., literal named path arguments | Complex arrays, meta-annotations and external server prefix configuration not guaranteed |
| ASP.NET Core | `MapGet/MapPost/...`, common `[Route]` + `[HttpGet]` controller attributes | Custom route conventions and runtime mapping not guaranteed |
| Laravel/PHP | File-backed PHP endpoints, `Route::get/post/...`, `Route::apiResource` | Runtime route groups/aliases not fully resolved; Laravel route declaration files are not incorrectly treated as public PHP endpoints |
| Go | `http.HandleFunc`, Go 1.22 `GET /x` ServeMux forms, common Gin/Echo/Fiber/Chi `.GET/.POST` routes | Complex router groups and middleware prefixes not guaranteed |
| Ruby on Rails | Simple `routes.rb` `get/post/put/patch/delete` declarations | `resources`, namespaces, concerns and mount composition not yet complete |
| Rust | Actix `#[get('/x')]` / `#[post('/x')]`, Axum `.route('/x',get(...))` | Complex nested router prefixes or macros remain uncertain |
| JS/TS HTTP clients | `fetch`, limited proven `fetch` wrappers (including nested default arguments and typed `request<Result>` calls), literal local API base prefixes, finite string-branch ternaries, and provable optional query-only suffixes, axios methods, axios-created shared instances, object-style axios, `ky`, `$fetch`/`useFetch`, Angular `HttpClient`, known optional query templates | Arbitrary URL construction or computed method names not interpreted; no API calls executed |
| HTML form submissions | Literal root-relative `<form action="/path" method="POST">` (or GET-default) | Relative form actions without a confirmed page base stay unresolved |
| Python HTTP clients | `requests`, `httpx`, common constructed session/client aliases | Variables holding URL values are not evaluated |
| API contracts | OpenAPI/Swagger JSON; conservative standard `paths` method blocks in YAML; GraphQL `type Query/Mutation/Subscription` fields; gRPC proto `service ... rpc` | No arbitrary YAML tags, schema imports or full OpenAPI semantic validation; GraphQL/gRPC are **not** REST endpoints |

**Important:** This is a capability inventory, *not a measured percentage of detection on the public GitHub population*. The tests are fixtures for supported patterns, not proof that any arbitrary repo's endpoints will be discovered perfectly.

## Filename and role inference

Every indexed file appears in `manifest.json → inventory.file_roles` with one or more **naming-based** labels, such as:

`dependency_manifest`, `dependency_lockfile`, `configuration`, `deployment_config`, `documentation`, `ci_workflow`, `ui_page`, `ui_component`, `ui_layout`, `ui_app_entrypoint`, `http_client`, `api_or_route`, `controller_or_handler`, `service_logic`, `model_or_type`, `database_access`, `authentication_logic`, `module_initializer`, `database_schema_or_migration`, `test`, `styling`, `static_asset`, `api_contract`, `other_source` and `other_file`.

`inventory.file_role_counts` provides a summary. A filename alone never proves implementation behavior. Unclassified files are retained rather than omitted.

## Data types and interpretation

- `declared_http_route`, `file_based_route`: server-side route candidates from source conventions; not confirmed reachable.
- `declared_api_contract`: HTTP operations declared by an OpenAPI/Swagger contract, which may be stale.
- `client_http_call`: source appears to construct a client request; no server contract is proven.
- `page_route`: candidate UI URL, **not** a verified HTTP GET.
- `graphql_operation`, `rpc_method`: *non-REST* API operations, never merged into HTTP route counts.
- `inventory.unresolved_api_sites`: bounded file/line/pattern diagnostics for common dynamic API expressions that the static analyzer cannot safely resolve. This is **not** a completeness denominator and doesn't prove an API endpoint exists.
- `external_dependency`: source references an external HTTP service. The analyzer intentionally does **not** connect to it or infer the remote server's implementation/database.

If a repository uses a language/framework API format that is absent from this table, the detector may return incomplete results. Those should be handled through a later parser/adapter extension rather than by guessing.

## Validation

Run all offline fixtures from the ProjectIQ repository root:

```powershell
python -m unittest discover -s projectiq_executioner/tests -p "test_*.py" -v
```

The fixtures exercise supported patterns plus safety invariants (no execution, no secret values or remote hosts copied into client-route labels, unknown dynamic expressions, isolated router prefixes, and correct evidence locations). For empirical coverage, also run the CLI on unrelated real repositories and manually compare the output against their source. Do not report the 81 fixtures as an externally validated recognition percentage.

## Security note for Phases 2–4

Static discovery may read route declarations containing untrusted text, but it never runs package scripts, starts containers, or calls API endpoints. In later phases, remote service calls (especially authentication, payment, orders, mutations, or admin APIs) require an *authorized, isolated test environment*. An external production API URL is **not** permission to test that service.

## Monorepos and external cloud dependencies (CloudDrop-style validation)

A single repository can include `frontend/` and `backend/` with independent npm and pip manifests. The analyzer recognizes common React/Vite and FastAPI conventions, including Python router prefixes. Static evidence such as a `sqlite:///...` configuration literal can identify a **SQLite dependency**; `boto3.client("s3")` identifies **Amazon S3** as an external cloud dependency, not a locally provisionable service. The mere presence of `boto3` does not prove S3 usage.

A proved JavaScript wrapper may use default argument calls such as `token = localStorage.getItem(KEY)`, and a literal `/api` prefix can be combined with literal paths supplied to that wrapper. One finite string-valued URL ternary can yield two possible route candidates, with no assumption which branch executes. Arbitrary computed suffixes, credentials, and runtime-generated URLs stay unresolved.

This is based on static source declarations, not confirmation of an S3 bucket, deployed backend, or working API. Phase 2 must use **isolated, authorized mocks/test resources**, never a real production bucket by default.
