"""Guest-only, trusted database fixture seeder for the PHP/MySQL pilot.

This file is copied separately from reviewed student source into /home/projectiq.
No operator-provided SQL is executed; identifiers are constrained and values
are passed as PDO prepared-statement parameters. Never return row values.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping

_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}\Z', re.ASCII)
_HEX = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_SENSITIVE = re.compile(r'(?:password|passwd|secret|token|credential|api_?key|private_?key|auth)', re.I)
_MAX_TABLES = 8
_MAX_ROWS = 24
_MAX_CELLS = 240
_MAX_BYTES = 16_384


class DbFixtureError(ValueError):
    """Reject an unsupported, unsafe, or inconsistent fixture bundle."""


def _reject(code: str) -> None:
    raise DbFixtureError(code)


def _json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DbFixtureError('NON_CANONICAL_FIXTURE_JSON') from exc


def validate_fixture_bundle(bundle: Mapping[str, Any], *, expected_source: Mapping[str, Any],
                            expected_adapter: str) -> dict:
    """Strictly validate on BOTH host and guest before any database operation.

    Never accept SQL text, paths, tables outside the scoped database, schema
    commands, row transformations, code, or credential-bearing columns.
    """
    if not isinstance(bundle, dict) or set(bundle) != {
        'schema_version', 'source', 'adapter', 'fixture_catalog_sha256', 'tables'
    } or bundle.get('schema_version') != '0.1':
        _reject('INVALID_FIXTURE_BUNDLE_SCHEMA')
    if (bundle['source'] != dict(expected_source) or
            bundle['adapter'] != expected_adapter or
            bundle['adapter'] != 'php_mysql_v1'):
        _reject('FIXTURE_SOURCE_OR_ADAPTER_MISMATCH')
    if not isinstance(bundle['fixture_catalog_sha256'], str) or not _HEX.fullmatch(bundle['fixture_catalog_sha256']):
        _reject('INVALID_FIXTURE_CATALOG_DIGEST')
    tables = bundle['tables']
    if not isinstance(tables, list) or not 1 <= len(tables) <= _MAX_TABLES:
        _reject('INVALID_FIXTURE_TABLE_COUNT')
    names: set[str] = set()
    total_rows = 0
    total_cells = 0
    for table in tables:
        if not isinstance(table, dict) or set(table) != {'name', 'rows'}:
            _reject('INVALID_FIXTURE_TABLE_SHAPE')
        name, rows = table['name'], table['rows']
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            _reject('INVALID_FIXTURE_TABLE_NAME')
        if name.lower() in {'mysql', 'information_schema', 'performance_schema', 'sys'}:
            _reject('RESERVED_FIXTURE_TABLE_NAME')
        if name.casefold() in names:
            _reject('DUPLICATE_FIXTURE_TABLE')
        names.add(name.casefold())
        if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_ROWS:
            _reject('INVALID_FIXTURE_ROWS')
        total_rows += len(rows)
        if total_rows > _MAX_ROWS:
            _reject('FIXTURE_ROW_BUDGET_EXCEEDED')
        for row in rows:
            if not isinstance(row, dict) or not 1 <= len(row) <= 12:
                _reject('INVALID_FIXTURE_ROW_SHAPE')
            total_cells += len(row)
            if total_cells > _MAX_CELLS:
                _reject('FIXTURE_CELL_BUDGET_EXCEEDED')
            for col, value in row.items():
                if not isinstance(col, str) or not _NAME.fullmatch(col) or _SENSITIVE.search(col):
                    _reject('INVALID_OR_SENSITIVE_FIXTURE_COLUMN')
                if value is None or type(value) is bool:
                    continue
                if type(value) is int and -(10**9) <= value <= 10**9:
                    continue
                if (type(value) is str and len(value.encode('utf-8')) <= 256
                        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
                    continue
                _reject('UNSUPPORTED_FIXTURE_VALUE')
    if len(_json(bundle)) > _MAX_BYTES:
        _reject('FIXTURE_BYTE_LIMIT_EXCEEDED')
    expected_digest = sha256(_json({'schema_version': '0.1',
                                    'source': bundle['source'],
                                    'tables': tables})).hexdigest()
    if bundle['fixture_catalog_sha256'] != expected_digest:
        _reject('FIXTURE_CONTENT_DIGEST_MISMATCH')
    return {'table_count': len(tables), 'row_count': total_rows}


# Trusted, static PHP program. Only the bounded data argument comes from a
# reviewed JSON bundle. DB credentials are injected by the guest controller.
# There is no operator-controlled PHP text or SQL clause.
_PHP_SEED = r'''
error_reporting(0);
try {
    $raw = stream_get_contents(STDIN, 20000);
    $doc = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
    if (!is_array($doc) || !isset($doc['tables']) || !is_array($doc['tables'])) exit(11);
    $db = getenv('PROJECTIQ_DB_NAME');
    if ($db !== 'projectiq_run') exit(12);
    $pdo = new PDO('mysql:host=127.0.0.1;dbname=projectiq_run;charset=utf8mb4',
        getenv('PROJECTIQ_DB_USER'), getenv('PROJECTIQ_DB_PASSWORD'), [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
        PDO::ATTR_TIMEOUT => 5
    ]);
    $pdo->beginTransaction();
    $count = 0;
    foreach ($doc['tables'] as $table) {
        $name = $table['name'];
        if (!is_string($name) || !preg_match('/\A[A-Za-z_][A-Za-z0-9_]{0,62}\z/D', $name)) exit(13);
        foreach ($table['rows'] as $row) {
            $cols = array_keys($row);
            foreach ($cols as $col) {
                if (!is_string($col) || !preg_match('/\A[A-Za-z_][A-Za-z0-9_]{0,62}\z/D', $col)) exit(14);
            }
            $quoted = array_map(static fn($col) => '`' . $col . '`', $cols);
            $sql = 'INSERT INTO `' . $name . '` (' . implode(',', $quoted) . ') VALUES (' .
                implode(',', array_fill(0, count($cols), '?')) . ')';
            $statement = $pdo->prepare($sql);
            $values = array_values($row);
            foreach ($values as $i => $value) {
                if (is_null($value)) $kind = PDO::PARAM_NULL;
                elseif (is_bool($value)) $kind = PDO::PARAM_BOOL;
                elseif (is_int($value)) $kind = PDO::PARAM_INT;
                else $kind = PDO::PARAM_STR;
                $statement->bindValue($i + 1, $value, $kind);
            }
            $statement->execute();
            if ($statement->rowCount() !== 1) exit(15);
            $count++;
        }
    }
    $pdo->commit();
    echo 'SEEDED_ROWS=' . $count;
} catch (Throwable $e) { exit(16); }
'''


def seed_mysql_fixture(bundle: dict, *, expected_source: Mapping[str, Any],
                       expected_adapter: str, env: dict,
                       trusted_process: Callable[..., Any]) -> dict:
    """Seed the disposable database BEFORE starting the student's HTTP server."""
    counts = validate_fixture_bundle(bundle, expected_source=expected_source,
                                     expected_adapter=expected_adapter)
    if not isinstance(env, dict) or env.get('PROJECTIQ_DB_NAME') != 'projectiq_run':
        _reject('UNSCOPED_DATABASE_ENVIRONMENT')
    content = _json({'tables': bundle['tables']})
    # Trusted guest process has only database-scoped credentials, not MySQL root.
    proc = trusted_process(['/usr/bin/php', '-r', _PHP_SEED], input=content,
                           timeout=25, env=env)
    if (proc.returncode != 0 or proc.stdout !=
            ('SEEDED_ROWS=' + str(counts['row_count'])).encode('ascii')):
        _reject('DATABASE_SEEDING_NOT_VERIFIED')
    return {'schema_version': '0.1', 'status': 'FIXTURES_SEEDED',
            'fixture_catalog_sha256': bundle['fixture_catalog_sha256'],
            'table_count': counts['table_count'], 'seeded_rows': counts['row_count'],
            'database_scope': 'DISPOSABLE_GUEST_DATABASE_ONLY'}
