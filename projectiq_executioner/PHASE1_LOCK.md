# Phase 1 baseline lock — November 2026

**Status:** Baseline scope locked after three different repository architecture
samples. Latest small fixes are covered by offline regression tests; users
should perform a fresh live rerun of each pinned repository for the last-mile
cross-platform release check.

| Sample | Previously validated evidence |
|---|---|
| Mini Task Collaboration | PHP + MySQL, 10 PHP file endpoints, 8 frontend requests, 25 indexed files |
| Moss and Meadows | Next.js/React TypeScript, 15 frontend requests, 20 UI URL patterns, external HTTP API |
| CloudDrop | FastAPI + React/Vite, 11 declared backend HTTP routes, 9 of 10 recognizable frontend API operations before final query-suffix fix, SQLite + Amazon S3 |

Final refinements made here: known optional query-string suffixes; Pydantic
BaseSettings required/default **names only**; static frontend/backend endpoint
correlation and deduplicated proposed test targets. Existing raw route records
are preserved. New fields are optional and schema version remains `1.0`.

## Acceptance criteria

- [x] Three distinct architectures analyzed without importing student code.
- [x] Phase 1 original routes/technologies tests still pass.
- [x] Unit regression fixtures for optional query suffix, Pydantic and route correlation.
- [x] Static safety properties: pinned provenance, archive controls and no secret values.
- [ ] Fresh pinned CloudDrop GitHub rerun with this final patch and manual JSON review.
- [ ] Fresh pinned Mini Task + Moss & Meadows reruns with this patch.

## Limits of the lock

"Locked" means a versioned, stable, explicitly scoped feature baseline,
**not** 100% recognition of every repository. Heuristic detection never proves
an endpoint responds, that an external resource exists, or that a project
starts. Unknowns must continue to be surfaced and cannot be turned into tests
with fabricated expected results.
