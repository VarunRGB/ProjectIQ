"""Strict versioned Phase-5 output contract; no raw student source in evidence."""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
import re

VERSION = '0.3'
MAX_FINDINGS = 750
MAX_FUNCTIONS = 5000
MAX_FILE_RECORDS = 2500
MAX_ANALYZED_FILES = 2000
MAX_TEXT_BYTES = 350_000
MAX_TOTAL_BYTES = 12_000_000
REASON_CODES = frozenset({
    'FILE_TOO_LARGE','TOTAL_BYTE_LIMIT','NON_UTF8','SYMLINK_OR_SPECIAL',
    'UNREADABLE','UNSUPPORTED_LANGUAGE','PARSER_LIMITATION',
    'SYNTAX_ERROR','FILE_LIMIT','NO_SOURCE','NO_FUNCTION_SYNTAX',
})
LANGUAGES = {
    '.py':'Python', '.js':'JavaScript','.jsx':'JavaScript',
    '.mjs':'JavaScript','.cjs':'JavaScript',
    '.ts':'TypeScript','.tsx':'TypeScript',
    '.php':'PHP', '.cpp':'C++','.cc':'C++','.cxx':'C++',
    '.hpp':'C++','.hh':'C++','.h':'C/C++','.c':'C',
    '.java':'Java', '.go':'Go', '.rs':'Rust', '.cs':'C#',
    '.kt':'Kotlin','.swift':'Swift','.rb':'Ruby','.dart':'Dart',
    '.vue':'Vue SFC','.svelte':'Svelte', '.html':'HTML', '.htm':'HTML',
    '.css':'CSS','.scss':'SCSS','.sql':'SQL', '.sh':'Shell',
}
FUNCTION_LANGUAGES = frozenset({'Python','JavaScript','TypeScript','PHP','C++','C/C++','C'})
QUALITY_LEVELS = frozenset({'PROVISIONAL','PROVISIONAL_LIMITED','INSUFFICIENT_COVERAGE'})
REPORT_STATES = frozenset({'ANALYZED','PARTIAL','NO_SOURCE','ERROR'})
FINDING_SEVERITIES = frozenset({'LOW','MEDIUM','HIGH','ADVISORY'})
_SHA = re.compile(r'^[a-f0-9]{64}$')
_CODE = re.compile(r'^[A-Z][A-Z0-9_]{2,70}$')


class ReportContractError(ValueError):
    pass


def safe_rel(path: object) -> bool:
    if not isinstance(path,str) or not 0 < len(path) <= 350:
        return False
    if '\\' in path or '\x00' in path or '\r' in path or '\n' in path or ':' in path:
        return False
    p = PurePosixPath(path)
    return not p.is_absolute() and path == p.as_posix() and all(
        bit not in ('','.','..') for bit in path.split('/'))


def _nonneg(n: object) -> bool:
    return type(n) is int and 0 <= n <= 10**8


def validate_report(r: object) -> None:
    if type(r) is not dict or set(r) != {
            'schema_version','stage','status','source','analysis','summary',
            'coverage','languages','files','functions','findings',
            'duplications','quality','limitations','security'}:
        raise ReportContractError('INVALID_REPORT_KEYS')
    if r['schema_version'] != VERSION or r['stage'] != 'phase5_code_quality' \
            or type(r['status']) is not str or r['status'] not in REPORT_STATES:
        raise ReportContractError('INVALID_REPORT_IDENTITY')
    source = r['source']
    if type(source) is not dict or set(source) != {'kind','repository','commit_sha'}:
        raise ReportContractError('INVALID_SOURCE')
    if source['kind'] not in ('local_unverified','github_pinned') or type(source['repository']) is not str \
            or len(source['repository']) > 160:
        raise ReportContractError('INVALID_SOURCE_IDENTITY')
    if source['kind'] == 'github_pinned':
        if type(source['commit_sha']) is not str or not re.fullmatch('[0-9a-f]{40}',source['commit_sha']):
            raise ReportContractError('INVALID_COMMIT')
    elif source['commit_sha'] is not None:
        raise ReportContractError('UNVERIFIED_COMMIT_MUST_BE_NULL')
    analysis = r['analysis']
    if type(analysis) is not dict or set(analysis) != {'method','source_execution','tool_execution','lint_scope','big_o_scope'} \
            or analysis != {'method':'bounded_static_analysis','source_execution':'NOT_PERFORMED',
                           'tool_execution':'NOT_PERFORMED',
                           'lint_scope':'BUILTIN_LIMITED', 'big_o_scope':'CONSERVATIVE_SUBSET'}:
        raise ReportContractError('UNSAFE_EXECUTION_CLAIM')
    summary = r['summary']
    if type(summary) is not dict or set(summary) != {'indexed_files','source_candidates',
            'analyzed_files','source_lines','blank_lines','comment_lines','code_lines',
            'functions_found','findings','duplicate_groups',
            'production_files','test_files','production_code_lines','test_code_lines'} \
            or not all(_nonneg(v) for v in summary.values()):
        raise ReportContractError('INVALID_SUMMARY')
    cov = r['coverage']
    if type(cov) is not dict or set(cov) != {'supported_files','unsupported_files',
            'skipped_files','limited_function_files','skipped_reasons','source_ratio', 'function_analysis_scope', 'function_eligible_files',
            'function_scanned_files', 'function_file_ratio', 'function_body_ratio',
            'procedural_php_files', 'big_o', 'limits',
            'production_function_body_ratio','ast_complexity_share','duplicate_production_lines',
            'duplicate_production_ratio'} or any(not _nonneg(cov[k]) for k in (
            'supported_files','unsupported_files','skipped_files','limited_function_files',
            'function_eligible_files','function_scanned_files','procedural_php_files',
            'duplicate_production_lines')):
        raise ReportContractError('INVALID_COVERAGE')
    big_o_cov=cov['big_o']
    if type(big_o_cov) is not dict or set(big_o_cov)!={'estimated_functions','unknown_functions','estimated_ratio'} or not all(
            _nonneg(big_o_cov[k]) for k in ('estimated_functions','unknown_functions')) or (
            type(big_o_cov['estimated_ratio']) not in (int,float) or not 0 <= big_o_cov['estimated_ratio'] <= 1):
        raise ReportContractError('INVALID_BIG_O_COVERAGE')
    limits=cov['limits']
    if type(limits) is not dict or set(limits)!={'functions_truncated','findings_truncated',
                                                 'duplications_truncated'} or any(
            type(v) is not bool for v in limits.values()):
        raise ReportContractError('INVALID_LIMITS')
    for ratio in ('function_file_ratio','function_body_ratio','production_function_body_ratio','ast_complexity_share',
                  'duplicate_production_ratio'):
        if type(cov[ratio]) not in (float,int) or not 0 <= cov[ratio] <= 1:
            raise ReportContractError('INVALID_COVERAGE_RATIO')
    if cov['function_analysis_scope'] not in ('AST_AND_HEURISTIC','NOT_AVAILABLE'):
        raise ReportContractError('INVALID_FUNCTION_COVERAGE')
    if not isinstance(cov['source_ratio'],(int,float)) or type(cov['source_ratio']) is bool \
            or not 0 <= cov['source_ratio'] <= 1:
        raise ReportContractError('INVALID_COVERAGE_RATIO')
    if type(cov['skipped_reasons']) is not dict or any(k not in REASON_CODES or not _nonneg(v)
            for k,v in cov['skipped_reasons'].items()):
        raise ReportContractError('INVALID_COVERAGE_REASONS')
    if type(r['languages']) is not dict or any(type(k) is not str or not _nonneg(v) for k,v in r['languages'].items()):
        raise ReportContractError('INVALID_LANGUAGES')
    if type(r['files']) is not list or len(r['files'])>MAX_FILE_RECORDS:
        raise ReportContractError('INVALID_FILES')
    for f in r['files']:
        if type(f) is not dict or set(f) != {'path','language','status','reason','lines','code_lines',
                'blank_lines','comment_lines','function_count','function_scope','sha256','scope',
                'procedural_decision_markers','procedural_loop_markers'} \
                or not safe_rel(f['path']) or f.get('scope') not in ('PRODUCTION','TEST') or type(f['language']) is not str or f['language'] not in LANGUAGES.values() \
                or f['status'] not in ('ANALYZED','SKIPPED','UNSUPPORTED') \
                or (f['reason'] is not None and f['reason'] not in REASON_CODES) \
                or f['function_scope'] not in ('AST','HEURISTIC_PARTIAL','UNSUPPORTED','NOT_ANALYZED') \
                or not all(_nonneg(f[k]) for k in ('lines','code_lines','blank_lines','comment_lines','function_count',
                 'procedural_decision_markers','procedural_loop_markers')) \
                or (f['sha256'] is not None and (type(f['sha256']) is not str or not _SHA.fullmatch(f['sha256']))):
            raise ReportContractError('INVALID_FILE_RECORD')
    if any(f['procedural_loop_markers']>f['procedural_decision_markers']+f['code_lines'] or
           (f['language']!='PHP' and (f['procedural_decision_markers'] or f['procedural_loop_markers']))
           for f in r['files']):
        raise ReportContractError('INVALID_PROCEDURAL_MARKERS')
    if len({f['path'] for f in r['files']}) != len(r['files']):
        raise ReportContractError('DUPLICATE_FILE_RECORD')
    if type(r['functions']) is not list or len(r['functions'])>MAX_FUNCTIONS:
        raise ReportContractError('INVALID_FUNCTIONS')
    for fn in r['functions']:
        if type(fn) is not dict or set(fn) != {'path','name','start_line','end_line','lines',
                'cyclomatic','max_nesting','method','big_o','big_o_confidence','big_o_reason','scope'} \
                or not safe_rel(fn['path']) or fn.get('scope') not in ('PRODUCTION','TEST') or type(fn['name']) is not str or len(fn['name'])>120 \
                or any(ord(c)<32 for c in fn['name']) or not all(type(fn[k]) is int for k in ('start_line','end_line','lines')) \
                or not 1<=fn['start_line']<=fn['end_line']<=10**7 \
                or fn['lines']!=fn['end_line']-fn['start_line']+1 \
                or not _nonneg(fn['cyclomatic']) or not _nonneg(fn['max_nesting']) \
                or fn['method'] not in ('PYTHON_AST','BRACE_HEURISTIC') \
                or fn['big_o_confidence'] not in ('MEDIUM','UNKNOWN') \
                or fn['big_o_reason'] not in ('SIMPLE_BOUNDED_LOOPS','STANDARD_SORT',
                        'CONSTANT_TIME_SUBSET','UNSUPPORTED_CONSTRUCTS','AMORTIZED_APPEND') \
                or type(fn['big_o']) is not str or not re.fullmatch(r'O\([nm0-9 +*^log()]+\)|UNKNOWN',fn['big_o']):
            raise ReportContractError('INVALID_FUNCTION_RECORD')
    if type(r['findings']) is not list or len(r['findings'])>MAX_FINDINGS:
        raise ReportContractError('INVALID_FINDINGS')
    for f in r['findings']:
        if type(f) is not dict or set(f) != {'code','severity','path','line','message','confidence','recommendation'} \
                or type(f['code']) is not str or not _CODE.fullmatch(f['code']) \
                or type(f['severity']) is not str or f['severity'] not in FINDING_SEVERITIES \
                or not safe_rel(f['path']) or type(f['line']) is not int or not 1<=f['line']<=10**7 \
                or type(f['confidence']) is not str or f['confidence'] not in ('HIGH','MEDIUM','LOW') \
                or any(type(f[k]) is not str or len(f[k])>220 or any(ord(c)<32 for c in f[k])
                        for k in ('message','recommendation')):
            raise ReportContractError('INVALID_FINDING')
    if type(r['duplications']) is not list or len(r['duplications'])>50:
        raise ReportContractError('INVALID_DUPLICATIONS')
    for d in r['duplications']:
        if type(d) is not dict or set(d) != {'path_a','line_a','path_b','line_b','lines','confidence'} \
                or not safe_rel(d['path_a']) or not safe_rel(d['path_b']) \
                or not all(type(d[k]) is int and 1<=d[k]<=10**7 for k in ('line_a','line_b','lines')) \
                or d['confidence'] != 'MEDIUM':
            raise ReportContractError('INVALID_DUPLICATION')
    security=r['security']
    if type(security) is not dict or set(security)!={'status','findings','scanned_source_files',
            'tracked_sensitive_files','truncated','scope','scanned_additional_files'} or security['status'] not in (
            'PARTIAL','INDICATORS_FOUND','NO_INDICATORS_FOUND') or security['scope'] != (
            'BOUNDED_PATTERNS_NO_SECRET_VALUES_OR_VALIDITY_CHECK') or \
            not _nonneg(security['scanned_source_files']) or not _nonneg(security['tracked_sensitive_files']) or \
            not _nonneg(security['scanned_additional_files']) or security['scanned_additional_files']>64 \
            or type(security['truncated']) is not bool or type(security['findings']) is not list \
            or len(security['findings'])>200:
        raise ReportContractError('INVALID_SECURITY_REPORT')
    if security['scanned_source_files']!=summary['analyzed_files'] or security['tracked_sensitive_files']>len(security['findings']):
        raise ReportContractError('SECURITY_COVERAGE_MISMATCH')
    approved_sec_codes={'TRACKED_ENV_FILE','POTENTIAL_SECRET_FILE','GITHUB_TOKEN_PATTERN',
        'AWS_ACCESS_KEY_PATTERN','GOOGLE_API_KEY_PATTERN','STRIPE_SECRET_KEY_PATTERN',
        'SLACK_TOKEN_PATTERN','PRIVATE_KEY_HEADER','HIGH_ENTROPY_SECRET_ASSIGNMENT'}
    for finding in security['findings']:
        if type(finding) is not dict or set(finding)!={'code','path','line','confidence'} \
                or finding['code'] not in approved_sec_codes or not safe_rel(finding['path']) \
                or (finding['line'] is not None and (type(finding['line']) is not int or not 1<=finding['line']<=10**7)) \
                or finding['confidence'] not in ('HIGH','MEDIUM'):
            raise ReportContractError('INVALID_SECURITY_FINDING')
    if security['tracked_sensitive_files'] != sum(f['line'] is None for f in security['findings']):
        raise ReportContractError('SECURITY_FILE_COUNT_MISMATCH')
    if security['truncated'] and security['status']!='PARTIAL':
        raise ReportContractError('SECURITY_TRUNCATION_MISMATCH')
    if cov['skipped_files'] and security['status']!='PARTIAL':
        raise ReportContractError('SECURITY_SKIP_MUST_BE_PARTIAL')
    if not (cov['skipped_files'] or security['truncated']):
        should='INDICATORS_FOUND' if security['findings'] else 'NO_INDICATORS_FOUND'
        if security['status']!=should:
            raise ReportContractError('SECURITY_FALSE_CLEAR_CLAIM')

    quality = r['quality']
    if type(quality) is not dict or set(quality) != {'status','score','dimensions','basis',
            'reason_codes','scoring_version','weights_used','confidence'} \
            or quality['status'] not in QUALITY_LEVELS \
            or type(quality['dimensions']) is not dict or set(quality['dimensions']) != {
            'lint','complexity','duplication','structure'} \
            or quality['basis'] != 'HEURISTIC_NOT_A_GRADE' \
            or quality['scoring_version'] != '0.3' \
            or quality['confidence'] not in ('NONE','LOW','MEDIUM') \
            or (quality['score'] is not None and (type(quality['score']) is not int or not 0<=quality['score']<=100)):
        raise ReportContractError('INVALID_QUALITY')
    approved_reasons={'INSUFFICIENT_CODE','LOW_SOURCE_READ_COVERAGE',
                       'TRUNCATED_EVIDENCE','LIMITED_COMPLEXITY_ANALYSIS'}
    if type(quality['reason_codes']) is not list or len(quality['reason_codes'])>6 or (
            len(set(quality['reason_codes']))!=len(quality['reason_codes']) or any(
                type(x) is not str or x not in approved_reasons for x in quality['reason_codes'])):
        raise ReportContractError('INVALID_SCORE_REASONS')
    if (quality['status'] == 'INSUFFICIENT_COVERAGE') != (quality['score'] is None):
        raise ReportContractError('INVALID_QUALITY_SCORE_STATUS')
    if quality['status'] in ('PROVISIONAL','PROVISIONAL_LIMITED') and any(cov['limits'].values()):
        raise ReportContractError('INVALID_SCORE_WHILE_INCOMPLETE')
    if quality['status']=='PROVISIONAL_LIMITED' and 'LIMITED_COMPLEXITY_ANALYSIS' not in quality['reason_codes']:
        raise ReportContractError('MISSING_LIMITATION_REASON')
    if quality['status']=='PROVISIONAL' and quality['reason_codes']:
        raise ReportContractError('UNQUALIFIED_PROVISIONAL')
    if quality['status']=='INSUFFICIENT_COVERAGE' and not quality['reason_codes']:
        raise ReportContractError('MISSING_SCORE_REASON')
    if any(v is not None and (type(v) is not int or not 0<=v<=100)
           for v in quality['dimensions'].values()):
        raise ReportContractError('INVALID_QUALITY_DIMENSIONS')
    weights=quality['weights_used']
    if type(weights) is not dict or any(k not in quality['dimensions'] or \
            quality['dimensions'][k] is None or type(v) not in (int,float) or not 0<v<=1
            for k,v in weights.items()):
        raise ReportContractError('INVALID_SCORE_WEIGHTS')
    base_weights={'lint':0.30,'complexity':0.30,'duplication':0.20,'structure':0.20}
    applicable={k:v for k,v in base_weights.items() if quality['dimensions'][k] is not None}
    total_weight=sum(applicable.values())
    if weights and (set(weights)!=set(applicable) or any(
            abs(weights[k]-applicable[k]/total_weight)>0.000002 for k in weights)):
        raise ReportContractError('SCORE_WEIGHT_MANIPULATION')
    if quality['score'] is None:
        if weights or quality['confidence']!='NONE' or any(v is not None for v in quality['dimensions'].values()):
            raise ReportContractError('INVALID_UNAVAILABLE_SCORE')
    else:
        if not weights or abs(sum(weights.values())-1)>0.00002:
            raise ReportContractError('INVALID_SCORE_WEIGHT_SUM')
        if abs(round(sum(quality['dimensions'][k]*v for k,v in weights.items()))-quality['score'])>1:
            raise ReportContractError('INVALID_SCORE_FORMULA')
        if (quality['status']=='PROVISIONAL_LIMITED') != (quality['confidence']=='LOW'):
            raise ReportContractError('INVALID_SCORE_CONFIDENCE')
        if quality['status']=='PROVISIONAL' and quality['confidence']!='MEDIUM':
            raise ReportContractError('INVALID_SCORE_CONFIDENCE')
    if type(r['limitations']) is not list or len(r['limitations'])>16 or any(
            type(x) is not str or len(x)>300 for x in r['limitations']):
        raise ReportContractError('INVALID_LIMITATIONS')
    # Claim-level accounting prevents apparent PASS with unanalysed source.
    if (summary['source_lines'] != sum(x['lines'] for x in r['files'])
            or summary['blank_lines'] != sum(x['blank_lines'] for x in r['files'])
            or summary['comment_lines'] != sum(x['comment_lines'] for x in r['files'])
            or summary['code_lines'] != sum(x['code_lines'] for x in r['files'])
            or summary['source_candidates'] != summary['analyzed_files'] + cov['skipped_files']
            or summary['production_files']+summary['test_files'] != summary['analyzed_files']
            or summary['production_code_lines']+summary['test_code_lines'] != summary['code_lines']
            or summary['production_code_lines']!=sum(f['code_lines'] for f in r['files'] if f['status']=='ANALYZED' and f['scope']=='PRODUCTION')
            or summary['test_code_lines']!=sum(f['code_lines'] for f in r['files'] if f['status']=='ANALYZED' and f['scope']=='TEST')
            or cov['duplicate_production_lines']>summary['production_code_lines']
            or abs(cov['duplicate_production_ratio']-round(
                cov['duplicate_production_lines']/max(summary['production_code_lines'],1),6))>0.000002
            or cov['supported_files'] + cov['unsupported_files'] != summary['analyzed_files']
            or cov['function_scanned_files']>cov['function_eligible_files']
            or cov['function_scanned_files']>cov['supported_files']
            or cov['function_eligible_files']>summary['source_candidates']
            or cov['skipped_files'] != sum(x['status']=='SKIPPED' for x in r['files'])
            or summary['analyzed_files'] != len([x for x in r['files'] if x['status']=='ANALYZED'])
            or any(x['lines'] != x['code_lines']+x['comment_lines']+x['blank_lines'] for x in r['files'])):
        raise ReportContractError('COVERAGE_ARITHMETIC_MISMATCH')
    if any(cov['limits'].values()) and r['status'] != 'PARTIAL':
        raise ReportContractError('TRUNCATED_REPORT_NOT_PARTIAL')
    if (big_o_cov['estimated_functions']+big_o_cov['unknown_functions'] != len(r['functions']) or
            big_o_cov['estimated_functions'] != sum(fn['big_o'] != 'UNKNOWN' for fn in r['functions'])):
        raise ReportContractError('BIG_O_COVERAGE_MISMATCH')
    fcount=Counter(x['path'] for x in r['functions'])
    if any(fcount[f['path']] != f['function_count'] for f in r['files']):
        raise ReportContractError('FUNCTION_FILE_MISMATCH')
    scopes={x['path']:x['scope'] for x in r['files']}
    if any(f['path'] not in scopes or f['scope'] != scopes.get(f['path']) for f in r['functions']):
        raise ReportContractError('FUNCTION_UNKNOWN_SOURCE')
    if summary['analyzed_files'] != sum(f['status']=='ANALYZED' for f in r['files']) \
            or summary['functions_found'] != len(r['functions']) \
            or summary['findings'] != len(r['findings']) \
            or summary['duplicate_groups'] != len(r['duplications']):
        raise ReportContractError('SUMMARY_MISMATCH')
