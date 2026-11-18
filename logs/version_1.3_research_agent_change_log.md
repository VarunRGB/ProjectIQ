# ProjectIQ — Version 1.3 Research Quality Agent Change Log

**Document type:** Technical development record, experimental history, final configuration, validation evidence, and known limitations  
**Scope:** Research Quality Agent (Phases 1, 2, 3 and 4A), including its interfaces with the existing ProjectIQ repository  
**Version:** **1.3** (project documentation version; intentionally not titled Version 1.2)  
**Compiled:** 18 November 2026  
**Development status:** Research Agent development round completed, committed, and integrated into the shared `main` history; further accuracy validation remains necessary  
**Reference implementation:** `projectiq_research/` in `VarunRGB/ProjectIQ`  
**Assessment date used for the final paper comparisons:** `2026-11-18`

> **Version boundary.** This is a standalone **ProjectIQ Version 1.3** engineering record, following `logs/version_1_baseline.md` and `logs/version_1.1_baseline.md`. It does not retroactively rename the prior Summary Agent release, and it does not purport to document a separate project-level Version 1.2 release. Internal *Phase 3* experiments were labelled v1.1, v1.2, v1.3 and v1.4; those are **implementation iterations**, not the version number of this report. The Integrity Agent was developed separately and subsequently merged; its internal experiments are not claimed as work performed in this Research Agent round.

---

## Executive summary

Version 1.3 adds a four-stage Research Quality Agent to ProjectIQ. It ingests an academic project report, extracts and links its bibliography, checks bibliographic identity against academic metadata, estimates each reference's **research-topic and methodological role**, and reports **publication recency** without inventing missing literature. It produces a machine-readable JSON analysis and a human-readable Markdown research report.

The development was iterative. The original parser handled common headings and references but failed on real publication-specific typography. The first semantic relevance rules were susceptible to generic keyword overlap and the location of a citation. Academic search sometimes proposed related but **incorrect identities**. An early report layer treated questionable bibliographic dates and duplicates too simplistically. These were addressed through targeted parser changes, separate identity and relevance confidence, model-assisted passage comparison with evidence checks, conservative deduplication, and reproducible regression tests.

The retained semantic approach uses the pretrained **`sentence-transformers/all-MiniLM-L6-v2`** bi-encoder to select related passages, then **`cross-encoder/ms-marco-MiniLM-L6-v2`** to produce additional passage-ranking signals. The Research Agent did **not** train or fine-tune either checkpoint. All relevance thresholds and decision rules remain heuristic and uncalibrated; model scores are **not** probabilities or proof that a citation supports a factual claim.

Final **real-paper** smoke tests were carried out on a 2009 Tesseract-related document (`1577802.1577809.pdf`) and a 2024 eBPF intrusion-detection paper (`410500a416.pdf`). They exercise materially different layouts, reference styles, and subject matter. On the user's Windows environment, the complete research-branch test suite reported **282 passed, 6 skipped**, and the subsequently combined ProjectIQ repository (including the separately developed Integrity Agent) reported **305 passed, 6 skipped**. These totals are software regression outcomes, **not research-label precision, recall, or an independent benchmark of academic judgment**.

**Core boundaries:** This agent does not perform OCR on image-only PDF pages, inspect the full text of every cited publication, establish factual claim-level citation support, search for newer missing papers (Phase 4B), assign an academically calibrated quality grade, or execute submitted software. The separately implemented Integrity and Summarizer modules are not automatically proven end-to-end integrated merely because their tests pass in the same repository.

## 1. Scope, purpose, and relation to earlier versions

The Version 1 baseline established a local summarisation/rubric-evaluation pipeline, and Version 1.1 documented Summary Agent prompt, chunking, and MiniLM-based minimisation experiments. Version 1.3 records the **new Research Quality Agent**, not a replacement for that earlier work.

The research-stage objective is to answer, as conservatively as practicable:

1. What references and in-text citations are actually present in the submitted document?
2. Can each purported scholarly work be matched to a bibliographic record with a compatible title, author list, DOI, and publication year?
3. Does each reference appear to study the same research problem, contribute a method relevant to the report, have a looser connection, or lack sufficient evidence to classify?
4. How recent is the **confirmed** body of cited literature, especially literature related to the central topic and citations inside a dedicated Related Work/Literature Review section?
5. Which individual items should a faculty reviewer inspect, and what observations underlie each concern?

The intended output is **review support**, not an automated verdict about research misconduct, factual truth, scholarly novelty, or pass/fail eligibility.

### Terminology

- **Bibliography entry:** One extracted row/record, even if duplicated or a software link rather than a paper.
- **Distinct work:** A bibliography identity after conservative grouping of sufficiently corroborated duplicates; original rows are still retained.
- **Citation mention:** A detected bracketed number or author–year expression in the report body; the extractor may also encounter numeric false positives.
- **Verified identity:** Bibliographic identity corroborated by the available external metadata and the implemented rule set. It is not full-text validation.
- **Method relevance:** Evidence of a technique or methodological connection, not proof that the reported implementation correctly used the method.
- **Reliable publication year:** A year associated with a sufficiently verified, distinct identity; disputed or missing years are excluded from reliable-recency denominators.
- **Software test pass:** A regression test passed its stated assertion; it does not establish model accuracy on unseen reports.

## 2. Final Version 1.3 architecture

```text
                    Submitted TXT / MD / PDF / DOCX
                                |
                                v
              Phase 1 — Document and reference extraction
        text + heading hints + links + bibliography + citations
                                |
                  structured ResearchExtraction
                                |
                                v
              Phase 2 — Scholarly identity verification
           Crossref DOI lookup / OpenAlex title candidates
            title + author + year + DOI corroboration
                                |
              candidate identity and abstract trust gate
                                |
                                v
              Phase 3 — Research/method relevance analysis
        report topic, methodology, citation context, abstracts
          MiniLM retrieval + cross-encoder + evidence rules
                                |
                                v
                Phase 4A — Recency and audit report
         confirmed dates + deduplication + relevance-aware
         denominators + flagged items + manual review notes
                                |
                  JSON analysis + Markdown report
```

The integration function is:

```python
from projectiq_research.agent import analyze_research_document

result = analyze_research_document(
    "student_report.pdf", verify=True, as_of_date="2026-11-18"
)
complete_json_payload = result.to_dict()
structured_report = result.research_report
```

`analyze_research_document()` loads the source document **once**, executes Phases 1–4A in order, and returns one serializable research result. `analyze_document(path, verify=True)` is retained as a **backwards-compatible Phase 1/2** interface. The full entry point always executes local Phase 3 when references are present; `verify=False` skips live Phase 2 lookups, **not** the relevance stage. Optional `encoder`, `cross_encoder`, and `paper_abstracts` injection supports offline testing and future inference-resource reuse.

### Current principal dependencies

| Capability | Library, model, or service | Purpose and boundary |
| --- | --- | --- |
| PDF extraction | `pypdf` | Selectable text and PDF URI annotations; no OCR of raster-only pages |
| DOCX extraction | `python-docx` | Body text, heading styles, and hyperlink relationships |
| Citation/reference parsing | Python regex/string heuristics | Deterministic extraction; not a universal bibliography parser |
| External scholarly metadata | Crossref REST API | DOI-first bibliographic retrieval |
| External scholarly metadata | OpenAlex REST API | Ranked title candidates, publication metadata and available abstracts |
| HTTP | `requests` | Bounded API requests, timeouts and error handling |
| Sentence embedding | `all-MiniLM-L6-v2` | Local semantic similarity across bounded report/paper passages |
| Passage reranking | `ms-marco-MiniLM-L6-v2` cross-encoder | Local relative passage evidence; scores uncalibrated |
| Numeric operations | NumPy | Embedding vectors and similarity calculations |
| Unit/regression testing | `pytest` | Fixtures, mocked API/model outputs and boundary assertions |

The Research Agent's Hugging Face model files are downloaded/cached as needed, and its inference is local once the files exist. Phase 2 is network-dependent. An unauthenticated-Hugging-Face warning is a download/rate-limit warning, not an inference error when the weights load successfully.

## 3. Development history, in the order techniques were introduced

### 3.1 Phase 1 foundation — multi-format parsing and section extraction

**Initial approach.** Introduce `document_parser.py`, `section_detector.py`, `citation_extractor.py`, `reference_extractor.py`, shared data classes, and `agent.py`. Support `.txt`, `.md`, `.pdf`, `.docx`; extract raw text, document hyperlinks, explicit heading hints, a dedicated Literature Review/Related Work section, and References/Bibliography sections. Preserve original raw reference text and source line numbers for auditing.

**Techniques retained:**

- PDF text and link-annotation extraction via `pypdf`; DOCX paragraph/style and relationship inspection via `python-docx`; Markdown/text URL extraction.
- Heading aliases including *Literature Review*, *Related Work*, *Literature Survey*, *Review of Literature*, *References*, *Bibliography*, *Works Cited* and other close variants.
- Numeric citations such as `[1]`, `[1, 3]`, `[2-5]` and `[20]–[22]`, and conservative parenthetical/narrative author–year patterns.
- Wrapped bibliography-entry assembly with raw entry retention, author/title/year/DOI/URL heuristics and extraction notes.
- Explicit missing-section and empty-text notes rather than silently fabricating a Literature Review.

**Initial limits discovered:** PDF text order/spacing differs by publisher; not every report has a dedicated Literature Review; bracketed numbers may not be citations; reference formats differ; scanned-only PDFs yield little/no text.

The regression fixture corpus grew to **20 numbered test-case files** covering heading aliases, missing sections, wrapped IEEE references, APA styles, citation/link variants, DOCX headings/hyperlinks, selectable PDFs, scanned-style image-only PDFs and intentionally irregular entries. These are **structural software tests**, not an academic accuracy benchmark.

### 3.2 Phase 1 correction — document-wide citations vs. Literature Review citations

**Failure mode.** A report with no dedicated Literature Review could still cite many papers elsewhere. Treating the missing review as “no citations” would incorrectly prevent reference linkage and deprive later phases of context.

**Retained solution:** Separate `literature_citations` (strictly within a detected review) from `document_citations` (entire document body **excluding** the bibliography). Introduce `citation_linker.py` to expand numeric groups/ranges and attempt conservative author–year mapping back to bibliography rows. Exclude the reference-section text by masking its characters **without removing line breaks**, preserving original line offsets. Record unresolved and ambiguous links instead of guessing.

**Consequence:** All bibliography entries can receive a Phase 2/3 record even when they are not cited, or when a dedicated review heading is absent. The global citation extractor does **not** re-label the Introduction as a Literature Review.

### 3.3 Phase 1 correction — real Tesseract bibliography with colon-separated titles

**Observed failure.** The 2009 Tesseract-related PDF (`1577802.1577809.pdf`) uses rows such as `R. Smith: An overview of the Tesseract OCR Engine, ...`; the earlier generalized parser did not reliably recover these unquoted titles. Its numbered headings (`2. APPROACH`, `3. TRAINING`) also risked being mistaken for numbered bibliography entries, allowing the Related Work section to run into unrelated sections.

**Fix retained:** Add conservative `Authors: Title, Venue, Year` parsing, normalise common Unicode PDF ligatures for **parsing** while retaining `raw`, and recognize numbered uppercase/title-case **section boundaries before generic bibliography-number checks** outside the References section. Separate actual academic entries from software/manual-page URLs; leave weakly supported titles as `null` rather than inventing them.

**Observed structural result:** All **11 references** detected; **nine** scholarly entries with extracted paper titles and two software/web entries retained as resources. `1.1 Related work` terminates before `2. APPROACH`. This was subsequently retested in the full live pipeline.

**Not adopted:** Aggressively deleting stray spaces throughout the entire PDF. Although this might repair instances such as `T esseract`, applying it globally risks corrupting real names and titles.

### 3.4 Phase 1 correction — 2024 DSN/eBPF period-delimited bibliography

**Observed failure.** The unrelated 2024 cybersecurity paper (`410500a416.pdf`) had **44 numbered references**, but its common `Authors. Title. Venue, Year.` style initially yielded only four unusable candidate title fragments. With titles missing, Phase 2 had no usable search query and Phase 3 returned mostly `insufficient_evidence`.

**Fix retained:** Add conservative **period-delimited author/title parsing** alongside existing colon, quoted-title and APA patterns. Account for multi-author full names, initials, wrapped names, arXiv/conference references, embedded colons/question marks and entries with a year but no explicit venue. Introduce stronger author-list validation to avoid promoting URLs/software resources to scholarly titles.

**Observed replay result:** **24 plausible academic/book titles** recovered from the same 44 raw entries; the other **20** are primarily website, tool or documentation references. The aim was faithful extraction, not a forced 100% scholarly-title yield.

### 3.5 Phase 1 correction — unusual headings and line-wrapped URLs

**Observed failure.** The same 2024 PDF contains an actual `VI. RELATED WORK` section, but its extracted heading appeared as `VI. R ELA TED WORK`. Early runs incorrectly reported no dedicated review, so Literature Review recency displayed `0/0` (N/A). The PDF also wrapped many hyperlinks, producing fragments such as `https://www.unb` or `https://docs.cilium`.

**Fix retained:** Compare the **compact form of a complete candidate heading** against a bounded set of known headings; this catches letter-spacing artifacts (`R ELA TED WORK`, `I NTRODUCTION`) without fuzzy-matching arbitrary body paragraphs. Apply compatible section-family handling to Phase 3. Introduce `url_utils.extend_wrapped_url()` for cautious reconstruction of clear path/host continuations while preserving the original reference and line positions.

**Final observed result:** The output contains `literature_review.found: true`, heading `VI. R ELA TED WORK`, and a populated section from lines **1068–1100**. Real review-citation links contribute to a **3/7 (42.9%)** reliable review-recency metric. Typical wrapped links such as `https://www.unb.ca/cic/research/applications.html` and `https://docs.cilium.io/en/latest/bpf/` are recovered.

**Remaining:** URL reconstruction is intentionally conservative; malformed URLs with spaces or unusual punctuation can still be truncated. Heading identification is heuristic and can confuse unusually formatted table rows or all-capital text.

### 3.6 Phase 2 foundation — Crossref DOI lookup and OpenAlex title search

**Initial retained design:** Use an extracted DOI, if present, to query **Crossref** first; otherwise search **OpenAlex** by extracted title. Normalize DOI prefixes and whitespace, normalize title punctuation/diacritics for comparison, and compare retrieved year, author surnames and title string similarity. Keep metadata evidence separate from the raw citation.

**Relevant implementation details:**

- Resolver: `projectiq_research/paper_resolver.py`; identity policy: `verification.py`.
- Default HTTP timeout: **12 seconds** per request.
- OpenAlex title search: up to **10 returned candidates** per query, ranked by evidence rather than trusting provider ordering.
- Title similarity uses normalized strings and `difflib.SequenceMatcher`.
- Typical policy thresholds in the final implementation: **0.90** for a strong title match, **0.72** for a candidate requiring additional corroboration. These are engineering heuristics, not measured operating points.
- Matching surname is a weak corroborating signal, not a cryptographic author identity guarantee.
- Optional `OPENALEX_API_KEY` is taken from the environment (or project `.env` if `python-dotenv` is installed); an optional Crossref contact can be configured via `CROSSREF_MAILTO`. Secrets are not committed into patch archives.

**Statuses retained:** `verified`, `needs_review`, `not_found`, `metadata_mismatch`, `insufficient_metadata`, `lookup_error`.

**Important distinction:** `not_found` means no sufficiently reliable record was resolved by this workflow, **not that the cited publication is fictional**. Missing API coverage, variant titles, old proceedings and API/network failures can all affect resolution.

### 3.7 Phase 2 correction — irrelevant search hits, wrong-year publications and contaminated titles

**Observed problem A:** A thematically similar OpenAlex search hit was initially at risk of becoming a bibliographic `metadata_mismatch` merely because it was the search result, not because the manuscript's claimed DOI demonstrably belonged to a different paper.

**Retained policy:** Reject poor title-search candidates as `not_found` rather than treating them as proof of a bad citation. Reserve `metadata_mismatch` primarily for stronger contradictory evidence, such as an exact DOI identifying a different title.

**Observed problem B:** A good title/author identity may disagree on publication year because of conference-year versus database-year, online-first records, preprints or editions.

**Retained policy:** Keep strong identity with a year conflict as `needs_review`; record the discrepancy and seek human verification. For example, the Tesseract paper's reference to *Gabor filter based multi-class classifier for scanned document images* cites **2003**, while the returned metadata reports **2005** despite agreeing on title/authors. The algorithm does **not** erase that conflict.

**Observed problem C:** PDF extraction can paste unrelated words into a title, harming search.

**Retained technique:** Where a very long title query fails, retry with a bounded title prefix, but compare the returned result against the **full** submitted reference. Long-title containment receives only limited, corroborated consideration.

### 3.8 Phase 2 correction — unrelated 2025 candidate for a 2023 cybersecurity paper

**Critical live failure.** In the 2024 eBPF article, reference **[6]** was submitted as *Generative intrusion detection and prevention on data stream*, by **HyungBin Seo and MyungKeun Yoon (2023)**. A search candidate instead described *AI-Based Intrusion Detection and Prevention System* by different authors (**2025**). The initial workflow marked it `needs_review` and risked supplying the wrong abstract to Phase 3, artificially increasing apparent relevance.

**Retained final safeguards:**

1. OpenAlex candidate ranking requires credible title resemblance **plus independent author or year corroboration** for weaker titles.
2. If **authors and publication year both conflict**, do not accept the search hit as the paper's identity without stronger exact-DOI evidence.
3. Return `not_found` with an explanation, rather than incorrectly lending an unrelated paper's title or abstract to the student reference.
4. Phase 3 applies a **second identity trust gate**, including to `needs_review`, before external metadata may supply semantic evidence.
5. Keep legitimate good-title/book-edition year discrepancies under review (e.g., *Introduction to Algorithms*) rather than rejecting every different year.

**Final observed outcome:** The 2024 PDF's status distribution changed from **18 verified / 2 needs review / 4 not found** to **18 verified / 1 needs review / 5 not found** (plus 20 entries with insufficient scholarly metadata). The unrelated candidate no longer masquerades as a verified or reviewable identity with a usable abstract.

### 3.9 Phase 3 foundation — embedding-based relevance, without reusing the Summary Agent

**Initial design retained in revised form:** Use local pretrained MiniLM embeddings for semantic passage retrieval and a separate MS MARCO MiniLM cross-encoder for stronger pairwise ranking evidence. Load models lazily and reuse them within a process; expose injected fake encoders for offline deterministic tests. Do **not** import the Summary Agent or retrain its embedding model.

**Initial inputs:** The report's topic/abstract/title, methodology, literature, reference title and available citation context. The first version relied mostly on title evidence because Phase 2 had not yet retained external abstracts.

**Initial risks discovered:** A cited paper can sound connected because the student placed its citation in a topical paragraph. Generic terms such as `CNN`, `classification`, `image` or `deep learning` can drive false topic or method similarities. A missing Literature Review does not mean the paper is irrelevant, but also should not be quietly filled with arbitrary Introduction text.

**Classification vocabulary:** `directly_relevant`, `methodologically_relevant`, `partially_relevant`, `possibly_unrelated`, `insufficient_evidence`. This is a **research-role estimation**, not a factual-citation-support verdict.

### 3.10 Phase 3 internal v1.1 — verified abstracts and bounded, source-aware passages

**Technique tried and retained:** Retrieve `abstract_inverted_index` in the **same OpenAlex request** used for verification and reconstruct available abstracts from positional indexes. Clean Crossref's JATS/XML-formatted abstracts. Add optional `verified_abstract` to the verification schema; pass abstracts into Phase 3 only when the identity gate permits it. No additional academic-API round trips are required for this field.

**Context refinement:** Capture short overlapping methodology passages over the **whole** relevant section rather than only its beginning; preserve topic passage candidates and citation-section provenance; exclude the bibliography from citation contexts. Bound context length and passage count to control cost.

**Purpose:** Improve judgments beyond title-only similarity, especially where the paper's abstract describes its actual research task or method. **Limitation:** Not every indexed paper has an abstract, abstracts can be incomplete, and even a correct abstract does not prove the paper supports the particular citing sentence.

### 3.11 Phase 3 internal v1.2 — resistance to citation-context and keyword bias

**Issues diagnosed:** The early classifier could over-promote papers based on a citation's location in methods/related work, or broad semantic overlap on general ML vocabulary; it also risked interpreting a generic model name as evidence of a specific adopted method.

**Changes retained:**

- Construct a more stable report-topic profile from title/abstract rather than allowing a long literature review to dominate the topic.
- Compute separate topic, methodology, citation-context, literature and **citation-independent methodology** similarities.
- Select the best report/paper passage **separately for each axis** before cross-encoding, instead of reusing a pair selected only for topical similarity.
- Distinguish method evidence from the mere presence of a citation in a methods section.
- Require concrete technique-related support, not just word overlap on generic terms.
- Mark unresolved identities as tentative, rather than confidently promoting a plausible-sounding extracted title.
- Introduce comparison utilities to inspect **before/after labels and evidence**, instead of assuming every label change was an improvement.

**Not retained:** Context-only methodological promotion and a rigid literal-word-overlap rule as the sole way to recognize paraphrased task descriptions.

### 3.12 Phase 3 internal v1.3 — hierarchical research-task vs. method classification

**Observed failure:** A paper on an unrelated task can share substantial algorithm and vocabulary with the submitted project. Conversely, a true task match can use different wording. Earlier approaches blurred **the scientific problem** with **the technique used to solve it**.

**Retained technique:** Extract a compact research-task clause from each report and paper title. Compute **`research_problem_similarity`** separately from generic topical cosine similarity. Check the central research task first, then evaluate methodological contribution. Use domain-neutral task/action families (recognition, detection, forecasting, classification, etc.), specific subject-word overlap, and extra caution for **technique-led** titles (those primarily naming an architecture or algorithm).

**Evidence safety:** A shared domain object such as `coral`, `bird` or `ceramic` does not by itself establish method adoption; likewise shared words such as `image` and `recognition` do not prove the same research task. Sentence-level citation-independent methods are preferred over removing arbitrary PDF physical lines.

**Diagnostic fields introduced:** `research_problem_similarity`, `research_problem_overlap`, `technique_led`, `method_use_evidence`.

**Important boundary:** The classifier uses heuristics and fixed pretrained models; this was **not** fine-tuning, supervised training, or a calibrated classifier release.

### 3.13 Phase 3 internal v1.4 — method evidence, aliases, identity and confidence

**Problem investigated:** Method-using reports may refer to a paper through a short model alias or an explicitly adopted technique, while deceptive/misplaced citations can create the appearance of method support. An earlier shortcut could either miss real technique use or overstate an unsupported citation.

**Retained refinements:**

- Reconstruct PDF-wrapped citation sentences to capture the complete claim.
- Match specific technique phrases (e.g., `gradient descent`, `Kalman filter`) and corroborate them against report methodology.
- Support carefully bounded architecture-alias evidence **only** when the reported adoption, specific report topic/method text and a sufficiently trustworthy method-paper identity are jointly compatible.
- Treat a generic word or a single citation mention as **insufficient** proof of method use.
- Introduce `method_evidence_strength`, `method_evidence_traces`, `same_research_task_family` and `evidence_reliability` diagnostics.
- Cap confidence for unresolved identities; do not let unverifiable metadata become a high-confidence direct/method result.

**What was not established:** That an alias named by the author truly points to a specific bibliographic article. Without independent full-text checking, alias support is a heuristic inference, not source verification.

### 3.14 Phase 3 correction — generalized heading roles in a real PDF

**Observed failure.** The 2009 Tesseract paper uses sections titled `2. APPROACH`, `3. TRAINING`, `4. CLASSIFICATION`, with nested headings, rather than literal `Methodology`. The original Phase 3 method collector left many method similarity fields empty even though the document contained extensive methodology.

**Fix retained:** Recognize **semantic families of headings** (methods, experiment/setup, related work, background, results, etc.) across numbered, Roman, Markdown and ordinary heading conventions. Allow numbered subsections to inherit a parent method role. Keep citation-bearing and citation-independent method passages distinguishable; prevent numbered procedural steps from automatically becoming bibliography entries.

**Observed structural result:** On the available real Tesseract PDF, an offline check recovered **32 bounded methodology passages**, **31 citation-independent passages**, and recognized the named Approach/Training/Classification sections. In the user's final live output, methodology similarities were populated for assessed scholarly references.

### 3.15 Phase 3 correction — explanation must match recorded evidence

**Observed inconsistency:** An output note claimed a specific method had been explicitly adopted, while the record said `method_evidence_strength: "none"` and contained no method traces. The prose overstated the structured evidence.

**Fix retained:** When a citation contains only a *possible* method-use cue, state that corroboration was lacking and **do not count the cue** as established method use. When corroborated evidence exists, describe the degree of corroboration without claiming independent verification of the cited paper's assertions. Keep `method_use_evidence`, `method_evidence_strength`, `method_evidence_traces` and the explanation aligned.

**Important qualification:** Other borderline semantic classifications may still be wrong even if the explanation is internally consistent.

### 3.16 Phase 3 performance and model choices

**Current model choices:** Same MiniLM embedding checkpoint as used elsewhere in ProjectIQ when appropriate for reuse, but **a separate** MS MARCO cross-encoder checkpoint for reranking. The Research Agent does not import the Summary Agent code to share the model automatically; an orchestrator could inject a shared encoder instance in the future.

**Computation strategy:** Build unique bounded passages, encode them in batches (normally **32**), choose strongest evidence pairs for each axis, and cross-encode the selected pairs in batches (normally **16**). The scorer validates tensor/output shapes and preserves one result per bibliography row.

**No benchmark claim:** The development records do not establish a controlled average latency, peak RAM/VRAM, cold-cache download time, throughput across machines, or comparison against other embedding/reranker architectures. Model loading and inference succeeded in the final user's Windows tests; reported software-suite times must not be mistaken for isolated Phase 3 model latency.

### 3.17 Adversarial stress testing — what was deliberately tried

A **synthetic 45-reference PDF** and companion answer-key CSV were used to expose false positives and poor assumptions. The document is a fabricated evaluation fixture, **not** a peer-reviewed research paper or an independently adjudicated ground-truth dataset. It includes 25 earlier references and 20 additional references/edge cases.

The deliberately included probes were:

- Genuine OCR/topic references; general algorithm/architecture method papers; marginal and interdisciplinary papers.
- Clearly unrelated research fields such as coral extinction, archaeology, economics and bioacoustics, some placed in persuasive topical paragraphs.
- Bibliography entries with genuine titles/DOIs that are nevertheless unrelated to the report.
- Invented titles, fabricated DOIs, and an OCR-sounding title paired with a DOI belonging to another field.
- Repeated works and intentionally wrong bibliographic years; a same-title potential-version ambiguity.
- Contaminated/pasted titles; genuine references not cited anywhere in the main body.
- Bracketed expressions `[99]`, `[64]`, `[1,2,3]`, `[10, 20]` intended to probe false citation extraction; numeric ranges and grouped author–year mentions.

**Earlier saved run (not final live-model accuracy):** **45 bibliography rows**, **70 detected citation-like mentions**, **26 verified / 12 review / 6 not found / 1 mismatch**, with semantic classifications **8 direct / 3 methodological / 20 partial / 8 possibly unrelated / 6 insufficient evidence**. These are from an **earlier Phase 3 snapshot**; subsequent inference rules changed. This distribution is not a precision/recall measurement, and false-positive citation mentions were deliberately present.

**Lesson:** Avoid judging a revision by whether it labels fewer entries “direct” or more entries “unrelated.” Inspect independent task/method evidence and identify both false promotions **and** valid references that became false negatives.

### 3.18 Phase 4A foundation — conservative citation age and recency

**Retained purpose:** Analyze the age of **only the references already in the report**. Phase 4A reuses Phase 1–3 data; it does not search Crossref/OpenAlex again and does **not** perform Phase 4B missing-paper discovery.

**Recency policy:**

- Parse an explicit `--as-of YYYY-MM-DD` date for reproducibility; default to local current date if omitted.
- Use the **assessment calendar year** to compute age. Categories: `very_recent` (0–3 years), `recent` (4–5), `older` (6–10), `historical` (>10), plus `future_dated` and `unknown`.
- Exclude disputed, unknown, unverified and future years from **reliable** publication-year denominators; show those uncertain entries separately rather than silently guessing.
- Compute overall verified-year recency, **verified directly relevant** recency, and dedicated Literature Review recency as different descriptive statistics.
- Preserve old foundational method citations as potentially legitimate; do not call them obsolete solely because of age.
- Never claim an “unexplored research gap” or a missing recent publication without an independent literature search.

**Example of denominator discipline:** `3/7` means *three of seven distinct works in the detected review with sufficiently reliable years* meet the five-year criterion; it does **not** mean three of every seven raw bibliography rows, nor does it prove that the report surveys all recent literature.

### 3.19 Phase 4A correction — deduplication, editions and report presentation

**Problem investigated:** Counting duplicated citations as different works inflates literature coverage. Conversely, merging superficially similar titles can erase distinct editions/publication versions. A long raw JSON dump is also difficult for faculty to interpret.

**Retained solution:**

- Group **confirmed** duplicate identities conservatively by compatible DOI and/or verified title evidence, with special checks on year and version.
- Preserve every original row. Count distinct confirmed works for aggregate percentages.
- Surface **possible version groups** for manual inspection instead of auto-merging all same-title records with differing publication metadata.
- Provide a structured `research_report` and a Markdown report with an executive summary, denominators, Phase 1–4A findings, attention items, duplicate/version observations, manual checks, and a per-reference appendix.
- Keep concise console output; provide `--details` for per-reference Phase 2/3 diagnostics.
- Support offline report rebuilding from saved JSON so formatting/recency changes can be tested **without** repeating model inference or live API calls.

**Earlier synthetic-data replay:** **45 raw bibliography rows → 41 distinct works**, with **four repeated entries** across the confirmed groups `[1,39]`, `[2,22,44]` and `[17,38]`. `[15,24]` were treated as possible versions, **not merged**. These numbers describe the **saved synthetic snapshot**, not the two final real-paper runs; both real-paper runs showed zero confirmed duplicates.

### 3.20 Consolidated final integrity/robustness patch

The final combined correction addressed the defects that remained after testing on the unrelated eBPF paper:

1. Recover PDF typography-distorted review headings and pass normalized section roles into Phase 3.
2. Reject weak OpenAlex title candidates with conflicting independent author/year evidence.
3. Prevent questionable external metadata/abstracts from contaminating Phase 3.
4. Preserve legitimate year/edition ambiguities as review findings.
5. Repair unambiguous URL line wraps without altering line numbering.
6. Add focused regression tests for these cases and preserve both earlier paper results.

In the isolated patch environment, the associated **local test subset** reported **173 passing**. The user's full repository run subsequently reported substantially more tests because it included the whole ProjectIQ test tree. The intermediate test totals recorded in individual patch READMEs are **local snapshots, not comparable versions of one single fixed test suite**.

## 4. Final configuration and decision boundaries

| Area | Final implementation | Why the distinction matters |
| --- | --- | --- |
| Input | TXT, MD, PDF, DOCX | Only selectable/parseable text; scanned image-only files are not OCR'd |
| Literature section | Bounded aliases + normalized letter-spacing + numbered headings | The section can be genuinely absent; Introduction is not automatically a review |
| Reference formats | Numbered entries, colon-delimited and period-delimited academic styles, APA/quoted patterns | Real publisher differences; raw text preserved |
| Citation scopes | `literature_citations` and `document_citations` | A missing review does not mean no document citations |
| Citation matching | Numeric groups/ranges plus conservative author–year linking | Unresolved/ambiguous markers are auditable, not force-linked |
| Crossref | DOI-first retrieval | Stronger bibliographic key when a DOI exists |
| OpenAlex | Title search and evidence-based candidate ranking | Search rank alone does not establish identity |
| Identity | Strong title and compatible corroborating fields; uncertainty statuses | A match is neither guaranteed nor the same as scientific correctness |
| Abstract use | Accepted/review identity trust gate; reconstructed/cleaned abstracts | Wrong metadata cannot quietly inflate relevance |
| Topic relevance | MiniLM topic/task passages + cross-encoder + task-family/subject rules | Generic shared ML vocabulary should not imply the same task |
| Method relevance | Independent methodology, named techniques, adoption/citation traces | A citation's placement is not proof of method adoption |
| Classification | Direct / methodological / partial / possibly unrelated / insufficient | Qualitative role estimates, not calibrated probabilities |
| Confidence | Low / medium / high and evidence reliability fields | Confidence is heuristic and should be reported with its basis |
| Age | Explicit assessment date, five-year rule and age bands | Present-day age differs from historical publication-time adequacy |
| Recency statistics | Verified distinct works; direct and Literature Review subsets | Unknown/disputed years cannot silently improve recency |
| Duplicate handling | Confirmed identities merged in counts; possible versions flagged | Preserve entries and avoid over-deduplication |
| Output | Per-file JSON and Markdown | Structured evidence for the eventual central orchestrator/UI |

### Identity and relevance are deliberately different decisions

A bibliographically **verified** work can be irrelevant to the student's project. A work that is genuinely relevant can still be **not found** in the metadata providers. A report can cite a method paper correctly by title but inaccurately describe its results. No one of these states implies another.

A `needs_review` record can be used conservatively for semantic context **only** when the implementation has adequate corroboration; it does not make a disputed publication year reliable for aggregate age statistics. A `metadata_mismatch` record cannot lend an unrelated abstract to a positive relevance judgment.

### Known implementation thresholds

In the reviewed source, title similarity of **0.90** is the strong-title threshold, while **0.72** is the candidate/review threshold that still requires corroboration for weak title matches. Some direct-relevance and method-relevance inference branches use cosine and cross-encoder cutoffs (including independent-method evidence). These numbers were selected as **heuristic engineering rules** during the development round, **not** tuned and validated on a representative, independently annotated academic dataset. They should not be described as statistically optimized operating thresholds.

### Output contract

A successful full run exposes, among other fields:

```text
source
literature_review
references
literature_citations
papers
literature_links
reference_links
hyperlinks
notes
verification_results
document_citations
citation_reference_matches
relevance_results
recency_results
recency_summary
research_report
```

A record's optional diagnostic fields may be `null` or empty if the corresponding evidence was unavailable. Such absence is a useful uncertainty signal, not proof of non-relevance or misconduct.

## 5. Validation strategy and evidence

### 5.1 What kinds of tests were used

| Test type | What it establishes | What it does **not** establish |
| --- | --- | --- |
| Offline unit tests | Deterministic parsing, branching, serialization and boundary behavior | Correct neural labels on real scholarship |
| Fake HTTP API responses | Identity outcomes for known metadata scenarios, failures and mismatches | Live API uptime or coverage |
| Injected fake MiniLM/cross-encoder | Valid data flow, model batching, expected invariant logic | Generalization of pretrained models |
| Phase 1 corpus (20 fixtures) | Coverage of selected headings, formats and missing-section cases | Universal PDF/reference support |
| 45-entry synthetic adversarial PDF | Reaction to planted decoys, duplicates, false citations and corrupted metadata | Representative real-world precision/recall |
| Real 2009 Tesseract paper | Live older-paper parser, heading, bibliography and semantic smoke test | Universal OCR-paper relevance quality |
| Real 2024 eBPF paper | Live cross-domain reference-style, review-heading, metadata and semantic smoke test | Universal cybersecurity or full-stack support |
| Full `pytest` suite on Windows | Tests in the checked-out repository execute and satisfy assertions | A completely integrated faculty-grade system |

### 5.2 Final real-paper results, 18 November 2026

| Observed metric | 2009 Tesseract-related paper | 2024 eBPF intrusion-detection paper |
| --- | ---: | ---: |
| Source file | `1577802.1577809.pdf` | `410500a416.pdf` |
| Bibliography entries | **11** | **44** |
| Distinct works | **11** | **44** |
| Scholarly identity: verified | **8** | **18** |
| Identity: needs review | **1** | **1** |
| Identity: not found | **0** | **5** |
| Identity: metadata mismatch | **0** | **0** |
| Identity: insufficient metadata | **2** | **20** |
| Relevance: directly relevant | **1** | **5** |
| Relevance: methodologically relevant | **0** | **5** |
| Relevance: partially relevant | **8** | **11** |
| Relevance: possibly unrelated | **0** | **1** |
| Relevance: insufficient evidence | **2** | **22** |
| Dedicated review section found | **Yes** (`1.1 Related work`) | **Yes** (`VI. R ELA TED WORK`) |
| Review citation mentions extracted | **6** | **7** (grouped/ranged mentions may identify multiple works) |
| Whole-document citation mentions | **13** | **85** (can include false positives) |
| Five-year review recency, reliable denominator | **0/5 (0.0%)** | **3/7 (42.9%)** |
| Five-year verified direct recency | **0/1 (0.0%)** | **2/5 (40.0%)** |
| Confirmed duplicate entries | **0** | **0** |
| Flagged references | **1** | **6** |

**Interpretation:** These counts were produced by the **final user-supplied JSON/terminal runs** at the fixed 2026-11-18 assessment date, rather than reconstructed from the synthetic fixture. A classification count of `insufficient_evidence` is compatible with many genuine web/manual references; it does **not** mean those sources are fake. Crossref/OpenAlex records and locally downloaded model versions can change over time, so exact live output distributions are not guaranteed to remain numerically identical.

**Older-paper recency caveat:** The 2009 paper's references look historical when evaluated **in 2026**. That does not establish that its bibliography was out of date **in 2009**, when it was published. To assess historical adequacy, use an appropriate historical `--as-of` date.

### 5.3 Test-suite ledger

| Environment/run | Reported outcome | Interpretation |
| --- | --- | --- |
| Initial Phase 1 development | 58 passing (earlier development log) | Early partial suite, not final |
| Phase 3 intermediate patches | Varied local passing counts, including 85, 94, 113 and 136 | Different isolated fixture snapshots, often with fake models |
| Consolidated integrity patch workspace | **173 passed** | Local patch regression subset; not the full repository |
| User's Research Agent branch | **282 passed, 6 skipped**, 117.55 s | Full suite at that point, prior to merging Integrity Agent |
| User's combined Research + Integrity codebase | **305 passed, 6 skipped**, 146.42 s | Combined repository tests, after merge/conflict resolution |

**No failed tests** were reported in the two final user-run suites above. The six skipped tests were **not** individually explained in the supplied terminal output; their skip reasons and any unexercised integration scenarios still need inspection. The 23 additional passes are consistent with tests added from the separately developed Integrity Agent and related merged work, **not** evidence that the Research Agent's semantic accuracy improved by 23 cases. The observed 117.55 s and 146.42 s are **suite wall-clock times**, not research inference benchmark results.

### 5.4 Reproducibility commands (Windows, repository root)

```powershell
# Verify the checked-out code and package environment.
git branch --show-current
git status
python -m pip check
python -m pytest tests/ -q

# Run the full Research Agent on two different real-paper formats.
python -m projectiq_research.test_research_document `
    "C:\Users\varun\Downloads\1577802.1577809.pdf" --as-of 2026-11-18

python -m projectiq_research.test_research_document `
    "C:\Users\varun\Downloads\410500a416.pdf" --as-of 2026-11-18

# Optional paper-level diagnostic console output.
python -m projectiq_research.test_research_document `
    "C:\Users\varun\Downloads\410500a416.pdf" --as-of 2026-11-18 --details
```

The test CLI saves:

- `outputs/<source-stem>_analysis.json` — full record-level and aggregate evidence;
- `outputs/<source-stem>_research_report.md` — faculty-readable multi-phase report.

`--no-verify` skips online Phase 2 calls; it is useful for offline module tests but should **not** be presented as equivalent to a complete externally verified analysis. For isolated Phase 1/2 callers, `analyze_document` remains available.

## 6. Observed failures, decisions and residual risk ledger

| Observed failure or engineering question | Attempt/decision retained | Residual uncertainty |
| --- | --- | --- |
| Missing Literature Review in a report with real citations | Separate document-wide citations from review-only citations | False bracketed-citation patterns remain possible |
| Section runs into `2. APPROACH` | Numbered heading detection before bibliography-entry suppression | Rare numeric tables/headings can still confuse heuristic boundaries |
| Tesseract `Authors: Title` rows not parsed | Colon author/title splitter; Unicode ligature normalization | Irregular or multilingual author names may still need manual review |
| eBPF `Authors. Title. Venue` rows lose titles | Period-delimited parser with author validation | Academic-style unconventional titles can still be missed |
| `VI. R ELA TED WORK` missed | Compact known-heading comparison | Weird typography may still escape aliases; some table text may look like headings |
| Wrapped URLs truncated | Conservative continuation of unmistakable URL fragments | Ambiguous spaces, punctuation and wrapped query parameters may remain broken |
| Numeric array/dimension mistaken for a citation | Citation extraction + conservative link status; tested adversarial expressions | Not all false positives are filtered automatically |
| Bad OpenAlex top hit treated as citation mismatch | Require candidate plausibility before using result as evidence | Provider coverage/search quality vary |
| Good title but changed publication year | `needs_review` with year-discrepancy notes | Correct print/online/edition year needs manual checking |
| Contaminated/overlong title defeats search | Short-prefix fallback with original full-reference verification | Aggressive prefix can generate weak hits, so guardrails remain essential |
| Wrong 2025 paper offered for 2023 ref [6] | Author/year conflict rejection + abstract trust gate | True paper remains `not_found` if provider does not return it |
| Title-only Phase 3 misses methodology | Fetch available abstracts and compare bounded topic/method passages | Abstracts missing for some genuine papers |
| Citation placement inflates relevance | Independent topic/method axes and evidence conditions | Misleading student prose can still affect the inferred role |
| Shared ML terms inflate direct relevance | Task clause and subject overlap, technique-led safeguards | Semantically similar but different task domains may remain borderline |
| Unfamiliar method section names ignored | Semantic heading families, numbered inheritance | Nonstandard layout, two-column text ordering and tables remain challenging |
| Citation cue described as confirmed adoption | Synchronize explanation with method strength and traces | Output rationale is heuristic, not external proof |
| Recent unrelated articles inflate apparent freshness | Compute direct-related and review-related reliable recency separately | The relevance classifier itself can mislabel directness |
| Duplicate works inflate bibliography counts | Verified identity grouping and possible-version flags | Complex preprint/edition/translation families need expert review |
| Report too verbose/opaque | Structured `research_report`, Markdown, brief console, offline rebuild | Final faculty-facing dashboard/UX is a separate integration task |

## 7. Limitations of the current Version 1.3 implementation

### 7.1 Document ingestion and extraction

1. **No OCR for image-only PDFs.** `pypdf` cannot reconstruct text from a scanned page without a text layer. A visually readable PDF may thus produce an empty or incomplete analysis. The test corpus contains a scanned-style fixture to check graceful behavior, not a successful OCR capability.
2. **PDF reading order and layout.** Two-column layouts, headers/footers, footnotes, tables and page boundaries can disrupt sentences, headings and references. The fixes handle identified cases but do not solve general document layout understanding.
3. **No universal heading detector.** Section aliases and semantic heading families remain pattern-based; highly novel headings may be missed. A section called “Related Work” can be detected without guaranteeing that the content is actually a literature review.
4. **Only the first duplicated target section is extracted.** When multiple Literature Review or References sections appear, the parser records a note but extracts the first detected one. Appendices or supplemental bibliographies can therefore be omitted.
5. **Title/author parsing remains heuristic.** Rare names, diacritics, author-list punctuation, broken ligatures, line wrapping and hybrid reference formats can produce missing or wrong fields. The raw entry is deliberately retained for correction.
6. **Web/software resources lack scholarly metadata.** A URL to a tool, API, framework or manual may be a legitimate source even though `title`, `authors`, `year` and academic identity remain unavailable. They should not be penalized as fabricated papers.
7. **URL extraction is not a link validator.** Reconstructed strings can still be malformed, stale, redirected or dead. The algorithm does not automatically confirm HTTP reachability or publication identity for every link.

### 7.2 Citation matching and bibliographic identity

8. **False-positive numeric citations.** Patterns such as tensor dimensions `[64]`, class-label lists `[1,2,3]`, bracketed coordinates and grant identifiers may be counted as citation-like mentions. Citation counts, “uncited” labels and nearby context must be interpreted cautiously.
9. **Ambiguous author–year linking.** Same-surname authors, multiple articles in the same year, missing initials and malformed reference rows can prevent unique linking. The current linker deliberately preserves ambiguity instead of guessing.
10. **Provider index coverage is incomplete.** OpenAlex and Crossref may omit proceedings, older works, theses, datasets, software references, regional journals and alternate versions. `not_found` is **not** an accusation of fabrication.
11. **Identity is based on metadata.** DOI, title and author corroboration support bibliographic existence/identity but do not demonstrate that the author read the paper, cited it appropriately, or reported its findings accurately.
12. **Year and edition ambiguity.** Preprint year, conference date, online-first date, journal issue and book edition may legitimately differ. The rules expose some conflicts but do not resolve all of them automatically.
13. **Network and API dependence.** Rate limits, timeouts, credentials, service changes and unexpected metadata shapes affect Phase 2. A retry/fallback does not guarantee a result. Offline `--no-verify` runs have weaker scholarly identity evidence.
14. **Incorrect search candidates remain possible.** The final checks reject the concrete 2023/2025 case and other tested weak matches, but author/title similarity heuristics are imperfect and may still admit/reject an unusual valid record.

### 7.3 Relevance, methods, evidence and confidence

15. **Pretrained model scores are not calibrated.** Bi-encoder cosine and MS MARCO cross-encoder outputs are retrieval/ranking signals, not probabilities of relevance or proof of research quality. The model was not fine-tuned on a ProjectIQ training corpus.
16. **No representative labeled evaluation.** There is no independently adjudicated, multi-domain benchmark establishing Phase 3 precision, recall, F1, confusion matrix, inter-rater agreement or probability calibration. Synthetic adversarial cases and two real smoke tests demonstrate selected behavior, not population accuracy.
17. **General-purpose phrase rules are incomplete.** Task verbs, architecture patterns and method vocabulary cover common CS/ML terminology but not every engineering, medical, legal, mathematical or interdisciplinary research practice. Cross-domain false positives and false negatives remain possible.
18. **Title/abstract-only candidate content.** Phase 3 generally sees bibliography title, accepted external abstract and report citation context, **not the cited full article**. It cannot verify equations, data, experiments or factual claim support against a complete source.
19. **Citation context can be deceptive.** The report author could describe an unrelated study as if it directly supports their method. Separate evidence gates reduce this problem but cannot make untrustworthy report prose into proof.
20. **Method adoption is inferred, not executable proof.** A method-use phrase, algorithm name, strong heading or model alias does not establish that the student implemented the method, that it runs, or that it was implemented correctly.
21. **Confidence is qualitative and heuristic.** `low`, `medium`, `high` and evidence-strength fields describe the model's rule-based evidentiary basis; they have not been statistically calibrated to error frequencies.
22. **Some existing classifications are debatable.** For example, the 2009 Tesseract-related paper's *Hybrid Page Layout Analysis via Tab-Stop Detection* received a `directly_relevant` label despite a research-task distinction that a domain expert may classify differently. Likewise the eBPF paper's mixture of direct/method/partial labels requires expert inspection rather than blind acceptance.
23. **Insufficient evidence is not irrelevance.** Missing abstracts, unreadable titles or weak identities can lead to `insufficient_evidence` even when the underlying source is highly pertinent.

### 7.4 Recency, reporting and research-quality interpretation

24. **Recency is not novelty or comprehensive coverage.** Phase 4A only analyzes cited works. It does not query the field for missing state-of-the-art papers, find actual research gaps or prove that a recent reference is necessary.
25. **Historical-year interpretation matters.** Assessing a paper published in 2009 as of 2026 makes its 1990s/2000s citations old; it does not show the paper was weak at its own publication time.
26. **Reliable denominators exclude uncertainty.** Conservative percentages may have small sample sizes. For instance `3/7` does not account for every source in a 44-entry bibliography; excluding unverified/missing years is necessary to avoid false certainty.
27. **Deduplication may under- or over-group edge cases.** Strong DOI/title identity groups reduce obvious inflation; complicated preprint-to-publication relations and editions still warrant manual review.
28. **No universal research grade or automatic penalties.** The final report offers counts, flags and review advice. It does not assign a validated score out of 100, classify citations as fraudulent, penalize older foundations, or declare misconduct.
29. **No versioned academic data snapshot.** APIs can revise metadata, and pretrained model packages can update. Repeated live runs with the same date need not give identical results unless dependencies, retrieved metadata and model revisions are pinned/cached.

### 7.5 Engineering, operational and integration limitations

30. **Local test success is not deployment validation.** The 282/305 passing test suites are encouraging software evidence but do not establish performance under concurrent students, malformed huge files, slow APIs, interrupted jobs, or adversarial submissions.
31. **No controlled resource/performance benchmark.** The records lack end-to-end per-phase p50/p95 latency, peak memory, throughput, tokenization cost and cold versus warm model-start metrics. Two working Windows runs are not a controlled benchmark.
32. **Shared GPU/CPU scheduling is not complete.** The model-injection interface permits future reuse of a sentence encoder, but concurrent multi-agent inference scheduling/locking is a separate integration concern.
33. **No automatic full ProjectIQ orchestration proof.** Research, Integrity and Summarizer reside in the same merged repository. Their independently successful tests do not demonstrate that a central system can coordinate a single submission, merge all findings and render one faculty report.
34. **Full-stack code execution is out of scope.** No sandboxed execution of student applications, browser testing, backend/database orchestration or automated functional correctness evaluation is provided by this Research Agent.
35. **Security, privacy and service governance need deployment review.** Phase 2 sends search terms/DOIs to public metadata APIs. Although the transformer scoring runs locally after model download, a deployed institutional system needs explicit logging, retention, permission, network and data-handling policies. A production security audit is not evidenced here.
36. **Six skipped tests remain to be accounted for.** The terminal outputs did not reveal their skip conditions. Review `pytest -rs` and any model/network-dependent skips before claiming a particular test category was executed.

## 8. What Version 1.3 demonstrates—and what it does not claim

### Demonstrated in the recorded development round

- A single Python API and CLI can analyze TXT/MD/PDF/DOCX reports through structured Research Agent Phases 1–4A, subject to parseable text.
- The parser can handle two materially different real-paper citation/section/bibliography formats after specific generalizing fixes.
- Academic metadata can corroborate a substantial subset of references and safely withhold clearly suspicious candidate abstracts.
- MiniLM plus a cross-encoder can produce topic/method evidence and qualitative labels for every bibliography entry with sufficient text, including entries not explicitly linked from the Literature Review.
- A recency report can distinguish confirmed-year/verified-topic subsets and retain raw duplicates and disputes for review.
- The final user-run software suite returned **282 passed / 6 skipped**, and the merged repository suite returned **305 passed / 6 skipped**; the two chosen real-paper runs completed and wrote their reports.

### Not demonstrated by those observations

- Correct or fair academic relevance classification on a representative large population of reports.
- Accurate detection of fabricated publications in every indexing gap or alternate citation format.
- Factual entailment between a cited paper and the particular sentence that cites it.
- Discovery of genuinely missing or newer state-of-the-art research (Phase 4B).
- A publication-quality benchmark comparing alternative research classifiers, model latency, false positives and false negatives.
- Faculty agreement, grade validity, or defensible automated penalties for research quality.
- Automatic execution/testing of full-stack student projects.
- Completed combined-agent end-to-end product integration or production security readiness.

The right interpretation is **a working, regression-tested, evidence-oriented capstone module that still requires human review and broader validation**.

## 9. File map of the retained Research Agent

| File | Principal purpose |
| --- | --- |
| `projectiq_research/agent.py` | Orchestrates Phases 1–4A and exposes the stable entry points |
| `document_parser.py` | Loads TXT/MD/PDF/DOCX, headings and hyperlinks |
| `section_detector.py` | Locates Literature Review and References, preserving source boundaries |
| `citation_extractor.py` | Extracts section-only/document-wide citation mentions and numeric ranges |
| `citation_linker.py` | Resolves citations conservatively against bibliography indices |
| `reference_extractor.py` | Segments entries; parses bibliographic titles/authors/years/DOIs/URLs |
| `url_utils.py` | Conservatively joins clear line-wrapped URLs |
| `models.py` | Parsed-document, reference and aggregated-result contracts |
| `paper_resolver.py` | Crossref/OpenAlex access, normalization, candidate ranking and abstracts |
| `verification.py` | Evidence rules and paper identity statuses |
| `verification_models.py` | Structured academic metadata and verification contracts |
| `relevance_context.py` | Report topic, heading roles, methodology and citation-claim context |
| `relevance_scorer.py` | MiniLM/cross-encoder scores and evidence-aware role classification |
| `relevance_models.py` | Per-reference relevance fields and diagnostics |
| `recency_analyzer.py` | Verified-year ages, distinct-work grouping and recency statistics |
| `recency_models.py` | Per-reference ages and aggregate/duplicate/version summary |
| `research_report.py` | Structured all-phase assessment and Markdown rendering |
| `test_research_document.py` | CLI smoke test and JSON/Markdown output generation |
| `test_research_flexible.py` | Additional flexible document testing utility |
| `test_research_verification_regressions.py` | Verification-focused regression utility |
| `test_cases/research/` | Structural fixture corpus and manifest |
| `tests/test_research_*.py` | Phase 1–4A unit and regression cases |
| `tests/check_phase3_context.py` | Diagnostic for method/heading context collection |
| `tests/check_reference_recovery.py` | Diagnostic for bibliography parser recovery |
| `tests/compare_relevance_runs.py` | Before/after relevance comparison |
| `tests/review_phase3_v14.py` | Method-evidence and identity diagnostic review |
| `tests/rebuild_research_report.py` | Offline report regeneration from previously saved JSON |

**Note on paths:** Several exploratory patch READMEs referred to a `tools/` directory. In the reviewed repository's final `main` tree, the corresponding helper scripts are located under **`tests/`**. Use the paths above for the current checkout; older patch instructions are historical records and should not override the merged file tree.

## 10. Recommended follow-up validation before institutional use

These are **future needs**, not completed Version 1.3 accomplishments.

1. Build a manually annotated, multi-domain corpus in which faculty members independently label each reference's research role and citation-support validity. Measure confusion matrices, precision/recall/F1 and inter-rater agreement, with development and held-out test splits.
2. Manually audit the high-confidence `directly_relevant` and `methodologically_relevant` cases from the two real PDFs. Compare decisions to expert judgments, especially borderline foundational/method sources.
3. Add a separate full-text claim-support stage if the product must decide whether the cited paper actually supports a sentence, rather than only classifying topic/method relationship.
4. Evaluate image-only PDFs, multi-column page order, languages/fields beyond CS, and deliberately adversarial reference text. Improve citation/URL/heading parsers based on new general regressions.
5. Log phase-specific runtime, CPU/RAM, batch sizes, API request counts, retries and model revision identifiers for reproducible performance work.
6. Run `python -m pytest tests/ -q -rs` to account for the six skips, and perform a **single-submission cross-agent integration test** once a central orchestrator is connected.
7. Plan any future **Phase 4B** as an explicitly separate, source-grounded literature-discovery component; do not infer missing references from age statistics alone.

## 11. Final assessment

ProjectIQ Version 1.3 records a substantial new Research Quality Agent: deterministic extraction with auditable raw records; conservative Crossref/OpenAlex identity checks; pretrained local semantic passage retrieval plus reranking; evidence-aware distinctions between research-task and methodological relationships; and verified-year, deduplicated recency reports. The engineering work was not linear: real papers and a synthetic adversarial fixture exposed important failures, and those failures motivated the adopted fixes.

The final research-branch regression suite and merged ProjectIQ suite passed the tests actually run, while the two final real-paper smoke tests produced complete JSON/Markdown results. The results support calling the **Research Agent development round functionally complete for the capstone**, **not** universally accurate, plagiarism-detecting, independently academically validated, or fully product-integrated.

The next version should improve **external validity and operational robustness**, not simply report a larger test count or more confident labels.

---

## Appendix A. Consolidated experimental chronology

| Sequence | Iteration / experiment | Main technical decision | Status in Version 1.3 |
| --- | --- | --- | --- |
| A01 | Initial Phase 1 parser | Multi-format parsing, section aliases, citation and reference records | Retained |
| A02 | 20-case structural fixture collection | Validate missing sections, DOCX/PDF layouts, reference styles and links | Retained |
| A03 | Document-wide citation linkage | Mask bibliography but preserve lines; separate review-only citations | Retained |
| A04 | Initial Phase 2 academic lookup | DOI-first Crossref, OpenAlex title search, title/author/year checks | Retained with safeguards |
| A05 | Weak search-hit and year-version regressions | Reject unsupported hits; review compatible year discrepancies | Retained |
| A06 | Phase 3 initial local MiniLM + cross-encoder | Topic/method/context similarity and qualitative labels | Retained with revised classification |
| A07 | Phase 3 internal v1.1 | Retain accepted abstracts; bounded multi-passage context | Retained |
| A08 | Phase 3 internal v1.2 | Separate axes; citation-independent method passages; reduce context bias | Retained |
| A09 | Phase 3 internal v1.3 | Research-task clauses; method-led safeguards; hierarchical labels | Retained |
| A10 | Phase 3 internal v1.4 | Method evidence/traces, aliases, confidence and reliability controls | Retained |
| A11 | 45-reference fabricated stress test | Probe unrelated papers, false citations, DOI conflicts, duplicates | Retained as synthetic test |
| A12 | Phase 4A recency model | Verified-year denominators and relevance-aware age | Retained |
| A13 | Phase 4A report polish | Conservative dedup/version review; structured and Markdown outputs | Retained |
| A14 | 2009 Tesseract parser failure | Colon bibliographies, section-boundary correction, ligatures | Fixed, tested |
| A15 | Tesseract method-context failure | Generalized numbered heading roles | Fixed, tested |
| A16 | Phase 3 explanatory overclaim | Align rationale with method-evidence strength | Fixed, tested |
| A17 | 2024 eBPF parser failure | Period-delimited bibliographies, recover 24 scholarly titles | Fixed, tested |
| A18 | 2024 eBPF review-heading failure | Compact heading matching, `VI. R ELA TED WORK` | Fixed, tested |
| A19 | eBPF false OpenAlex candidate | Corroboration, candidate rejection, abstract trust gate | Fixed, tested |
| A20 | Wrapped links in PDF references | Conservative URL continuation | Fixed for tested cases |
| A21 | Final research-branch suite | 282 passed / 6 skipped | Recorded |
| A22 | Final merged ProjectIQ suite | 305 passed / 6 skipped | Recorded; separate agents not fully orchestrated |

## Appendix B. Source and evidence provenance

This document was prepared from the following categories of records:

- **Project repository:** `README.md`, `logs/version_1_baseline.md`, `logs/version_1.1_baseline.md`, the merged `projectiq_research/` implementation, its tests and reference fixtures in `VarunRGB/ProjectIQ` (reviewed 18 November 2026).
- **Intermediate development artifacts:** Phase 1, Phase 2, Phase 3 initial/v1.1/v1.2/v1.3/v1.4, Phase 4A, generalized-heading, evidence-consistency, colon/period-reference and consolidated integrity patch README files and test utilities created during the development discussions.
- **Synthetic evidence:** The 45-reference ProjectIQ comprehensive stress-test PDF, answer-key CSV and saved earlier JSON, used for adversarial software validation and **not** scholarly accuracy ground truth.
- **Real-paper evidence:** Uploaded final analysis JSONs and user-run terminal summaries for `1577802.1577809.pdf` and `410500a416.pdf`, both assessed as of 18 November 2026.
- **Repository tests:** User-run `pytest` terminal outputs: `282 passed, 6 skipped in 117.55s` and `305 passed, 6 skipped in 146.42s`.

The document deliberately separates **source-code behavior**, **observed test outputs**, **synthetic design intentions**, and **future work**. Where no controlled measurement or independently annotated ground truth was available, no such claim is made.

**End of Version 1.3 technical record.**
