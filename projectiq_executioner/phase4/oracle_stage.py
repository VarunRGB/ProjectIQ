"""Phase 4C-A offline CLI: explicit functional oracle design and review staging.

Never launches QEMU, makes HTTP requests, provisions secrets, or modifies the
Phase 3 or Phase 4B test bundles. A staged oracle requires further review and a
separately implemented guest-only executor before any functional request runs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .oracle_contracts import (OracleContractError, digest, make_oracle_template,
                               validate_oracle_catalog)

_STATUS_APPROVED = 'CANDIDATE_NEEDS_VM_VERIFICATION'
_ADAPTERS = frozenset(('static_html_v1', 'php_mysql_v1', 'node_vite_offline_v1',
                       'node_express_offline_v1', 'node_fullstack_offline_v1',
                       'python_web_offline_v1'))


def _load_strict_json(path: Path, limit: int) -> dict:
    """Bound file size and reject ambiguous duplicate keys / non-JSON numbers."""
    if not path.is_file() or path.stat().st_size > limit:
        raise OracleContractError('INPUT_NOT_FOUND_OR_TOO_LARGE')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise OracleContractError('DUPLICATE_JSON_KEY')
            obj[key] = value
        return obj
    def reject_constant(value):
        raise OracleContractError('NONFINITE_JSON_NUMBER')
    try:
        document = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                              parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OracleContractError('INVALID_JSON') from exc
    if not isinstance(document, dict):
        raise OracleContractError('EXPECTED_JSON_OBJECT')
    return document


def stage_oracles(plan: Mapping[str, Any], catalog: Mapping[str, Any], *,
                  candidate: Mapping[str, Any] | None = None,
                  vm_config: Mapping[str, Any] | None = None) -> dict:
    """Compile advisory requests; never construct a runnable 4B guest bundle."""
    scenarios = validate_oracle_catalog(catalog, plan)
    source = dict(plan['source'])
    adapter = None
    candidate_hash = None
    issues: list[str] = []
    status = 'REVIEW_REQUIRED' if scenarios else 'NO_SCENARIOS'

    if candidate is not None:
        if not isinstance(candidate, Mapping) or candidate.get('source') != source:
            raise OracleContractError('PHASE2_SOURCE_MISMATCH')
        if (candidate.get('schema_version') != '0.3'
                or candidate.get('stage') != 'vm_execution_candidate'
                or candidate.get('worker_provider') != 'qemu_qcow2_v1'
                or candidate.get('launch_authorization') != 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES'
                or candidate.get('status') != _STATUS_APPROVED
                or candidate.get('adapter') not in _ADAPTERS):
            raise OracleContractError('INVALID_PHASE2_CANDIDATE')
        adapter = candidate['adapter']
        if plan['execution_context'].get('adapter') not in (None, adapter):
            raise OracleContractError('PHASE3_ADAPTER_MISMATCH')
        candidate_hash = digest(candidate)
        if source.get('kind') != 'github':
            raise OracleContractError('PINNED_GITHUB_SOURCE_REQUIRED')

    if candidate is not None and vm_config is not None:
        if not isinstance(vm_config, Mapping) or not isinstance(vm_config.get('approved_snapshots'), list):
            raise OracleContractError('INVALID_VM_APPROVAL_CONFIG')
        identity = source['repository'] + '@' + source['commit_sha']
        if identity not in vm_config['approved_snapshots']:
            issues.append('PINNED_SHA_NOT_OPERATOR_APPROVED')
            status = 'BLOCKED' if scenarios else 'NO_SCENARIOS'
        elif scenarios:
            status = 'STAGED_FOR_REVIEW'
    elif scenarios:
        issues.append('PHASE2_ADAPTER_AND_APPROVAL_NOT_VERIFIED')

    return {
        'schema_version': '0.1', 'phase': 4,
        'stage': 'functional_oracle_contract_staging', 'status': status,
        'source': source, 'adapter': adapter,
        'phase3_plan_sha256': digest(plan),
        'phase2_candidate_sha256': candidate_hash,
        'oracle_catalog_sha256': digest(catalog),
        'execution': 'NOT_PERFORMED',
        'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
        'oracle_review': 'NOT_PERFORMED',
        'launch_authorization': 'NOT_GRANTED',
        'runner_support': 'PHASE4C_EXECUTOR_NOT_IMPLEMENTED',
        'scenarios': scenarios,
        'summary': {'total': len(scenarios),
                    'requires_runtime_fixtures': sum(bool(x['fixture_refs']) for x in scenarios),
                    'needs_oracle_review': len(scenarios)},
        'issues': [{'code': x, 'stage': 'oracle_staging'} for x in issues],
        'limitations': [
            'Operator-authored expected results are proposals, not automatically trusted ground truth.',
            'Nothing in this report is executable by the Phase 4B guest interpreter.',
            'Fixture references are symbolic; no credentials or database state have been provisioned.',
            'VM execution requires a separate reviewed, guest-only 4C runner and all Phase 2 gates.',
        ],
    }


def _save_exclusive(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Phase 4C-A: offline functional oracle review staging (no VM)')
    p.add_argument('--plan', required=True, type=Path, help='Versioned Phase 3 test_plan.json')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--template', action='store_true', help='Produce empty catalog and eligible target list')
    mode.add_argument('--oracles', type=Path, help='Operator-authored oracle catalog JSON')
    p.add_argument('--candidate', type=Path, help='Optional matching Phase 2 VM candidate')
    p.add_argument('--vm-config', type=Path, help='Optional operator VM config; only checked when candidate supplied')
    p.add_argument('--out', required=True, type=Path, help='New evidence directory; never overwritten')
    args = p.parse_args(argv)
    if args.vm_config and not args.candidate:
        p.error('--vm-config requires --candidate')
    if args.template and (args.candidate or args.vm_config):
        p.error('--template only requires the Phase 3 plan')
    files = (['oracle_template.json', 'eligible_targets.json'] if args.template
             else ['oracle_review.json'])
    if any((args.out / file).exists() for file in files):
        p.error('Refusing to overwrite existing oracle outputs')

    try:
        plan = _load_strict_json(args.plan, 2_000_000)
        if args.template:
            template, eligible = make_oracle_template(plan)
            _save_exclusive(args.out / 'oracle_template.json', template)
            _save_exclusive(args.out / 'eligible_targets.json', eligible)
            print('Phase 4C-A — Offline Oracle Template')
            print(f"  Eligible discovered targets: {len(eligible['eligible_targets'])}")
            print('  Execution: NOT PERFORMED')
            return 0
        catalog = _load_strict_json(args.oracles, 256_000)
        candidate = _load_strict_json(args.candidate, 200_000) if args.candidate else None
        # Never import or launch the Phase 2 worker. Only read the JSON config.
        config = _load_strict_json(args.vm_config, 100_000) if args.vm_config else None
        result = stage_oracles(plan, catalog, candidate=candidate, vm_config=config)
        _save_exclusive(args.out / 'oracle_review.json', result)
        print('Phase 4C-A — Offline Functional Oracle Review')
        print('  Status:     ' + result['status'])
        print('  Scenarios:  ' + str(result['summary']['total']))
        print('  Execution:  NOT PERFORMED')
        print('  JSON:       ' + str(args.out / 'oracle_review.json'))
        return 0 if result['status'] in ('STAGED_FOR_REVIEW', 'REVIEW_REQUIRED', 'NO_SCENARIOS') else 2
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        # Input failures should not expose repository contents, credentials or host paths.
        print('Phase 4C-A rejected invalid oracle planning input (nothing executed)', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
