# Integrity Agent (projectiq_integrity)

Collects integrity evidence for each submission and returns it to the Rubric Agent.
It never assigns or deducts marks. Every finding carries quoted evidence, a
confidence value and its limitations, and a faculty member makes the decision.

## Main use: a research document against its references

How much of a student's research or project document was taken from the papers it
uses, from which paper, and whether whole chunks were copied.

```bash
python -m projectiq_integrity --document paper.pdf --references refs/ --out outputs/doc
```

Then open `outputs/doc/paper_similarity.html` in a browser.

| Type | How it is decided | Counted as copying |
|---|---|---|
| verbatim | word 3-gram overlap 0.80 or more | yes |
| near-verbatim | word overlap 0.45 or more (a few words changed) | yes |
| paraphrase | MiniLM finds a sentence with the same meaning (similarity 0.60 or more), then DeBERTa confirms that each sentence implies the other | yes |
| similar meaning (not confirmed) | MiniLM finds it, DeBERTa does not confirm both ways | no, shown for review |

- **Copied chunk:** 3 or more consecutive sentences copied from the same part of one reference (one unmatched sentence allowed inside).
- **Percentages** are shares of the document's words: overall, per type, per reference, and inside chunks.
- Text in quotation marks and the reference list are never counted. Copied text that carries a citation but no quotation marks is labelled "cited, not quoted".
- A reference's own abstract and title page count as source text; only its reference list is skipped.

Many students at once, each folder holding one document and a `references/` subfolder:

```bash
python -m projectiq_integrity --submissions cohort/ --out outputs/docs
```

`summary.csv` then lists every student's percentages, with a link to each HTML report.

## Also available: comparing a whole class

| Check | How | Flags when |
|---|---|---|
| Code similarity | JPlag 6.1.0 on every pair of submissions, starter code excluded | similarity is 50% or more (provisional) |
| Report similarity | Research Integrity Analyzer's `overlap_analyzer.py`, run against the other students' reports and a reference folder | 2 or more near-verbatim or reworded sentences come from one source (provisional) |
| AI-style text | Style measures plus perplexity from distilgpt2, run locally | both checks agree; never for typo-heavy or short text |

A finding is shown only if it has at least one quoted passage or code fragment.

## 1. One-time setup (Windows)

**Java 21.** JPlag 6.1.0 (the version automate-jplag downloads) needs Java 21 or newer.
Java 17 is too old. Keep JDK 17 installed if you need it; install 21 next to it:

```powershell
winget install EclipseAdoptium.Temurin.21.JDK
```

Open a new PowerShell window, then put JDK 21 first for this window only:

```powershell
$jdk = (Get-ChildItem "C:\Program Files\Eclipse Adoptium\jdk-21*")[0].FullName
$env:Path = "$jdk\bin;" + $env:Path
java -version     # must say 21
```

To make it permanent: Start, "Edit the system environment variables", Path, move
the JDK 21 `bin` entry above the JDK 17 one.

**Python packages and offline models** (from the ProjectIQ folder, venv active, online once):

```powershell
pip install -r requirements.txt
python scripts\setup_offline.py          # JPlag jar + 5 models into tools\ and models\ (about 780 MB)
python scripts\setup_offline.py --check  # everything should say [ok]
```

After this, no step goes online. To set up another laptop without internet, copy
the `models\` and `tools\` folders. Both are in `.gitignore`: share them through a
drive, not git.

## 2. Run it on a cohort

```text
cohort\
  code\        S101\ (folder) or S101.zip or S101.ipynb, one per student
  starter\     the starter code every student was given (optional, recommended)
  reports\     S101.pdf / S101.docx / S101.txt, one per student
  references\  papers or notes students might copy from (optional)
```

The file or folder name is the submission id, so code and report must use the same name.

```powershell
python -m projectiq_integrity --code cohort\code --base-code cohort\starter `
    --reports cohort\reports --references cohort\references --out outputs\integrity
```

Output: one `S101.json` per submission (the shared `AgentResult`) and `summary.csv`.
Add `--template dept_template.docx` to ignore the department's report template text,
and `--old-code last_year\code` to compare against earlier years.

Try it on the bundled samples (5 students, 2 planted copies, 1 AI-style report):

```powershell
python -m projectiq_integrity --code tests\fixtures\integrity\code --base-code tests\fixtures\integrity\starter `
    --reports tests\fixtures\integrity\reports --references tests\fixtures\integrity\references
```

## 3. Validate (plan Days 5 to 8)

```powershell
python scripts\validate.py --marks marks.csv --split test
python scripts\validate.py --controls controls.csv --integrity-dir outputs\integrity
```

Examples of both CSV files are in `tests\fixtures\integrity\validation\`. The script
reports Spearman correlation (with a 95% interval), quadratic weighted kappa, mean
absolute error, mean signed error and the share of marks within 1 point; for the
planted controls, detection rate, precision and false flags per check.

**Before reporting any number:** tune the thresholds marked PROVISIONAL in
`projectiq_integrity/config.py` on the dev split only, then report the test split.

## 4. What changed from automate-jplag

| automate-jplag | Integrity Agent |
|---|---|
| Merged each student's files into one file | Keeps files separate, so matches show the real file and line numbers |
| No starter-code exclusion | `--base-code` removes code everyone was given |
| `--min-tokens 5` (matches tiny fragments) | JPlag's default (12 for Python) |
| Excel sheet of percentages | `Finding` objects with both code fragments quoted |
| Downloads the jar at run time | Jar fetched once by `setup_offline.py` |
| Unzip, notebook conversion | Kept, with a guard against unsafe zip paths; notebook conversion no longer needs nbconvert |

## 5. Files

| File | Purpose |
|---|---|
| `contracts.py` | `Evidence`, `Finding`, `AgentResult`: the shared result object |
| `jplag_runner.py` | Prepare submissions, run JPlag, parse the `.jplag` result |
| `document_check.py` | Document vs its references: types, chunks, percentages, HTML report |
| `report_similarity.py` | Report loading, cohort and reference overlap, template removal |
| `ai_indicators.py` | Style plus perplexity, agreement rule |
| `evidence.py` | Evidence builder and confidence estimator |
| `agent.py`, `__main__.py` | The agent and its command line |
| `ria/` | Research Integrity Analyzer (Surabhi, Gaurav, Luv; CCNLP), vendored |
| `../scripts/validate.py` | Agreement with faculty and planted-control metrics |
| `../scripts/setup_offline.py` | One-time download of JPlag and models |
