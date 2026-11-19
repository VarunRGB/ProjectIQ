"""Phase 2D one-command orchestration, reusing every existing Phase 2C gate.

Planning: a pinned public repository or an existing Phase 1 manifest.
Execution: ONLY a full-SHA, operator-approved repository, using the unchanged
Phase 2C VM runner. No student dependencies execute on Windows.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ...analyzer import analyze_directory
from ...github_client import GitHubClient
from ...report import write_reports
from ...safe_archive import extract_snapshot
from ..planner import build_execution_plan
from ..preflight import create_preflight
from ..vm_worker.contracts import VMPlanError, build_vm_plan, load_json
from ..vm_worker.orchestrator import execute_vm_candidate, load_config
from .capabilities import build_capability_plan

_FULL_SHA = re.compile(r'^[0-9a-fA-F]{40}$')


def _write_json(destination: Path, data: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def plan_from_manifest(manifest: Mapping[str, Any], output_dir: Path) -> tuple[dict, dict]:
    """Produce all 2A / 2B / 2C / 2D reports, without launching a VM."""
    p2a = build_execution_plan(manifest)
    p2b = create_preflight(manifest, p2a)
    p2c = build_vm_plan(manifest, p2a, p2b)
    p2d = build_capability_plan(manifest, p2a, p2b, p2c)
    root = Path(output_dir)
    _write_json(root / 'phase2a_plan.json', p2a)
    _write_json(root / 'phase2b_preflight.json', p2b)
    _write_json(root / 'phase2c_candidate.json', p2c)
    _write_json(root / 'phase2d_capabilities.json', p2d)
    return p2c, p2d


def run_pipeline(*, output_dir: Path, repository_url: str | None = None,
                 ref: str | None = None, manifest_path: Path | None = None,
                 vm_config: Path | None = None, run_reviewed_fixture: bool = False,
                 work_root: Path | None = None) -> dict:
    """Orchestrate source analysis and 2A->2D, optionally approved Phase 2C.

    `--manifest` is intentionally plan-only: execution requires a fresh pinned
    GitHub source resolution plus the operator's explicit allowlist approval.
    """
    if bool(repository_url) == bool(manifest_path):
        raise VMPlanError('Specify exactly one: --repo or --manifest')
    if run_reviewed_fixture and (not repository_url or vm_config is None):
        raise VMPlanError('Execution requires --repo and --vm-config')
    if run_reviewed_fixture and (not isinstance(ref, str) or not _FULL_SHA.fullmatch(ref)):
        raise VMPlanError('Execution requires --ref set to a full 40-character pinned GitHub SHA')
    if manifest_path and ref:
        raise VMPlanError('--ref cannot be combined with --manifest')
    root = Path(output_dir)
    if root.exists() and not root.is_dir():
        raise VMPlanError('Output path exists and is not a directory')
    root.mkdir(parents=True, exist_ok=True)

    if manifest_path:
        manifest = load_json(manifest_path)
        p2c, p2d = plan_from_manifest(manifest, root)
    else:
        client = GitHubClient(timeout=25)
        source = client.resolve(repository_url, ref)
        if run_reviewed_fixture and source.commit_sha.lower() != ref.lower():
            raise VMPlanError('Resolved source commit differs from explicitly pinned SHA')
        # Check explicit source approval *before* downloading the student repo.
        config = None
        if run_reviewed_fixture:
            config = load_config(vm_config)
            identity = source.repository + '@' + source.commit_sha.lower()
            if identity not in config['approved_snapshots']:
                raise VMPlanError('Pinned repository is not approved in local VM config')
        with tempfile.TemporaryDirectory(prefix='projectiq_phase2d_source_') as temp:
            work = Path(temp)
            zip_path = work / 'snapshot.zip'
            client.download(source, zip_path)
            checkout = extract_snapshot(zip_path, work / 'source')
            analyzed = analyze_directory(checkout, source)
            manifest = analyzed.to_dict()
            source_record = manifest.get('source', {})
            if (source_record.get('repository') != source.repository or
                    source_record.get('commit_sha') != source.commit_sha):
                raise VMPlanError('Phase 1 source provenance differs from verified download')
            p2c, p2d = plan_from_manifest(manifest, root)
            write_reports(analyzed, root / 'phase1')

    result: dict[str, Any] = {
        'schema_version': '0.1', 'stage': 'phase2d_integrated_pipeline',
        'source': p2c['source'], 'phase2c_adapter': p2c['adapter'],
        'phase2d_status': p2d['status'],
        'status': 'PLANNED_NO_EXECUTION',
        'execution': 'NOT_PERFORMED',
        'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
        'artifacts': {
            'phase2a': 'phase2a_plan.json', 'phase2b': 'phase2b_preflight.json',
            'phase2c': 'phase2c_candidate.json', 'phase2d': 'phase2d_capabilities.json',
        },
    }
    if run_reviewed_fixture:
        # The Phase 2C worker re-downloads and re-inspects the immutable SHA;
        # this intentionally preserves its independent source validation.
        if p2c['status'] != 'CANDIDATE_NEEDS_VM_VERIFICATION':
            result['status'] = 'NEEDS_ADAPTER'
        else:
            # `config` was verified before downloading, and the existing VM
            # worker separately enforces SHA, image pinning and cleanup.
            run_result = execute_vm_candidate(
                p2c, config, root / 'vm_execution.json',
                reviewed_fixture=True, work_root=work_root,
            )
            result['status'] = run_result['status']
            result['execution'] = run_result['execution']
            result['cleanup'] = run_result['cleanup']
            result['artifacts']['vm_run'] = 'vm_execution.json'
    _write_json(root / 'pipeline_summary.json', result)
    return result
