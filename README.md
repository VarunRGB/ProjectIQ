# ProjectIQ: Summariser and Rubric Evaluator (Module 1)

This module reads a project report, summarises it, scores it against a rubric,
and gives a **recommendation with a confidence score**. Everything runs on a
local model through Ollama, so no report ever leaves your machine.

For every submission it produces:

- a **summary** and key points
- a **content type**: poorly written, highly technical, likely AI-generated, or standard
- a **score out of 10** for each rubric criterion, each with a reason and a quote from the text
- a **recommendation**: Accept, Accept with minor revisions, Revise and resubmit, or Flag for human review
- a **confidence score out of 100**, with a breakdown showing where it came from

---

## 1. Setup (Windows, one time)

1. Install Ollama from https://ollama.com/download and open it once.
2. Download a model (about 5 GB):
   ```
   ollama pull llama3.1:8b
   ```
3. In this folder, create a virtual environment and install the packages:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

Python 3.9 or newer is needed.

## 2. Run the three test cases

```
python run_test_cases.py
```

This runs the three required cases in `test_cases/`:

| File | Case | Expected recommendation |
|---|---|---|
| `1_poorly_written.txt` | Poorly written content | Revise and resubmit |
| `2_highly_technical.txt` | Highly technical content | Accept (or accept with minor revisions) |
| `3_ai_written.txt` | Obviously AI-written content | Flag for human review |

Each result is printed, and full reports are saved in `outputs/`:
`<case>.md` (readable report), `<case>.json` (machine-readable), and
`test_case_summary.md` (one table for all three, useful for slides).

Useful options:

```
python run_test_cases.py --samples 1          # about 3x faster, less complete confidence
python run_test_cases.py --model qwen3:8b     # try a different local model
```

## 3. Run it on any report

```
python -m projectiq_summarizer path\to\report.pdf
python -m projectiq_summarizer report1.docx report2.txt --samples 3 --out results
```

Supported: `.txt`, `.md`, `.pdf`, `.docx`. Long reports are split into chunks
automatically.

## 4. Offline tests (no model needed)

```
python -m pytest -q
```

34 tests check the text signals, chunking, the recommendation rules, the
confidence maths, quote checking, and the full pipeline end to end against a
fake Ollama server. Two regression tests replay exactly what llama3.1:8b
returned on the first real run, to make sure those mistakes stay fixed.

---

## How it works

```
report ──> text signals (plain code, no model)  ─────────────┐
       ──> summariser (map-reduce over chunks)                │
       ──> rubric judge, run 3 times ──> recommendation rules ├──> confidence ──> report
                                                              │
                        evidence quotes checked against text ─┘
```

**Summariser.** Short text is summarised in one call. Long text is split into
chunks of about 1,500 tokens; each chunk is summarised, then those summaries
are merged. This keeps every call small enough for an 8B model.

**Rubric judge.** Before scoring, the model must list the checkable details it
finds (numbers, parameters, named tools, results) and the generic claims.
Then it scores each criterion in `default_rubric.json` from 0 to 10, with a
quote and a reason. Every listed detail is checked against the text, and only
real ones count.

**Recommendation rules.** The final recommendation is decided by fixed rules
on the scores, not by the model, so the same scores always give the same answer:

1. **Flag for human review** when the text checks are strongly AI-like on their
   own, or when the model rates AI style high and the text checks lean the same
   way. Text full of spelling and grammar errors is never flagged, because that
   is a human pattern. The model's AI opinion alone is never enough.
2. Fewer than 2 checkable details found: specificity is capped at 3/10.
3. Weighted score below 5, or clarity below 4: **Revise and resubmit**
4. Weighted score below 7: **Accept with minor revisions**
5. Otherwise: **Accept**

Why rule 1 needs two sources: on the first real run, llama3.1:8b called the
poorly written text AI-generated (because it was vague) and called the obviously
AI-written text "highly technical". The text checks got both right. Requiring
agreement fixes both mistakes, and when the model and the text checks disagree,
the confidence score drops so a human knows to look closely.

**Confidence score (0 to 100).** It measures how much to trust the
recommendation, not how good the work is. It combines three independent checks:

| Check | Weight | What it measures |
|---|---|---|
| Self-consistency | 45% | The judge scores the text 3 times. Do the runs agree on the recommendation, content type and score? |
| Signal agreement | 35% | Plain-code measurements (typo rate, AI-style phrases, sentence rhythm, technical density) reach their own verdict without the model. Does it match the judge? |
| Evidence grounding | 20% | Can every quote the judge used actually be found in the text? |

75 or above is **High**, 50 to 75 is **Medium**, below 50 is **Low**. A low
score means a faculty member should look at that submission carefully.

**About AI detection.** Research shows AI-text detectors wrongly flag a large
share of writing by non-native English speakers (Liang et al., 2023, *Patterns*).
So an AI-style result here is only ever a flag with evidence for a human to
check. It never reduces marks on its own.

## Files

| File | Purpose |
|---|---|
| `projectiq_summarizer/summariser.py` | Map-reduce summariser |
| `projectiq_summarizer/judge.py` | Rubric judge prompt and recommendation rules |
| `projectiq_summarizer/signals.py` | Text measurements used as a second opinion |
| `projectiq_summarizer/confidence.py` | Confidence score and quote checking |
| `projectiq_summarizer/pipeline.py` | Runs all the steps for one submission |
| `projectiq_summarizer/llm.py` | Ollama client with JSON-schema output and retry |
| `projectiq_summarizer/schemas.py` | Data models, including the shared `AgentResult` |
| `projectiq_summarizer/default_rubric.json` | The five-criterion rubric (edit freely) |
| `run_test_cases.py` | Runs and checks the three required test cases |
| `tests/` | Offline tests and the fake Ollama server |

## Troubleshooting

- **"Cannot reach Ollama"**: open the Ollama app, or run `ollama serve` in a terminal.
- **"Model ... is not downloaded"**: run the `ollama pull` command it prints.
- **Very slow**: without a GPU, an 8B model can take a minute or more per call
  (the first real run took 5 to 8 minutes per case). Use `--samples 1` while
  testing, or a smaller model: `ollama pull llama3.2:3b` then `--model llama3.2:3b`.
- **Better judgement**: `qwen2.5:7b` usually follows scoring instructions more
  closely than llama3.1:8b at the same size: `ollama pull qwen2.5:7b` then
  `--model qwen2.5:7b`.
- **Results differ slightly between runs**: expected with a language model. That
  variation is exactly what the self-consistency part of the confidence score measures.
