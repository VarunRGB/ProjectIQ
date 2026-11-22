"""D5 offline input staging helper: copy reviewed JSON only, never approve or launch.

The helper creates a new local operator-reviewable suite bound to exact input
JSON digests. Source approval, oracle approval and execution are separate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .cross_repository_contracts import (
    CrossRepositoryReviewError, _LIMITS, validate_manifest,
)
from .oracle_contracts import digest
from .oracle_stage import _load_strict_json


def prepare(*, db_files: dict[str, Path], api_files: dict[str, Path], out: Path) -> dict:
    if out.exists():
        raise CrossRepositoryReviewError('D5_PREPARE_OUTPUT_EXISTS')
    required_db = {'plan', 'candidate', 'auth', 'oracles', 'postconditions'}
    required_api = {'plan', 'candidate', 'oracles'}
    if set(db_files) != required_db or set(api_files) != required_api:
        raise CrossRepositoryReviewError('D5_PREPARE_INPUT_SHAPE_INVALID')
    manifest = {'schema_version': '0.1',
                'strategy': 'SEQUENTIAL_DISTINCT_DISPOSABLE_VMS', 'cases': []}
    staged = []
    for identifier, workflow, files in (
            ('registration', 'D3_REGISTRATION', db_files),
            ('json-api', 'REVIEWED_JSON_API', api_files)):
        docs = {}
        for field, path in files.items():
            path = Path(path)
            stat = path.lstat()
            if path.is_symlink() or (getattr(stat, 'st_file_attributes', 0) & 0x400):
                raise CrossRepositoryReviewError('D5_PREPARE_SYMLINK_INPUT_REJECTED')
            docs[field] = _load_strict_json(path, _LIMITS[field])
        source = docs['plan'].get('source')
        candidate = docs['candidate']
        if (not isinstance(source, dict) or candidate.get('source') != source
                or not isinstance(candidate.get('adapter'), str)):
            raise CrossRepositoryReviewError('D5_PREPARE_SOURCE_MISMATCH')
        entry = {'id': identifier, 'workflow': workflow,
                 'source': dict(source), 'adapter': candidate['adapter'],
                 'files': {label: f'{identifier}/{label}.json' for label in files},
                 'input_sha256': digest(docs)}
        manifest['cases'].append(entry)
        staged.append((identifier, docs))
    validate_manifest(manifest)
    # Every input was inspected and exact shapes verified before writing.
    out.mkdir(parents=True, exist_ok=False)
    for identifier, docs in staged:
        folder = out / 'inputs' / identifier
        folder.mkdir(parents=True, exist_ok=False)
        for key, obj in docs.items():
            (folder / f'{key}.json').write_text(
                json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True,
                           allow_nan=False) + '\n', encoding='utf-8')
    (out / 'd5_suite.json').write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True,
                   allow_nan=False) + '\n', encoding='utf-8')
    return manifest


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Stage two exact-source D5 pilot inputs (offline)')
    for label in ('db-plan', 'db-candidate', 'db-auth', 'db-oracles', 'db-postconditions',
                  'api-plan', 'api-candidate', 'api-oracles', 'out'):
        p.add_argument('--' + label, type=Path, required=True)
    a = p.parse_args(argv)
    try:
        manifest = prepare(
            db_files={'plan': a.db_plan, 'candidate': a.db_candidate,
                      'auth': a.db_auth, 'oracles': a.db_oracles,
                      'postconditions': a.db_postconditions},
            api_files={'plan': a.api_plan, 'candidate': a.api_candidate,
                       'oracles': a.api_oracles}, out=a.out)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        print('D5 offline staging rejected invalid or incompatible inputs', file=sys.stderr)
        return 2
    print('D5 INPUTS STAGED; NO VM EXECUTED AND NO APPROVAL GRANTED')
    for row in manifest['cases']:
        print('  ' + row['id'] + ': ' + row['source']['repository'] +
              '@' + row['source']['commit_sha'] + ' (' + row['adapter'] + ')')
    print('  Manifest: ' + str(a.out / 'd5_suite.json'))
    print('  Inputs:   ' + str(a.out / 'inputs'))
    print('  Review before approving: cross-repo#' + digest(manifest))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
