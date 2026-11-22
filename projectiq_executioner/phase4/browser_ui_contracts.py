"""4D-C: independently reviewed real-browser assertions, pinned to source and image.

No browser launch happens here. An operator approves exact source/case digest and
pinned base image; the Phase 2 VM boundary recompiles this review before run.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..phase3.contracts import validate_test_plan
from .guest_browser_ui import BrowserUiError, validate_guest_browser_bundle
from .oracle_contracts import digest

_BROWSER = frozenset(('/usr/bin/google-chrome-stable', '/usr/bin/chromium',
                      '/opt/google/chrome/chrome'))


class BrowserReviewError(ValueError):
    """Missing or inconsistent operator approval."""


def _deny(code: str) -> None:
    raise BrowserReviewError(code)


def compile_reviewed_browser(plan: Mapping, catalog: Mapping,
                             candidate: Mapping, config: Mapping) -> dict:
    try:
        validate_test_plan(plan)
    except (ValueError, KeyError, TypeError) as exc:
        raise BrowserReviewError('BROWSER_PHASE3_PLAN_INVALID') from exc
    source = plan.get('source')
    if (not isinstance(candidate, Mapping) or candidate.get('source') != source
            or candidate.get('stage') != 'vm_execution_candidate'
            or candidate.get('schema_version') != '0.3'
            or candidate.get('status') != 'CANDIDATE_NEEDS_VM_VERIFICATION'
            or candidate.get('worker_provider') != 'qemu_qcow2_v1'
            or candidate.get('adapter') != 'node_fullstack_offline_v1'
            or plan.get('execution_context', {}).get('adapter') != 'node_fullstack_offline_v1'
            or candidate.get('service_roots') != {'frontend': 'frontend', 'backend': 'backend'}):
        _deny('BROWSER_REVIEWED_FULLSTACK_CANDIDATE_REQUIRED')
    if (not isinstance(catalog, Mapping) or set(catalog) != {
            'schema_version', 'source', 'page_path', 'checks'}
            or catalog['schema_version'] != '0.1' or catalog['source'] != source):
        _deny('INVALID_BROWSER_REVIEW_CATALOG')
    if not isinstance(config, Mapping):
        _deny('INVALID_BROWSER_POLICY')
    identity = source['repository'] + '@' + source['commit_sha']
    if identity not in config.get('approved_snapshots', []):
        _deny('BROWSER_SOURCE_NOT_OPERATOR_APPROVED')
    catalog_hash = digest(catalog)
    approvals = config.get('approved_browser_ui')
    if (not isinstance(approvals, list) or len(approvals) > 256
            or any(type(x) is not str or len(x) > 260 for x in approvals)
            or identity + '#' + catalog_hash not in approvals):
        _deny('BROWSER_CATALOG_NOT_OPERATOR_APPROVED')
    runtime = config.get('browser_runtime')
    if (not isinstance(runtime, dict) or set(runtime) != {'binary', 'base_sha256'}
            or runtime.get('binary') not in _BROWSER
            or runtime.get('base_sha256') != config.get('base_sha256')
            or not isinstance(runtime.get('base_sha256'), str)
            or len(runtime['base_sha256']) != 64):
        _deny('BROWSER_RUNTIME_NOT_PINNED_TO_BASE_IMAGE')
    guest = {'schema_version': '0.1', 'source': dict(source),
             'adapter': candidate['adapter'], 'catalog_sha256': catalog_hash,
             'page_path': catalog['page_path'], 'checks': catalog['checks'],
             'browser_binary': runtime['binary']}
    try:
        validate_guest_browser_bundle(guest, expected_source=source,
                                      expected_adapter=candidate['adapter'])
    except (BrowserUiError, ValueError, TypeError, KeyError) as exc:
        raise BrowserReviewError('BROWSER_GUEST_INSTRUCTIONS_INVALID') from exc
    return {'browser': guest, '_review': {'plan': dict(plan), 'catalog': dict(catalog)}}


def validate_reviewed_browser(bundle: Any, candidate: Mapping, config: Mapping) -> dict:
    if (not isinstance(bundle, dict) or set(bundle) != {'browser', '_review'}
            or not isinstance(bundle['_review'], dict)
            or set(bundle['_review']) != {'plan', 'catalog'}):
        _deny('BROWSER_HOST_REVIEW_PROOF_REQUIRED')
    proof = bundle['_review']
    rebuilt = compile_reviewed_browser(proof['plan'], proof['catalog'], candidate, config)
    if rebuilt != bundle:
        _deny('BROWSER_BUNDLE_CHANGED_AFTER_REVIEW')
    return bundle['browser']
