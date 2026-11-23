"""Escaped Markdown and deterministic JSON for Phase 5."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import validate_report


def esc(value:object)->str:
    return (str(value).replace('\\','\\\\').replace('|','\\|')
            .replace('`','\\`').replace('<','&lt;').replace('>','&gt;')
            .replace('\r',' ').replace('\n',' ').replace(']','\\]'))


def markdown(report:dict)->str:
    validate_report(report)
    s=report['summary'];coverage=report['coverage'];quality=report['quality']
    lines=['# ProjectIQ Phase 5 — Code Quality', '',
           f'**Status:** {esc(report["status"])}',
           f'**Source:** {esc(report["source"]["repository"])}',
           '**Execution:** NOT PERFORMED (static source only)', '',
           '## Inventory and coverage','',
           f'- Indexed files: {s["indexed_files"]}',
           f'- Candidate source files: {s["source_candidates"]}',
           f'- Analyzed files: {s["analyzed_files"]} ({coverage["source_ratio"]*100:.1f}%)',
           f'- Source/code/comment/blank lines: {s["source_lines"]} / {s["code_lines"]} / {s["comment_lines"]} / {s["blank_lines"]}',
           f'- Recognized functions: {s["functions_found"]}',
           f'- Production / test source files: {s["production_files"]} / {s["test_files"]}',
           f'- Production / test code lines: {s["production_code_lines"]} / {s["test_code_lines"]}',
           f'- Findings: {s["findings"]}; duplicate groups: {s["duplicate_groups"]}',
           f'- Function-eligible files: {coverage["function_eligible_files"]}; scanned: {coverage["function_scanned_files"]} ({coverage["function_file_ratio"]*100:.1f}%)',
           f'- Recognized function-body code coverage: {coverage["function_body_ratio"]*100:.1f}% (approximate)',
           f'- Production function-body coverage: {coverage["production_function_body_ratio"]*100:.1f}% (approximate)',
           f'- AST-backed share of recorded production function span lines: {coverage["ast_complexity_share"]*100:.1f}%',
           f'- Cross-file duplicate production lines (approximate): {coverage["duplicate_production_lines"]}; rate {coverage["duplicate_production_ratio"]*100:.1f}%',
           f'- Procedural PHP files without detected named functions: {coverage["procedural_php_files"]}',
           f'- Big-O estimated / unknown: {coverage["big_o"]["estimated_functions"]} / {coverage["big_o"]["unknown_functions"]} (estimates are conditional)',
           f'- Limited function-analysis files: {coverage["limited_function_files"]}',
           f'- Report caps: ' + ', '.join(k for k,v in coverage['limits'].items() if v) if any(coverage['limits'].values()) else '- Report caps: no detected truncation',
           '', '## Language distribution','', '| Language | Files |','|---|---:|']
    for name,count in report['languages'].items():lines.append(f'| {esc(name)} | {count} |')
    lines+=['','## Procedural PHP control-flow markers (advisory)','',
            '| File | Decision markers | Loop markers |', '|---|---:|---:|']
    for f in report['files']:
        if f['language']=='PHP' and f['status']=='ANALYZED' and f['function_count']==0:
            lines.append(f'| {esc(f["path"])} | {f["procedural_decision_markers"]} | {f["procedural_loop_markers"]} |')
    lines+=['', '_Markers are lexical observations, not cyclomatic complexity or a complete procedural-code audit._']
    if quality['status']=='PROVISIONAL_LIMITED':
        score_label='Measured-dimensions-only indicator (NOT an overall project grade)'
        measured=', '.join(k for k,v in quality['dimensions'].items() if v is not None) or 'none'
        excluded=', '.join(k for k,v in quality['dimensions'].items() if v is None) or 'none'
        score_explanation=(
            '**Low-confidence interpretation:** Only '+measured+' were scored. '
            'Unmeasured '+excluded+' were excluded and the remaining weights '
            'renormalized. A score of 100 means no deductions in these limited '
            'checks, NOT perfect code quality.')
    elif quality['status']=='PROVISIONAL':
        score_label='Provisional static-maintainability indicator (NOT a grade)'
        score_explanation=(
            '**Medium-confidence interpretation:** All selected scoring dimensions '
            'have supported evidence, but the analysis is heuristic, uncalibrated '
            'and not a comprehensive code audit.')
    else:
        score_label='Unavailable (insufficient coverage or truncated evidence)'
        score_explanation=(
            '**No numeric quality claim:** The source or analysis coverage was '
            'insufficient; review the limitation reasons below.')
    lines+=['','## Overall quality indicator','',
            f'**{score_label}:** {quality["score"] if quality["score"] is not None else "not available"}',
            f'**Status:** {quality["status"]}; **Confidence:** {quality["confidence"]}',
            f'**Score eligibility:** {", ".join(quality["reason_codes"]) if quality["reason_codes"] else "PROVISIONAL_ONLY"}',
            f'**Scoring model:** {quality["scoring_version"]}',
            f'**Dimensions:** {", ".join(k + "=" + (str(v) if v is not None else "NOT_MEASURED") for k,v in quality["dimensions"].items())}',
            f'**Weights used:** {", ".join(k + "=" + str(v) for k,v in quality["weights_used"].items()) or "none"}',
            '', score_explanation,
            '', '**Important:** Quality results are heuristics, not student grades. Unknown Big-O patterns do not lower the score.',
            '', '## Credential exposure indicators (separate from quality score)','',
            f'**Scan status:** {report["security"]["status"]}; **Indicators:** {len(report["security"]["findings"])}',
            f'**Additional documentation/config files scanned:** {report["security"]["scanned_additional_files"]}',
            '**Caution:** Indicators are not verified active credentials; no secret values are read from .env files or included in reports.',
            '', '| Indicator | Location | Confidence |', '|---|---|---|',
            *[f'| {esc(x["code"])} | {esc(x["path"])}{":" + str(x["line"]) if x["line"] else ""} | {x["confidence"]} |' for x in report['security']['findings'][:40]],
            '', '## Highest-risk findings','',
            '| Severity | Issue | Location | Suggested improvement |',
            '|---|---|---|---|']
    levels={'HIGH':0,'MEDIUM':1,'LOW':2,'ADVISORY':3}
    for f in sorted(report['findings'],key=lambda x:(levels[x['severity']],x['path'],x['line']))[:40]:
        lines.append(f'| {f["severity"]} | {esc(f["message"])} | {esc(f["path"])}:{f["line"]} | {esc(f["recommendation"])} |')
    if not report['findings']:lines.append('| — | No supported findings detected | — | — |')
    lines+=['','## Complexity hotspots','','| Function | Location | CC (estimated) | Nesting | Big-O | Reason |',
            '|---|---|---:|---:|---|---|']
    for f in sorted(report['functions'],key=lambda x:(-x['cyclomatic'],-x['lines'],x['path']))[:35]:
        lines.append(f'| {esc(f["name"])} | {esc(f["path"])}:{f["start_line"]} | '
                     f'{f["cyclomatic"]} | {f["max_nesting"]} | {esc(f["big_o"])} ({f["big_o_confidence"]}) | {esc(f["big_o_reason"])} |')
    if not report['functions']:lines.append('| — | No supported functions found | — | — | — | — |')
    lines+=['','## Duplicate code sequences','',
            '| First occurrence | Second occurrence | Matched lines |',
            '|---|---|---:|']
    for d in report['duplications'][:30]:
        lines.append(f'| {esc(d["path_a"])}:{d["line_a"]} | {esc(d["path_b"])}:{d["line_b"]} | {d["lines"]} |')
    if not report['duplications']:lines.append('| — | None found by exact-block heuristic | — |')
    lines+=['','## Coverage and limitations','']
    for reason,count in coverage['skipped_reasons'].items():lines.append(f'- Skipped {count}: {reason}')
    for file in report['files']:
        if file['status'] == 'SKIPPED':
            lines.append(f'- Skipped source {esc(file["path"])}: {esc(file["reason"])}')
    lines.extend('- '+esc(v) for v in report['limitations'])
    return '\n'.join(lines)+'\n'


def write_reports(report:dict,destination:Path)->tuple[Path,Path]:
    validate_report(report)
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    path_json=destination/'code_quality.json'
    path_md=destination/'code_quality.md'
    # Files are output-only and are never read as code.
    path_json.write_text(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf8')
    path_md.write_text(markdown(report),encoding='utf8')
    return path_json,path_md
