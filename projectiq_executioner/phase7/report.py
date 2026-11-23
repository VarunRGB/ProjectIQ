"""Fixed, evidence-safe Phase 7 report; no student-derived text is rendered."""
from __future__ import annotations

def markdown(r:dict) -> str:
    m=r['metrics'];q=m['score_calibration']
    lines=['# ProjectIQ Executioner — Phase 7 Benchmark', '',
           '**Status:** '+r['status'],
           '**Execution:** NOT PERFORMED — offline report evaluation',
           '**Ground truth:** reviewer-declared, not independently attested by this program',
           '', '## Benchmark coverage', '',
           f"- Reviewed cases: {m['cases']}; evaluated: {m['evaluated']}; rejected: {m['rejected']}",
           f"- Unique repository identifiers: {m['repositories']}",
           f"- Labeled test events: {m['event_labels']}",
           f"- Diagnoses missing from independently expected set: {m['missing_diagnostic_codes']}",
           f"- Unexpected diagnosis codes: {m['unexpected_diagnostic_codes']}",
           '', '## Diagnostic metrics', '',
           '| Metric | Value |','|---|---:|',
           f"| Event outcome accuracy | {fmt(m['event_accuracy'])} |",
           f"| False passes (reviewed FAIL, reported PASS) | {m['false_passes']} |",
           f"| False failures (reviewed PASS, reported FAIL) | {m['false_failures']} |",
           f"| Missing labeled events | {m['missing_expected_events']} |",
           f"| Diagnostic-code precision (set-based) | {fmt(m['diagnostic_code_precision'])} |",
           f"| Diagnostic-code recall (set-based) | {fmt(m['diagnostic_code_recall'])} |",
           f"| Root-cause Top-3 candidate inclusion | {fmt(m['root_cause_top3_hit_rate'])} |",
           f"| Known root causes reviewed | {m['root_cause_labeled_failures']} |",
           f"| Historical comparison matches | {m['historical_comparison_matches']}/{m['historical_comparison_checks']} |",
           f"| False student attribution claims | {m['false_student_attributions']} |",
           f"| Input receipts independently re-hashed | {m['input_receipts_crosschecked']} |",
           '', '## Phase 5 maintainability calibration (not grading)', '',
           f"- Eligible independent report/rating pairs: {q['pairs']}",
           f"- Readiness: {q['status']}",
           f"- Mean absolute indicator difference: {fmt(q['mae'])}",
           f"- Spearman rank correlation: {fmt(q['spearman_rank_correlation'])}",
           '- These reviewer ratings must assess **maintainability**, not total academic project quality.',
           '', '## Release policy checks','',
           '| Check | Result |','|---|---|']
    for k,v in sorted(m['release_checks'].items()):
        lines.append(f'| `{k}` | {"PASS" if v else "NOT MET"} |')
    lines.extend(['','## Case results','','| Case | Cohort | Evidence | Diagnosis gaps | Labeled outcomes |',
                  '|---|---|---|---:|---|'])
    for x in r['results']:
        if x['result']=='REJECTED':
            lines.append(f"| `{x['id']}` | {x['cohort']} | REJECTED ({x['error']}) | — | — |")
            continue
        ok=sum(e['correct'] for e in x['events']);total=len(x['events'])
        lines.append(f"| `{x['id']}` | {x['cohort']} | VALIDATED | {len(x['missing_codes'])}/{len(x['unexpected_codes'])} | {ok}/{total} |")
    lines.extend(['', '## Limits and interpretation', ''])
    lines.extend('- '+value for value in r['limitations'])
    lines.append('')
    return '\n'.join(lines)


def fmt(value):
    return 'NOT_MEASURED' if value is None else str(value)
