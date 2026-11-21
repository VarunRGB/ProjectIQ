"""Phase 3E cross-repository release benchmark: offline, deterministic, inert.

Replays saved Phase 1 manifests/candidates and validates existing Phase 3
plans. Never downloads repositories, runs students' tests, imports their code,
or starts a VM. Produces bounded, machine-readable release evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .contracts import TestPlanContractError, validate_test_plan
from .generator import (TestPlanInputError, _path, _bounded, _NON_HTTP_ROUTE_KINDS,
                        _SUPPORTED_METHODS, generate_test_plan, load_input_json,
                        MAX_CANDIDATE_BYTES, MAX_RECORDS_PER_FIELD)

BENCHMARK_SCHEMA = '0.1'
MAX_BENCHMARK_CASES = 64
_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
_DISCOVERED_KINDS = {'declared_http_route', 'file_based_route', 'file_endpoint',
                     'declared_api_contract', 'page_route', 'client_http_call', 'form_action'}


class BenchmarkInputError(ValueError):
    pass


def _name(value: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise BenchmarkInputError('Invalid benchmark case name')
    return value


def _checks_for_manifest(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Check discovery coverage, not guessed functional correctness."""
    expected_http: set[tuple[str | None, str]] = set()
    expected_other: set[tuple[str, str]] = set()
    routes = manifest.get('routes') or []
    if not isinstance(routes, list):
        routes = []
    for route in routes[:MAX_RECORDS_PER_FIELD]:
        if not isinstance(route, Mapping):
            continue
        kind = route.get('kind')
        if kind in _NON_HTTP_ROUTE_KINDS:
            operation = _bounded(route.get('path'), 180)
            if operation:
                expected_other.add((kind, operation))
            continue
        if kind not in _DISCOVERED_KINDS:
            continue
        raw = route.get('path')
        path = _path(raw, allow_relative=kind == 'file_endpoint')
        if path is None:
            continue  # deliberately unresolved/unsafe; no test target may be invented
        method = route.get('method')
        method = method.upper() if isinstance(method, str) else None
        if method not in _SUPPORTED_METHODS:
            method = None
        expected_http.add((method, path))
    actual_http = {(c['target']['method'], c['target']['path']) for c in plan['test_cases']
                   if c['category'] in ('HTTP_READINESS', 'HTTP_ROUTE', 'UI_PAGE')}
    actual_other = {(kind, c['target']['label']) for c in plan['test_cases']
                    if c['category'] == 'NON_HTTP_API'
                    for kind in c['evidence_kinds'] if kind in _NON_HTTP_ROUTE_KINDS}
    missing_http = expected_http - actual_http
    missing_other = expected_other - actual_other
    truncated = any(i['code'] in ('TEST_PLAN_TRUNCATED', 'ROUTE_INVENTORY_TRUNCATED')
                    for i in plan['issues'])
    return {
        'eligible_http_operations': len(expected_http),
        'covered_http_operations': len(expected_http - missing_http),
        'missing_http_operations': len(missing_http),
        'eligible_non_http_operations': len(expected_other),
        'covered_non_http_operations': len(expected_other - missing_other),
        'missing_non_http_operations': len(missing_other),
        'truncated': truncated,
    }


def _permute_manifest(manifest: Mapping[str, Any]) -> dict:
    """Exercise stable generation even when Phase 1 inventories are reordered."""
    other = deepcopy(manifest)
    for name in ('routes', 'pages', 'features', 'tests', 'entrypoints',
                 'database_schema_files', 'proposed_test_targets'):
        value = other.get(name)
        if isinstance(value, list):
            value.reverse()
    return other


def _source_record(plan: Mapping[str, Any]) -> dict:
    src = plan['source']
    return {'kind': src.get('kind'), 'repository': src.get('repository'),
            'commit_sha': src.get('commit_sha')}


def check_manifest(name: str, manifest: Mapping[str, Any],
                   candidate: Mapping[str, Any] | None = None) -> dict:
    """Generate twice, validate, and compare exact JSON after input permutation."""
    _name(name)
    first = generate_test_plan(manifest, candidate)
    validate_test_plan(first)
    second = generate_test_plan(_permute_manifest(manifest), candidate)
    validate_test_plan(second)
    stable = json.dumps(first, sort_keys=True, ensure_ascii=True) == json.dumps(second, sort_keys=True, ensure_ascii=True)
    coverage = _checks_for_manifest(manifest, first)
    missing = coverage['missing_http_operations'] + coverage['missing_non_http_operations']
    status = 'FAIL' if missing and not coverage['truncated'] or not stable else 'LIMITED' if coverage['truncated'] else 'PASS'
    return {
        'name': name, 'input': 'PHASE1_MANIFEST_REPLAY', 'status': status,
        'source': _source_record(first), 'plan_status': first['status'],
        'deterministic_under_reordering': stable,
        'plan_validation': 'PASS', 'route_coverage': coverage,
        'summary': first['summary'], 'issues': [x['code'] for x in first['issues']],
        'execution': 'NOT_PERFORMED',
    }


def check_plan(name: str, plan: Mapping[str, Any]) -> dict:
    """Validate a previously generated plan when its manifest is unavailable."""
    _name(name)
    validate_test_plan(plan)
    return {
        'name': name, 'input': 'EXISTING_TEST_PLAN',
        'status': 'FAIL' if plan['status'] == 'INPUT_ERROR' else 'LIMITED',
        'source': _source_record(plan), 'plan_status': plan['status'],
        'deterministic_under_reordering': None, # cannot replay without the Phase 1 manifest
        'plan_validation': 'PASS', 'route_coverage': None,
        'summary': plan['summary'],
        'issues': [x['code'] for x in plan['issues']] + ['MANIFEST_REPLAY_NOT_PERFORMED'],
        'execution': 'NOT_PERFORMED',
    }


def _failed(name: str, input_type: str, code: str) -> dict:
    return {'name': name, 'input': input_type, 'status': 'FAIL',
            'source': None, 'plan_status': 'UNAVAILABLE',
            'deterministic_under_reordering': None,
            'plan_validation': 'FAIL', 'route_coverage': None,
            'summary': None, 'issues': [code], 'execution': 'NOT_PERFORMED'}


def benchmark_specs(specs: list[tuple[str, str, Path, Path | None]]) -> dict:
    """Run a bounded offline release benchmark with isolated per-case errors."""
    if not 1 <= len(specs) <= MAX_BENCHMARK_CASES:
        raise BenchmarkInputError('Benchmark requires between 1 and 64 inputs')
    names = [_name(s[0]) for s in specs]
    if len(set(names)) != len(names):
        raise BenchmarkInputError('Duplicate benchmark input names')
    results = []
    for name, input_type, path, cand in sorted(specs, key=lambda s: s[0]):
        if input_type not in ('manifest', 'plan'):
            raise BenchmarkInputError('Unsupported benchmark input type')
        try:
            if input_type == 'manifest':
                m = load_input_json(path)
                c = load_input_json(cand, limit=MAX_CANDIDATE_BYTES) if cand else None
                result = check_manifest(name, m, c)
            else:
                result = check_plan(name, load_input_json(path))
        except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            # Only stable, fixed classification codes leave this process, not
            # pathnames, stack traces or raw student-controlled exception text.
            code = ('INVALID_PLAN_CONTRACT' if isinstance(exc, TestPlanContractError)
                    else 'INVALID_INPUT_OR_GENERATION')
            result = _failed(name, 'PHASE1_MANIFEST_REPLAY' if input_type == 'manifest'
                             else 'EXISTING_TEST_PLAN', code)
        results.append(result)

    counts = Counter(item['status'] for item in results)
    scenarios = Counter()
    for item in results:
        s = item.get('summary')
        if s:
            for k in ('total', 'ready_for_phase4', 'needs_oracle', 'unsupported_by_runner'):
                scenarios[k] += s.get(k, 0)
    return {
        'schema_version': BENCHMARK_SCHEMA, 'stage': 'phase3e_release_benchmark',
        'status': 'FAIL' if counts['FAIL'] else 'LIMITED' if counts['LIMITED'] else 'PASS',
        'execution': 'NOT_PERFORMED',
        'inputs': len(specs), 'results': results,
        'summary': {'passed': counts['PASS'], 'limited': counts['LIMITED'], 'failed': counts['FAIL'],
                    'total_scenarios': scenarios['total'], 'ready_specifications': scenarios['ready_for_phase4'],
                    'need_oracles': scenarios['needs_oracle'],
                    'unsupported': scenarios['unsupported_by_runner']},
        'limitations': [
            'Manifest replay is static and offline; source-derived hashes require a verified checkout.',
            'An existing test plan cannot establish permutation stability without its manifest.',
            'PASS indicates valid deterministic test discovery, not correctness or runnable application status.',
            'Phase 4 must independently revalidate source, operator approval, assertions and VM isolation.',
        ],
    }


def _table_escape(value: Any) -> str:
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '\\|').replace('`', '\\`').replace('\r', ' ').replace('\n', ' ')


def render_markdown(report: Mapping[str, Any]) -> str:
    """Small evidence-conscious report, without publishing local file paths."""
    s = report['summary']
    lines = ['# ProjectIQ Phase 3E — Cross-repository release benchmark', '',
             '**Overall:** ' + report['status'] + ' (offline; no student code executed)', '',
             f"**Inputs:** {report['inputs']} · **Passed:** {s['passed']} · **Limited:** {s['limited']} · **Failed:** {s['failed']}", '',
             '| Case | Contract | Scenarios | Ready specs | Missing routes | Result |',
             '|---|---|---:|---:|---:|---|']
    for item in report['results']:
        info = item['summary'] or {}
        coverage = item['route_coverage']
        missing = ('n/a' if coverage is None else str(coverage['missing_http_operations'] + coverage['missing_non_http_operations']))
        lines.append('| ' + ' | '.join(_table_escape(x) for x in (
            item['name'], item['plan_validation'], info.get('total', '—'),
            info.get('ready_for_phase4', '—'), missing, item['status'])) + ' |')
    lines.extend(['', '## Notes', '',
                  '- Replays **saved Phase 1 manifests**, checks versioned JSON, expected route coverage and stability under inventory reordering.',
                  '- Previously generated plans are validated as-is, without replay/determinism claims.',
                  '- `READY_FOR_PHASE4` means test *specification readiness*, not permission to run or proof of correctness.',
                  '- New repo snapshots / new application types may have zero runnable oracles. This is not a test failure.',
                  '- `LIMITED` means a cap was reached **or** an existing plan could only be validated, not replayed. It is not full release acceptance.', ''])
    return '\n'.join(lines)


def discover_batch(batch: Path, candidate_root: Path | None = None) -> list[tuple[str, str, Path, Path | None]]:
    """Find exact known Phase 2D output layout, never recursively scan arbitrary trees."""
    if not batch.is_dir():
        raise BenchmarkInputError('Phase 2D batch directory not found')
    out = []
    for entry in sorted(batch.iterdir()):
        if not entry.is_dir() or entry.is_symlink() or not _NAME.fullmatch(entry.name):
            continue
        manifest = entry / 'phase1' / 'manifest.json'
        if not manifest.is_file() or manifest.is_symlink():
            continue
        preferred = (candidate_root / entry.name / 'phase2c_candidate.json') if candidate_root else None
        older = entry / 'phase2c_candidate.json'
        cand = preferred if preferred and preferred.is_file() else older if older.is_file() else None
        out.append((entry.name, 'manifest', manifest, cand))
        if len(out) > MAX_BENCHMARK_CASES:
            raise BenchmarkInputError('Too many batch entries')
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Offline Phase 3E benchmark: validation and deterministic replay, no VMs')
    p.add_argument('--batch-root', type=Path, help='Directory of Phase 2D batch cases, each with phase1/manifest.json')
    p.add_argument('--candidate-root', type=Path, help='Optional newer Phase 2C candidate directory by case name')
    p.add_argument('--manifest', type=Path, action='append', default=[], help='Additional Phase 1 manifest (repeatable)')
    p.add_argument('--plan', type=Path, action='append', default=[], help='Existing Phase 3 test_plan.json (repeatable)')
    p.add_argument('--out', type=Path, required=True, help='Fresh directory for benchmark.json and benchmark.md')
    args = p.parse_args(argv)
    if args.candidate_root and not args.batch_root:
        p.error('--candidate-root requires --batch-root')
    try:
        specs = discover_batch(args.batch_root, args.candidate_root) if args.batch_root else []
        for i, path in enumerate(args.manifest, 1):
            specs.append((f'ExtraManifest{i:02d}', 'manifest', path, None))
        for i, path in enumerate(args.plan, 1):
            specs.append((f'ExistingPlan{i:02d}', 'plan', path, None))
        if (args.out / 'benchmark.json').exists() or (args.out / 'benchmark.md').exists():
            p.error('Refusing to overwrite benchmark results; use a fresh --out')
        report = benchmark_specs(specs)
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / 'benchmark.json').open('x', encoding='utf-8') as fh:
            json.dump(report, fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.write('\n')
        with (args.out / 'benchmark.md').open('x', encoding='utf-8') as fh:
            fh.write(render_markdown(report))
    except (OSError, ValueError) as exc:
        p.error(str(exc))
    print('ProjectIQ Phase 3E — Offline Release Benchmark')
    print('  Status:       ' + report['status'])
    s = report['summary']
    print(f"  Repositories: {report['inputs']}  Passed: {s['passed']}  Limited: {s['limited']}  Failed: {s['failed']}")
    print(f"  Scenarios:    {s['total_scenarios']}  Ready specs: {s['ready_specifications']}  Need oracles: {s['need_oracles']}")
    print('  JSON:         ' + str(args.out / 'benchmark.json'))
    print('  Markdown:     ' + str(args.out / 'benchmark.md'))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
