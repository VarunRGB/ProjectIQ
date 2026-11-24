# ProjectIQ v1.5 — unified evaluation foundation

## Scope and roadmap

Base reviewed: `0b4bbdef6b01ec2669448608f513dfe1af8f7ce0`. This additive package does not modify the four existing agents, their dependencies, VM approvals, or configuration.

1. **Unified foundation** — shared result contract, adapters, offline import, local sequential collection. Gate: a valid four-agent evaluation envelope and failure isolation; real agent acceptance on a local machine.
2. **Rubric engine** — teacher-defined criteria, explicit evidence references, required coverage, strict scoring policy and reproducible proposed marks; separate advisory integrity evidence. Gate: missing, conflicting and fabricated evidence cannot silently become marks; compare to faculty judgments.
3. **Faculty experience** — React/TypeScript/Vite results dashboard, upload workflow, rubric builder, detailed agent views, evidence explorer, print preview. Gate: real saved evaluation data renders accurately, including partial and failed states. No mock data presented as real.
4. **Unified PDF and live bridge** — shared view model for accessible print report and UI, minimal local Python API, real run statuses and artifact access. Gate: submission to report workflow; no default untrusted VM execution.
5. **Release acceptance** — representative project fixtures, evaluation and grading validation, usability, resource checks, end-to-end regression, root README and version log. Gate: demo reproducible and known limitations disclosed.

Phase 1 deliberately has no final grade, no frontend, no automatic marks deductions and no server. It does not implement the future AI detector. The goal is to prevent UI and rubric work from depending on four incompatible native data formats.

## Install and test

From the ProjectIQ root, activate the existing virtual environment. The core itself uses Pydantic v2 (already in root requirements). For offline contract tests only, the agents and their models need not be importable.

```powershell
python -m unittest discover -s tests -p "test_projectiq_core_phase1.py" -v
python -m projectiq_core --help
```

## Collect live outputs (local smoke test)

Use the report, reference documents and GitHub repository of **one actual submission**. Existing model prerequisites remain: Ollama and configured model, local neural model caches for Integrity and Research, and the normal agent Python dependencies. A single judge sample is a smoke-test default, not full confidence; use `--samples 3` for a fuller run. The live runner requests offline Hugging Face model loading, but this is not a network sandbox. GitHub ingestion needs network access. Online research metadata verification is opt-in with `--verify-research`; do not enable it without permission to use the relevant bibliographic metadata. No VM is launched.

```powershell
$report = Read-Host "Path to the actual project report (PDF, DOCX, TXT or MD)"
$refs = Read-Host "Path to its reference papers folder"
$repo = Read-Host "GitHub HTTPS URL (https://github.com/owner/repo, without .git)"
$title = Read-Host "Project title"
$out = "outputs\unified\phase1_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
python -m projectiq_core collect --report $report --references $refs --repo $repo --title $title --submission-id "MVP-001" --samples 1 --out $out
$exit = $LASTEXITCODE
$r = Get-Content "$out\evaluation.json" -Raw | ConvertFrom-Json
$r.agents.PSObject.Properties | ForEach-Object {
    [pscustomobject]@{Agent=$_.Name; Status=$_.Value.status; Native=$_.Value.native_status; Error=$_.Value.error_code}
} | Format-Table -AutoSize
Write-Host "Collector exit: $exit"
```

Optionally specify an immutable Git commit with `--ref <40-character SHA>`. Select agents using `--agents summarizer integrity research executioner` (or any subset). The reference folder is optional. Without it, Integrity still attempts its native AI-writing analysis against a private empty reference corpus; similarity is unavailable, not zero. Research parses the bibliography from the report itself; no bibliography yields limited research coverage rather than a collection failure. External reference PDFs are used for text comparison, not required to extract citations. Without a repository URL, Executioner is not run. A custom report-writing rubric may be supplied with `--rubric PATH`. The legacy rubric score is not the final project grade.

The collector returns exit 2 if an individual agent fails, preserving all other results. Exit 1 denotes a collection/setup error. Exit 0 does not mean every assessment is complete; review statuses and limitations. A run uses a new output directory. Do not reuse or overwrite it.

## Bundle existing JSON results without running agents

This is for operator-selected results known to concern the same submission. The importer checks report names and exact repository identity/SHA, but cannot establish historical report-byte identity or authenticate imported results. Never combine unrelated agent outputs and present them as a genuine combined evaluation.

```powershell
python -m projectiq_core bundle --report $report --title $title --submission-id MVP-002 --repo $repo --ref $sha --summarizer $summaryJson --integrity $similarityJson --research $researchJson --executioner $executionJson --out "outputs\unified\import_$(Get-Date -Format yyyyMMdd_HHmmssfff)"
```

Set the variables to the actual matching artifacts first. Any subset can be imported. Integrity requires the detailed `*_similarity.json` (`DocumentReport.to_dict()`), not the notes-only `AgentResult`. Research requires `ResearchExtraction.to_dict()` from `analyze_research_document`, including `research_report`, not Phase 1/2 alone. Executioner imports can include existing reviewed runs but require an exact requested SHA and matching repository. The live path only runs STATIC.

## Contract and integration

`evaluation.json` contains `schema_version`, `submission`, `mode`, `agents`, `limitations` and a reserved `final_assessment: null`. Every agent is represented even if not run. Each envelope has a status, native status, typed metrics with units/scopes, structured details, limitations and a native artifact receipt. Native JSON is saved separately under `native/`. The private report snapshot is in `input/`; Executioner also saves its detailed artifacts in `executioner_artifacts/`. These outputs contain confidential student text, findings and local paths: do not commit or publish them. SHA-256 receipts identify saved bytes, not proof of accuracy or authenticity. Frontend work must render untrusted strings as text and redact local paths for exported reports. The current collector is a trusted local CLI, not a hardened service.

`projectiq_core/evaluation.schema.json` is the frontend schema. Regenerate with `python -m projectiq_core schema` or use `--out NEW_PATH`. Adapter details retain native field names and nested structures; Phase 2 will add a common evidence-reference layer rather than inventing source offsets or treating every native statement as verified. Phase 1 uses conservative PARTIAL status for legacy report-writing and research outputs because required rubric/verification coverage has not been certified. Native generation status is retained separately. No combined mark is assigned. A genuine measured zero is different from a null/unavailable value.

### Future Integrity AI percentage

The unified `agents.integrity.ai_detection` object is present even when the feature is missing. Current default:

```json
{"status":"NOT_AVAILABLE","estimated_percent":null,"interpretation":null,"confidence":null,"flagged_passages":[],"limitations":[]}
```

When the teammate's detector is ready, explicitly map its documented output to `AIAnalysis`. `COMPLETED` requires a finite number 0–100 and a nonempty interpretation defining what the percentage measures. `NOT_AVAILABLE`, `INCONCLUSIVE` and `FAILED` must carry null, never 0. The current adapter accepts an explicitly structured native `ai_detection` subsection, and isolates invalid AI data without discarding similarity. No assumption about the teammate's yet-unfinished native field names is made. The frontend/PDF must label estimates as advisory; no automatic penalties or misconduct decisions.

## Acceptance and limitations

The package was tested against synthetic contract-shaped inputs, including isolated exceptions, missing references, source mismatch, invalid AI values, exact saved-byte receipts and STATIC-only request construction. These tests do not run Ollama, neural models, GitHub ingestion, or QEMU. Native output signatures were inspected at the base commit; a real local collection remains necessary. Do not start designing grading logic around fake scores. Use the first real unified run to refine evidence normalization before Phase 2.

### Local model memory lifecycle

The unified live collector keeps Ollama warm during the Summarizer stage, then asks the local server to unload only its configured model and checks `/api/ps`. This is attempted in a `finally` block after a successful model availability check, even if evaluation fails. The private `ollama_release.json` receipt records the outcome; failed verification warns without discarding a valid report. This assumes a dedicated local Ollama instance: other clients using the same model can be affected. It does not terminate the Ollama server or remove downloaded weights. `UNLOAD_VERIFIED` and `ALREADY_UNLOADED` refer only to the model listing at the check time, not a measured RAM guarantee. Integrity/Research Python models can still remain cached in the process. Before enabling reviewed VM execution in the future, use separate worker processes, enforce available-memory admission and retain existing VM review gates. The current live collector remains STATIC-only.
