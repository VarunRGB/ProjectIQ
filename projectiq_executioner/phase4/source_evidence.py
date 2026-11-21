"""Phase 4C-B: inert, bounded HTTP source evidence; never an executable oracle.

Only analyze Phase 3-discovered targets, without importing project modules,
executing source, connecting to services, or manufacturing expected results.
Heuristics are intentionally advisory: a code branch is NOT proof of behavior.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..phase3.contracts import TestPlanContractError, validate_test_plan
from ..phase3.source_contracts import _relative_path, source_bytes
from .oracle_contracts import digest

SCHEMA_VERSION = '0.1'
MAX_TARGETS = 400
MAX_FILES_PER_TARGET = 6
MAX_SOURCE_BYTES = 250_000
MAX_TOTAL_FILES = 96
MAX_LINES_PER_FILE = 3000
MAX_HANDLER_LINES = 120
MAX_ITEMS_PER_KIND = 24
MAX_SPEC_FILES = 8
MAX_SPEC_PATHS = 500
_CODE_EXT = {'.php', '.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'}
_HTTP_CATEGORIES = {'HTTP_READINESS', 'HTTP_ROUTE', 'UI_PAGE'}
_NAME = r'[A-Za-z_][A-Za-z0-9_]{0,63}'
_SAFE_NAME = re.compile(r'^' + _NAME + r'$')
_PHP_FIELD = re.compile(r'\$_(POST|GET|REQUEST)\s*\[\s*[\'\"](' + _NAME + r')[\'\"]\s*\]')
_PHP_FILTER = re.compile(r'\bfilter_input\s*\(\s*INPUT_(POST|GET)\s*,\s*[\'\"](' + _NAME + r')[\'\"]')
# PHP applications frequently decode JSON request bodies into an array/object.
# Only literal, direct php://input decoding establishes a request-body alias;
# arbitrary local arrays, session objects or decoded constants are not inputs.
_PHP_JSON_BODY_BIND = re.compile(
    r'\$(' + _NAME + r')\s*=\s*json_decode\s*\(\s*file_get_contents\s*'
    r'\(\s*[\'\"]php://input[\'\"]\s*\)\s*(?:,\s*(true|false))?\s*\)', re.I)
_PHP_ARRAY_ACCESS = re.compile(r'\$(' + _NAME + r')\s*\[\s*[\'\"](' + _NAME + r')[\'\"]\s*\]')
_PHP_OBJECT_ACCESS = re.compile(r'\$(' + _NAME + r')\s*->\s*(' + _NAME + r')\b')
_PHP_DYNAMIC_ACCESS = re.compile(r'\$(' + _NAME + r')\s*\[\s*\$')
_PHP_RAW_JSON_INPUT = re.compile(r'file_get_contents\s*\(\s*[\'\"]php://input[\'\"]\s*\)', re.I)
_JS_DOT = re.compile(r'\breq\.(body|query|params)\.(' + _NAME + r')\b')
_JS_BRACKET = re.compile(r'\breq\.(body|query|params)\s*\[\s*[\'\"](' + _NAME + r')[\'\"]\s*\]')
_JS_DESTRUCTURE = re.compile(r'\b(?:const|let|var)\s*\{([^{}]{1,180})\}\s*=\s*req\.(body|query|params)\b')
_PY_FIELD = re.compile(r'\brequest\.(form|args|json)\s*\[\s*[\'\"](' + _NAME + r')[\'\"]\s*\]')
_PY_GET = re.compile(r'\brequest\.(form|args|json)\.get\s*\(\s*[\'\"](' + _NAME + r')[\'\"]')
_PY_VALUES = re.compile(r'\brequest\.values\.get\s*\(\s*[\'\"](' + _NAME + r')[\'\"]')
_PHP_STATUS = re.compile(r'\bhttp_response_code\s*\(\s*([1-5][0-9]{2})\s*\)')
_JS_STATUS = re.compile(r'\b(?:res|reply)\.(?:status|sendStatus)\s*\(\s*([1-5][0-9]{2})\s*\)')
_PY_STATUS = re.compile(r'(?:\bstatus_code\s*=\s*|\babort\s*\(\s*)([1-5][0-9]{2})\b')
_PY_RETURN_STATUS = re.compile(r',\s*([1-5][0-9]{2})\s*(?:\)|$)')
_ROUTE_DECL = re.compile(r'\b(?:app|router|server|blueprint|bp)\.(?:get|post|put|patch|delete|route)\s*\(|^\s*@\w+(?:\.\w+)*\.(?:get|post|put|patch|delete|route)\s*\(', re.I)
_SQL_WRITE = re.compile(r'\b(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b', re.I)
_PY_DATABASE = re.compile(r'\b(?:db\.session\.(?:add|delete|commit)|\w+\.objects\.(?:create|update)|\w+\.save\s*\()', re.I)
_JS_DATABASE = re.compile(r'\b(?:\w+\.create\s*\(|\w+\.(?:updateOne|deleteOne|findOneAndUpdate)\s*\()', re.I)
_SESSION = re.compile(r'\$_SESSION\b|\b(?:req\.session|request\.session|session\[|session\.get\s*\(|login_required|jwt_required|req\.user)\b', re.I)
_REDIRECT = re.compile(r'\b(?:redirect\s*\(|res\.redirect\s*\(|header\s*\(\s*[\'\"]Location\s*:)', re.I)
_PHP_JSON = re.compile(r'\bjson_encode\s*\(')
_JS_JSON = re.compile(r'\b(?:res|reply)\.json\s*\(')
_PY_JSON = re.compile(r'\bjsonify\s*\(')
_DYNAMIC_BODY = re.compile(r'\breq\.body\b|\brequest\.get_json\s*\(|\$_POST\b')
_EXTERNAL_DEP = re.compile(r'\b(?:requests\.(?:get|post|put)|axios\.(?:get|post|put)|send_mail|smtplib|curl_exec)\s*\(', re.I)
_JSON_KEY = re.compile(r'(?:[\'\"](' + _NAME + r')[\'\"]|\b(' + _NAME + r'))\s*(?:=>|:)')
_PY_KWARG = re.compile(r'\b(' + _NAME + r')\s*=')
_SPEC_FILENAMES = ('openapi.json', 'swagger.json', 'api/openapi.json', 'api/swagger.json',
                   'docs/openapi.json', 'docs/swagger.json')


class SourceEvidenceError(ValueError):
    """Unsafe or inconsistent source-evidence input."""


def _fail(code: str) -> None:
    raise SourceEvidenceError(code)


def _evidence(path: str, line: int | None, kind: str) -> dict:
    return {'path': path, 'line': line, 'kind': kind}


def _append_record(store: dict, key: tuple, record: dict, *, limit: int = MAX_ITEMS_PER_KIND) -> None:
    if len(store) < limit or key in store:
        store[key] = record


def _file_paths(case: Mapping[str, Any]) -> list[str]:
    candidates = set()
    for item in case.get('evidence', []):
        if item.get('source') == 'repository':
            path = item.get('path')
            if _relative_path(path) and Path(path).suffix.lower() in _CODE_EXT:
                candidates.add(path)
    path = case['target']['path']
    if isinstance(path, str) and path.lower().endswith('.php'):
        rel = path.lstrip('/')
        if _relative_path(rel):
            candidates.add(rel)
    # No arbitrary directory walk or search across unrelated source files.
    return sorted(candidates)[:MAX_FILES_PER_TARGET]


def _route_anchor(line: str, method: str, path: str) -> bool:
    """Use an explicit method + literal route declaration, never a fuzzy match."""
    if line.lstrip().startswith(('#', '//', '*', '/*')) or not _ROUTE_DECL.search(line):
        return False
    # Route source can express a path segment with :param or {param}; don't
    # guess across dynamic transformations or concatenation.
    variants = [path, re.sub(r':(' + _NAME + r')', r'{\1}', path)]
    quoted = any((quote + p + quote) in line for p in variants for quote in ('\'', '"', '`'))
    if not quoted:
        return False
    lower = line.lower()
    if re.search(r'\.(?:get|post|put|patch|delete)\s*\(', lower):
        return bool(re.search(r'\.' + method.lower() + r'\s*\(', lower))
    # route() with methods=[...] may span several lines; refuse to attribute
    # an unspecified method rather than guessing.
    if re.search(r'\.route\s*\(', lower):
        listed = re.search(r'\bmethods\s*=\s*\[([^\]]*)\]', line, re.I)
        if listed:
            return bool(re.search(r'[\"\']' + method + r'[\"\']', listed[1], re.I))
        return method == 'GET'  # Flask's default only when no methods are declared
    return False


def _handler_lines(text: str, extension: str, path: str, method: str) -> tuple[list[tuple[int, str]], str]:
    lines = text.splitlines()[:MAX_LINES_PER_FILE]
    if extension == '.php' and path.lstrip('/').lower().endswith('.php'):
        # File-based PHP route: a file maps to a single URL in the usual adapter.
        # Shared includes are not traversed; conditional branches remain advisory.
        return list(enumerate(lines[:MAX_HANDLER_LINES * 3], 1)), 'FILE_BASED_PHP_HANDLER'
    anchors = [i for i, line in enumerate(lines) if _route_anchor(line, method, path)]
    if len(anchors) != 1:
        return [], 'ROUTE_SCOPE_NOT_ESTABLISHED'
    start = anchors[0]
    end = min(len(lines), start + MAX_HANDLER_LINES)
    for i in range(start + 1, end):
        if (not lines[i].lstrip().startswith(('#', '//', '*', '/*'))
                and _ROUTE_DECL.search(lines[i])):
            end = i
            break
    return [(i+1, lines[i]) for i in range(start, end)], 'ROUTE_DECLARATION_SLICE'


def _php_or_py_or_js_input(line: str, ext: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if ext == '.php':
        for m in _PHP_FIELD.finditer(line):
            found.append(({'POST': 'FORM', 'GET': 'QUERY', 'REQUEST': 'REQUEST_UNSPECIFIED'}[m[1]], m[2]))
        for m in _PHP_FILTER.finditer(line):
            found.append(({'POST': 'FORM', 'GET': 'QUERY'}[m[1]], m[2]))
    elif ext == '.py':
        for pat in (_PY_FIELD, _PY_GET):
            for m in pat.finditer(line):
                found.append(({'form': 'FORM', 'args': 'QUERY', 'json': 'JSON'}[m[1]], m[2]))
        for m in _PY_VALUES.finditer(line):
            found.append(('REQUEST_UNSPECIFIED', m[1]))
    else:
        for pat in (_JS_DOT, _JS_BRACKET):
            for m in pat.finditer(line):
                found.append(({'body': 'JSON_OR_FORM', 'query': 'QUERY', 'params': 'PATH'}[m[1]], m[2]))
        for m in _JS_DESTRUCTURE.finditer(line):
            location = {'body': 'JSON_OR_FORM', 'query': 'QUERY', 'params': 'PATH'}[m[2]]
            for part in m[1].split(','):
                key = part.strip().split(':', 1)[0].strip()
                if _SAFE_NAME.fullmatch(key):
                    found.append((location, key))
    return found


def _response_keys(line: str, ext: str) -> list[str]:
    """Only expose field NAMES; never expected literal values or source text."""
    target = (_PHP_JSON if ext == '.php' else _PY_JSON if ext == '.py' else _JS_JSON)
    match = target.search(line)
    if not match:
        return []
    snippet = line[match.end():]
    if len(snippet) > 300 or not any(t in snippet for t in ('{', '[', '=')):
        return []
    if ext == '.py':
        return sorted(set(m[1] for m in _PY_KWARG.finditer(snippet)))[:MAX_ITEMS_PER_KIND]
    return sorted(set((m[1] or m[2]) for m in _JSON_KEY.finditer(snippet)))[:MAX_ITEMS_PER_KIND]


def _source_heuristics(text: str, ext: str, method: str, path: str, file_path: str) -> dict:
    selected, scope = _handler_lines(text, ext, path, method)
    inputs: dict[tuple, dict] = {}
    signals: dict[tuple, dict] = {}
    requirements: set[str] = set()
    # Route parameters are explicitly present in the Phase 3-discovered target.
    for name in re.findall(r':(' + _NAME + r')', path):
        _append_record(inputs, ('PATH', name), {'location': 'PATH', 'name': name,
                       'required': 'REQUIRED', 'evidence': _evidence(file_path, None, 'DISCOVERED_ROUTE')})
    if not selected:
        return {'inputs': list(inputs.values()), 'signals': [], 'requirements': [], 'scope': scope}
    statuses = _PHP_STATUS if ext == '.php' else _PY_STATUS if ext == '.py' else _JS_STATUS
    json_aliases: dict[str, str] = {}
    has_raw_json_input = False
    if ext == '.php':
        # Alias discovery precedes field extraction to accommodate nearby code
        # without attributing every $data['field'] in the file to HTTP input.
        for _line_no, candidate in selected:
            if candidate.lstrip().startswith(('#', '//', '*', '/*')):
                continue
            has_raw_json_input |= bool(_PHP_RAW_JSON_INPUT.search(candidate))
            for binding in _PHP_JSON_BODY_BIND.finditer(candidate):
                # json_decode(..., true) -> array; default/false -> object.
                json_aliases[binding[1]] = ('array' if binding[2] and
                                            binding[2].lower() == 'true' else 'object')
    json_fields_found = False
    json_dynamic_access = False
    for line_no, line in selected:
        # Ignore single-line comments to avoid treating TODO text as runtime evidence.
        stripped = line.lstrip()
        if stripped.startswith(('#', '//', '*', '/*')):
            continue
        for loc, name in _php_or_py_or_js_input(line, ext):
            _append_record(inputs, (loc, name), {'location': loc, 'name': name,
                  'required': 'UNKNOWN', 'evidence': _evidence(file_path, line_no, 'SOURCE_ACCESS')})
        if ext == '.php':
            for pattern, mode in ((_PHP_ARRAY_ACCESS, 'array'),
                                  (_PHP_OBJECT_ACCESS, 'object')):
                for match in pattern.finditer(line):
                    if json_aliases.get(match[1]) != mode:
                        continue
                    name = match[2]
                    _append_record(inputs, ('JSON', name), {'location': 'JSON', 'name': name,
                        'required': 'UNKNOWN',
                        'evidence': _evidence(file_path, line_no, 'SOURCE_ACCESS')})
                    json_fields_found = True
            json_dynamic_access |= any(m[1] in json_aliases
                                       for m in _PHP_DYNAMIC_ACCESS.finditer(line))
        explicit_statuses = [int(m[1]) for m in statuses.finditer(line)]
        if ext == '.py' and line.lstrip().startswith('return '):
            explicit_statuses.extend(int(m[1]) for m in _PY_RETURN_STATUS.finditer(line))
        for value in explicit_statuses:
            _append_record(signals, ('DECLARED_STATUS', value), {
                'kind': 'DECLARED_STATUS', 'value': value,
                'evidence': _evidence(file_path, line_no, 'SOURCE_BRANCH')})
        if _REDIRECT.search(line):
            _append_record(signals, ('REDIRECT_PRESENT',), {
                'kind': 'REDIRECT_PRESENT', 'value': None,
                'evidence': _evidence(file_path, line_no, 'SOURCE_BRANCH')})
        for key in _response_keys(line, ext):
            _append_record(signals, ('JSON_KEY_POSSIBLE', key), {
                'kind': 'JSON_KEY_POSSIBLE', 'value': key,
                'evidence': _evidence(file_path, line_no, 'SOURCE_BRANCH')})
        if _SQL_WRITE.search(line) or (ext == '.py' and _PY_DATABASE.search(line)) or (
                ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs') and _JS_DATABASE.search(line)):
            _append_record(signals, ('DATABASE_MUTATION_POSSIBLE',), {
                'kind': 'DATABASE_MUTATION_POSSIBLE', 'value': None,
                'evidence': _evidence(file_path, line_no, 'SOURCE_BRANCH')})
            requirements.add('CONTROLLED_DATABASE_STATE_REQUIRED')
        if _SESSION.search(line):
            requirements.add('SESSION_OR_AUTH_CONTEXT_POSSIBLE')
        if _DYNAMIC_BODY.search(line) and not _php_or_py_or_js_input(line, ext):
            requirements.add('DYNAMIC_REQUEST_INPUTS_UNRESOLVED')
        if _EXTERNAL_DEP.search(line):
            requirements.add('EXTERNAL_SERVICE_CONTEXT_POSSIBLE')
    if ext == '.php' and has_raw_json_input and (not json_fields_found or json_dynamic_access):
        # Never silently report a fully understood JSON input shape when the
        # source reads a raw body but literal fields were not established.
        requirements.add('DYNAMIC_REQUEST_INPUTS_UNRESOLVED')
    return {'inputs': list(inputs.values()), 'signals': list(signals.values()),
            'requirements': sorted(requirements), 'scope': scope}


def _spec_paths(root: Path, plan: Mapping[str, Any]) -> list[str]:
    names = set(_SPEC_FILENAMES)
    for entry in plan['inventory']['source_contracts']:
        rel = entry.get('file')
        if _relative_path(rel) and rel.lower().endswith('.json') and any(
                x in Path(rel).name.lower() for x in ('openapi', 'swagger')):
            names.add(rel)
    # Only a small known set or prior indexed source-contract files.
    return sorted(names)[:MAX_SPEC_FILES]


def _normalize_openapi_path(path: str) -> str:
    return re.sub(r'\{(' + _NAME + r')\}', r':\1', path)


def _openapi_evidence(body: bytes, spec_path: str, method: str, path: str) -> dict | None:
    def unique_pairs(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError('Ambiguous duplicate OpenAPI JSON key')
            value[name] = item
        return value

    def reject_number(value):
        raise ValueError('Nonfinite JSON value')

    try:
        spec = json.loads(body.decode('utf-8'), object_pairs_hook=unique_pairs,
                          parse_constant=reject_number)
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(spec, dict) or not re.match(r'^3\.[0-9]', str(spec.get('openapi', ''))):
        return None
    paths = spec.get('paths')
    if not isinstance(paths, dict) or len(paths) > MAX_SPEC_PATHS:
        return None
    matches = [(item, op) for key, item in paths.items()
               if isinstance(key, str) and _normalize_openapi_path(key) == path
               and isinstance(item, dict)
               for op in (item.get(method.lower()),) if isinstance(op, dict)]
    if len(matches) != 1:
        return None
    item, operation = matches[0]
    evidence = _evidence(spec_path, None, 'DECLARED_OPENAPI_CONTRACT')
    inputs: dict[tuple, dict] = {}
    signals: dict[tuple, dict] = {}
    requirements: set[str] = set()
    if (spec.get('security') or operation.get('security') or spec.get('servers')
            or operation.get('servers') or item.get('servers')):
        requirements.add('OPENAPI_AUTH_OR_SERVER_CONTEXT_REVIEW')
    item_params = item.get('parameters') or []
    op_params = operation.get('parameters') or []
    if not isinstance(item_params, list) or not isinstance(op_params, list):
        requirements.add('OPENAPI_REFERENCE_OR_PARAMETER_UNRESOLVED')
        item_params, op_params = [], []
    for param in (item_params + op_params)[:MAX_ITEMS_PER_KIND * 3]:
        if not isinstance(param, dict) or '$ref' in param:
            requirements.add('OPENAPI_REFERENCE_OR_PARAMETER_UNRESOLVED')
            continue
        loc, name = param.get('in'), param.get('name')
        if loc in ('query', 'path', 'header') and isinstance(name, str) and _SAFE_NAME.fullmatch(name):
            if loc == 'header':
                requirements.add('OPENAPI_HEADER_CONTEXT_REVIEW')
                continue
            loc = loc.upper()
            _append_record(inputs, (loc, name), {'location': loc, 'name': name,
                'required': 'REQUIRED' if param.get('required') is True or loc == 'PATH' else 'OPTIONAL',
                'evidence': evidence})
    request = operation.get('requestBody')
    if isinstance(request, dict):
        content = request.get('content')
        if isinstance(content, dict):
            for mime, loc in (('application/json', 'JSON'), ('application/x-www-form-urlencoded', 'FORM')):
                media = content.get(mime)
                if not isinstance(media, dict):
                    continue
                schema = media.get('schema')
                if not isinstance(schema, dict) or '$ref' in schema:
                    requirements.add('OPENAPI_REFERENCE_OR_PARAMETER_UNRESOLVED')
                    continue
                props = schema.get('properties')
                required = schema.get('required')
                required = set(required) if isinstance(required, list) and all(isinstance(v, str) for v in required) else set()
                if isinstance(props, dict):
                    for key in sorted(props)[:MAX_ITEMS_PER_KIND]:
                        if isinstance(key, str) and _SAFE_NAME.fullmatch(key):
                            _append_record(inputs, (loc, key), {'location': loc, 'name': key,
                                'required': 'REQUIRED' if key in required else 'OPTIONAL',
                                'evidence': evidence})
    responses = operation.get('responses')
    if isinstance(responses, dict):
        for status, record in sorted(responses.items(), key=lambda pair: str(pair[0]))[:MAX_ITEMS_PER_KIND * 3]:
            if (isinstance(status, str) and status.isdecimal() and 100 <= int(status) <= 599
                    and isinstance(record, dict)):
                _append_record(signals, ('DECLARED_STATUS', int(status)), {
                    'kind': 'DECLARED_STATUS', 'value': int(status), 'evidence': evidence})
                content = record.get('content')
                media = content.get('application/json') if isinstance(content, dict) else None
                schema = media.get('schema') if isinstance(media, dict) else None
                if isinstance(schema, dict) and '$ref' not in schema:
                    props = schema.get('properties')
                    if isinstance(props, dict):
                        for key in sorted(props)[:MAX_ITEMS_PER_KIND]:
                            if isinstance(key, str) and _SAFE_NAME.fullmatch(key):
                                _append_record(signals, ('JSON_KEY_POSSIBLE', key), {
                                    'kind': 'JSON_KEY_POSSIBLE', 'value': key, 'evidence': evidence})
            elif status == 'default':
                requirements.add('OPENAPI_DEFAULT_RESPONSE_UNRESOLVED')
    return {'inputs': list(inputs.values()), 'signals': list(signals.values()),
            'requirements': sorted(requirements), 'scope': 'EXACT_OPENAPI_OPERATION'}


def analyze_source_evidence(plan: Mapping[str, Any], source_root: Path, *,
                            snapshot_verified: bool = False) -> dict:
    """Inspect only bounded, non-symlink source tied to discovered Phase 3 targets.

    'snapshot_verified' may only be asserted by a caller that *itself* pinned
    GitHub archival extraction; this flag never grants execution approval.
    """
    try:
        validate_test_plan(plan)
    except (TestPlanContractError, TypeError, ValueError) as exc:
        raise SourceEvidenceError('INVALID_PHASE3_PLAN') from exc
    root = Path(source_root)
    if not root.is_dir() or root.is_symlink():
        _fail('INVALID_SOURCE_ROOT')
    if snapshot_verified and plan['source']['kind'] != 'github':
        _fail('UNPINNED_SNAPSHOT_CANNOT_BE_VERIFIED')
    items: list[dict] = []
    cache: dict[str, bytes | None] = {}
    issues: set[str] = set()

    def read(rel: str) -> bytes | None:
        if rel not in cache:
            if len(cache) >= MAX_TOTAL_FILES:
                issues.add('GLOBAL_FILE_SCAN_LIMIT')
                return None
            cache[rel] = source_bytes(root, rel, limit=MAX_SOURCE_BYTES)
        return cache[rel]

    specs: list[tuple[str, bytes]] = []
    for spec in _spec_paths(root, plan):
        body = read(spec)
        if body is not None:
            specs.append((spec, body))

    for case in sorted(plan['test_cases'], key=lambda c: c['id']):
        target = case['target']
        method, path = target['method'], target['path']
        if case['category'] not in _HTTP_CATEGORIES or not isinstance(method, str):
            items.append({'test_id': case['id'], 'method': method, 'path': path,
                          'status': 'NON_HTTP_NOT_ANALYZED', 'source_files': [],
                          'inputs': [], 'response_signals': [], 'requirements': [],
                          'scope': [], 'review': 'NOT_AN_EXECUTABLE_ORACLE'})
            continue
        inputs: dict[tuple, dict] = {}
        signals: dict[tuple, dict] = {}
        requirements: set[str] = set()
        scopes: set[str] = set()
        files: list[dict] = []
        candidate_paths = _file_paths(case)
        if len(candidate_paths) == MAX_FILES_PER_TARGET:
            issues.add('PER_TARGET_FILE_SCAN_LIMIT_POSSIBLE')
        for rel in candidate_paths:
            body = read(rel)
            if body is None:
                continue
            # Decoding errors mean no source claims for that file.
            try:
                text = body.decode('utf-8-sig')
            except UnicodeError:
                issues.add('NON_UTF8_SOURCE_SKIPPED')
                continue
            result = _source_heuristics(text, Path(rel).suffix.lower(), method, path, rel)
            scopes.add(result['scope'])
            files.append({'path': rel, 'sha256': sha256(body).hexdigest()})
            for inp in result['inputs']:
                _append_record(inputs, (inp['location'], inp['name']), inp)
            for sig in result['signals']:
                _append_record(signals, (sig['kind'], sig['value']), sig)
            requirements.update(result['requirements'])
        for spec_path, body in specs:
            res = _openapi_evidence(body, spec_path, method, path)
            if res is None:
                continue
            scopes.add(res['scope'])
            files.append({'path': spec_path, 'sha256': sha256(body).hexdigest()})
            for inp in res['inputs']:
                # Prefer explicit OpenAPI required/optional facts to heuristic UNKNOWN.
                key = (inp['location'], inp['name'])
                inputs[key] = inp
            for sig in res['signals']:
                _append_record(signals, (sig['kind'], sig['value']), sig)
            requirements.update(res['requirements'])
        # Route placeholders are supported even if source is unavailable, and
        # are tied to discovered route declarations rather than guessed body.
        for name in re.findall(r':(' + _NAME + r')', path or ''):
            _append_record(inputs, ('PATH', name), {'location': 'PATH', 'name': name,
                           'required': 'REQUIRED',
                           'evidence': _evidence('', None, 'DISCOVERED_ROUTE')})
        source_found = bool(files)
        has_data = bool(inputs or signals or requirements)
        items.append({'test_id': case['id'], 'method': method, 'path': path,
                      'status': ('EVIDENCE_FOUND' if source_found and has_data else
                                 'SOURCE_INSPECTED_NO_CONTRACT' if source_found else 'SOURCE_UNAVAILABLE'),
                      'source_files': sorted(files, key=lambda v: v['path']),
                      'inputs': sorted(inputs.values(), key=lambda v: (v['location'], v['name'])),
                      'response_signals': sorted(signals.values(), key=lambda v: (v['kind'], str(v['value']))),
                      'requirements': sorted(requirements), 'scope': sorted(scopes),
                      'review': 'REQUIRED_BEFORE_ORACLE_PROMOTION'})
    counts = Counter(x['status'] for x in items)
    overall = ('EVIDENCE_EXTRACTED' if counts['EVIDENCE_FOUND'] else
               'SOURCE_INSPECTED_NO_CONTRACT' if counts['SOURCE_INSPECTED_NO_CONTRACT'] else
               'NO_USABLE_SOURCE_EVIDENCE')
    return {'schema_version': SCHEMA_VERSION, 'phase': 4,
            'stage': 'source_grounded_oracle_evidence', 'status': overall,
            'source': dict(plan['source']), 'phase3_plan_sha256': digest(plan),
            'snapshot_verified': snapshot_verified, 'operator_approval': 'NOT_EVALUATED',
            'execution': 'NOT_PERFORMED', 'oracle_promotion': 'NOT_PERFORMED',
            'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
            'summary': {'targets': len(items), 'evidence_found': counts['EVIDENCE_FOUND'],
                        'inspected_without_contract': counts['SOURCE_INSPECTED_NO_CONTRACT'],
                        'source_unavailable': counts['SOURCE_UNAVAILABLE'],
                        'non_http': counts['NON_HTTP_NOT_ANALYZED'],
                        'source_files_read': sum(x is not None for x in cache.values()),
                        'possible_status_oracles': sum(1 for x in items if any(
                            v['kind'] == 'DECLARED_STATUS' for v in x['response_signals']))},
            'targets': items, 'issues': sorted(issues),
            'limitations': [
                'Source branches and API documentation are advisory, not execution-derived truth.',
                'No request inputs, credentials, response values or database state are invented.',
                'Request inputs and response signals are NOT runnable Phase 4B assertions.',
                'A reviewed oracle catalog and a separately approved guest runner are still required.',
            ]}
