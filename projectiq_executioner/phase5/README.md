# ProjectIQ Executioner — Phase 5: Code Quality and Complexity

**Release:** Polished MVP v0.3.1 (23 November 2026)  
**Output schema:** `0.3` (unchanged from v0.3)  
**Scoring model:** `0.3` (unchanged from v0.3)  
**Package:** `projectiq_executioner.phase5`  
**Execution model:** Bounded, read-only, source-based analysis. **Student programs are not executed.**

> **What Phase 5 does:** It examines an application's source files, reports source/function metrics, selected lint and maintainability findings, conditional algorithmic-complexity estimates, approximate repeated-code evidence, cautious code-quality indicators, and potential credential exposure **without installing dependencies, running student code, or invoking untrusted project configuration**.
>
>
> **What Phase 5 does not do:** It does not prove correctness, runtime performance, complete security, algorithmic optimality, or that any secret is valid. Its 0–100 indicator is **not an academic grade**. It does not replace professional language-specific linters and parsers.

## Quick start

Run the CLI from the ProjectIQ repository root using Python 3.11+ (tested with Python 3.13). No third-party Python runtime dependencies are required for the Phase 5 MVP.

```powershell
# Offline, local checkout — no repository code is executed.
python -m projectiq_executioner.phase5 `
    --source '.\projectiq_executioner' `
    --out 'outputs\executioner\phase5_self'

# Public GitHub checkout — Phase 1's safe downloader pins the requested ref
# to an immutable Git commit; never directly run the remote project.
python -m projectiq_executioner.phase5 `
    --source 'https://github.com/VarunRGB/Mini-Task-Collaboration-App' `
    --ref 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37' `
    --out 'outputs\executioner\phase5_minicollab'
```

Each run writes `code_quality.json` and `code_quality.md` in the chosen output directory. Specify an output directory **outside** a local source tree. For GitHub input, the CLI accepts *public HTTPS GitHub repository root URLs*, resolves a ref to a full commit SHA, downloads the bounded snapshot, and extracts it through the existing safe archive pathway. Private repositories, arbitrary Git hosts, Git history analysis, and remote build execution are not supported.

### Inspect the results in PowerShell

```powershell
$r = Get-Content 'outputs\executioner\phase5_self\code_quality.json' -Raw | ConvertFrom-Json
$r.summary | Format-List
$r.coverage | Format-List
$r.quality | Format-List
$r.security | Select-Object status,tracked_sensitive_files,scanned_source_files,scanned_additional_files,truncated | Format-List
$r.security.findings | Select-Object code,path,line,confidence | Format-Table -AutoSize
$r.functions | Sort-Object cyclomatic -Descending | Select-Object -First 15 path,name,cyclomatic,big_o,big_o_reason | Format-Table -AutoSize
$r.findings | Group-Object code | Sort-Object Count -Descending | Select-Object -First 15 Count,Name
```

Do **not** commit generated `outputs/` evidence unless your team has explicitly reviewed its sensitivity and retention rules. The report omits matched credential values, but file paths and source metadata still describe a repository.

## 1. Capabilities and actual analysis depth

| Area | Implemented in MVP | Evidence and important qualifications |
|---|---|---|
| Repository inventory | Files, language counts, source candidates, analyzed/skipped files, production/test separation | Based on the bounded Phase 1 indexer. Excludes typical dependency/build outputs. Detection is convention-based. |
| Source metrics | Physical, blank, comment-only and code-line counts; per-file metrics | Comment classification is language-aware but heuristic for some syntaxes. Counts are not semantic statements/LOC. |
| Function statistics | Name, source file, start/end line, physical length, inferred analysis method | Python uses `ast`; JS/TS/PHP/C/C++ use intentionally incomplete brace/lexical recognition. Other languages receive file metrics only. |
| Complexity hotspots | Python AST cyclomatic complexity and nesting; advisory brace-language metrics | Only AST-backed complexity can affect the quality indicator. Brace depth can include object literals. |
| Big-O estimates | Some constant, linear, nested-loop, sorting and other recognized patterns | **Conditional estimates**, not proofs; unsupported, recursion-heavy, dynamic, external-call, DOM/DB and ambiguous paths are `UNKNOWN`. |
| Static lint and smells | Bounded built-in rules; structural recommendations with source positions | Not Ruff, ESLint, PHPStan, clang-tidy or a type checker. Rules can miss many problems. |
| Duplication | Exact/normalized cross-file repeated-block matches, with approximate duplicated-line coverage | Boilerplate may be flagged; near-duplicates and semantic duplicates may be missed. |
| Improvement advice | Fixed recommendations associated with each finding | Human review is required; it does not rewrite the code. |
| Quality indicator | Evidence-dependent 0–100 indicator, component scores, weights, limitations, confidence | `PROVISIONAL_LIMITED` measures **available dimensions only**, not overall quality; score is uncalibrated. |
| Secret exposure indicators | Sensitive filenames, selected token formats, guarded high-entropy assignments | Flags plausible exposures in readable source (including HTML/CSS/SQL) and limited documentation/config scans. No value disclosure or credential validation. |

The phases complement each other: Phase 3 generates grounded test plans, Phase 4 runs approved functional checks in disposable VMs, and **Phase 5 remains a separate static inspection pathway**. A Phase 4 execution failure does not automatically invalidate the Phase 5 source report.

### Supported file types

- **Function metrics (varying completeness):** `.py`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.php`, `.c`, `.cpp`, `.cc`, `.cxx`, `.h`, `.hh`, `.hpp`.
- **Source-file metrics and secret patterns:** the above, plus `.java`, `.go`, `.rs`, `.cs`, `.kt`, `.swift`, `.rb`, `.dart`, `.vue`, `.svelte`, `.html`, `.htm`, `.css`, `.scss`, `.sql`, `.sh` and supported names/extensions in the Phase 5 `LANGUAGES` mapping.
- **Supplemental secret scanning:** selected `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.xml` files, subject to their own small read limits.
- **Intentionally excluded:** dependency/build folders, typical generated/minified files, secret-named file **contents**, symlinks and oversized/undecodable files; their exclusion or skip must not be interpreted as inspected code.

## 2. Source selection, coverage and evidence

Phase 5 reuses `projectiq_executioner.indexer.SourceIndex`. It identifies candidate extensions, reads files through `phase5.source.read_bounded`, and records skipped reasons instead of guessing. Typical limits in this release are:

| Bound | Value |
|---|---:|
| Repository indexed files | 12,000 (Phase 1 default) |
| Selected file records | 2,500 |
| Analyzed source files | 2,000 |
| Individual source file | 350,000 bytes |
| Aggregate analyzed source | 12,000,000 bytes |
| Reported functions | 5,000 |
| Reported quality findings | 750 |
| Reported credential indicators | 200 |
| Supplemental doc/config files | 64 files and 1 MiB total; 100 KB per file |

See `contracts.py` and `security.py` for authoritative values; future releases may change these. Reaching a function/finding/duplication limit is disclosed in `coverage.limits`, and an incomplete inventory cannot silently receive a complete quality score. Reports can be `ANALYZED`, `PARTIAL`, `NO_SOURCE`, or `ERROR`, independently of the quality score and secret-scan status.

**Coverage fields have different meanings:**

- `coverage.source_ratio`: readable source candidates divided by candidate source files—not parser precision.
- `coverage.function_file_ratio`: function-eligible files that were processed by the recognized discovery route—not a statement that every function was found.
- `coverage.function_body_ratio` and `production_function_body_ratio`: fraction of eligible code lines falling within recognized function spans. For PHP endpoints with top-level statements this may be low even if the file was read successfully.
- `coverage.ast_complexity_share`: share of recorded production function span lines backed by Python AST. C-style heuristic metrics do not count as AST complexity.
- `coverage.big_o`: counts functions with conditional estimates and those marked `UNKNOWN`.
- `security.status` has its **own** coverage and can be `PARTIAL` even when the quality inventory is otherwise usable.

The pipeline records repository-relative paths, line numbers where supported, fixed finding codes and non-sensitive explanations. No raw source snippets or token strings should appear in reports. Local-input provenance is labeled `local_unverified`; it does **not** certify the local files match a GitHub commit. A pinned GitHub report records the resolved commit SHA.

## 3. Linting, function statistics and suggestions

The built-in rule set detects selected patterns such as:

| Language | Selected checks |
|---|---|
| Python | Bare/empty `except`, mutable defaults, wildcard imports, `eval`/`exec`, some unreachable statements, comparisons to `None`, long functions and high AST decision counts. |
| JS/TS | `var`, `debugger`, `eval`, loose equality (advisory), console logging (advisory), approximate long/complex function patterns. |
| PHP | `eval`, legacy `mysql_query`, error suppression (advisory), procedural decision/loop markers (advisory). |
| C/C++ | `gets`, `strcpy`, `sprintf` patterns (severity depends on rule), along with basic structure checks. |
| Other analyzed text | Trailing whitespace and very long source lines; applicable source metrics. |

Rules are intentionally bounded and can have false positives. For brace languages, `cyclomatic` and `max_nesting` values are **heuristic**, not equivalent to compiler/AST control-flow measurements. Even a lint dimension of `100` means *no meaningful deduction under this small rule set*, not that a project passed a comprehensive lint audit.

Findings include `code`, `severity`, `confidence`, `path`, `line`, fixed `message`, and fixed `recommendation`. Structural and language-specific findings can be grouped in the Markdown report. `findings.py` is the authoritative rule catalog.

## 4. Time complexity: what is actually supported

The estimator in `complexity.py` is intentionally conservative. For selected Python AST patterns and simple C-style loops, it may report `O(1)`, `O(n)`, `O(m)`, `O(n+m)`, `O(n*m)`, `O(n²)` or `O(n log n)` (and supported related compositions). Exact support is limited to the patterns recognized in code.

Examples of **conditional** estimates:

```python
# Recognizable for typed numeric bounds and simple loop bodies:
def count(n: int):
    for i in range(n):
        pass

# Conditional on builtin list semantics and amortized append:
def build(n: int):
    result = []
    for i in range(n):
        result.append(i)
    return result
```

Examples that may remain `UNKNOWN` (correctly):

```python
def combine(a, b):
    return a + b             # Could be numeric, strings, lists or overloaded classes.

def process(records):
    return custom_api(records) # External/dynamic call; no safe cost contract.
```

The assumptions include built-in runtime behavior, fixed-width RAM numeric operations and type annotations being honored (Python does **not** enforce annotations). Standard Python integers are arbitrary precision, and object methods may execute unknown code. Some operations are amortized rather than worst-case per call. The estimator deliberately avoids confident estimates for many dynamic dispatch, recursion, comprehension, container mutation, database, network and UI paths. `UNKNOWN` is **not** a failure or a penalty and **does not enter the score**.

Dedicated analyzers such as `bigopy` and `worstcase`, or model-based Big-O classifiers, are **not bundled or run** in this MVP. Future optional analyzers would require pinned versions, isolated execution, license review and independent accuracy benchmarks.

## 5. Interpreting the quality indicator

The system reports a **static maintainability indicator**; it is not a student grade or a complete measure of source quality.

Nominal weights, before exclusions:

| Dimension | Nominal weight | Evidence basis |
|---|---:|---|
| `lint` | 30% | Built-in lint/code-smell findings per production lines of code; excludes advisory findings from the penalty. |
| `complexity` | 30% | Excess Python AST cyclomatic complexity, only when AST and function coverage thresholds are met. |
| `duplication` | 20% | Approximate proportion of duplicated **production** source lines detected by bounded cross-file matching. |
| `structure` | 20% | Very long production files and long recognized production functions. |

The score uses only measured dimensions. If complexity is not eligible, the 30% share is **not automatically perfect**: remaining weights are renormalized (lint 42.857%, duplication 28.571%, structure 28.571%). This is why a procedural PHP project might report `100` with status `PROVISIONAL_LIMITED`, `LOW` confidence and `complexity=NOT_MEASURED`: it means **no deductions in these limited checks**, *not* an overall 100/100 grade.

Status meanings:

| `quality.status` | Meaning | Presentation |
|---|---|---|
| `PROVISIONAL` | Sufficient supported evidence for the selected dimensions; still heuristic and uncalibrated | **Provisional static-maintainability indicator**, medium confidence |
| `PROVISIONAL_LIMITED` | Some dimensions cannot be supported, but enough file-level evidence remains | **Measured-dimensions-only indicator**, low confidence; not an overall grade |
| `INSUFFICIENT_COVERAGE` | Too little usable code, excessive unreadable source or truncated evidence | No numeric score; check `reason_codes` |

Score gates include fewer than 10 production code lines, less than 85% source-read coverage, or truncated evidence. AST-backed complexity is only used with sufficient AST share (at least 70%), production function-body recognition (at least 60%) and function-file recognition (at least 80%). Scores can change when the analyzer version or scoring rules change **even if student code does not**. Do not compare v0.2 and v0.3 results as a progress trend. Within v0.3.1, the formula and `quality.scoring_version='0.3'` are preserved from v0.3.

**Important limitations:** files in test/fixture locations are counted separately and do not dilute production quality dimensions; the split is naming-based and may misclassify projects. Some production code (e.g., procedural PHP or top-level JS) is outside named-function analysis. A score should be accompanied by its `status`, `confidence`, dimensions, weights, analyzer scope and repository revision. Formal grading or grade-equivalent comparisons require Phase 7 calibration.

## 6. Potential secret exposure indicators

Secret scanning is deliberately separate from code-quality scoring. It flags **potential exposure** using only filenames and bounded pattern matching. It never proves that a credential is active.

**Filename checks:** included `.env`, `.env.production`, `.npmrc`, `.pypirc`, known private-key filenames, private-key-like extensions, service-account-style names, and similar candidates. Template examples such as `.env.example`, `.env.sample`, `.env.template` are excluded from the `.env` filename warning. The word `tracked` in a finding indicates the file was **present in the scanned input**; for local directories that does *not* independently verify a Git index entry.

**Content checks (readable non-sensitive-named files):** selected GitHub token shapes, AWS access-key IDs, Google API keys, Stripe live secret keys, Slack token shapes, private-key PEM headers, and sufficiently long/high-entropy values in recognized credential assignments. Source files now include **HTML/HTM, CSS/SCSS and SQL**, including embedded JS/markup and SQL data. A bounded set of documentation/config files is also checked. A line mentioning “example” is not enough to suppress a clearly provider-shaped token elsewhere on that line; placeholder filtering for generic assignments operates on the **captured value**.

**Privacy controls:**

- The scanner **never opens the contents of `.env` or private-key-named files**. It only reports their filenames and a fixed code.
- Reports do not serialize matched token text, raw source, entropy values, or excerpts. Security findings have only `code`, repository-relative `path`, `line` (or null) and `confidence`.
- File content scanning is bounded and read-only. No credential is contacted, validated, or uploaded to a secret-verification service.
- Findings can be false positives; absence of indicators is **not evidence of complete security**. For detected real credentials, rotate/revoke them and audit Git history through an authorized process. **This MVP does not scan Git history**.
- Security scans may be `PARTIAL` due to skipped, truncated or unreadable inputs; additional documents and binary/unsupported formats are not exhaustively inspected.

Note that some *public* provider identifiers (for instance web API keys used alongside server-side access restrictions) may still match a recognizable pattern. A flagged match is a review lead, not proof of unsafe deployment.

## 7. Architecture and module map

```text
projectiq_executioner/
  indexer.py             Existing Phase 1 safe file inventory
  github_client.py       Existing bounded public GitHub snapshot retrieval
  safe_archive.py        Existing archive safety boundary
  phase5/
    __main__.py          CLI, input selection, output and status presentation
    pipeline.py          Read-only orchestration and report assembly
    source.py            Source selection, decoding, bounded reads, hashes
    lexical.py           Language-aware approximate line classification
    functions.py         Python AST + limited brace-language function spans
    complexity.py        Cyclomatic/nesting and conservative Big-O subsets
    findings.py          Fixed lint/structural findings and recommendations
    duplication.py       Bounded cross-file duplicate-block detection
    scope.py             Production-versus-test name heuristics
    procedural.py        Advisory top-level PHP control-flow markers
    security.py          Redacted, bounded filename and token-pattern scanning
    quality.py           Weighted, eligibility-aware heuristic indicator
    contracts.py         Strict JSON/report integrity validation and budgets
    report.py            Escaped Markdown and deterministic JSON output
    README.md            This document
  tests/
    test_phase5*.py      Unit, adversarial and cross-language regressions
```

No Phase 2/QEMU VM, untrusted tool plugins, student build scripts, external code-analysis services or AI models are involved in Phase 5's current analysis path. This is intentionally different from Phase 4's approved disposable-VM functional execution.

## 8. Report contract and consuming Phase 5 from other agents

The JSON is schema `0.3`, with top-level `source`, `analysis`, `summary`, `coverage`, `languages`, `files`, `functions`, `findings`, `duplications`, `security`, `quality`, `limitations`, `status`, `stage`, and `schema_version`.

- `files`: relative path, language, production/test scope, source-line counts, source SHA-256 where readable, function count, and skip reasons.
- `functions`: source location, function name, `PYTHON_AST`/brace heuristic method, complexity/nesting, Big-O result/confidence/reason, and scope.
- `findings`: fixed lint/maintainability codes, severity, confidence, source location and remediation suggestions.
- `duplications`: two source locations, matched line count and confidence. They are matches, not exact percentage of all semantically duplicated code.
- `security`: `status`, redacted indicator records, scan coverage and truncation flags. **Never treat `NO_INDICATORS_FOUND` as security attestation.**
- `quality`: integer `score` or null, `status`, dimension breakdown, normalized `weights_used`, `reason_codes`, `confidence`, `scoring_version`, `basis='HEURISTIC_NOT_A_GRADE'`.

The validator in `contracts.py` rejects unexpected fields, malformed paths, inconsistent report counts, implausible score weights, false coverage claims and non-whitelisted secret details. Phase 6/7/8 consumers should validate the JSON and **retain the status/confidence/source SHA**, rather than using `quality.score` alone. No quality or security result authorizes execution of unreviewed student code.

### Suggested presentation to a student or reviewer

> **Code quality indicator:** 100/100 on *measured dimensions only* (`PROVISIONAL_LIMITED`, low confidence). Lint 99, duplication 100, structure 100; complexity not measured. Several procedural PHP endpoints were not evaluated for function-level complexity. This is **not** an overall grade. Review findings and coverage before interpreting the score.

The example is illustrative. Numeric scores should never be promoted to official assessment grades without a validated rubric and calibrated benchmarks.

## 9. Testing and release gate

Run on the `executioner` branch from ProjectIQ's root:

```powershell
python -m unittest discover -s projectiq_executioner/tests -p 'test_phase5*.py' -q
if ($LASTEXITCODE -ne 0) { throw 'Phase 5 tests failed. STOP.' }

git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace/diff validation failed. STOP.' }
```

Use a **cross-language acceptance set**, rather than only MiniCollab: Python functions/AST, JS/TS with arrows/inline methods, PHP procedural endpoints, C/C++ function declarations, HTML/CSS/SQL token scanning, and malformed/oversized/symlink inputs. Include both clean fixtures and intentionally flawed/secret-bearing fixtures. Confirm that synthetic credential strings **never appear** in JSON, Markdown or CLI output; review `security.status` separately from `quality.status`.

The last patch includes `tests/test_phase5_final.py` for the finishing fixes and preserves the earlier Phase 5 regression suite. On Windows, test the **guarded install/check/verify** before acceptance. Preserve output evidence for local review; do not blindly stage it into Git.

Before committing, review `git status --short` and `git diff --cached --name-only`, and stage **only** the intended `projectiq_executioner/phase5/` code/README and Phase 5 tests. Don't include extracted ZIPs, candidate artifacts, .env files or generated reports.

## 10. Known limitations and explicit future scope

### Next practical improvements (not required for this MVP)

1. **Language-specific analyzers:** Use pinned Ruff for Python, ESLint for JS/TS, PHPStan for PHP and clang-tidy or a compiler frontend for C/C++ **only after** reviewing their execution/plugin/configuration boundaries. Project-provided executable configs must not run on the Windows host.
2. **Better parsers:** Replace brace heuristics with trustworthy, version-pinned AST parsers for JS/TS, PHP, C/C++, including top-level procedural code and modern language constructs. This is the main way to improve cross-language complexity coverage fairly.
3. **Big-O calibration:** Evaluate conservative estimates using manually checked algorithms and public benchmark suites. Consider `bigopy`/`worstcase` as isolated *secondary* tools only after examining their false-O(1), recursion, external-call and container-operation behavior. Optional AI models must not override grounded evidence.
4. **Duplication accuracy:** Token/AST-based clone detection, generated-code attribution, boilerplate classification and line-level overlap review. Current duplication ratios are heuristic.
5. **Secret scanning:** Broader token formats, context-sensitive false-positive reduction, authorized Git history scanning, optional local secret-scanning tool adapters. Never send user code/keys to third-party verification endpoints without explicit permission.
6. **Maintainability measurement:** Add test quality, size distribution, coupling/cohesion and module dependency metrics with evidence and a proven benefit to review decisions.
7. **Benchmark/scoring calibration (Phase 7):** Clean/defective cross-language repositories, blinded human reviews, false-positive/false-negative measurement, confidence calibration, stable scoring version and score fairness across different project sizes/stacks.
8. **Failure attribution (Phase 6):** Relate concrete Phase 5 findings to Phase 2/4 failures **only** when there is direct supporting evidence; never infer that high cyclomatic complexity caused a failed test.
9. **Agent integration (Phase 8):** Feed independently validated Phase 5 indicators into a shared result contract; preserve unknowns, provenance and scoring limitations rather than automatically translating a heuristic score into a rubric grade.

### What remains out of scope

- Guaranteed whole-project runtime/space complexity proofs, arbitrary-recursion solving, dynamic performance profiling and benchmarked production workloads.
- A comprehensive security audit, malware detector, credential validity checker, or arbitrary Git history forensic scanner.
- A full-language compiler, developer IDE, automated code repair platform or plagiarism analyzer.
- Replacing Phase 4 execution testing: static quality and functional correctness are distinct.

**Release decision:** Phase 5 is suitable as a **read-only, evidence-labeled MVP** if its final Windows regressions and cross-language smoke reports pass. It is **not** a calibrated grading or security-attestation service. Freeze it at this boundary and move the main development effort to Phase 6, with calibration and optional tool integration deferred to Phase 7.
