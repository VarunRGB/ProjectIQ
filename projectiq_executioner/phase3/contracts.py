"""Fail-closed Phase 3 -> Phase 4 declarative plan validation.

This is a shape/provenance-consistency gate, NOT an authorization to run any
student project. Phase 4 must independently bind a test to a reviewed SHA,
verified source and a guest-only executor before it can execute anything.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping
import re

SCHEMA_VERSION = "1.1"
_STATUSES = {"READY_FOR_PHASE4", "NEEDS_ORACLE", "UNSUPPORTED_BY_RUNNER"}
_REPORT_STATUSES = {"GENERATED", "DISCOVERY_ONLY", "NO_TEST_TARGETS", "INPUT_ERROR"}
_CATEGORIES = {"HTTP_READINESS", "HTTP_ROUTE", "UI_PAGE", "NON_HTTP_API", "PROGRAM_ENTRYPOINT"}
_HTTP_CATEGORIES = {"HTTP_READINESS", "HTTP_ROUTE", "UI_PAGE"}
_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
_ASSERTIONS = {"HTTP_STATUS_EQUALS", "HTTP_BODY_SHA256_EQUALS"}
_BASES = {"PHASE2_ADAPTER_READINESS_CONTRACT", "STATIC_ADAPTER_RESOURCE_CONTRACT", "DECLARED_OPENAPI_GET_200"}
_ADAPTERS = {"static_html_v1", "php_mysql_v1", "node_vite_offline_v1", "node_express_offline_v1",
             "node_fullstack_offline_v1", "python_web_offline_v1"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_TEST_ID = re.compile(r"TC-[A-F0-9]{16}$")
_URL_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,218}$")


class TestPlanContractError(ValueError):
    """Malformed or internally inconsistent test plan."""


def _fail(message: str) -> None:
    raise TestPlanContractError(message)


def _http_path(value: Any) -> bool:
    return (isinstance(value, str) and bool(_URL_PATH.fullmatch(value))
            and not value.startswith('//') and '//' not in value
            and all(part not in ('.', '..') for part in value.split('/')))


def _evidence_ok(value: Any) -> bool:
    if not isinstance(value, list) or len(value) > 64:
        return False
    for item in value:
        if not isinstance(item, Mapping) or item.get('source') not in ('repository', 'phase2_candidate'):
            return False
        path, line, reason = item.get('path'), item.get('line'), item.get('reason')
        if not isinstance(path, str) or not path or len(path) > 220 or '\x00' in path:
            return False
        if item['source'] == 'repository' and (path.startswith(('/', '\\')) or '..' in path.split('/')):
            return False
        if line is not None and (type(line) is not int or not 1 <= line <= 10_000_000):
            return False
        if not isinstance(reason, str) or len(reason) > 180:
            return False
    return True


def validate_test_plan(plan: Mapping[str, Any]) -> None:
    """Validate schema 1.1 with security-relevant *consistency* checks.

    The validator accepts an unresolved candidate with no oracle, but refuses
    to describe it as runnable. It never contacts a repository or executes code.
    """
    if (not isinstance(plan, Mapping) or plan.get('schema_version') != SCHEMA_VERSION
            or type(plan.get('phase')) is not int or plan['phase'] != 3
            or plan.get('stage') != 'deterministic_test_generation'
            or plan.get('status') not in _REPORT_STATUSES):
        _fail('Unsupported Phase 3 plan contract')
    source = plan.get('source')
    generator = plan.get('generator')
    context = plan.get('execution_context')
    cases = plan.get('test_cases')
    inventory = plan.get('inventory')
    summary = plan.get('summary')
    issues = plan.get('issues')
    if any(not isinstance(x, Mapping) for x in (source, generator, context, inventory, summary)):
        _fail('Malformed Phase 3 report metadata')
    if (not isinstance(cases, list) or len(cases) > 400
            or not isinstance(issues, list) or len(issues) > 100
            or generator.get('llm_used') is not False
            or generator.get('student_code_executed') is not False
            or context.get('operator_approval') != 'NOT_EVALUATED'
            or type(context.get('source_snapshot_verified')) is not bool):
        _fail('Phase 3 report claims unsupported generation or execution state')
    kind, repo, sha = source.get('kind'), source.get('repository'), source.get('commit_sha')
    if kind == 'github':
        if (not isinstance(repo, str) or not repo.strip() or len(repo) > 240
                or not isinstance(sha, str) or not _SHA40.fullmatch(sha)):
            _fail('Invalid pinned GitHub source identity')
    elif kind == 'local':
        if not isinstance(repo, str) or not repo.strip() or sha is not None:
            _fail('Invalid local source identity')
    elif kind != 'unknown' or plan.get('status') != 'INPUT_ERROR' or sha is not None:
        _fail('Unknown source is permitted only for input-error reports')
    adapter = context.get('adapter')
    if adapter is not None and adapter not in _ADAPTERS:
        _fail('Unknown selected adapter')
    if context['source_snapshot_verified'] and kind != 'github':
        _fail('Unpinned local source cannot be marked snapshot-verified')
    if plan['status'] == 'INPUT_ERROR' and (cases or any(inventory.values()) or not issues):
        _fail('Input-error reports cannot claim discovered or runnable tests')

    for name in ('page_sources', 'existing_tests', 'program_entrypoints', 'database_schemas',
                 'features', 'proposals', 'source_contracts'):
        if not isinstance(inventory.get(name), list) or len(inventory[name]) > 3000:
            _fail('Malformed or unbounded evidence inventory')
    for item in inventory['source_contracts']:
        if (not isinstance(item, Mapping) or not isinstance(item.get('path'), str)
                or not isinstance(item.get('file'), str)
                or type(item.get('assertable')) is not bool):
            _fail('Invalid source contract inventory record')

    for item in issues:
        if (not isinstance(item, Mapping) or not isinstance(item.get('code'), str)
                or not isinstance(item.get('stage'), str)
                or len(item['code']) > 100 or len(item['stage']) > 100):
            _fail('Invalid issue record')

    known_pages = {item.get('path') for item in inventory['page_sources']
                   if isinstance(item, Mapping)}
    declared_contracts = {(item['path'], item['file']) for item in inventory['source_contracts']
                          if item['assertable']}
    ids = set()
    http_targets = set()
    for case in cases:
        if not isinstance(case, Mapping) or case.get('status') not in _STATUSES:
            _fail('Malformed test case status')
        identity = case.get('id')
        if not isinstance(identity, str) or not _TEST_ID.fullmatch(identity) or identity in ids:
            _fail('Missing or duplicate test ID')
        ids.add(identity)
        category = case.get('category')
        target = case.get('target')
        assertions = case.get('assertions')
        oracle = case.get('oracle')
        evidence = case.get('evidence')
        blockers = case.get('blockers')
        required_adapter = case.get('required_adapter')
        if (category not in _CATEGORIES or not isinstance(target, Mapping)
                or not isinstance(assertions, list) or len(assertions) > 8
                or not isinstance(oracle, Mapping) or not isinstance(blockers, list)
                or len(blockers) > 32 or any(not isinstance(b, str) or len(b) > 100 for b in blockers)
                or not _evidence_ok(evidence)
                or (required_adapter is not None and required_adapter not in _ADAPTERS)):
            _fail('Malformed test category, evidence or target')
        method, path, label = target.get('method'), target.get('path'), target.get('label')
        if category in _HTTP_CATEGORIES:
            if (not _http_path(path) or (method is not None and method not in _METHODS)
                    or label is not None):
                _fail('Unsafe or malformed HTTP test target')
            # A separate unresolved external-service observation is not a second
            # runnable local HTTP target. Keep strict local duplicate rejection.
            external = 'EXTERNAL_SERVICE_ORIGIN_NOT_VERIFIED' in blockers
            if external and (category != 'HTTP_ROUTE' or case['status'] != 'NEEDS_ORACLE'
                    or assertions or 'SERVER_ROUTE_NOT_VERIFIED' not in blockers
                    or oracle.get('status') != 'NOT_ESTABLISHED'):
                _fail('External service target must remain advisory')
            marker = ('external_advisory' if external else 'local', method, path)
            if marker in http_targets:
                _fail('Duplicate HTTP target across scenarios')
            http_targets.add(marker)
        elif method is not None or path is not None or not isinstance(label, str) or not label:
            _fail('Non-HTTP scenarios cannot target HTTP operations')
        if case.get('execution') != 'NOT_PERFORMED':
            _fail('Phase 3 must not claim student code execution')
        ready = case['status'] == 'READY_FOR_PHASE4'
        if ready:
            if (not assertions or blockers or oracle.get('status') != 'GROUNDED'
                    or required_adapter != adapter or adapter is None
                    or category not in _HTTP_CATEGORIES or method != 'GET'
                    or any(s in path for s in (':', '%', '*')) or not evidence):
                _fail('Unjustified ready test')
        elif not blockers:
            _fail('Non-ready test is missing its blocking reason')
        if assertions:
            if (oracle.get('status') != 'GROUNDED' or oracle.get('basis') not in _BASES
                    or category not in _HTTP_CATEGORIES or method != 'GET'):
                _fail('Unjustified test assertions')
            if case['status'] == 'NEEDS_ORACLE':
                _fail('Oracle is present but case claims to need one')
        elif oracle.get('status') != 'NOT_ESTABLISHED' or oracle.get('basis') is not None:
            _fail('Oracle status differs from available assertions')
        found = set()
        for assertion in assertions:
            if not isinstance(assertion, Mapping) or assertion.get('kind') not in _ASSERTIONS:
                _fail('Unsupported Phase 4 assertion')
            kind, expected = assertion['kind'], assertion.get('expected')
            if kind in found:
                _fail('Repeated assertion kind in one test')
            found.add(kind)
            if kind == 'HTTP_STATUS_EQUALS' and (type(expected) is not int or expected != 200):
                # Generator 0.2 only establishes exact 200 contracts. A future
                # widened set of status oracles must bump/review this contract.
                _fail('Unsupported expected HTTP status')
            if kind == 'HTTP_BODY_SHA256_EQUALS':
                if (not isinstance(expected, str) or not _SHA256.fullmatch(expected)
                        or not context['source_snapshot_verified']
                        or adapter != 'static_html_v1' or not path.lower().endswith(('.html', '.htm'))
                        or path[1:] not in known_pages):
                    _fail('Unjustified source-body hash assertion')
        if assertions and oracle['basis'] == 'DECLARED_OPENAPI_GET_200':
            # An unverified checkout can carry an advisory OpenAPI oracle, but
            # it may not mark the scenario READY_FOR_PHASE4.
            if ready and not context['source_snapshot_verified']:
                _fail('Unverified OpenAPI source cannot produce runnable tests')
            if not any((x.get('source') == 'repository' and (path, x.get('path')) in declared_contracts)
                       for x in evidence):
                _fail('OpenAPI oracle requires matching declared source contract')
            if not context['source_snapshot_verified'] and 'SOURCE_SNAPSHOT_NOT_VERIFIED' not in blockers:
                _fail('Unverified source oracle is missing its blocker')
        if assertions and oracle['basis'] == 'PHASE2_ADAPTER_READINESS_CONTRACT':
            if (category != 'HTTP_READINESS'
                    or context.get('phase2_candidate_status') != 'CANDIDATE_NEEDS_VM_VERIFICATION'
                    or not any(x['source'] == 'phase2_candidate' and x['path'] == 'probe_path'
                               for x in evidence)):
                _fail('Adapter readiness requires a valid Phase 2 candidate')
        if assertions and oracle['basis'] == 'STATIC_ADAPTER_RESOURCE_CONTRACT':
            if (adapter != 'static_html_v1' or category != 'UI_PAGE'
                    or not path.lower().endswith(('.html', '.htm')) or path[1:] not in known_pages):
                _fail('Static resource oracle requires inventoried static page')

    counts = Counter(c['status'] for c in cases)
    categories = dict(sorted(Counter(c['category'] for c in cases).items()))
    if (summary.get('total') != len(cases)
            or summary.get('ready_for_phase4') != counts['READY_FOR_PHASE4']
            or summary.get('needs_oracle') != counts['NEEDS_ORACLE']
            or summary.get('unsupported_by_runner') != counts['UNSUPPORTED_BY_RUNNER']
            or summary.get('categories') != categories):
        _fail('Phase 3 summary counts mismatch')
    expected = {k: len(v) for k, v in sorted(inventory.items())}
    if summary.get('inventory_counts') != expected:
        _fail('Phase 3 inventory counts mismatch')
    if cases and plan['status'] != 'GENERATED':
        _fail('Test cases must use GENERATED report status')
    if not cases and plan['status'] == 'GENERATED':
        _fail('Empty plan cannot claim test generation')
    if not cases and plan['status'] == 'NO_TEST_TARGETS' and any(inventory.values()):
        _fail('Evidence-only plan cannot claim no targets')
    if not cases and plan['status'] == 'DISCOVERY_ONLY' and not any(inventory.values()):
        _fail('Discovery-only plan must contain an evidence inventory')
