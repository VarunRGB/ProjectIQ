# Integrity benchmark

An invented student paper (`student_paper.pdf`) with copying planted on purpose, four
invented reference papers (`references/`, one unrelated), and an answer key
(`answer_key.csv`) giving the right answer for every planted sentence.

Run it:

```bash
python -m projectiq_integrity.benchmark          # table in the terminal
streamlit run projectiq_integrity/app.py         # choose "Benchmark paper (with answer key)"
```

Edit the cases in `scripts/make_benchmark.py`, then run it to rebuild the PDFs and the key.

## The 25 planted cases

| Cases | What is planted | Correct answer |
|---|---|---|
| V3 to V6 | 4 consecutive sentences copied word for word | verbatim, one copied chunk |
| V2, V8 | copied with a citation but no quotation marks | verbatim |
| V1 | copied, hyphen removed ("Real-time" to "Real time") | verbatim |
| V10, V11 | copied with a typo, or British to American spelling | verbatim |
| V7, V8 | two copied sentences in reverse order | verbatim, but no chunk |
| V9 | a standard definition copied | verbatim (a reviewer would accept it) |
| N1 to N3 | 3 consecutive sentences with one or two words swapped | near-verbatim, one copied chunk |
| N4 | end of a sentence cut off | near-verbatim |
| P1 to P5 | fully reworded, same meaning | paraphrase |
| M1 | two source sentences merged into one | paraphrase, near-verbatim or similar |
| H1 | copied inside quotation marks | not counted |
| H2 | the student's own method, related to a source claim | none or similar |
| H3 | **opposite** meaning of a source sentence | none or similar, never paraphrase |
| H4 | same topic, different statement | none or similar |
| own (12) | the student's own writing | none |
| crop_yield_prediction | an unrelated reference | 0% |

## What to expect

- **Word-based cases** (V, N, H1): decided by word overlap, so they are right even
  without the models. The automated test checks this.
- **Meaning-based cases** (P, M1, H2 to H4): need the real MiniLM and DeBERTa. These are
  the real test of the architecture: how many paraphrases are confirmed, and whether
  H3, the opposite-meaning trap, is correctly rejected.
- If paraphrases come out as "similar" instead of "paraphrase", DeBERTa is being strict:
  lower `paraphrase_entailment` in `projectiq_integrity/config.py`. If none are even
  "similar", MiniLM is not finding them: lower `paraphrase_candidate_cosine`. Change one
  value at a time and rerun.
