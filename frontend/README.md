# ProjectIQ faculty frontend

React + TypeScript + Vite interface for teachers: set up an evaluation, watch it run,
review and approve marks, inspect every agent's evidence, and print a report.

It is a separate package. It reads the backend's own artifacts and does not change
any agent, the orchestrator or the rubric engine.

## Run it

```powershell
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173, demo backend with generated fixtures
npm test             # scoring parity tests against the real engine
npm run build
```

### Run it against the real backend

```powershell
# From the combined ProjectIQ root, in its activated Python environment
$env:PROJECTIQ_BRIDGE_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(32))").Trim()
Write-Host "Local operator token: $env:PROJECTIQ_BRIDGE_TOKEN"
python frontend\bridge\server.py --root outputs\unified --allow-origin http://127.0.0.1:5173 --vm-config vm_config.json --review-root outputs\executioner --enable-trusted-demo

# In another terminal (no token in Vite environment or build)
cd frontend
$env:VITE_BACKEND="http"; npm run dev
# Open http://127.0.0.1:5173 and enter the local operator token when prompted.
```

The bridge (`bridge/server.py`, Python standard library only, binds to 127.0.0.1) composes
existing entry points; no agent or engine logic is changed:

| UI action | Backend call |
|---|---|
| New evaluation (STATIC or report only) | `python -m projectiq_core evaluate --report ... --rubric ...` with live progress from the backend's own output |
| REVIEWED / RESUME | `python -m projectiq_executioner.orchestration --request ... --vm-config ...`, then `projectiq_core collect` for the other agents, `projectiq_core bundle`, and `projectiq_core evaluate --evaluation ... --acknowledge-imported` |
| Import an existing Executioner result | the same bundle path without a VM |
| Approve, override, remarks, exclude optional | validated with the real `scoring.assess()`; rejected decisions come back with the engine's code (for example `EVIDENCE_COVERAGE_NOT_MET`) |
| Change weights, total marks, applicability, levels | `orchestrator.reuse_decisions()` then `assess()`; only criteria whose policy changed lose their mark |
| Reassess a criterion / retry a deferral | `python -m projectiq_core.phase2 propose --criteria ... [--retry-deferred]` (local Ollama) |

Teacher work is saved beside each run in `faculty/` (`review.json`, `decisions.json`,
`assessment.json`, `current.json` after a reassessment, `reassess_*/`). The original
`result.json`, `rubric/` and `collection/` files are never modified. Put
`faculty_meta.json` (`{"students": [...], "course": "..."}`) next to older runs to show names.

REVIEWED and RESUME retain their specialized run-then-bundle path and acknowledged
imported receipt provenance. Bridge job actions separately describe what ran now.
Automatic evaluation (TRUSTED_DEMO) uses the existing unified `evaluate()` directly
with `--trust-demo-source --demo-vm-config` and optional `--confirm-disposable-state`.
It is opt-in at server startup and requires the local operator to confirm an exact
commit. It never requires reviewed catalogs or imported-evidence acknowledgment.
Generated observations do not become independently reviewed marks. Unsupported
projects may retain only static/readiness evidence. See `F1_GUIDE.md`.

## Where the data comes from

| UI uses | Backend artifact | Contract |
|---|---|---|
| Grade, criteria, groups | `result.json` → `assessment` | `phase2/contracts.py` `Assessment` 2.0 |
| Rubric | `result.json` → `rubric` | `Rubric` 2.0 |
| Agent views | `result.json` → `agents` | `orchestrator._frontend_agents()`, native fields kept by `adapters.py` |
| Evidence explorer, citations | `rubric/evidence.json` | `EvidenceBundle` 2.0 |
| Model decisions (defer/retry) | `rubric/decisions.json` | `DecisionSet` 2.0 |
| AI-writing estimate | `agents.integrity.ai_detection` | `AIAnalysis`, Desklib detector output (merged) |
| IEEE format check | `result.ieee_format` (`projectiq_core/ieee_integration.py`) | advisory score, verdict, categories and checks; shown in the Format tab, overview and report |
| Additional semantic matches | `agents.integrity.details.matches[].secondary_review` | advisory second NLI pass (merged), never counted |

All types are mirrored in `src/api/types.ts`. No new data contract was invented.
Teacher review state (approvals, overrides, remarks, rubric changes) is a
frontend-side layer (`TeacherReview`) mapped onto `Decision` objects with
`source: "TEACHER"`, the shape the engine already accepts.

## Demo fixtures are produced by the real engine

`tools/generate_fixtures.py` builds schema-faithful native outputs for three fictional
projects, then runs the actual `collect()` → `build_evidence()` → `evaluate()` →
Phase 2 rubric engine. Only the Ollama rubric model is replaced by a deterministic
stand-in answering through the real constrained schema, so hardening still rejects or
defers proposals like a live run. Regenerate with `npm run fixtures` (from `frontend/`,
Python env with the root requirements). Add `--keep DIR` to also keep the full run
directories so the bridge can serve and re-grade them.

| Fixture | Shows |
|---|---|
| CampusConnect | Imported RESUME/REVIEWED Executioner evidence (fresh + reused suites, failed and blocked tests), research with metadata conflicts, integrity overlap and AI estimate, research criterion deferred so the grade is withheld |
| MediTrack | Live STATIC run, Research agent failure, no reference corpus (similarity unavailable), AI detector missing |
| Irrigation | Report-only submission, custom template, research excluded with weight redistribution, complete proposed grade |

## Visual design notes

* The run screen shows one progress ring per step (format check, summary, integrity, research,
  executioner, rubric) joined by flowing arrows, plus an overall loading bar. Running steps show a
  time-based estimate that only reaches 100% when the backend reports the step finished; the
  rubric step uses the real batch count when available. No logs are shown to teachers.
* Results use rings (section scores, tests passed, verified references, format areas), donuts
  (test outcomes, verification, relevance) and speed-test gauges (AI-like writing, overlap, format
  score). Missing values are drawn as an empty dashed ring labelled n/a, never as 0.

## Grading behaviour

* Proposed (AI) marks are drawn dashed violet; approved marks solid teal; unassessed
  criteria are hatched and never counted as zero. The complete grade is withheld until
  every applicable criterion has a mark.
* Changing weights, section weights, total marks or applicability recalculates instantly
  with `src/lib/scoring.ts`, a port of `scoring.assess()`. `npm test` checks it against
  the engine's own `reweight()` for all fixtures. No agent reruns.
* Changing a criterion's levels, descriptors or evidence policy discards only that
  criterion's mark (same rule as `orchestrator.reuse_decisions`) and offers a
  rubric-model reassessment for that criterion only.
* Model deferrals can be retried (`--retry-deferred`). Teacher overrides need a remark.
* With the bridge, every teacher change is re-validated by the engine before it is saved.

## Limits that remain

* The unified `evaluate()` supports STATIC and explicit TRUSTED_DEMO. REVIEWED and RESUME
  retain the separate reviewed/import path and operator approvals.
* Stop requests wait for the current backend command to finish; a unified command may finish
  the whole evaluation. Never kill the bridge or VM while active. One bridge process only.
* The bridge is a trusted local operator tool for one machine, not a multi-user service.
* Reassessment needs the local Ollama model; if it is not running, the UI says so and the
  criterion stays deferred for the teacher.

## Layout

```
src/api/            types, Backend interface, mock and HTTP backends
src/lib/            scoring port, rubric rules and templates, view model, labels
src/components/     UI primitives, mark-sheet ledger, evidence cards
src/features/       evaluations list, new-evaluation wizard, rubric library,
                    run progress, results tabs, printable report
src/mock/           generated fixtures and the backend's rubric files
tools/              fixture generator and its input reports
bridge/             local API over the backend (server.py)
```

Outputs contain confidential student work. Do not commit generated runs or expose the
bridge beyond 127.0.0.1.
