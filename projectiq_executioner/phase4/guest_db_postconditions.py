"""D3 guest-only, read-only MySQL witness for D2 registration persistence.

Never import or evaluate student modules here. A fixed PHP program reads the
scoped database using prepared parameters. It emits counts and booleans only:
no email, credential, row data, SQL text, or application response body crosses
the VM boundary.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping

_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}\Z', re.ASCII)
_HEX = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_SAFE_VALUE = re.compile(r'[A-Za-z0-9 _.,:/@-]{1,128}\Z', re.ASCII)
_SENSITIVE = re.compile(r'(password|passwd|secret|token|credential|api_?key|private_?key|auth)', re.I)
_MAX_CHECKS = 8


class DbPostconditionError(ValueError):
    """Reject unsafe, unauthorized or unverifiable DB witness data."""


def _deny(code: str) -> None:
    raise DbPostconditionError(code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise DbPostconditionError('INVALID_POSTCONDITION_JSON') from exc


def _check_specs(checks: Any) -> list[dict]:
    if not isinstance(checks, list) or not 1 <= len(checks) <= _MAX_CHECKS:
        _deny('POSTCONDITION_CHECK_COUNT')
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) !=
                {'table', 'lookup_column', 'expected_fields'}):
            _deny('INVALID_POSTCONDITION_CHECK')
        table, column, fields = check['table'], check['lookup_column'], check['expected_fields']
        if (not isinstance(table, str) or not _NAME.fullmatch(table)
                or table.casefold() in {'mysql', 'sys', 'information_schema', 'performance_schema'}
                or not isinstance(column, str) or not _NAME.fullmatch(column)
                or _SENSITIVE.search(column) or not isinstance(fields, dict)
                or not 1 <= len(fields) <= 8):
            _deny('UNSAFE_POSTCONDITION_IDENTIFIER')
        target = (table.casefold(), column.casefold())
        if target in seen:
            _deny('DUPLICATE_POSTCONDITION_TARGET')
        seen.add(target)
        for key, value in fields.items():
            if (not isinstance(key, str) or not _NAME.fullmatch(key)
                    or _SENSITIVE.search(key)
                    or type(value) is not str or not _SAFE_VALUE.fullmatch(value)):
                _deny('UNSAFE_POSTCONDITION_EXPECTATION')
        out.append({'id': 'PC-' + sha256(_canonical(check)).hexdigest()[:16].upper(), **check})
    if len(_canonical({'checks': checks})) > 16_384:
        _deny('POSTCONDITION_BYTE_LIMIT')
    return out


def validate_postcondition_catalog(catalog: Any, source: Mapping[str, Any]) -> list[dict]:
    if (not isinstance(catalog, dict) or set(catalog) != {'schema_version', 'source', 'checks'}
            or catalog['schema_version'] != '0.1' or catalog['source'] != dict(source)):
        _deny('INVALID_POSTCONDITION_CATALOG_OR_SOURCE')
    if len(_canonical(catalog)) > 32_768:
        _deny('POSTCONDITION_CATALOG_TOO_LARGE')
    return _check_specs(catalog['checks'])


def validate_guest_postcondition_bundle(bundle: Any, *, expected_source: Mapping[str, Any],
                                        expected_adapter: str) -> list[dict]:
    if (not isinstance(bundle, dict) or set(bundle) != {
            'schema_version', 'source', 'adapter', 'postcondition_catalog_sha256',
            'auth_catalog_sha256', 'oracle_catalog_sha256', 'checks'}
            or bundle['schema_version'] != '0.1'
            or bundle['source'] != dict(expected_source)
            or bundle['adapter'] != expected_adapter or bundle['adapter'] != 'php_mysql_v1'
            or any(not isinstance(bundle.get(field), str) or not _HEX.fullmatch(bundle[field])
                   for field in ('postcondition_catalog_sha256', 'auth_catalog_sha256',
                                 'oracle_catalog_sha256'))):
        _deny('INVALID_GUEST_POSTCONDITION_BUNDLE')
    checks = bundle['checks']
    if not isinstance(checks, list):
        _deny('INVALID_GUEST_CHECKS')
    raw = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {'id', 'table', 'lookup_column', 'expected_fields'}:
            _deny('INVALID_GUEST_CHECK')
        raw.append({key: check[key] for key in ('table', 'lookup_column', 'expected_fields')})
    compiled = _check_specs(raw)
    if compiled != checks:
        _deny('POSTCONDITION_IDS_NOT_CANONICAL')
    catalog = {'schema_version': '0.1', 'source': bundle['source'], 'checks': raw}
    if sha256(_canonical(catalog)).hexdigest() != bundle['postcondition_catalog_sha256']:
        _deny('POSTCONDITION_DIGEST_MISMATCH')
    return compiled


# Static trusted PHP; variable database identifiers are strictly validated on
# host and guest. Only the email is a bound SQL parameter. Never print rows.
_PHP_QUERY = r'''
error_reporting(0);
try {
    $raw = stream_get_contents(STDIN, 20000);
    $doc = json_decode($raw, true, 20, JSON_THROW_ON_ERROR);
    if (!is_array($doc) || !isset($doc['checks']) || !is_array($doc['checks']) ||
        !isset($doc['email']) || !is_string($doc['email'])) exit(11);
    if (getenv('PROJECTIQ_DB_NAME') !== 'projectiq_run') exit(12);
    $pdo = new PDO('mysql:host=127.0.0.1;dbname=projectiq_run;charset=utf8mb4',
        getenv('PROJECTIQ_DB_USER'), getenv('PROJECTIQ_DB_PASSWORD'), [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_EMULATE_PREPARES => false, PDO::ATTR_TIMEOUT => 5
        ]);
    $out = [];
    foreach ($doc['checks'] as $check) {
        $names = array_merge([$check['table'], $check['lookup_column']],
                             array_keys($check['expected_fields']));
        foreach ($names as $name) {
            if (!is_string($name) || !preg_match('/\A[A-Za-z_][A-Za-z0-9_]{0,62}\z/D', $name)) exit(13);
        }
        $cols = array_keys($check['expected_fields']);
        $selected = implode(',', array_map(static fn($name) => '`' . $name . '`', $cols));
        $statement = $pdo->prepare('SELECT ' . $selected . ' FROM `' . $check['table'] .
                                   '` WHERE `' . $check['lookup_column'] . '` = ? LIMIT 2');
        $statement->execute([$doc['email']]);
        $rows = $statement->fetchAll(PDO::FETCH_ASSOC);
        $count = count($rows);
        $match = $count === 1;
        if ($match) {
            foreach ($check['expected_fields'] as $key => $expected) {
                if (!array_key_exists($key, $rows[0]) || !is_string($rows[0][$key]) ||
                    $rows[0][$key] !== $expected) $match = false;
            }
        }
        $out[] = ['id' => $check['id'], 'matches' => $count, 'fields_match' => $match];
    }
    echo json_encode($out, JSON_THROW_ON_ERROR);
} catch (Throwable $e) { exit(14); }
'''


def make_observer(bundle: Mapping[str, Any], *, expected_source: Mapping[str, Any],
                  expected_adapter: str, env: dict, trusted_process: Callable[..., Any]):
    """Return a guest-only hook and opaque witness states; no host secrets."""
    checks = validate_guest_postcondition_bundle(bundle, expected_source=expected_source,
                                                  expected_adapter=expected_adapter)
    if (not isinstance(env, dict) or env.get('PROJECTIQ_DB_NAME') != 'projectiq_run'):
        _deny('DATABASE_SCOPE_NOT_VERIFIED')
    observations: dict[str, list[dict]] = {}

    def observe(stage: str, email: str) -> None:
        if (stage not in {'BEFORE', 'AFTER'} or stage in observations
                or not isinstance(email, str)
                or not re.fullmatch(r'piq-[a-f0-9]{24}@example\.invalid', email, re.ASCII)):
            _deny('INVALID_DB_OBSERVATION_CONTEXT')
        request = _canonical({'checks': checks, 'email': email})
        proc = trusted_process(['/usr/bin/php', '-r', _PHP_QUERY], input=request,
                               timeout=20, env=env)
        if proc.returncode != 0 or not isinstance(proc.stdout, bytes) or len(proc.stdout) > 8192:
            _deny('DB_READ_PROBE_FAILED')
        try:
            rows = json.loads(proc.stdout.decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise DbPostconditionError('DB_READ_REPORT_INVALID') from exc
        if not isinstance(rows, list) or len(rows) != len(checks):
            _deny('DB_READ_REPORT_INVALID')
        for spec, row in zip(checks, rows):
            if (not isinstance(row, dict) or set(row) != {'id', 'matches', 'fields_match'}
                    or row['id'] != spec['id'] or type(row['matches']) is not int
                    or row['matches'] not in (0, 1, 2) or type(row['fields_match']) is not bool
                    or row['fields_match'] and row['matches'] != 1):
                _deny('DB_READ_REPORT_INVALID')
        observations[stage] = rows

    return observe, observations


def build_guest_postcondition_report(bundle: Mapping[str, Any], auth_report: Mapping[str, Any],
                                     observations: Mapping[str, list[dict]]) -> dict:
    checks = validate_guest_postcondition_bundle(bundle, expected_source=bundle['source'],
                                                  expected_adapter=bundle['adapter'])
    reg = next((s for s in auth_report['results'] if s['step'] == 'REGISTRATION'), None)
    results = []
    counts = Counter()
    for idx, check in enumerate(checks):
        before = observations.get('BEFORE', [])
        after = observations.get('AFTER', [])
        b = before[idx] if len(before) == len(checks) else None
        a = after[idx] if len(after) == len(checks) else None
        if b is None or a is None or reg is None or reg['result'] != 'PASS':
            verdict, issue = 'INCONCLUSIVE', 'PREREQUISITE_NOT_SATISFIED'
        else:
            verdict = 'PASS' if b['matches'] == 0 and a['matches'] == 1 and a['fields_match'] else 'FAIL'
            issue = None
        counts[verdict] += 1
        results.append({'id': check['id'], 'result': verdict,
                        'before_matches': b['matches'] if b else None,
                        'after_matches': a['matches'] if a else None,
                        'expected_fields_match': a['fields_match'] if a else None,
                        'issue_code': issue})
    return {'schema_version': '0.1', 'phase': 4, 'stage': 'guest_database_postconditions',
            'source': dict(bundle['source']), 'adapter': bundle['adapter'],
            'postcondition_catalog_sha256': bundle['postcondition_catalog_sha256'],
            'execution': 'READ_ONLY_GUEST_MYSQL_OBSERVATION',
            'runtime_attestation': 'NOT_EVALUATED_BY_DB_INTERPRETER',
            'results': results,
            'summary': {'total': len(checks), 'passed': counts['PASS'], 'failed': counts['FAIL'],
                        'inconclusive': counts['INCONCLUSIVE']},
            'status': 'INCOMPLETE' if counts['INCONCLUSIVE'] else 'COMPLETE'}
