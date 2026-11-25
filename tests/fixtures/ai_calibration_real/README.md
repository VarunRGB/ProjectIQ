# Real paragraphs for setting the AI threshold

This folder is where the AI threshold gets decided, so it needs REAL text.

**Nothing here except this README and .gitignore is uploaded to GitHub.** Students'
reports are their writing: keep them on your own computer.

## What to put here

Plain `.txt` files, one paragraph or section each, **at least 200 words** (passages
under 100 words are not scored). Aim for 15 or more of each kind.

| Kind | Where to get it | `expected` |
|---|---|---|
| Human | Reports, assignments or lab write-ups written **before December 2022** (before ChatGPT): seniors' project reports, your own old assignments, the department's old reports. Copy the student's own paragraphs (not quotes from papers). | `human` |
| AI | Generate yourself with ChatGPT, Gemini, Claude, Copilot... on similar project topics. Vary the prompts: plain, "write like a student", "keep it simple", "add a few mistakes", "paraphrase this paragraph". | `ai` |
| Mixed (optional) | A human paragraph and an AI paragraph in one file, separated by a blank line. | `mixed` |

Cover different styles on the human side too: formal, casual, non-native English,
very short sentences, very long ones, typo-heavy. Those are where false alarms happen.

## labels.csv

One line per file:

    file,expected,what_it_tests
    h01_senior_report_2021_intro.txt,human,2021 capstone report introduction
    a01_chatgpt_plain_methods.txt,ai,ChatGPT asked to write a methods section plainly

## Run

    python scripts/check_ai_thresholds.py --folder tests/fixtures/ai_calibration_real

At the end it prints the lowest threshold that flags no human passage, and how much AI
text that catches. Put the value in `CLASSIFIER_THRESHOLD` in
`projectiq_integrity/config.py`.
