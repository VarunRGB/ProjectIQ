"""Evidence-bounded, read-only Phase 5 analysis independent of project runtime.

No student application, module, build tool, linter configuration or dependency
is executed. Each output metric is labeled by its actual analysis coverage.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..indexer import SourceIndex
from .complexity import complexity
from .contracts import (
    FUNCTION_LANGUAGES, LANGUAGES, MAX_FILE_RECORDS, MAX_FUNCTIONS, MAX_FINDINGS,
    VERSION, validate_report,
)
from .duplication import find_duplications
from .findings import language_lints, structural_findings, stable_findings
from .functions import discover
from .lexical import code_line_numbers, source_lines
from .quality import compute_quality
from .source import collect
from .security import scan_exposure
from .procedural import procedural_markers
from .scope import file_scope

_LIMITATIONS = [
    'Static source analysis only: no project code, dependencies or linter configurations executed.',
    'Brace-language function discovery and complexity are heuristic, not parser-complete.',
    'Big-O results assume standard builtins, runtime compliance with type hints, fixed-width numeric operations, and amortized append where labeled; unknown otherwise.',
    'Top-level PHP and JavaScript statements are not counted as functions or fully assessed for complexity.',
    'Exact cross-file duplication is advisory and may include shared boilerplate.',
    'Lint findings use limited built-in Python/JS/TS/PHP/C++ rules, not Ruff, ESLint, PHPStan or clang-tidy.',
    'Quality score is a non-calibrated maintainability indicator; low-confidence scores exclude unmeasured complexity dimensions.',
    'Scores and inventories are withheld or marked PARTIAL when report evidence is truncated.',
    'High static quality does not prove behavior, algorithmic performance, or application security.',
    'Local filesystem source provenance is unverified; concurrent modification is not attested.',
    'Potentially exposed credentials are identified only by patterns or filenames; never printed or verified.',
]


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def analyze_project(root: Path, *, repository: str | None = None) -> dict:
    """Inspect inert source content and return a strict, source-located report."""
    root = Path(root).resolve()
    index = SourceIndex.build(root)
    selected, kept, skipped = collect(index)
    if len(selected) > MAX_FILE_RECORDS:
        raise ValueError('Phase 5 source evidence exceeds the file record budget')

    function_eligible = sum(LANGUAGES[Path(path).suffix.lower()] in FUNCTION_LANGUAGES
                            for path in selected)
    rows: list[dict] = []
    functions: list[dict] = []
    findings: list[dict] = []
    langs: Counter = Counter()
    limited = parsed = body_code_lines = total_function_code_lines = procedural_php = 0
    production_body_lines = production_function_lines = 0
    functions_truncated = False
    for file in kept:
        nlines, code, blank, comment = source_lines(file.text, file.language)
        scope_name=file_scope(file.path)
        spans, scope, parse_error = discover(file.text, file.language)
        decisions,loops=procedural_markers(file.text,file.language,bool(spans))
        if scope in ('HEURISTIC_PARTIAL', 'UNSUPPORTED', 'NOT_ANALYZED'):
            limited += 1
        if scope in ('AST', 'HEURISTIC_PARTIAL'):
            parsed += 1
        if file.language == 'PHP' and not spans and code:
            # Procedural scripts are legitimate code, but their logic is not
            # automatically counted as a named function or graded as such.
            procedural_php += 1
        if file.language in FUNCTION_LANGUAGES:
            actual_code_lines = code_line_numbers(file.text, file.language)
            total_function_code_lines += len(actual_code_lines)
            # Count unique covered lines, so nested functions are not counted
            # twice. The estimate measures recognition, not semantic correctness.
            covered: set[int] = set()
            for span in spans:
                covered.update(range(span.start_line, span.end_line + 1))
            contained=len(covered.intersection(actual_code_lines))
            body_code_lines += contained
            if scope_name=='PRODUCTION':
                production_body_lines+=contained
                production_function_lines+=len(actual_code_lines)

        remaining = max(0, MAX_FUNCTIONS - len(functions))
        if len(spans) > remaining:
            functions_truncated = True
        recorded = spans[:remaining]
        local: list[tuple[int, int]] = []
        for span in recorded:
            cc, depth, big_o, confidence, reason = complexity(span)
            functions.append({
                'path': file.path, 'name': span.name,
                'start_line': span.start_line, 'end_line': span.end_line,
                'lines': span.end_line - span.start_line + 1,
                'cyclomatic': cc, 'max_nesting': depth,
                'method': span.method, 'scope': scope_name, 'big_o': big_o,
                'big_o_confidence': confidence, 'big_o_reason': reason,
            })
            local.append((cc, depth))
        findings.extend(structural_findings(recorded, local, file.path))
        findings.extend(language_lints(file.text, file.language, file.path))
        langs[file.language] += 1
        rows.append({
            'path': file.path, 'language': file.language, 'scope': scope_name, 'status': 'ANALYZED',
            'reason': parse_error, 'lines': nlines, 'code_lines': code,
            'blank_lines': blank, 'comment_lines': comment,
            'function_count': len(recorded), 'function_scope': scope,
            'procedural_decision_markers':decisions,'procedural_loop_markers':loops,
            'sha256': file.sha256,
        })
    for path, reason in sorted(skipped.items()):
        rows.append({
            'path': path, 'language': LANGUAGES[Path(path).suffix.lower()],
            'scope': file_scope(path),
            'status': 'SKIPPED', 'reason': reason, 'lines': 0,
            'code_lines': 0, 'blank_lines': 0, 'comment_lines': 0,
            'function_count': 0, 'function_scope': 'NOT_ANALYZED', 'sha256': None,
            'procedural_decision_markers':0,'procedural_loop_markers':0,
        })
    rows.sort(key=lambda x: x['path'])
    findings, findings_truncated = stable_findings(
        findings, max_findings=MAX_FINDINGS, with_truncation=True)
    duplications, duplicates_truncated = find_duplications(kept, with_truncation=True)
    limits = {
        'functions_truncated': functions_truncated,
        'findings_truncated': findings_truncated,
        'duplications_truncated': duplicates_truncated,
    }
    analyzed = len(kept)
    estimated_big_o = sum(f['big_o'] != 'UNKNOWN' for f in functions)
    unknown_big_o = len(functions) - estimated_big_o
    source_ratio = _ratio(analyzed, len(selected))
    function_file_ratio = _ratio(parsed, function_eligible)
    function_body_ratio = _ratio(body_code_lines, total_function_code_lines)
    code_lines = sum(x['code_lines'] for x in rows)
    prod_files=[f for f in rows if f['status']=='ANALYZED' and f['scope']=='PRODUCTION']
    prod_functions=[f for f in functions if f['scope']=='PRODUCTION']
    prod_paths={f['path'] for f in prod_files}
    prod_findings=[f for f in findings if f['path'] in prod_paths]
    duplicate_lines=set()
    for d in duplications:
        if d['path_a'] in prod_paths and d['path_b'] in prod_paths:
            for base,path in ((d['line_a'],d['path_a']),(d['line_b'],d['path_b'])):
                duplicate_lines.update((path,base+i) for i in range(d['lines']))
    prod_code=sum(f['code_lines'] for f in prod_files)
    quality = compute_quality(
        code_lines=total_function_code_lines, functions=functions,
        findings=prod_findings, duplications=duplications,
        analyzed=parsed, candidates=function_eligible,
        source_read_ratio=source_ratio,
        function_file_ratio=function_file_ratio,
        function_body_ratio=_ratio(production_body_lines,production_function_lines),
        truncated=any(limits.values()),
        production_code_lines=prod_code, production_files=prod_files,
        production_functions=prod_functions,
        duplicated_production_lines=len(duplicate_lines),
    )
    security=scan_exposure(index,kept,skipped)
    summary = {
        'indexed_files': len(index.files), 'source_candidates': len(selected),
        'production_files':len(prod_files),
        'test_files':sum(f['status']=='ANALYZED' and f['scope']=='TEST' for f in rows),
        'production_code_lines':prod_code,
        'test_code_lines':sum(f['code_lines'] for f in rows if f['scope']=='TEST'),
        'analyzed_files': analyzed,
        'source_lines': sum(x['lines'] for x in rows),
        'blank_lines': sum(x['blank_lines'] for x in rows),
        'comment_lines': sum(x['comment_lines'] for x in rows),
        'code_lines': code_lines, 'functions_found': len(functions),
        'findings': len(findings), 'duplicate_groups': len(duplications),
    }
    reason_counts = Counter(skipped.values())
    status = ('NO_SOURCE' if not selected else
              'PARTIAL' if skipped or limited or security['truncated'] or any(limits.values()) else
              'ANALYZED')
    report = {
        'schema_version': VERSION, 'stage': 'phase5_code_quality',
        'status': status,
        'source': {'kind': 'local_unverified',
                   'repository': (repository or root.name)[:160],
                   'commit_sha': None},
        'analysis': {'method': 'bounded_static_analysis',
                     'source_execution': 'NOT_PERFORMED',
                     'tool_execution': 'NOT_PERFORMED',
                     'lint_scope': 'BUILTIN_LIMITED',
                     'big_o_scope': 'CONSERVATIVE_SUBSET'},
        'summary': summary,
        'coverage': {
            'supported_files': parsed,
            'unsupported_files': analyzed - parsed,
            'skipped_files': len(skipped),
            'limited_function_files': limited,
            'skipped_reasons': dict(sorted(reason_counts.items())),
            'source_ratio': source_ratio,
            'function_analysis_scope': ('AST_AND_HEURISTIC' if functions
                                        else 'NOT_AVAILABLE'),
            'function_eligible_files': function_eligible,
            'function_scanned_files': parsed,
            'function_file_ratio': function_file_ratio,
            'function_body_ratio': function_body_ratio,
            'production_function_body_ratio':_ratio(production_body_lines,production_function_lines),
            'ast_complexity_share':_ratio(sum(f['lines'] for f in prod_functions if f['method']=='PYTHON_AST'),
                                           sum(f['lines'] for f in prod_functions)),
            'duplicate_production_lines':len(duplicate_lines),
            'duplicate_production_ratio':_ratio(len(duplicate_lines),prod_code),
            'procedural_php_files': procedural_php,
            'big_o': {'estimated_functions': estimated_big_o,
                      'unknown_functions': unknown_big_o,
                      'estimated_ratio': _ratio(estimated_big_o, len(functions))},
            'limits': limits,
        },
        'languages': dict(sorted(langs.items())), 'files': rows,
        'functions': sorted(functions,
                            key=lambda x: (x['path'], x['start_line'], x['name'])),
        'findings': findings, 'duplications': duplications,
        'security': security,
        'quality': quality, 'limitations': list(_LIMITATIONS),
    }
    validate_report(report)
    return report
