# ProjectIQ — Version 1.1 Summary Agent Change Log

**Scope:** Changes made to the Summary Agent after Version 1 was established  
**Log compiled:** 20 October 2026  
**Status:** Current Version 1.1 Summary Agent configuration recorded as finalized for this development round

> **Version boundary:** This is a standalone Version 1.1 change log. It does not repeat the Version 1 baseline report, the unchanged evaluation pipeline, or benchmark results for other agents. It records only the Summary Agent work reported as completed after Version 1 had been established. Where exact prompts, experiment counts, parameter sweep results, or measurements were not supplied, they are marked as not recorded rather than inferred.

## Executive summary

After Version 1 was established, the Summary Agent was iteratively refined to improve how it divides reports into chunks and how it reduces its final output. The development sequence was:

1. Test different prompts and select one prompt.
2. Test structure-aware chunking; retain it because it performed better in the observed tests.
3. Add post-processing because the final output was too long.
4. Try a standard BERT-based approach to reduce output size; it was not satisfactory and was not selected.
5. Move to vector embeddings using `sentence-transformers/all-MiniLM-L6-v2` and use them in the minimisation step.
6. Tune parameters and finalize the current settings.

The finalized configuration records a target range of 200–300 words and 8–15 sentences, with `mmr_lambda = 0.66`. The exact embedding model identifier is `sentence-transformers/all-MiniLM-L6-v2`.

In the observed tests, summary size changed with report size and was described as directly proportional to the report size. No per-test word-count series was supplied, so the direction of this relationship is recorded as an observation, not as a calculated correlation or a proven mathematical rule.

Two earlier large-report validation runs took 452.574 seconds and 514.069 seconds for nine chunks. A subsequent final run processed all six test files, with a suite wall-clock runtime of 1,331.600 seconds; the per-file timings and internal Ollama telemetry are recorded in Section 8. In that final run, the large report took 765.239 seconds across nine chunks, and the highly technical report took 63.591 seconds in one chunk. The highly technical summary preserved many important metrics, but it stated `ef_search=64` while the source report says the final configuration kept `ef_search=128`; that discrepancy remains a known summary-quality observation.

## 1. Scope and Version 1.1 boundary

This log begins after Version 1 had already been established. It is focused exclusively on changes to the Summary Agent and does not reprint the Version 1 end-to-end measurements, hardware profile, signals-mode measurements, rubric-mode measurements, or recommendations produced by the wider pipeline.

The Version 1.1 changes documented here concern:

- prompt selection;
- chunking strategy;
- post-processing to reduce an overlong final output;
- evaluation of a BERT-based minimisation approach;
- adoption of MiniLM embeddings for the minimisation step;
- final parameter settings and observed summary behaviour.

The information supplied does not establish that the main generative LLM was changed as part of this work. The MiniLM identifier below is recorded specifically as the model used for the embedding-based minimisation step; it should not be described as replacing the generative LLM unless that is separately confirmed.

## 2. Change history, in order tested

### 2.1 Prompt experiments and prompt selection

**Change tested:** Different prompts were tried for the Summary Agent.

**Decision:** One prompt was selected and retained as the finalized prompt for the current version.

**Evidence available:** The developer confirmed that prompt variants were tested and one was finalized.

**Not recorded:** The complete text of each prompt, the number of prompt variants, per-prompt run times, individual output examples for every variant, or numerical quality scores. No prompt names or scores have been added to this log because they were not supplied.

### 2.2 Structure-aware chunking

**Change tested:** Structure-aware chunking was introduced and compared with the earlier approach.

**Observed result:** Structure-aware chunking performed better in the tests that were run.

**Decision:** Structure-aware chunking was retained in the finalized Summary Agent.

**Not recorded:** The precise parsing/splitting rules, supported document structures, settings for each attempted chunker, or a numerical before/after comparison. No unprovided chunk-size or overlap setting is attributed to the finalized structure-aware implementation.

### 2.3 Post-processing added because the final output was too long

**Problem observed:** The final summary output was too long for the intended use.

**Change made:** A post-processing/minimisation step was added to reduce the final output.

This step is distinct from the initial prompt and chunking changes: prompt selection and chunking address how report information is summarised, while the subsequent post-processing work was introduced specifically because the resulting final output was too long.

**Not recorded:** The exact date of the change, the original output-length distribution, or a before/after table of word counts for the first post-processing attempt.

### 2.4 BERT-based minimisation attempt

**Approach tested:** A standard BERT-based approach was tried to reduce the length of the generated summary.

**Observed result:** The BERT approach was not good enough for the intended output, according to the developer's assessment.

**Decision:** It was not retained as the final approach.

**Not recorded:** The exact BERT model identifier, implementation details, parameter values, representative BERT outputs, timing, or a numerical evaluation. This log therefore records the outcome qualitatively and does not claim a specific failure mode beyond the reported unsatisfactory result.

### 2.5 MiniLM embeddings and summary minimisation

**Approach selected:** The development moved from the unsatisfactory BERT attempt to vector embeddings with `sentence-transformers/all-MiniLM-L6-v2`, followed by summary minimisation.

**Decision:** The MiniLM embedding model was retained for the current configuration.

The exact identifier recorded for the final configuration is:

```python
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
```

**Not recorded:** A complete table comparing BERT and MiniLM, exact embedding/vector count, similarity thresholds, or a separate runtime for the MiniLM step. No such values are inferred here.

### 2.6 Parameter tuning and finalization

After moving to MiniLM-based minimisation, parameters were tuned over multiple attempts and a configuration was selected. The final recorded values are listed in Section 3.

**Not recorded:** The full parameter search space, number of tuning runs, per-run values, intermediate outputs, and a quantitative ranking of all parameter combinations. Only the finalized settings and the qualitative observation about output length are preserved in the supplied notes.

## 3. Finalized Version 1.1 configuration

| Setting | Final recorded value | Meaning in this log |
| --- | --- | --- |
| `min_words` | `200` | Configured lower word-count bound for the summary minimisation step. |
| `max_words` | `300` | Configured upper word-count bound for the summary minimisation step. |
| `min_sentences` | `8` | Configured lower sentence-count bound. |
| `max_sentences` | `15` | Configured upper sentence-count bound. |
| `mmr_lambda` | `0.66` | Final recorded MMR lambda value. |
| `MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Exact embedding model identifier used by the selected minimisation approach. |
| Chunking method | Structure-aware chunking | Retained after it performed better in the observed tests. |
| Prompt | One selected prompt | Prompt experimentation was completed, but the prompt text was not supplied for this log. |
| Post-processing | MiniLM embedding-based minimisation | Selected after the final output was found to be too long and the BERT-based attempt was not satisfactory. |

Equivalent compact configuration record:

```python
min_words: int = 200
max_words: int = 300
min_sentences: int = 8
max_sentences: int = 15
mmr_lambda: float = 0.66
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
```

These are the final settings reported by the developer. The supplied information does not include the source-code file/commit that implements them, so this log records the values but does not claim a code-level audit was performed.

## 4. Observed summary-length behaviour

The developer reported that the exact summary size depends on report size and, according to the tests, is directly proportional to report size.

This log records that as an observed relationship from the tests described. The available records do not include word counts for every input document, so a numerical proportionality coefficient, correlation, or fitted formula cannot be calculated from the supplied data.

| Behaviour or constraint | Current recorded status |
| --- | --- |
| Summary length varies with report size | Observed and reported by the developer. |
| Larger report produces a larger summary in the tests | Reported as directly proportional in the tested cases. |
| Configured word-count bounds | `min_words = 200`; `max_words = 300`. |
| Configured sentence-count bounds | `min_sentences = 8`; `max_sentences = 15`. |
| Per-test exact word counts | Not supplied in the recorded examples. |
| Per-test exact sentence counts | Not supplied as a separate measurement. |
| Mathematical relationship between input size and output size | Not calculated; “directly proportional” is retained as the reported test observation, not asserted as a proven formula. |

## 5. Validation records for the finalized Summary Agent

Only the tests and outputs described in the supplied notes are recorded below. The times are wall-clock runtimes as reported; warm/cold state, background load, and other environmental controls were not supplied for these individual tests.

### 5.1 Prior large car-rental report: repeat-run record before the final suite

**Input:** `large_7000_tokens.txt`  
**Chunk count:** 9 in both recorded runs  
**Earlier recorded runtime:** 452.574 seconds  
**Rerun before final all-file suite:** 514.069 seconds

| Run | Chunks | Runtime (seconds) | Runtime (minutes) |
| --- | ---: | ---: | ---: |
| Earlier recorded run | 9 | 452.574 | 7 minutes 32.574 seconds |
| Rerun before final all-file suite | 9 | 514.069 | 8 minutes 34.069 seconds |
| Difference: rerun minus earlier | — | +61.495 | +1 minute 1.495 seconds |

The 514.069-second rerun was approximately **13.6% slower** than the earlier 452.574-second run. Both had nine chunks. Chunk count alone does not establish that all workload details were identical, and the available information does not identify the cause of the timing difference. It is therefore recorded as run-to-run variation, not as evidence of a code regression or an improvement.

#### Summary output from the 514.069-second rerun

The following preserves the latest summary supplied for `large_7000_tokens.txt`. The spelling/spacing in the supplied output is retained where possible so that the record represents the observed output, rather than a silently edited version.

**Main summary:**

> The Full-Stack Car Rental System is a web-based application designed to digitize and simplify vehicle rental operations. It provides a centralized platform for customers to browse vehicles, compare rental prices, and create reservations, while administrators can maintain vehicle inventory and supervise reservations.The application follows a client-server architecture and uses REST APIs with JSON data. The system must account for concurrent booking requests, unauthorized access, invalid input, incomplete database operations, and unexpected server failures. The application must allow new customers to register, verify credentials, and establish an authenticated session. The vehicle catalogue must display active listings and allow customers to filter by category, price, and other criteria. The booking module must validate pickup and return dates, calculate rental charges, and create a reservation. The system uses a MySQL database with primarykeys, foreign keys, and unique constraints. The frontend communicates with the backend through HTTP requests and JSON. The application layer contains the Express backend, which authenticates users, authorizes actions, and validates requests. The system should expire sessions according to defined rules and configure cookie flags such as HttpOnly, Secure, and SameSite policy. The user interface should provide clear and accessible information to customers, while the data privacy and operational policies should protect sensitive customer information.

**Key points returned by the summariser:**

- The system uses a client-server architecture with REST APIs and JSON data.
- The application must prevent duplicate accounts when email addresses are used as unique identifiers.
- The vehicle catalogue must display active listings and relevant specifications.
- The booking module must validate pickup and return dates, calculate rental charges, and create a reservation.
- The system uses a MySQL database with primary keys, foreign keys, and unique constraints.
- The frontend communicates with the backend through HTTP requests and JSON.
- The system should expire sessions according to defined rules and configure cookie flags such as HttpOnly, Secure, and SameSite policy.

**Recorded metadata:** `Chunks: 9`; `Runtime: 514.069s`.

#### Observations from the latest large-report output

- The main paragraph and key points retain the high-level architecture, catalogue and booking requirements, MySQL constraints, frontend/backend communication, and session-cookie settings.
- Several ideas recur in both the paragraph and the key points, including architecture, booking, database constraints, JSON communication, and session settings. This is a visible redundancy in the output, not a separately measured quality score.
- The supplied text contains spacing/word-joining defects such as `reservations.The` and `primarykeys`. They are recorded as output defects; this log does not imply the post-processing step corrected them.
- The output contains relevant details about concurrent requests, invalid input, database failures, server failures, registration/authentication, and data privacy. No source-to-summary completeness score was provided for this report.

### 5.2 Highly technical report: single-chunk test

**Input:** `2_highly_technical.txt`  
**Chunk count:** 1  
**Earlier standalone record runtime:** Not provided. **Final full-suite runtime:** 63.591 seconds (see Section 8).

#### Summary output supplied

**Main summary:**

> This project aimed to optimize retrieval latency for a search system. The initial approach used a flat inner-product index over 1.2 million MiniLM-L6-v2 embeddings, but it was not suitable for 40 concurrent evaluators. The team moved to an HNSW index and experimented with its parameters to improve recall@10 and reduce build time. Quantization was also used to reduce memory and latency. The final configuration achieved a p95 latency of 44 ms and p99 latency of 71 ms under a Locust load test.

**Key points returned by the summariser:**

- The initial flat index had a p95 query latency of 212 ms on a Ryzen 7 5800X workstation.
- The team moved to an HNSW index with parameters M=16, ef_construction=200, and ef_search=64.
- Quantization reduced memory by 3.9x and dropped p95 latency to 38 ms.
- Product quantisation (PQ) was tried but resulted in a recall drop to 0.91.
- Filtering out near-duplicate chunks before calibration recovered 2.4 points of recall.
- The final configuration achieved a p95 latency of 44 ms and p99 latency of 71 ms under a Locust load test.

**Recorded metadata:** `Chunks: 1`. The earlier standalone record did not include runtime; the final full-suite run recorded 63.591 seconds (Section 8).

#### Quality review against the source report

The summary retained the project's goal, the limitation of the initial flat index, the move to HNSW, the use of quantization, the failed PQ experiment, the near-duplicate filtering finding, and the final p95/p99 load-test results. It also retained important numerical values such as 1.2 million embeddings, 212 ms, 38 ms, 3.9x, 0.91 recall, 2.4 points, 44 ms, and 71 ms.

A configuration inconsistency was found when comparing the output with the supplied source document:

- The summary's key points state `ef_search=64` for the HNSW configuration.
- The source document says raising `ef_search` from 64 to 128 increased recall@10 to 0.981 at an extra 3.1 ms per query, and that the team **kept 128**.

The summary therefore preserves the intermediate value but omits the final selection. This is a specific technical detail loss and should remain in the log as a known quality observation. The summary was nevertheless treated as an accepted output for the current test; this log does not redefine the finalization decision.

Other source details not represented in the summary include the embedding dimension and dtype (384 dimensions, float32), the absolute memory change (1.8 GB to 462 MB), the recall@10 value of 0.962 for the selected HNSW construction settings, the increase to 0.981 with `ef_search=128`, build time changing from 41 minutes to 17 minutes, the 0.7-point recall drop with INT8, the MinHash Jaccard threshold of 0.85, zero timeouts during the 40-user load test, and the re-ranker taking 22 ms for the top 50 candidates. These are omissions from the condensed summary, not necessarily incorrect statements.

## 6. What is finalized and what is not recorded

### Finalized for this development round

- One prompt was selected after testing different prompts.
- Structure-aware chunking was retained because it performed better in observed testing.
- A post-processing stage was added because final summaries were too long.
- The BERT-based minimisation attempt was not selected.
- MiniLM embeddings using `sentence-transformers/all-MiniLM-L6-v2` were selected for the minimisation approach.
- The final recorded parameters are 200–300 words, 8–15 sentences, and MMR lambda 0.66.
- The output size was observed to vary with report size in the tests described.
- The earlier repeated large-report record had 9 chunks and a runtime of 514.069 seconds; the subsequent final all-file run recorded 765.239 seconds for the same input file (Section 8).
- The final all-file run recorded the highly technical report as 1 chunk and 63.591 seconds (Section 8).

### Not available in the supplied development record

- Full text of the selected prompt and previous prompt variants.
- Number of prompt variants tested.
- Exact structure-aware chunking implementation and its parameter settings.
- Exact BERT model name and its numerical test results.
- All intermediate MiniLM parameter settings and tuning-run outputs.
- Per-report final word counts and sentence counts.
- Separate historical timings for the prompt, chunker, BERT, and MiniLM tuning experiments. The final run's per-call LLM timing metrics are included in Section 8, but those do not isolate the earlier development experiments.
- The host/resource conditions for the earlier 452.574-second and 514.069-second large-report runs and the final suite run.
- An independent aggregate score for summary completeness, factuality, redundancy, and readability.

These items are listed as missing from the supplied log, not as work that was necessarily never performed.

## 7. Version 1.1 Summary Agent status

The current Summary Agent configuration is recorded as finalized for this development round based on the reported sequence of prompt selection, improved structure-aware chunking, output-length post-processing, rejection of the unsatisfactory BERT approach, adoption of MiniLM embeddings, and parameter tuning.

The test outputs show that the agent can produce summaries of both a large requirements report and a one-chunk highly technical report. Two earlier large-report runs took 452.574 and 514.069 seconds for nine chunks; the later final-suite run took 765.239 seconds. The cause of timing differences was not established. The highly technical example preserves much of the source's central information but loses the final `ef_search=128` selection, which is a concrete example of technical detail loss during compression. The full six-file final-suite measurements are documented in Section 8.

This status applies only to the Summary Agent changes documented in this Version 1.1 log. It does not claim that the wider ProjectIQ evaluation pipeline was changed, re-benchmarked, or finalized by these summarisation experiments.


## 8. Final all-file run: performance and output record

This section records the final run supplied on 20 October 2026. It is an additional Version 1.1 Summary Agent run record, not a replacement for the earlier Version 1 baseline. The run processed all six files and logged per-call Ollama metrics. The runner wrote its runtime log to `C:\Users\varun\Code\ProjectIQ\outputs\agent_runtime.csv`.

### 8.1 Final per-file results

| File | Input characters | Chunks | LLM calls | `Total summarise()` (s) | Footer runtime (s) | Final summary characters | Final key points | Combined partial-summary characters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `small_1000_tokens.txt` | 5,889 | 1 | 1 | 79.151 | 79.153 | 535 | 5 | — |
| `medium_4000_tokens.txt` | 17,951 | 4 | 5 | 343.612 | 343.613 | 732 | 7 | 4,692 |
| `large_7000_tokens.txt` | 49,402 | 9 | 10 | 765.239 | 765.239 | 1,981 | 7 | 10,940 |
| `1_poorly_written.txt` | 1,417 | 1 | 1 | 38.422 | 38.422 | 412 | 5 | — |
| `2_highly_technical.txt` | 2,126 | 1 | 1 | 63.590 | 63.591 | 495 | 6 | — |
| `3_ai_written.txt` | 2,104 | 1 | 1 | 41.507 | 41.508 | 604 | 5 | — |

`Combined partial-summary characters` is available only for the map-reduce files, where the console explicitly logged that aggregate. For single-chunk files the field is shown as not applicable. The log records `Total summarise()` and the footer `Runtime` separately, even when they differ by 0.001–0.002 seconds.

### 8.2 Suite totals and aggregate telemetry

| Metric | Value | Interpretation / note |
| --- | ---: | --- |
| Suite wall-clock runtime | 1,331.600 s (22 min 11.600 s) | Reported by the runner at completion. |
| Sum of six footer runtimes | 1,331.526 s | Sum of the per-file `Runtime` values in Section 8.1. |
| Difference: suite wall clock minus footer-runtime sum | +0.074 s | Both values are retained as logged; the difference was not separately diagnosed. |
| Mean of six per-file footer runtimes | 221.921 s (3 min 41.921 s) | Descriptive statistic across six different files, not repeated trials of one file. |
| Median of six per-file footer runtimes | 71.372 s (1 min 11.372 s) | Descriptive statistic across six different files. |
| Fastest file | 1_poorly_written.txt — 38.422 s | One chunk, one LLM call. |
| Slowest file | large_7000_tokens.txt — 765.239 s (12 min 45.239 s) | Nine map chunks plus one reduce call. |
| Total LLM calls | 19 | 13 map calls, 2 reduce calls, and 4 single-summary calls. |
| Validation/retry record | 19/19 calls: validation attempt 1 = 0.000 s; one attempt each | No retry is shown in these call logs. |
| Sum of logged HTTP request timings | 1,331.446 s | Sum of the 19 `[LLM TIMING] Ollama HTTP request` values. |
| Sum of logged Ollama totals | 1,331.403 s | Sum of the 19 `[LLM METRICS] Ollama total` values. |
| Sum of model-load timings | 10.196 s | Sum of the per-call reported model-load values. |
| Sum of prompt-evaluation timings | 495.781 s | Sum of per-call prompt-evaluation values. |
| Sum of token-generation timings | 822.110 s | Sum of per-call token-generation values. |
| Prompt tokens, summed across calls | 23,996 | Sum of logged prompt-token counts; includes map and reduce calls. |
| Generated tokens, summed across calls | 4,423 | Sum of logged generated-token counts; includes intermediate map summaries and final reductions. |

The prompt-evaluation, token-generation, and model-load values are sums of the component metrics logged for each call. They are not asserted to sum exactly to the suite wall-clock duration.

### 8.3 Per-call latency ledger

The following tables preserve each call's logged timings. `chat_json / stage total` records the reported `chat_json total` (the matching map/reduce stage total where shown). Each call logged exactly one attempt, with validation attempt 1 taking 0.000 seconds. The HTTP timing, the Ollama-internal total, component timings, and wrapper/stage timing are listed separately because the log did not treat them as interchangeable.

#### 8.3.1 Request and processing timings

| File / stage | HTTP request (s) | Ollama total (s) | Model load (s) | Prompt eval (s) | Token generation (s) | `chat_json` / stage total (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `small_1000_tokens.txt` — single-summary | 79.147 | 79.144 | 10.054 | 28.477 | 40.577 | 79.151 |
| `medium_4000_tokens.txt` — map chunk 1/4 | 57.850 | 57.847 | 0.009 | 24.320 | 33.308 | 57.851 |
| `medium_4000_tokens.txt` — map chunk 2/4 | 70.658 | 70.656 | 0.016 | 27.359 | 43.114 | 70.659 |
| `medium_4000_tokens.txt` — map chunk 3/4 | 81.802 | 81.801 | 0.007 | 26.798 | 54.897 | 81.803 |
| `medium_4000_tokens.txt` — map chunk 4/4 | 25.591 | 25.589 | 0.009 | 6.168 | 19.328 | 25.592 |
| `medium_4000_tokens.txt` — reduce-final-summary | 107.697 | 107.692 | 0.007 | 43.971 | 63.589 | 107.698 |
| `large_7000_tokens.txt` — map chunk 1/9 | 62.695 | 62.692 | 0.012 | 28.417 | 33.950 | 62.697 |
| `large_7000_tokens.txt` — map chunk 2/9 | 65.415 | 65.413 | 0.006 | 28.882 | 36.384 | 65.417 |
| `large_7000_tokens.txt` — map chunk 3/9 | 70.915 | 70.914 | 0.006 | 28.064 | 42.735 | 70.917 |
| `large_7000_tokens.txt` — map chunk 4/9 | 74.782 | 74.778 | 0.006 | 30.089 | 44.578 | 74.783 |
| `large_7000_tokens.txt` — map chunk 5/9 | 69.956 | 69.955 | 0.006 | 32.875 | 36.959 | 69.958 |
| `large_7000_tokens.txt` — map chunk 6/9 | 72.859 | 72.857 | 0.008 | 30.180 | 42.525 | 72.872 |
| `large_7000_tokens.txt` — map chunk 7/9 | 66.170 | 66.168 | 0.006 | 29.863 | 36.200 | 66.171 |
| `large_7000_tokens.txt` — map chunk 8/9 | 72.994 | 72.993 | 0.008 | 30.596 | 42.111 | 72.995 |
| `large_7000_tokens.txt` — map chunk 9/9 | 52.449 | 52.447 | 0.006 | 13.950 | 38.426 | 52.450 |
| `large_7000_tokens.txt` — reduce-final-summary | 156.954 | 156.953 | 0.008 | 50.896 | 105.153 | 156.956 |
| `1_poorly_written.txt` — single-summary | 38.419 | 38.416 | 0.006 | 11.788 | 26.462 | 38.420 |
| `2_highly_technical.txt` — single-summary | 63.588 | 63.585 | 0.007 | 14.422 | 49.069 | 63.590 |
| `3_ai_written.txt` — single-summary | 41.505 | 41.503 | 0.009 | 8.666 | 32.745 | 41.506 |

#### 8.3.2 Token counts, throughput, and intermediate output sizes

| File / stage | Prompt tokens | Generated tokens | Prompt processing (tok/s) | Generation (tok/s) | Output characters | Key points |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `small_1000_tokens.txt` — single-summary | 1,352 | 226 | 47.48 | 5.57 | 535 | 5 |
| `medium_4000_tokens.txt` — map chunk 1/4 | 1,338 | 187 | 55.02 | 5.61 | 428 | 4 |
| `medium_4000_tokens.txt` — map chunk 2/4 | 1,379 | 241 | 50.40 | 5.59 | 523 | 5 |
| `medium_4000_tokens.txt` — map chunk 3/4 | 1,353 | 287 | 50.49 | 5.23 | 797 | 5 |
| `medium_4000_tokens.txt` — map chunk 4/4 | 421 | 112 | 68.26 | 5.79 | 324 | 3 |
| `medium_4000_tokens.txt` — reduce-final-summary | 1,651 | 324 | 37.55 | 5.10 | 732 | 7 |
| `large_7000_tokens.txt` — map chunk 1/9 | 1,308 | 183 | 46.03 | 5.39 | 405 | 4 |
| `large_7000_tokens.txt` — map chunk 2/9 | 1,318 | 205 | 45.63 | 5.63 | 582 | 4 |
| `large_7000_tokens.txt` — map chunk 3/9 | 1,348 | 237 | 48.03 | 5.55 | 444 | 8 |
| `large_7000_tokens.txt` — map chunk 4/9 | 1,382 | 245 | 45.93 | 5.50 | 476 | 4 |
| `large_7000_tokens.txt` — map chunk 5/9 | 1,449 | 206 | 44.08 | 5.57 | 350 | 4 |
| `large_7000_tokens.txt` — map chunk 6/9 | 1,339 | 229 | 44.37 | 5.39 | 541 | 5 |
| `large_7000_tokens.txt` — map chunk 7/9 | 1,316 | 182 | 44.07 | 5.03 | 331 | 5 |
| `large_7000_tokens.txt` — map chunk 8/9 | 1,310 | 223 | 42.82 | 5.30 | 528 | 5 |
| `large_7000_tokens.txt` — map chunk 9/9 | 742 | 218 | 53.19 | 5.67 | 457 | 5 |
| `large_7000_tokens.txt` — reduce-final-summary | 2,726 | 501 | 53.56 | 4.76 | 1,981 | 7 |
| `1_poorly_written.txt` — single-summary | 659 | 152 | 55.90 | 5.74 | 412 | 5 |
| `2_highly_technical.txt` — single-summary | 916 | 275 | 63.51 | 5.60 | 495 | 6 |
| `3_ai_written.txt` — single-summary | 689 | 190 | 79.50 | 5.80 | 604 | 5 |

#### 8.3.3 Per-file map/reduce timing details

| File | Map chunk wall-clock totals (s) | Reduce wall-clock total (s) | `Total summarise()` (s) |
| --- | --- | ---: | ---: |
| `medium_4000_tokens.txt` | 57.851, 70.659, 81.803, 25.592 | 107.698 | 343.612 |
| `large_7000_tokens.txt` | 62.697, 65.417, 70.917, 74.783, 69.958, 72.872, 66.171, 72.995, 52.450 | 156.956 | 765.239 |

The map/reduce stage times above come from each call's wrapper timing. The outer `Total summarise()` timers can differ by small amounts because they are separate measurements and are preserved exactly as printed.

### 8.4 Final summaries and key points from this run

The text below records the final outputs printed by the summariser in the supplied run. Visible spacing/word-joining issues are retained rather than silently corrected. The output-character counts in Section 8.1 are the `Final summary characters` values logged by the runner.

#### small_1000_tokens.txt

**Main summary:**

> The project developed a full-stack web application for car rental management, allowing customers to view available cars, compare prices, book vehicles, and manage reservations. The system also provides an administrative interface for rental companies to manage vehicles, customers, and bookings. The application uses React.js for the frontend, Node.js and Express.js for the backend, and MySQL as the relational database. The system includes user authentication, vehicle management, search and booking, and booking management features.

**Key points:**

- The application uses React.js for the frontend, Node.js and Express.js for the backend, and MySQL as the relational database.
- User authentication is implemented using role-based access control, with ordinary customers and administrators distinguished.
- The system includes features for vehicle management, search and booking, and booking management.
- The database design includes three main entities: Users, Cars, and Bookings, with primary keys and foreign keys used to establish relationships.
- The application should be tested using both normal and exceptional scenarios, including functional, security, database, and performance testing.

#### medium_4000_tokens.txt

**Main summary:**

> The Full-Stack Car Rental System is a web-based application designed to simplify vehicle rental operations. It follows a client-server architecture with React.js, Node.js, Express.js, and MySQL. The initial version focuses on essential rental operations, with future enhancements such as payment gateways and GPS tracking considered. The system uses server-side validation and client-side validation to ensure critical business rules and usability. The administration dashboard allows administrators to manage vehicle listings, customer records, and reservation statuses. The system includes secure authentication, access control, and database integrity, and can be scaled up with careful implementation and additional integrations.

**Key points:**

- The system architecture consists of a React frontend, an Express backend, and a MySQL database.
- The database design includes three main tables: Users, Cars, and Bookings, with columns for user information, vehicle information, and reservation information.
- The API design exposes endpoints for registration, login, vehicle search, reservation creation, and administration, with input validation and suitable HTTP status codes.
- The testing strategy includes functional, boundary, concurrency, security, and database testing to ensure the system's reliability and security.
- The system uses a password-hashing algorithm such as Argon2id or bcrypt to store passwords securely and applies authentication and authorization checks.
- The vehicle catalogue presents all vehicles available for browsing, and administrators can add new vehicles, update specifications, and change pricing.
- The system checks reservation date ranges to determine availability and performs availability validation and reservation creation within an appropriate concurrency-control strategy.

#### large_7000_tokens.txt

**Main summary:**

> The Full-Stack Car Rental System is a web-based application designed to digitize and simplify vehicle rental operations. It provides a centralized platform for customers to browse vehicles, compare rental prices, and create reservations, while administrators can maintain vehicle inventory and supervise reservations.The application follows a client-server architecture and uses REST APIs with JSON data. The system must account for concurrent booking requests, unauthorized access, invalid input, incomplete database operations, and unexpected server failures. The application must prevent duplicate accounts when email addresses areused as unique identifiers. The vehicle catalogue must display active listings and relevant specifications, and customers can filter by category, maximum daily price, fuel type, transmission type, and seating capacity. The booking module must validate pickup and return dates, calculate rental charges, check availability, and create a reservation only when the relevant rules are satisfied. The administration module must restrict inventory modifications and administrative booking operations to authorized users. The application uses a MySQL database with primary keys, foreign keys, and unique constraints. The system architecture is divided into three primary layers: presentation, application, and data. The frontend communicates with the backend through HTTP requests using JSON format. The application must wait for the server's response and update the interface according to the actual result. The system must use a concurrency-control strategy to prevent conflicting reservations, such as transactions with suitable locking or serialization of critical operations. The application must define valid transitions for booking status, including pending, confirmed, cancelled, active, and completed, and verify that the customer owns the reservation or that an authorized administrator is performing the operation before updating the record.

**Key points:**

- The system uses a client-server architecture with a frontend, backend, and database, and REST APIs with JSON data.
- The application must account for concurrent booking requests, unauthorized access, invalid input, incomplete database operations, and unexpected server failures.
- The vehicle catalogue must display active listings and relevant specifications, and customers can filter by category, maximum daily price, fuel type, transmission type, and seating capacity.
- The booking module must validate pickup and return dates, calculate rental charges, check availability, and create a reservation only when the relevant rules are satisfied.
- The application uses a MySQL database with primary keys, foreign keys, and unique constraints.
- The system architecture is divided into three primary layers: presentation, application, and data.
- The frontend communicates with the backend through HTTP requests using JSON format.

#### 1_poorly_written.txt

**Main summary:**

> This project is a library management system developed for a college library. The system aims to simplify the librarian's work and allow students to browse andborrow books online. The system has multiple modules, including login, book, student, and issue-return modules. It uses PHP and MySQL for development and has been tested without errors. Future enhancements include barcode scanning and SMS notifications.

**Key points:**

- The system has multiple modules: login, book, student, and issue-return.
- It uses PHP and MySQL for development.
- The database has tables for books, students, and issues.
- The system has been tested without errors.
- Future enhancements include barcode scanning and SMS notifications.

#### 2_highly_technical.txt

**Main summary:**

> This project aimed to optimize retrieval latency for a search system. The initial approach used a flat inner-product index over 1.2 million MiniLM-L6-v2 embeddings, but it was not suitable for 40 concurrent evaluators. The team moved to an HNSW index and experimented with its parameters to improve recall@10 and reduce build time. Quantization was also used to reduce memory and latency. The final configuration achieved a p95 latency of 44 ms and p99 latency of 71 ms under a Locust load test.

**Key points:**

- The initial flat index had a p95 query latency of 212 ms on a Ryzen 7 5800X workstation.
- The team moved to an HNSW index with parameters M=16, ef_construction=200, and ef_search=64.
- Quantization reduced memory by 3.9x and dropped p95 latency to 38 ms.
- Product quantisation (PQ) was tried but resulted in a recall drop to 0.91.
- Filtering out near-duplicate chunks before calibration recovered 2.4 points of recall.
- The final configuration achieved a p95 latency of 44 ms and p99 latency of 71 ms under a Locust load test.

#### 3_ai_written.txt

**Main summary:**

> This project developed a Smart Attendance System using facial recognition technology to streamline attendance management in educational institutions. The system leverages artificial intelligence and computer vision techniques to reduce manual effort, enhance accuracy, and provide a user-friendly interface. The system prioritizes data security and privacy, and has the potential to be integrated with existing institutional platforms. The project aimed to address the critical need for efficient attendance management and lay a foundation for future advancements in intelligent educational technologies.

**Key points:**

- The system uses facial recognition technology to record attendance.
- It leverages artificial intelligence and computer vision techniques to reduce manual effort and enhance accuracy.
- The system prioritizes data security and privacy with robust measures to protect sensitive information.
- The system has a user-friendly interface and can be integrated with existing institutional platforms.
- The project aimed to address the critical need for efficient attendance management in educational institutions.

### 8.5 Reading this run correctly

- This run is the final all-file timing record supplied for the Version 1.1 Summary Agent. It should be compared with earlier runs only with their differing runtime and execution context made clear.
- The three car-rental input targets used 5,889, 17,951, and 49,402 characters and were processed in 1, 4, and 9 chunks respectively. Their footer runtimes were 79.153, 343.613, and 765.239 seconds. These observations show runtime increasing with input size for these three files in this run; the log does not turn that into a universal scaling formula.
- Map/reduce runtime is dominated by the logged model-call durations; the nine-chunk large report required nine map calls and one reduce call. The single-chunk files each used one LLM call.


---

**End of Version 1.1 Summary Agent Change Log**
