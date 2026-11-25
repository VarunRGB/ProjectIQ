# Integrity Agent (projectiq_integrity)

Checks a student's research or project document against the reference papers it
uses, fully offline, and returns the evidence to the Rubric Agent. It never assigns
or deducts marks: every finding carries quoted evidence, a confidence value and its
limitations, and a faculty member makes the decision.

## What it checks

Every sentence of the document (outside its reference list) is matched to the closest
sentence in the reference papers.

| Type | How it is decided | Counted as copying |
|---|---|---|
| verbatim | the same words; hyphens, dashes, quotes, capitals and up to 2 misspelt words ignored | yes |
| near-verbatim | word overlap 0.45 or more: words swapped, added or removed | yes |
| paraphrase | MiniLM finds a sentence with the same meaning (similarity 0.60 or more), then DeBERTa confirms that each sentence implies the other | yes |
| similar meaning (not confirmed) | MiniLM finds it, DeBERTa does not confirm both ways | no, shown for review |

- **Copied chunk:** 3 or more consecutive sentences copied from the same part of one
  reference (one unmatched sentence allowed inside).
- **Percentages** are shares of the document's words: overall, per type, per reference,
  and inside copied chunks.
- Text in quotation marks and the reference list are never counted. Copied text with a
  citation but no quotation marks is labelled "cited, not quoted".
- **AI percentage** (`ai_detection.py`): the share of the student's OWN words (copied
  sentences left out) that read as machine-written. The text is split into passages of
  about 100 to 300 words, and each passage is scored with Binoculars: two local
  models (Qwen2.5 base and Instruct; 0.5B by default, 1.5B or 3B with
  `PROJECTIQ_AI_MODEL_SIZE`) read it, and a low score means machine-like. AI % = words in flagged passages / words scored. Stock AI phrases are
  shown for each flagged passage as the "why". Output is the `ai_detection` block the
  orchestrator expects (status, estimated_percent, interpretation, confidence,
  flagged_passages, limitations). A finding goes to the Rubric Agent from 20%.
  The thresholds (`AI_THRESHOLDS` in config.py, one per model size) are PROVISIONAL:
  set them with `python scripts/check_ai_thresholds.py` on REAL paragraphs in
  `tests/fixtures/ai_calibration_real/` (kept off GitHub). Advisory only.
- **Fallback:** if the two Qwen models are missing, the older yes/no check runs (style
  measures and distilgpt2 perplexity must both agree).

## 1. One-time setup

```bash
pip install -r requirements.txt
python scripts/setup_offline.py          # 5 models into models/ (about 700 MB), online once
python scripts/setup_offline.py --check  # everything should say [ok]
```

After this nothing is downloaded at run time. To set up another laptop without
internet, copy the `models/` folder. It is in `.gitignore`: share it through a drive,
not git.

## 2. See it on screen (demo app)

```bash
streamlit run projectiq_integrity/app.py
```

Your browser opens http://localhost:8501; the app runs on this computer only. Run the
sample, or upload a document and its reference papers. It shows the overlap
percentage, a breakdown by type and by reference, copied chunks side by side, the
document with copied sentences highlighted (hover one to see its source), the AI-style
result, and downloads for the HTML and JSON reports.

This is a temporary screen for showing the Integrity Agent. The faculty review screen
is a separate module.

## 3. Command line

One document:

```bash
python -m projectiq_integrity --document paper.pdf --references refs/ --out outputs/doc
```

Then open `outputs/doc/paper_similarity.html` in a browser.

Many students, each folder holding one document and a `references/` subfolder:

```bash
python -m projectiq_integrity --submissions cohort/ --out outputs/docs
```

`summary.csv` lists every student's percentages, AI-style flag and HTML report.

Sample: `tests/fixtures/research/` holds a report with a copied 4-sentence chunk and two
paraphrases (`S201_report.txt`), an original report (`S202_original.txt`), and two
reference papers.

## 4. Benchmark: how well does it work?

`tests/fixtures/benchmark/` has a student paper with 25 planted cases (verbatim,
near-verbatim, paraphrase, and traps that must not be flagged), 4 reference papers and
an answer key. Run `python -m projectiq_integrity.benchmark`, or pick "Benchmark paper"
in the app, to see what the tool gets right. Details in that folder's README.

## 5. From other ProjectIQ code

```python
from projectiq_integrity import run_integrity

result = run_integrity("S101/report.pdf", "S101/references", submission_id="S101")
for finding in result.findings:          # Finding: kind, summary, evidence, confidence, limitations
    print(finding.kind, finding.confidence, finding.summary)
```

`result` is the shared `AgentResult` from `contracts.py`.

## 6. Validate (plan Days 5 to 8)

```bash
python scripts/validate.py --marks marks.csv --split test
python scripts/validate.py --controls controls.csv --integrity-dir outputs/docs
```

Examples of both CSV files are in `tests/fixtures/validation/`. The script reports
Spearman correlation (with a 95% interval), quadratic weighted kappa, mean absolute
error, mean signed error and the share of marks within 1 point; for planted controls,
detection rate, precision and false flags per finding type.

**Before reporting any number:** tune the thresholds marked PROVISIONAL in
`projectiq_integrity/config.py` on the dev split only, then report the test split.

## 7. Files

| File | Purpose |
|---|---|
| `document_check.py` | Matching, types, copied chunks, percentages, HTML report |
| `documents.py` | Reading the document and reference papers (PDF, Word, text) |
| `ai_indicators.py` | Style measures plus perplexity, agreement rule |
| `evidence.py` | Evidence builder (no quote, no finding) and confidence estimator |
| `contracts.py` | `Evidence`, `Finding`, `AgentResult`: the shared result object |
| `agent.py`, `__main__.py` | `run_integrity()` and the command line |
| `app.py` | Temporary demo screen (Streamlit) |
| `benchmark.py` | Compares a check with an answer key |
| `config.py` | Thresholds |
| `ria/` | Sentence matching, PDF reading and model loading (Research Integrity Analyzer) |
| `../scripts/validate.py` | Agreement with faculty and planted-control metrics |
| `../scripts/setup_offline.py` | One-time model download |
| `../scripts/make_benchmark.py` | Builds the benchmark paper, references and answer key |
