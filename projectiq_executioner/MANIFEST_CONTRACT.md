# ProjectIQ Executioner Phase 1 — Manifest Contract v1.0

The `manifest.json` file is the durable handoff from repository discovery to later phases. It is **descriptive** and **never a claim of successful code execution**.

## Core invariants

- `schema_version`: string `"1.0"`.
- `phase`: integer `1`.
- `source.kind`: `"github"` or `"local"`; GitHub sources carry canonical `owner/repo`, branch/ref, and full immutable commit SHA. Local sources have null commit SHA.
- `architecture`: `fullstack_web`, `frontend_web`, `backend_service`, `cli_or_library`, or `unknown`. This is a heuristic classification; not a verified deployability claim.
- `architecture_confidence`: `high`, `medium`, or `low`. Heuristic confidence, **not** a numeric probability.
- `languages`: map of file-extension-classified language to count of indexed files. A language's presence does not prove it is used in production.
- `technologies`: name/category/confidence with one or more `evidence` locations.
- `services`: inferred **logical** frontend/backend/database components or Compose-declared service candidates. A record does not establish a separate process/container, confirm startup, or prove credentials are available.
- `routes`: records with `path`, `method`, `kind`, `confidence`, `evidence`. Backend route records and `client_http_call` records are distinct observations; their sum is **not** a count of unique working HTTP endpoints. `client_http_call` means source code appears to issue a request, not that the server responds. PHP `file_endpoint` paths are repository paths, not confirmed public URLs. Client-method hints in proposed targets do not establish accepted server methods.
- `pages`: statically discovered page files.
- `entrypoints`: startup, declared script, page, or PHP endpoint hints. **Not commands to execute directly.**
- `features`: README claims and other bounded static cues, with provenance. They are not validated requirements or automatically runnable tests.
- `tests`: discovered test files/configurations/test scripts; **not test results**.
- `setup_files`, `package_scripts`, `environment_variables`, `database_schema_files`: setup inventory. Package script **bodies are withheld** because repository content is untrusted. Environment variables are **names only**, not values.
- `findings`: structured risk/diagnostic warnings. Credential-like assignments include **file and line, never a captured secret**.
- `unknowns`: explicit information the source scan cannot establish.
- `proposed_test_targets`: suggested areas for later test-oracle design, **not** test cases and not pass/fail assessments.
- `inventory`: scan counts and bounded inspection status. Optional additive metadata now includes `file_roles` (repository-relative filename → naming-based role labels), `file_role_counts` (aggregate role counts), and `unresolved_api_sites` (bounded filename, line, pattern-kind entries for unresolved dynamic expressions). None proves completeness or behavior. The top-level schema remains `1.0` for backward compatibility.
- `limitations`: fixed Phase 1 limitations users should see.

## Evidence model

```json
{
  "path": "src/server.ts",
  "line": 17,
  "reason": "Static route or HTTP client call"
}
```

All paths are repository-relative with forward slashes. Line numbers are 1-based or null if the evidence is a filename convention. `reason` describes the detection rule and does **not** include raw code snippets, credential values, or LLM-generated claims.

## Contract for Phase 2

Phase 2 may consume `services`, `entrypoints`, `setup_files`, `environment_variables`, `database_schema_files`, `source.commit_sha`, and `unknowns` to propose a sandbox plan. It **must not** assume a route exists, start a command directly from a README, or mark environment readiness solely from Phase 1 evidence.

## Contract for Phase 3

Phase 3 may use `features`, `routes`, `pages`, and `proposed_test_targets` as candidates. It must build a validated **expected result / test oracle** independently, preserve provenance, and mark ambiguous or blocked tests rather than inventing expected behavior.

## Versioning policy

Future incompatible field removals, field semantics changes, or structure changes require a schema version increment and migration tests. New optional data may be appended without silently changing the meaning of existing fields.


## Cross-stack discovery annotations

The `routes[].kind` vocabulary includes `file_endpoint`,
`declared_http_route`, `file_based_route`, `client_http_call` and
`page_route`. A `page_route` records a framework-derived UI URL pattern;
its GET method is a conventional browser-page inference, **not** an
observed successful request. A `client_http_call` indicates source usage,
**not** a working backend endpoint. These records must never be added
up to claim independent server endpoints.

A `services[].kind = external_dependency` identifies evidence of an
external service: either a hosted HTTP API whose implementation is *not* in
the inspected snapshot or a cloud dependency such as Amazon S3 evidenced by
SDK usage. Phase 2 must treat it as **unprovisioned and unverified**, and
must not direct destructive integration tests at production resources.
An authorized, isolated test backend or mock cloud service is required. `source_inferred` features derived from file-based pages have
low confidence and are not independently verified functionality.

## Expanded API operation kinds (additive)

New route kinds include `form_action` (literal root-relative form submission URL, not proof of working behavior), `declared_api_contract` (HTTP methods declared in OpenAPI/Swagger), `graphql_operation` (`method=QUERY|MUTATION|SUBSCRIPTION`) and `rpc_method` (`method=RPC`). GraphQL and gRPC are **not HTTP routes** and must never be counted or probed as REST endpoints. A declared API contract may be inaccurate or out of date until validated independently.

Source-derived mount prefixes are applied only for supported literal import/mount conventions; potentially dynamic paths are not fabricated. Unresolved patterns are exposed in `inventory.unresolved_api_sites` without argument strings. Refer to `NOMENCLATURE_COVERAGE.md` for detailed support boundaries and test fixtures.

## Phase 1 locked baseline — additive handoff metadata (November 2026)

The current baseline retains `schema_version = "1.0"`. Its `inventory` has two
additional *optional* fields:

- `environment_variable_details`: map of **variable name to static hints**:
  `required_hint`, `default_present`, `origin`, `path`, and `line`.
  Direct annotated Pydantic `BaseSettings` fields are recognized. Values, including
  defaults, are **never** serialized. A required hint is not proof that no other
  runtime configuration source can satisfy the requirement.
- `api_links`: a list of static matches between client call patterns and one
  uniquely matching locally declared endpoint. Fields include client/server
  paths, methods, evidence source paths, `confidence`, and `status`. Literal
  route parameters are normalized by *position*, not by variable spelling;
  `:param` and `:file_id` can match. An unknown PHP server method remains
  `method_unverified` even when the frontend calls GET/POST. Endpoint
  correspondence is **not** evidence of a successful request.

Matched client calls remain separately visible in `routes`; Phase 1's
`proposed_test_targets` includes a single contract target for the matched
server operation instead of generating duplicate client/server targets. This
is an additive metadata and target-list refinement, not a change to what a
route record means. Ambiguities remain unknown.

Phase 2 consumes Phase 1 **as untrusted data**. New Phase 2A preflight plans
are separate JSON artifacts; they do not mutate or upgrade Phase 1 reports.
