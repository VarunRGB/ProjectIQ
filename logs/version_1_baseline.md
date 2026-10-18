# ProjectIQ — Version 1 Baseline Report

**Performance, summarisation behaviour, observed outcomes, limitations, and Version 1.1 starting point**

Baseline date: 18 October 2026  |  Report status: Initial prototype benchmark  |  Model path: Local Ollama

> **Note:** Purpose: preserve the Version 1 evidence as a reproducible reference before changing the summariser in Version 1.1. All measured values below are retained from the runs shared for this baseline. Calculations have been rechecked; methodology caveats are called out explicitly.

## Executive summary

Version 1 is an initial text-based project evaluation pipeline. It combines text-signal analysis, LLM-based summarisation, rubric-based judging, evidence checks, and recommendation/report generation. The current model setup reported for this experiment is Ollama running llama3.1:8b locally.

Six test files were benchmarked through the full pipeline. Total measured runtime was 3,663.295 seconds (61 minutes 3.295 seconds), with an average of 610.549 seconds per document (10 minutes 10.549 seconds) and a median of 527.339 seconds (8 minutes 47.339 seconds). The slowest was the large scalability report at 1,045.064 seconds (17 minutes 25.064 seconds).

Standalone runs were also recorded for signals, summarisation, and a rubric mode. Signals took 0.004–0.194 seconds. The latest standalone summariser run ranged from 25.888 to 430.160 seconds. The rubric-mode run ranged from 364.133 to 576.052 seconds, but that mode prepares its judging input by invoking summarisation; it is therefore not a pure measurement of judge-only time.

- Input-size trend: in the latest summary run, the ~7,000-token target file took 9.81 times as long to summarise as the ~1,000-token target file. These are target labels, not verified tokenizer counts.
- Primary Version 1.1 measurement gap: calls, retries, chunk counts, actual prompt/output tokens, map time, reduce time, and judge-only time were not independently logged.
- Critical quality observation: an observed highly technical test report showed evidence-grounding score 0.00 with 5/5 evidence quotes not found, yet the full-pipeline recommendation was ACCEPT. This is a correctness issue to investigate before treating recommendations as reliable.
## 1. Purpose, scope, and terminology

This document records what Version 1 currently does, what was measured, the exact timing data shared, how the basic statistics were calculated, and what remains unverified. It is intended to function as the baseline log for evaluating Version 1.1 changes.

The benchmark uses the term “agent” because the local runner exposes modes such as summary, signals, rubric, and all. These modes should not be taken as proof that each component is an autonomous agent. The measurements are runner-mode wall-clock times unless per-function timings are explicitly available.

This is an initial engineering benchmark, not a statistically controlled performance study. Runs were not repeated a fixed number of times under documented, identical warm/cold model conditions. Exact tokenizer counts, host resource utilisation, model quantisation, and software versions beyond the supplied OS/model details were not recorded.

## 2. Version 1 workflow

| Stage | Role in the current pipeline | What this baseline establishes |
| --- | --- | --- |
| Input loading | Load the text report being evaluated. | Six local text files were exercised; broader file-format support was not established. |
| Text signals | Compute lightweight text or content signals. | Standalone timings were sub-second and negligible relative to LLM-oriented operations. |
| Summariser | Condense the report into a summary and key points. | Measured separately twice; runtime grows strongly for the longer test reports. |
| Rubric judging | Evaluate the prepared project information against a rubric. | A rubric runner mode was timed, but it includes summarisation/preparation and cannot be treated as judge-only time. |
| Evidence checks | Check whether cited evidence supports evaluation claims. | At least one observed technical test had a serious grounding mismatch. |
| Recommendation/report | Produce a decision such as ACCEPT or REVISE_AND_RESUBMIT and a report. | The full pipeline produced an outcome for all six test files; outcome correctness was not independently validated. |


High-level flow: input text → signals → summary → rubric judgement → evidence checks → recommendation/report. Exact orchestration may include additional report/configuration steps; this is the functional pipeline represented by the Version 1 experiment notes.

## 3. Environment and hardware

The hardware details below were supplied for this report. The benchmark runner did not record live CPU/GPU/RAM utilisation or thermal state, so the table documents the reported machine rather than asserting that any specific resource was saturated during a run.

| Property | Reported configuration |
| --- | --- |
| Processor | AMD Ryzen 5 7535HS with Radeon Graphics, 3.30 GHz |
| Installed RAM | 16.0 GB installed; 13.3 GB reported usable |
| Graphics | AMD Radeon(TM) 660M integrated graphics; 2 GB reported |
| Storage | 477 GB total; 266 GB used at the time reported |
| Operating system | Windows 11 Home Single Language, 64-bit, x64-based |
| Windows version / build | Version 25H2; OS build 26200.9457 |
| OS install date | 05 August 2025 (interpreting the supplied date as day-month-year) |
| Python version | Not recorded in the benchmark log |
| Ollama version | Not recorded in the benchmark log |
| LLM model | Ollama local model: llama3.1:8b |
| Model quantisation / GPU offload settings | Not recorded |
| Power mode / background load / thermal metrics | Not recorded |


> **Note:** For reproducibility, Version 1.1 should save environment metadata alongside every timing run. Do not include device serial numbers, Windows Product IDs, or device IDs in benchmark reports; they do not help explain model performance.

## 4. Test corpus

| Test file | Intended size / test purpose | Notes |
| --- | --- | --- |
| small_1000_tokens.txt | ~1,000-token target | Short report about a full-stack car-rental system. |
| medium_4000_tokens.txt | ~4,000-token target | Medium report about the same full-stack car-rental system. |
| large_7000_tokens.txt | ~7,000-token target | Long report about the same full-stack car-rental system. |
| 1_poorly_written.txt | Content-quality case | Intentionally poorly written submission/report. |
| 2_highly_technical.txt | Content-quality case | Highly technical report. |
| 3_ai_written.txt | Content-quality case | AI-written test case used to observe system behaviour. |


The 1,000 / 4,000 / 7,000 token labels are intended test-size targets; exact counts from the model tokenizer were not saved. Character count and chunk count were also not saved with the timing records. The remaining three test files are labelled by quality/style, not by a verified token count.

## 5. Full-pipeline baseline results

| Test file | Runtime (s) | Runtime (min) | Pipeline result |
| --- | --- | --- | --- |
| small_1000_tokens.txt | 554.720 | 9.25 min | ACCEPT |
| medium_4000_tokens.txt | 778.029 | 12.97 min | ACCEPT WITH MINOR REVISIONS |
| large_7000_tokens.txt | 1,045.064 | 17.42 min | ACCEPT WITH MINOR REVISIONS |
| 1_poorly_written.txt | 381.281 | 6.35 min | REVISE AND RESUBMIT |
| 2_highly_technical.txt | 499.958 | 8.33 min | ACCEPT |
| 3_ai_written.txt | 404.243 | 6.74 min | FLAG FOR REVIEW |


| Aggregate metric | Value |
| --- | --- |
| Number of documents | 6 |
| Total runtime | 3,663.295 s (61.055 min) |
| Mean runtime per document | 610.549 s (10.176 min) |
| Median runtime per document | 527.339 s (8.789 min) |
| Fastest run | 381.281 s (6.355 min) — 1_poorly_written.txt |
| Slowest run | 1,045.064 s (17.418 min) — large_7000_tokens.txt |
| Max–min range | 663.783 s |
| Population standard deviation (descriptive only) | 233.569 s |


Observed full-pipeline size scaling for the three car-rental reports:

| Comparison | Absolute runtime change | Relative change |
| --- | --- | --- |
| Small → Medium | +223.309 s | +40.3% |
| Medium → Large | +267.035 s | +34.3% |
| Small → Large | +490.344 s | +88.4% |


The large report took 88.4% longer than the small report in the full-pipeline run. This is a useful baseline observation, but it does not isolate summarisation: the full runtime also includes judging, evidence checks, report creation, retries where they occur, and other orchestration overhead.

## 6. Standalone stage/mode timings

The following were separate runner invocations, rather than internal timers recorded in a single full-pipeline execution. They should be compared cautiously because warm/cold model state, system load, and run-to-run variability were not controlled or documented.

### 6.1 Signals mode

| Test file | Runtime (s) |
| --- | --- |
| small_1000_tokens.txt | 0.194 |
| medium_4000_tokens.txt | 0.036 |
| large_7000_tokens.txt | 0.089 |
| 1_poorly_written.txt | 0.009 |
| 2_highly_technical.txt | 0.005 |
| 3_ai_written.txt | 0.004 |


| Statistic | Value |
| --- | --- |
| Total | 0.337 s |
| Mean | 0.056 s |
| Median | 0.0225 s |
| Minimum | 0.004 s |
| Maximum | 0.194 s |
| Range | 0.190 s |


Observation: signal computation is extremely fast in these runs compared with the tens-to-hundreds of seconds recorded for summarisation and the hundreds of seconds recorded in rubric mode. This conclusion is limited to the measured runner path and these six inputs.

### 6.2 Summariser mode — first standalone run

| Test file | Runtime (s) | Runtime (min) |
| --- | --- | --- |
| small_1000_tokens.txt | 71.192 | 1.19 min |
| medium_4000_tokens.txt | 268.861 | 4.48 min |
| large_7000_tokens.txt | 613.726 | 10.23 min |
| 1_poorly_written.txt | 40.589 | 0.68 min |
| 2_highly_technical.txt | 53.377 | 0.89 min |
| 3_ai_written.txt | 35.418 | 0.59 min |


| Statistic | Value |
| --- | --- |
| Total | 1,083.163 s (18.053 min) |
| Mean | 180.527 s (3.009 min) |
| Median | 62.2845 s (1.038 min) |
| Minimum | 35.418 s — 3_ai_written.txt |
| Maximum | 613.726 s — large_7000_tokens.txt |
| Range | 578.308 s |


In this earlier run, the small / medium / large target-size reports took 71.192 / 268.861 / 613.726 seconds respectively. The large report took 8.62× as long as the small report.

### 6.3 Summariser mode — later standalone run

| Test file | Runtime (s) | Runtime (min) |
| --- | --- | --- |
| small_1000_tokens.txt | 43.862 | 0.73 min |
| medium_4000_tokens.txt | 197.600 | 3.29 min |
| large_7000_tokens.txt | 430.160 | 7.17 min |
| 1_poorly_written.txt | 25.888 | 0.43 min |
| 2_highly_technical.txt | 44.408 | 0.74 min |
| 3_ai_written.txt | 28.923 | 0.48 min |


| Statistic | Value |
| --- | --- |
| Total | 770.841 s (12.847 min) |
| Mean | 128.474 s (2.141 min) |
| Median | 44.1350 s (0.736 min) |
| Minimum | 25.888 s — 1_poorly_written.txt |
| Maximum | 430.160 s — large_7000_tokens.txt |
| Range | 404.272 s |


In this later run, the small / medium / large reports took 43.862 / 197.600 / 430.160 seconds. The large report took 9.81× as long as the small report, about 880.7% more time.

Every entry in the later summary run was faster than the matching entry in the first summary run. This must not be reported as a software optimisation: no code change or controlled cause was established between the two measurements. Possible confounders include different model warmness, system state, background load, or other settings, but none was confirmed in the log.

### 6.4 Rubric runner mode — later standalone run

| Test file | Runtime (s) | Runtime (min) |
| --- | --- | --- |
| small_1000_tokens.txt | 537.635 | 8.96 min |
| medium_4000_tokens.txt | 544.002 | 9.07 min |
| large_7000_tokens.txt | 576.052 | 9.60 min |
| 1_poorly_written.txt | 370.074 | 6.17 min |
| 2_highly_technical.txt | 470.094 | 7.83 min |
| 3_ai_written.txt | 364.133 | 6.07 min |


| Statistic | Value |
| --- | --- |
| Total of six rubric-mode runs | 2,861.990 s (47.700 min) |
| Mean per rubric-mode run | 476.998 s (7.950 min) |
| Median | 503.8645 s (8.398 min) |
| Minimum | 364.133 s — 3_ai_written.txt |
| Maximum | 576.052 s — large_7000_tokens.txt |
| Range | 211.919 s |


> **Note:** Interpretation caveat: according to the runner logic used for these measurements, rubric mode first creates the summary / judging input and then performs the rubric judge calls. Therefore, these 364.133–576.052 second values represent the complete rubric-mode path, including summary preparation. They are not isolated rubric-judge timings. The number of seconds spent inside judge calls alone remains unknown.

## 7. Summariser implementation recorded for Version 1

The current implementation was inspected before planning Version 1.1. The following describes the implementation recorded for the current public project version; it should be reconfirmed against the exact local commit before a formal release.

| Component / setting | Version 1 behaviour recorded |
| --- | --- |
| Model / transport | Local Ollama model llama3.1:8b, accessed through Ollama chat API at /api/chat; not a paid external cloud API in this path. |
| Short-input path | A short report can be sent for a single summarisation call. |
| Long-input path | Map-reduce pattern: split report into chunks, summarise each chunk, then reduce the intermediate summaries into one final summary. |
| Chunk maximum | 6,000 characters per chunk. |
| Overlap | 400 characters between adjacent chunks. |
| Map output instruction | Each chunk summary targets 2–4 sentences and 2–5 factual key points. |
| Reduce output instruction | Final summary targets 4–6 sentences and 3–6 factual key points. |
| Nominal call count | 1 call for the short-input path; for N chunks, approximately N map calls plus 1 reduce call (N + 1). |
| Response format | JSON/schema-constrained structured response. |
| Context / model retention | Context setting recorded as 8,192; Ollama keep_alive recorded as 15m. |
| Retry behaviour | A failed JSON/schema response can be retried once; actual retries were not counted by the benchmark log. |
| Concurrency | Chunk summarisation requests appear sequential in the described path; parallel map execution was not established. |


Important: chunk sizes are measured in characters, not model tokens. The test-file labels such as “1000 tokens” do not reveal actual chunk counts. For a long report split into N chunks, the nominal N+1 call count applies only when all calls succeed on the first attempt. Schema failures/retries can increase the number of actual HTTP model requests.

Current summary-length policy: the final reduction prompt specifies a fixed 4–6 sentence summary and 3–6 key points rather than adapting the requested final length directly to source-report size. This is the clearest initial design question for Version 1.1: test whether a size-aware target improves coverage without producing bloated summaries.

## 8. Cross-run observations

### 8.1 Input size and summary latency

| Summary comparison | Run 1 latency | Run 2 latency | Observation |
| --- | --- | --- | --- |
| Small target (~1,000 tokens) | 71.192 s | 43.862 s | Later run is faster, but cause is unknown. |
| Medium target (~4,000 tokens) | 268.861 s | 197.600 s | Longer source takes much longer than the small source. |
| Large target (~7,000 tokens) | 613.726 s | 430.160 s | Highest summary runtime in both runs. |


In the latest summary measurements, moving from the ~1,000-token target report to the ~7,000-token target report increases runtime by approximately 9.81×, while the intended size ratio is 7×. This is only a coarse comparison because exact token counts, characters, chunk counts, and output token counts were not stored. It does not by itself identify whether prompt processing, number of chunks, generation length, or retries caused the increase.

### 8.2 Rubric-mode runtime does not isolate the judge

Rubric mode has a high wall-clock total, but the runner first calls the summariser. Adding summary-mode time to rubric-mode time would double-count work conceptually, because summary preparation is already included in rubric mode. Therefore, a combined “summary + rubric” total or a claim that the rubric judge alone consumes a particular percentage of runtime is not valid from these measurements.

The simple comparison of rubric-mode time across the three size targets rises only from 537.635 seconds (small) to 576.052 seconds (large), about 7.1%. However, this is not a clean measurement of judge cost: the summary sub-operation has separate timing variation across runs, and mode-level execution is not instrumented internally. Version 1.1 must separate summary, judge-input preparation, each judge model call, evidence checks, and report generation within the same run.

### 8.3 Signal mode

Signals mode took 0.337 seconds total across all six documents, a mean of 0.056 seconds and a median of 0.0225 seconds. This is several orders of magnitude below the LLM-oriented wall-clock paths in the recorded runs. Unless profiling shows otherwise, optimising signals is a lower priority than instrumenting and optimising model calls.

### 8.4 Full-pipeline outcome distribution

| Pipeline outcome | Count | Test(s) |
| --- | --- | --- |
| ACCEPT | 2 | small_1000_tokens.txt; 2_highly_technical.txt |
| ACCEPT WITH MINOR REVISIONS | 2 | medium_4000_tokens.txt; large_7000_tokens.txt |
| REVISE AND RESUBMIT | 1 | 1_poorly_written.txt |
| FLAG FOR REVIEW | 1 | 3_ai_written.txt |


These outcomes show that the pipeline returned differentiated recommendations on the six cases. They do not establish classification accuracy: there is no independent gold-standard evaluation in this baseline, and at least one observed evidence-grounding anomaly contradicts the confidence implied by the ACCEPT result.

## 9. Known quality issue and reliability concerns

### 9.1 Evidence-grounding mismatch

An observed report for the highly technical test had an evidence-grounding score of 0.00 and 5/5 evidence quotes reported as not found, while its final recommendation was ACCEPT. This is a high-priority failure mode. It may indicate that missing or invalid evidence is not sufficiently connected to the final decision, or that the evidence matcher / quote-normalisation logic is failing; the exact root cause has not been established.

Version 1.1 should preserve the complete judge input, cited quotes, match results, grounding score, and final recommendation for a reproducible failing example. The system should also test whether a zero grounding score forces a review / downgrade, unless an explicitly justified exception exists.

### 9.2 Other limits of the current evidence

- No repeat-run distribution: averages and medians here summarise six different test inputs, not repeated trials of the same input. They are not a measure of run-to-run jitter.
- No exact token telemetry: target labels were used, but actual model tokenizer counts and generated output token counts were not captured.
- No internal call traces: total HTTP requests, retries, time-to-first-token, map-call times, reduce-call times, and judge-only time are not isolated.
- No recorded live hardware telemetry: CPU/GPU use, RAM pressure, clock speed, fan/thermal throttling, disk activity, and Ollama GPU offload were not logged during each run.
- No independent quality rubric: summary factual coverage, claim support, omission rate, and hallucination rate were not scored against a human-labelled reference.
- No controlled model conditions: model/quantisation details, prompt versions, exact configuration, power mode, and warm/cold state were not recorded with every timing row.
## 10. What Version 1 does and does not claim

### Version 1 does

- Accept a text report through the current evaluation path and produce a recommendation/report for the six tested files.
- Compute lightweight text signals, summarise text using an LLM, and perform rubric-oriented judging with evidence checks in the current pipeline.
- Handle longer reports through chunking and map-reduce summarisation, subject to current chunk and prompt limits.
- Run with a local Ollama model, reducing dependence on a paid cloud-model endpoint for the described text path.
### Version 1 does not yet demonstrate

- A fully autonomous multi-agent architecture. Pipeline stages and model calls are not, by themselves, proof of independent agent planning and coordination.
- Independent execution or correctness testing of submitted source code.
- Comprehensive plagiarism detection or reliable proof that a report was human-written versus AI-written.
- That cited claims are true merely because they appear in a summary or judge output.
- Statistically validated confidence, grading accuracy, or production-scale performance.
- Comprehensive support for arbitrary file formats, very large corpus sizes, concurrent users, or production deployment conditions.
## 11. Version 1.1 plan: measurement before optimisation

| Priority | Change / experiment | What to record or compare | Why it matters |
| --- | --- | --- | --- |
| P0 | Add internal per-operation timing | Chunking, each map request, reduce request, summary total, judge-input preparation, each judge sample, evidence checks, final report creation. | Separates true bottlenecks and avoids double-counting nested operations. |
| P0 | Add request-level observability | HTTP request count, retries, response status, prompt tokens, generated tokens, latency per call; time-to-first-token if available. | Explains where time is spent and why calls repeat. |
| P0 | Record input metadata | Characters, tokenizer input tokens, chunk count, overlap, chunk lengths, summary word count, key-point count. | Makes size-versus-output behaviour measurable. |
| P0 | Fix / gate evidence grounding | Persist quote matches and test zero-score behaviour against final recommendation. | Addresses the most important observed correctness risk. |
| P1 | Run repeated controlled baselines | At least 3 repeated runs per test under a documented warm/cold-model protocol; report median and spread per same document. | Separates input differences from run-to-run variance. |
| P1 | Test adaptive summary length | Compare fixed 4–6 sentences against size-aware word/sentence bands. | Tests whether large reports need longer summaries, without simply scaling length linearly. |
| P1 | Test chunk-size/overlap alternatives | Change one setting at a time; report chunk count, calls, runtime, coverage, and grounding. | May reduce request overhead but can affect context and information retention. |
| P1 | Quality-check summaries | Human or reference-based fact coverage, numbers/technical-term preservation, unsupported statements, omissions, redundancy. | Speed is not an improvement if important information is lost. |
| P2 | Profile the host during runs | CPU/GPU/RAM use, clock speeds, temperature, memory and GPU-offload details. | Helps determine whether runtime is model-bound, thermal-bound, or resource-bound. |


---

## 12. Baseline interpretation and conclusion

Version 1 establishes a usable functional baseline but not a fully instrumented or validated benchmark. Its full pipeline took an average of about 10.2 minutes per document across the six supplied cases. Signals were negligible in the recorded measurements. The summariser is strongly size-sensitive, with the largest target-size report taking 9.81× the small target-size report in the latest summary run.

The rubric-mode numbers should be treated as cumulative mode runtimes, not judge-only latency, because the current runner prepares a summary before judging. The earlier informal interpretation that attributed a share of combined summary-plus-rubric timing to the rubric judge should not be used as a measured finding. Internal instrumentation is necessary to make that distinction.

For Version 1.1, the cleanest first step is instrumentation-only: do not change prompts, chunking, or model settings yet. Capture per-call timings and token/chunk metadata, repeat the baseline under controlled conditions, and save the summary outputs. Then test adaptive output length and chunking against both performance and factual coverage. The evidence-grounding anomaly should be tracked as a correctness issue, not merely a performance issue.

### Version 1.1 comparison anchors

| Measure | Version 1 baseline | Interpretation |
| --- | --- | --- |
| Full-pipeline runtime across six documents | 3,663.295 s total; 610.549 s mean; 527.339 s median | End-to-end baseline; includes all stages and orchestration. |
| Slowest full-pipeline case | large_7000_tokens.txt — 1,045.064 s | Use same file and setup for repeat comparisons. |
| Signals mode | 0.337 s total; 0.056 s mean; 0.0225 s median | Low-priority optimisation target unless profiling changes this finding. |
| Summary mode, latest run (small / medium / large) | 43.862 / 197.600 / 430.160 s | Large-to-small ratio: 9.81×; exact tokenizer counts were not recorded. |
| Rubric runner mode | 476.998 s mean; 503.865 s median | Includes summary preparation; not pure judge-only latency. |
| Known quality risk | Technical test: grounding score 0.00; 5/5 quotes not found; outcome ACCEPT | Track as a correctness issue alongside performance. |


---

## Appendix A. Raw measurement ledger

This appendix restates every timing set in readable tables for copying into CSV or a results notebook. All runtimes are seconds.

### A1. Full pipeline and outcome

| File | Full pipeline (s) | Full pipeline (min) | Exact outcome |
| --- | --- | --- | --- |
| small_1000_tokens.txt | 554.720 | 9.25 min | ACCEPT |
| medium_4000_tokens.txt | 778.029 | 12.97 min | ACCEPT WITH MINOR REVISIONS |
| large_7000_tokens.txt | 1,045.064 | 17.42 min | ACCEPT WITH MINOR REVISIONS |
| 1_poorly_written.txt | 381.281 | 6.35 min | REVISE AND RESUBMIT |
| 2_highly_technical.txt | 499.958 | 8.33 min | ACCEPT |
| 3_ai_written.txt | 404.243 | 6.74 min | FLAG FOR REVIEW |


### A2. Signals mode

| File | Signals (s) |
| --- | --- |
| small_1000_tokens.txt | 0.194 |
| medium_4000_tokens.txt | 0.036 |
| large_7000_tokens.txt | 0.089 |
| 1_poorly_written.txt | 0.009 |
| 2_highly_technical.txt | 0.005 |
| 3_ai_written.txt | 0.004 |


### A3. Summariser mode, run 1 and run 2

| File | Summary run 1 (s) | Summary run 2 (s) |
| --- | --- | --- |
| small_1000_tokens.txt | 71.192 | 43.862 |
| medium_4000_tokens.txt | 268.861 | 197.600 |
| large_7000_tokens.txt | 613.726 | 430.160 |
| 1_poorly_written.txt | 40.589 | 25.888 |
| 2_highly_technical.txt | 53.377 | 44.408 |
| 3_ai_written.txt | 35.418 | 28.923 |


---

### A4. Rubric runner mode

| File | Rubric mode (s) |
| --- | --- |
| small_1000_tokens.txt | 537.635 |
| medium_4000_tokens.txt | 544.002 |
| large_7000_tokens.txt | 576.052 |
| 1_poorly_written.txt | 370.074 |
| 2_highly_technical.txt | 470.094 |
| 3_ai_written.txt | 364.133 |


Rubric mode includes summary preparation according to the runner path used for this baseline; it is not judge-only time. The exact status codes observed in the original outputs were ACCEPT, ACCEPT_WITH_MINOR_REVISIONS, REVISE_AND_RESUBMIT, and FLAG_FOR_REVIEW.

## Appendix B. Benchmark metadata still needed

- Git commit hash / local repository state at experiment time.
- Python version, Ollama version, full model identifier and quantisation, and complete ProjectIQ settings.
- Exact input characters, actual tokenizer input tokens, chunk count, chunk lengths, final summary word count, and output token counts.
- Per-call timestamps, HTTP request count, retries, and map-versus-reduce timing.
- Repeated runs of the same file under controlled model warmness and system-load conditions.
- Hardware telemetry during each run and whether Ollama actually used the integrated GPU or mostly CPU inference.
- A human-reviewed expected outcome and summary-quality checklist for each test case.
