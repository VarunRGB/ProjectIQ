"""Reuse trusted Phase 1–6 implementations; do not duplicate their logic."""
from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .contracts import validate_source, reject


@contextmanager
def pinned_checkout(repository_url: str, ref: str | None) -> Iterator[tuple[dict,Path]]:
    """Exactly one bounded GitHub acquisition for static/planning stages.

    Phase 2's approved VM worker independently re-downloads the exact commit
    as a separate trust boundary. This is intentional, not redundant caching.
    """
    from projectiq_executioner.github_client import GitHubClient, DownloadLimits
    from projectiq_executioner.safe_archive import extract_snapshot, ArchiveLimits

    client = GitHubClient(timeout=25, limits=DownloadLimits(30*1024*1024))
    source = client.resolve(repository_url, ref)
    identity = validate_source({'kind':'github','repository':source.repository,
                                'commit_sha':source.commit_sha})
    if ref and len(ref) == 40 and ref.lower() != identity['commit_sha']:
        reject('RESOLVED_COMMIT_MISMATCH')
    with tempfile.TemporaryDirectory(prefix='projectiq_executioner_snapshot_') as tmp:
        root = Path(tmp)
        client.download(source,root/'source.zip')
        checkout = extract_snapshot(root/'source.zip',root/'checkout',
                    ArchiveLimits(max_uncompressed_bytes=120*1024*1024))
        yield identity,checkout


def inspect_source(checkout: Path, source: dict, dest: Path) -> dict:
    from projectiq_executioner.analyzer import analyze_directory
    from projectiq_executioner.report import write_reports
    from projectiq_executioner.models import RepositorySource
    # RepositorySource constructed from the pinned resolver's public identity;
    # field names are consumed by the existing Phase 1 analyzer.
    record = RepositorySource(kind='github',repository=source['repository'],
                              commit_sha=source['commit_sha'],
                              url='https://github.com/'+source['repository'],
                              requested_ref=source['commit_sha'],
                              resolved_ref=source['commit_sha'])
    manifest = analyze_directory(checkout,record)
    data = manifest.to_dict()
    if validate_source(data.get('source')) != source:
        reject('PHASE1_SOURCE_MISMATCH')
    write_reports(manifest,dest)
    return data


def compile_phase2(manifest: dict, dest: Path) -> tuple[dict,dict]:
    from projectiq_executioner.phase2.phase2d.pipeline import plan_from_manifest
    return plan_from_manifest(manifest,dest)


def compile_phase3(manifest: dict, candidate: dict|None, checkout: Path, dest: Path) -> dict:
    from projectiq_executioner.phase3.generator import generate_test_plan, write_test_plan
    from projectiq_executioner.phase3.contracts import validate_test_plan
    plan = generate_test_plan(manifest,candidate,source_root=checkout,source_verified=True)
    validate_test_plan(plan)
    write_test_plan(plan,dest/'test_plan.json')
    return plan


def analyze_quality(checkout: Path, source: dict, dest: Path) -> dict:
    from projectiq_executioner.phase5.pipeline import analyze_project
    from projectiq_executioner.phase5.contracts import validate_report
    from projectiq_executioner.phase5.report import write_reports
    report = analyze_project(checkout,repository=source['repository'])
    report['source']={'kind':'github_pinned','repository':source['repository'],
                      'commit_sha':source['commit_sha']}
    validate_report(report)
    write_reports(report,dest)
    return report


def diagnose(*, plan: dict|None, runs: list[tuple[dict,str,dict|None]],
             quality: dict|None, receipts: list[dict], dest: Path) -> dict:
    from projectiq_executioner.phase6.pipeline import build
    from projectiq_executioner.phase6.report import markdown,validate_report
    from projectiq_executioner.phase6.contracts import write_json_atomic
    if plan is None and quality is None and not runs:
        reject('NO_PHASE6_EVIDENCE')
    report=build(plans=[(plan,'PLAN')] if plan else [],run_inputs=runs,
                 quality_input=(quality,'QUALITY') if quality else None,receipts=receipts)
    validate_report(report)
    dest.mkdir(parents=True,exist_ok=True)
    write_json_atomic(report,dest/'phase6_diagnosis.json')
    with (dest/'phase6_diagnosis.md').open('x',encoding='utf-8',newline='\n') as f:
        f.write(markdown(report))
    return report


def operator_vm_config(path: Path) -> dict:
    from projectiq_executioner.phase2.vm_worker.orchestrator import load_config
    return load_config(path)
