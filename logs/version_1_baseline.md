# ProjectIQ — Version 1 Baseline Experiment Log

**Project:** ProjectIQ — AI-Assisted Project Evaluation System  
**Version:** 1.0 — Baseline  
**Date:** 18 October 2026  
**Status:** Initial functional prototype; performance and evaluation quality require further validation  
**Primary model:** Ollama `llama3.1:8b`  
**Primary implementation language:** Python

---

## 1. Executive Summary

ProjectIQ is a prototype intended to assist with evaluating academic project submissions using language-model-based analysis and rule-based processing. Its goal is to make evaluations more structured by combining text analysis, summarisation, rubric-based judgment, evidence checking, and recommendation generation.

Version 1 implements a text-oriented evaluation pipeline. It accepts a submission, processes its contents, generates a summary, evaluates the submission against a rubric, and produces an evaluation report containing a recommendation and associated assessment information.

The prototype has been tested on six documents representing different input sizes and writing characteristics. These include three scalability test cases—a small, medium, and large document—and three content-quality test cases: poorly written, highly technical, and AI-written content.

Initial results demonstrate that the pipeline can process these different inputs and produce recommendations. However, execution time is high, particularly during rubric evaluation and summarisation. A preliminary profiling experiment identified rubric judging as the largest measured performance bottleneck.

Version 1 should therefore be considered a working baseline rather than a validated automated grading system. Its recommendations require further correctness testing, its evidence-grounding behaviour needs improvement, and its performance requires controlled experimentation.

## 2. Objectives of Version 1

The objectives of the first version are:

1. Establish a functioning text-evaluation pipeline.
2. Integrate a locally served language model for summarisation and rubric-based evaluation.
3. Extract measurable characteristics from submitted text.
4. Produce structured assessment results and recommendations.
5. Establish a small set of repeatable test cases.
6. Measure end-to-end runtime and investigate individual pipeline-stage costs.
7. Identify functional, accuracy, and performance limitations to guide subsequent development.

## 3. Implemented Architecture

The current text-evaluation workflow can be represented as follows:

**Input document → Text-signal analysis → Summarisation → Rubric evaluation → Evidence checks → Recommendation and report**

The exact execution order and dependencies are determined by the current Python implementation. The following sections describe the responsibilities of the stages identified in the existing pipeline.

### 3.1 Input loading and preprocessing

The system loads a document from a text file and supplies its contents to the evaluation pipeline.

**Current role:**
- Read text-based test documents.
- Supply document contents to the downstream processing stages.
- Support repeatable testing using fixed input files.

**Limitations:**
- The current experiments use text documents rather than a complete ingestion workflow for arbitrary project artefacts.
- Input token counts have not yet been consistently measured and recorded using the model's tokenizer.
- File loading does not itself establish the validity or truthfulness of the submission.

### 3.2 Text-signal analysis

The signal-analysis stage extracts measurable characteristics of the text. The current implementation includes word-count and text-profile information, including a profile score associated with the `likely_ai_generated` category.

This stage uses local processing rather than requiring a language-model inference call for the measurements reported in the initial profiling experiment.

**Current role:**
- Calculate lightweight text characteristics.
- Produce signals that may inform later interpretation.
- Provide a fast processing stage that can be benchmarked independently.

**Limitations:**
- Text characteristics are indicators, not proof of plagiarism, AI authorship, or academic misconduct.
- A signal score should not independently determine whether a student has used AI.
- The current experiments do not establish the accuracy of the signal-analysis method against a sufficiently large labelled dataset.

### 3.3 Summarisation

The summariser uses the language model to produce a condensed representation of the submission and its important points.

The intended benefit is to provide a concise representation of a report that can support subsequent evaluation.

**Current role:**
- Summarise submitted text.
- Extract or present important points.
- Provide summary information used by the rubric-evaluation workflow.

**Limitations:**
- Summarisation introduces substantial inference time, particularly for larger documents.
- A summary may omit important technical details or qualifications.
- The current benchmark log does not yet consistently record input tokens, output tokens, chunk count, or the time taken by each individual model call.
- Summary quality has not yet been evaluated systematically against a human-written reference summary.

### 3.4 Rubric-based evaluation

The rubric judge uses the language model to assess a submission against defined evaluation criteria. The system can perform repeated judge runs, controlled by the configured sample count.

The intention is to make assessment more structured than asking a model to provide an unrestricted opinion about a project.

**Current role:**
- Evaluate the submission against a rubric.
- Generate structured judgments and assessment information.
- Support downstream recommendation generation.
- Allow repeated judge runs through a configurable sample count.

**Limitations:**
- Rubric judgments are generated by a language model and can be incorrect or unsupported.
- Repeated judgments increase execution time.
- The current benchmark does not yet isolate every individual judge call consistently.
- Agreement between model runs does not necessarily imply correctness.
- The rubric's agreement with expert human evaluators has not yet been established.

### 3.5 Evidence checking

Evidence checking is intended to assess whether evidence cited by the evaluation is present in, or supported by, the original submission.

This is important because a model may generate plausible claims that are not substantiated by the input.

**Current role:**
- Check evidence associated with the evaluation.
- Contribute to the reliability of the final assessment.

**Known issue:**
An earlier run of the highly technical test case reported an evidence-grounding score of 0.00, with five evidence quotations that could not be found in the source text, even though the final recommendation was `ACCEPT`.

This demonstrates that a plausible recommendation can coexist with a serious evidence-grounding failure.

**Limitations:**
- Finding an exact quotation in the source does not necessarily prove that the quotation supports the associated claim.
- The known failure indicates that evidence-grounding behaviour requires investigation.
- The relationship between evidence failures and the final recommendation needs to be reviewed and tested.
- Evidence verification should distinguish between an absent quotation, an inaccurate quotation, and a quotation that is present but does not substantiate the claim.

### 3.6 Recommendation and reporting

The pipeline produces a final recommendation and associated evaluation information. The test runner displays results and saves reports.

Observed recommendation categories include:

- `ACCEPT`
- `ACCEPT_WITH_MINOR_REVISIONS`
- `REVISE_AND_RESUBMIT`
- `FLAG_FOR_REVIEW`

**Current role:**
- Convert evaluation results into a recommendation.
- Present the outcome in a structured form.
- Save reports for subsequent inspection.

**Limitations:**
- A recommendation is not proof that an assessment is correct.
- Confidence values should not automatically be interpreted as statistically calibrated probabilities.
- The relationship between evidence-grounding failures and recommendation decisions requires further validation.

## 4. Test Environment and Configuration

The initial experiments use the following setup:

| Parameter | Version 1 configuration |
|---|---|
| Language | Python |
| Language-model serving | Ollama |
| Model used for baseline evaluation | `llama3.1:8b` |
| Input format in the reported experiments | Plain-text documents |
| Evaluation approach | Text processing and LLM-based assessment |
| Test categories | Scalability and content quality |
| Number of baseline documents | Six |
| Runtime measurement | Seconds |
| Judge sampling | Configurable; baseline runner defaults to three judge runs per case |

The reported runtimes were obtained on the local development environment. Hardware details, exact model and dependency versions, model quantisation, and system load were not consistently captured in the experiment records. Consequently, the results should not yet be treated as fully reproducible benchmarks across machines.

The approximately 1,000-, 4,000-, and 7,000-token labels describe the intended sizes of the scalability documents. Their exact token counts have not yet been verified using the model tokenizer.

## 5. Test Dataset

Version 1 uses six test documents.

| Test case | Intended purpose |
|---|---|
| `small_1000_tokens.txt` | Evaluate runtime on a short document |
| `medium_4000_tokens.txt` | Evaluate runtime on a medium-sized document |
| `large_7000_tokens.txt` | Evaluate runtime on a long document |
| `1_poorly_written.txt` | Examine behaviour on poorly written content |
| `2_highly_technical.txt` | Examine behaviour on technical content |
| `3_ai_written.txt` | Examine behaviour on content identified as AI-written for this test |

The three scalability documents support preliminary input-size comparisons. The other three explore differences in writing quality and style.

The AI-written case is a test scenario, not independent proof that the system can reliably detect AI authorship. Similarly, a poorly written report does not necessarily describe a technically poor project.

The six-document dataset is suitable for an initial prototype evaluation, but it is too small to establish general accuracy, fairness, or reliability.

## 6. Baseline End-to-End Results

The following results were recorded from the initial full-pipeline runs.

| Test document | Target size | Runtime (seconds) | Recommendation |
|---|---:|---:|---|
| Small | ~1,000 tokens | 554.720 | `ACCEPT` |
| Medium | ~4,000 tokens | 778.029 | `ACCEPT_WITH_MINOR_REVISIONS` |
| Large | ~7,000 tokens | 1,045.064 | `ACCEPT_WITH_MINOR_REVISIONS` |
| Poorly written | Not measured | 381.281 | `REVISE_AND_RESUBMIT` |
| Highly technical | Not measured | 499.958 | `ACCEPT` |
| AI-written | Not measured | 404.243 | `FLAG_FOR_REVIEW` |

### 6.1 Aggregate statistics

Across the six full-pipeline runs:

- Total recorded runtime: **3,663.295 seconds**, or approximately 61 minutes and 3 seconds.
- Mean runtime: **610.549 seconds per document**, or approximately 10 minutes and 11 seconds.
- Median runtime: **527.339 seconds per document**, or approximately 8 minutes and 47 seconds.
- Fastest run: **381.281 seconds**, for the poorly written document.
- Slowest run: **1,045.064 seconds**, for the large document.

These statistics describe the six recorded runs only. They are not estimates of the runtime distribution for all possible project submissions.

### 6.2 Scalability observations

| Comparison | Runtime increase |
|---|---:|
| Small to medium | 223.309 seconds, approximately 40.3% |
| Medium to large | 267.035 seconds, approximately 34.3% |
| Small to large | 490.344 seconds, approximately 88.4% |

The large document has seven times the intended token count of the small document, while its recorded runtime is approximately 1.88 times as large.

This suggests that total runtime is not determined solely by the input token count. Model-call overhead, repeated judgments, output generation, and other processing stages may contribute significantly.

These observations do not establish a formal complexity relationship. More controlled measurements are required.

## 7. Individual Stage Profiling

A separate profiling experiment measured the signals, summary, and rubric stages independently.

The latest combined summary-and-rubric measurements are recorded below.

| Test document | Summary (seconds) | Rubric (seconds) | Combined stage time (seconds) |
|---|---:|---:|---:|
| Small | 43.862 | 537.635 | 581.497 |
| Medium | 197.600 | 544.002 | 741.602 |
| Large | 430.160 | 576.052 | 1,006.212 |
| Poorly written | 25.888 | 370.074 | 395.962 |
| Highly technical | 44.408 | 470.094 | 514.502 |
| AI-written | 28.923 | 364.133 | 393.056 |

These are sums of independently measured stage runtimes. They should not be confused with independently measured end-to-end execution times.

### 7.1 Signal-analysis runtime

The six signal-analysis runs recorded the following runtimes:

| Document | Runtime (seconds) |
|---|---:|
| Small | 0.194 |
| Medium | 0.036 |
| Large | 0.089 |
| Poorly written | 0.009 |
| Highly technical | 0.005 |
| AI-written | 0.004 |

Combined runtime was **0.337 seconds**, giving a mean of approximately **0.056 seconds per document**.

Signal analysis is therefore negligible compared with the LLM-backed stages in these measurements. Further performance optimisation of this stage is unlikely to meaningfully reduce end-to-end runtime.

### 7.2 Summarisation runtime

Across the latest six runs, summarisation took a total of **770.841 seconds**, with a mean of approximately **128.474 seconds per document**.

The small, medium, and large documents took 43.862, 197.600, and 430.160 seconds respectively in the latest combined profiling run.

The large document's summary stage took approximately 9.81 times as long as the small document's summary stage.

This indicates a strong association between input size and summarisation runtime in these three measurements. Further investigation should record exact token counts, chunk counts, model-call counts, and output tokens.

### 7.3 Rubric-evaluation runtime

Across the six latest profiling runs, rubric evaluation took a total of **2,861.990 seconds**, with a mean of approximately **477.0 seconds per document**.

The median rubric runtime was approximately **470.094 seconds**.

Rubric evaluation accounted for approximately **78.8% of the combined measured summary-and-rubric runtime** across these six cases.

It was approximately 3.71 times as expensive as summarisation in aggregate.

The rubric stage is therefore the primary performance bottleneck identified by the current profiling experiment.

Its runtime changes comparatively little between the small, medium, and large documents: 537.635, 544.002, and 576.052 seconds respectively. This suggests a substantial per-document cost, potentially associated with repeated model inference. The exact cause has not yet been established.

### 7.4 Combined stage statistics

| Metric | Result |
|---|---:|
| Total summary runtime | 770.841 s |
| Mean summary runtime | 128.474 s |
| Total rubric runtime | 2,861.990 s |
| Mean rubric runtime | 476.998 s |
| Total summary + rubric runtime | 3,632.831 s |
| Mean combined stage runtime | 605.472 s |
| Signal-analysis runtime across six cases | 0.337 s |

These statistics apply to the latest set of stage-specific runs. They should not be added to the initial end-to-end runtimes because they represent additional executions.

## 8. Current Performance Bottlenecks

### Bottleneck 1: Rubric judging

Rubric judging is the largest measured performance bottleneck.

Potential contributing factors include:
- Multiple judge samples per submission.
- Language-model inference latency.
- Length and complexity of judge prompts.
- Amount of generated output.
- Model warm-up and local hardware constraints.

These are hypotheses rather than verified causes.

**Next steps:** instrument every judge call, record its duration, and compare configurations using one versus three judge samples. Any reduction in sample count should be evaluated for both runtime and recommendation consistency.

### Bottleneck 2: Summarisation of large documents

Summarisation time increases significantly as the document becomes larger.

Potential contributing factors include:
- Increased input-token processing.
- Chunking and multiple summarisation calls.
- Longer prompts or generated summaries.
- Model inference speed.

**Next steps:** record input tokens, number of chunks, number of model calls, output tokens, and per-call runtime. Consider optimisations only after establishing which factors dominate.

### Bottleneck 3: Evaluation correctness and evidence grounding

Performance is not the only concern. The earlier highly technical test produced an accepted recommendation despite a reported evidence-grounding score of zero and five quotations that could not be found in the source.

This is a functional correctness issue.

**Next steps:** investigate the evidence-matching implementation, distinguish exact-match failure from semantic support failure, and ensure that evidence problems are visible in the final report.

## 9. Known Limitations of Version 1

### 9.1 Evaluation accuracy is not established

Six documents are insufficient to establish reliable performance across diverse academic submissions. The current results demonstrate that the pipeline produces outputs, not that those outputs are consistently correct.

### 9.2 AI authorship detection is not validated

The AI-written test case demonstrates a particular observed response. It does not establish detection accuracy, false-positive rates, or false-negative rates.

### 9.3 Evidence grounding needs improvement

The known quotation failure shows that evaluation claims may not be adequately supported by the source. Exact quotation matching alone is also insufficient to establish semantic support.

### 9.4 Confidence is not necessarily calibrated

A confidence score produced by the system should not automatically be interpreted as a probability that the recommendation is correct. Calibration requires comparison against labelled outcomes.

### 9.5 Runtime measurements are preliminary

Repeated summary measurements differ substantially. For example, the large document's summary runtime was 613.726 seconds in one run and 430.160 seconds in a later run.

Possible contributors include model warm-up, system load, thermal throttling, or differences in execution settings. The available data does not establish which factor caused the difference.

### 9.6 Limited dataset and ground truth

The test set contains only six documents. It does not yet provide a broad, independently reviewed dataset with authoritative scores and expected recommendations.

### 9.7 Limited experiment metadata

The current results do not consistently capture exact token counts, model-call counts, chunk counts, output-token counts, hardware details, dependency versions, and per-call timing.

### 9.8 Human evaluation remains important

The system is intended to assist academic evaluation. Final academic decisions should remain subject to appropriate human review, especially when evidence is missing, contradictory, or uncertain.

## 10. What Version 1 Does Not Currently Claim to Do

The following capabilities must not be treated as completed features unless verified in the implementation.

- **Full autonomous multi-agent evaluation:** the current pipeline has identifiable processing stages, but not every stage is necessarily an independent agent.
- **Independent code execution and verification:** the present text-evaluation benchmarks do not demonstrate that submitted projects are automatically built, executed, and tested.
- **Comprehensive plagiarism detection:** no such capability is established by the six reported experiments.
- **Reliable AI-authorship classification:** the current AI-written test does not validate general detection performance.
- **Proof that every technical claim is true:** evidence checks do not independently establish that claimed functionality works.
- **Statistically validated confidence scores:** calibration has not yet been demonstrated.
- **Production-scale performance:** the current results do not establish throughput or reliability under concurrent users or large datasets.
- **Comprehensive file-format support:** the current reported tests use text documents.
- **Fully validated academic grading:** recommendations have not yet been compared systematically against expert human assessments.

These are limitations of the demonstrated Version 1 functionality, not necessarily permanent exclusions from the overall ProjectIQ roadmap.

## 11. Planned Improvements for Version 2

The next version should prioritise correctness, observability, and controlled experimentation.

### Priority 1 — Improve evidence grounding

- Record each claim and its supporting source passage.
- Check whether the passage exists in the original submission.
- Assess whether the passage actually supports the claim.
- Make missing or unsupported evidence visible in the final recommendation.
- Add regression tests for previously observed evidence failures.

### Priority 2 — Improve performance instrumentation

- Measure every language-model call independently.
- Record stage-level and total runtime.
- Record exact input and output token counts.
- Record chunk counts and judge sample counts.
- Capture model and environment configuration.
- Repeat experiments and report median runtime and variability.

### Priority 3 — Conduct controlled runtime experiments

- Compare one versus three judge samples.
- Keep the model, input, and environment constant within comparisons.
- Compare runtime alongside recommendation stability and evidence quality.
- Avoid reducing evaluation quality merely to improve speed.

### Priority 4 — Expand evaluation validation

- Create a larger set of representative submissions.
- Establish expert-reviewed expected outcomes.
- Evaluate false positives and false negatives for any AI-authorship signals.
- Measure agreement with human evaluators.
- Test whether poor writing is improperly penalised when the underlying technical work is strong.

### Priority 5 — Extend the architecture where required

Further capabilities may include integrity checking, code execution, additional specialist agents, and a human-review interface. Each should be implemented and evaluated as a distinct capability rather than assumed to exist because it appears in the overall project design.

## 12. Conclusion

Version 1 establishes a working baseline for ProjectIQ's text-oriented academic project evaluation pipeline. It combines lightweight text analysis, language-model-based summarisation, rubric judging, and downstream evaluation logic to produce structured recommendations.

The initial six-document benchmark indicates that larger inputs increase overall processing time, while individual stage measurements identify rubric judging as the dominant measured performance bottleneck. Signal analysis is inexpensive, whereas summarisation is substantially more sensitive to document size.

The most important outstanding issues are evidence-grounding correctness, reproducibility of performance measurements, and validation of evaluation quality. These should be addressed before making strong claims about accuracy, fairness, or production readiness.

Version 1 will serve as the baseline against which subsequent improvements are measured. Future versions should demonstrate measurable improvements in runtime, evidence reliability, and agreement with human-reviewed outcomes while preserving the transparency of the evaluation process.