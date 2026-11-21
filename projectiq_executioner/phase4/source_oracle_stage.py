"""Phase 4C-B CLI. Static source inspection only, never imports project code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

from .oracle_stage import _load_strict_json
from .source_evidence import SourceEvidenceError, analyze_source_evidence

_SHA40 = re.compile(r'^[a-f0-9]{40}$')
_REPOSITORY = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def _save_exclusive(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def _with_pinned_source(plan: dict, *, repo: str, ref: str | None) -> dict:
    """A Phase 1-style GitHub checkout pinned to the exact plan identity."""
    source = plan.get('source', {})
    owner_repo, sha = source.get('repository'), source.get('commit_sha')
    if (source.get('kind') != 'github' or not isinstance(owner_repo, str)
            or not _REPOSITORY.fullmatch(owner_repo)
            or not isinstance(sha, str) or not _SHA40.fullmatch(sha)
            or (ref is not None and ref != sha)
            or repo.rstrip('/') != 'https://github.com/' + owner_repo):
        raise SourceEvidenceError('PINNED_SOURCE_IDENTITY_MISMATCH')
    from ..github_client import GitHubClient
    from ..safe_archive import extract_snapshot
    client = GitHubClient(timeout=25)
    resolved = client.resolve(repo, sha)
    if resolved.commit_sha != sha or resolved.repository != owner_repo:
        raise SourceEvidenceError('PINNED_SOURCE_IDENTITY_MISMATCH')
    with tempfile.TemporaryDirectory(prefix='projectiq_phase4cb_') as directory:
        temp = Path(directory)
        archive = temp / 'snapshot.zip'
        client.download(resolved, archive)
        checkout = extract_snapshot(archive, temp / 'source')
        return analyze_source_evidence(plan, checkout, snapshot_verified=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Phase 4C-B: read-only grounded HTTP source evidence')
    parser.add_argument('--plan', required=True, type=Path, help='Existing validated Phase 3 test_plan.json')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--source-root', type=Path, help='Local checkout; provenance NOT verified')
    mode.add_argument('--repo', help='Public GitHub https://github.com/owner/repo; exact pinned SHA from plan')
    parser.add_argument('--ref', help='Optional exact 40-character commit SHA (must match Phase 3)')
    parser.add_argument('--out', type=Path, required=True, help='Fresh evidence output directory')
    args = parser.parse_args(argv)
    if args.source_root and args.ref:
        parser.error('--ref is only for --repo')
    dest = args.out / 'source_evidence.json'
    if dest.exists():
        parser.error('Refusing to overwrite existing evidence')
    try:
        plan = _load_strict_json(args.plan, 2_000_000)
        if args.source_root:
            report = analyze_source_evidence(plan, args.source_root, snapshot_verified=False)
        else:
            report = _with_pinned_source(plan, repo=args.repo, ref=args.ref)
        _save_exclusive(dest, report)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError):
        print('Phase 4C-B rejected unavailable or inconsistent source evidence (no execution)', file=sys.stderr)
        return 2
    s = report['summary']
    print('ProjectIQ Phase 4C-B — Source-Grounded Oracle Evidence')
    print(f"  Targets:      {s['targets']}")
    print(f"  Evidence:     {s['evidence_found']}")
    print(f"  Unavailable:  {s['source_unavailable']}")
    print(f"  Status hints: {s['possible_status_oracles']}")
    print(f"  Pinned source verified: {report['snapshot_verified']}")
    print('  Execution:    NOT PERFORMED')
    print('  Oracles:      NOT APPROVED')
    print(f'  JSON:         {dest}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
