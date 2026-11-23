"""Phase 7 benchmark contracts: independent labels, bounded offline evidence.

A ground-truth file is a reviewer assertion, not a cryptographic credential.
Do not benchmark by copying Phase 6 predictions into expectations.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path, PurePosixPath
import hashlib
import json
import re

from ..phase6.catalog import CATALOG
from ..phase6.contracts import read_json, source_key

VERSION = '0.1'
MAX_CASES = 128
MAX_EVENTS_PER_CASE = 400
MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 96_000_000
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
EVENT_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,89}$')
CODE = re.compile(r'^[A-Z][A-Z0-9_]{1,89}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
COHORTS = {'CLEAN', 'APPLICATION_DEFECT', 'INFRASTRUCTURE', 'INVALID_ORACLE',
           'SECURITY', 'INCONCLUSIVE', 'UNSUPPORTED', 'STATIC_ONLY'}
REVIEW_BASES = {'CONTROLLED_INJECTION', 'INDEPENDENT_REVIEW', 'DOCUMENTED_INFRASTRUCTURE',
                'REVIEWED_TEST_ORACLE', 'DOCUMENTED_UNSUPPORTED'}
OUTCOMES = {'PASS','FAIL','INCONCLUSIVE','BLOCKED','UNTESTED','INVALID_TEST'}
COMPARISONS = {'CONSISTENT','OUTCOME_INCONSISTENT_REVIEW_REQUIRED'}
REPORT_STATES = {'DIAGNOSED','PARTIAL','SECURITY_HOLD'}
CASE_KEYS = {'id','cohort','source','phase6','ground_truth'}
TRUTH_KEYS = {'reviewed','reviewer_id','reviewed_at','basis','expected_report_status',
              'expected_codes','expected_events','expected_comparisons','maintainability_rating'}
DEFAULT_POLICY = {'min_cases': 12, 'min_repositories': 2,
                  'min_cohorts': 3, 'min_application_defects': 3,
                  'max_false_passes': 0, 'max_false_attributions': 0,
                  'min_event_labels': 8, 'min_known_causes': 2, 'min_calibration_pairs': 5}


class BenchmarkError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(test, code):
    if not test:
        raise BenchmarkError(code)


def name(v):
    require(type(v) is str and ID.fullmatch(v) is not None, 'INVALID_CASE_ID')
    return v


def relpath(v):
    require(type(v) is str and 0 < len(v) <= 240 and '\\' not in v and ':' not in v and '\x00' not in v,
            'UNSAFE_DATASET_PATH')
    p = PurePosixPath(v)
    require(not p.is_absolute() and str(p) == v and all(x not in ('','.','..') for x in v.split('/')),
            'UNSAFE_DATASET_PATH')
    return v


def safe_path(root: Path, raw: str) -> Path:
    p = root.joinpath(*relpath(raw).split('/'))
    require(not root.is_symlink() and not any(x.is_symlink() for x in (p,*p.parents)), 'UNSAFE_DATASET_PATH')
    require(p.is_file(), 'EVIDENCE_FILE_NOT_REGULAR')
    return p


def load_bytes(path: Path) -> tuple[bytes,str]:
    require(path.is_file() and not path.is_symlink() and not any(x.is_symlink() for x in path.parents),
            'UNSAFE_DATASET_PATH')
    size = path.stat().st_size
    require(0 < size <= MAX_FILE_BYTES, 'EVIDENCE_SIZE_LIMIT')
    content = path.read_bytes()
    require(len(content) == size, 'EVIDENCE_CHANGED')
    return content, hashlib.sha256(content).hexdigest()


def load_json(path: Path) -> tuple[dict,str,int]:
    raw, checksum = load_bytes(path)
    def pairs(items):
        result={}
        for k,v in items:
            require(k not in result, 'DUPLICATE_JSON_KEY')
            result[k]=v
        return result
    try:
        data=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs,
                        parse_constant=lambda _:require(False,'NONFINITE_JSON_NUMBER'))
    except BenchmarkError:
        raise
    except (UnicodeError,ValueError,RecursionError):
        raise BenchmarkError('INVALID_JSON') from None
    require(type(data) is dict, 'INVALID_JSON_ROOT')
    return data, checksum, len(raw)


def validate_dataset(manifest: dict) -> None:
    require(set(manifest)=={'schema_version','stage','benchmark_id','cases','policy'} and
            manifest['schema_version']==VERSION and manifest['stage']=='phase7_benchmark_dataset',
            'INVALID_MANIFEST_SCHEMA')
    name(manifest['benchmark_id'])
    cases=manifest['cases']
    require(type(cases) is list and 1 <= len(cases) <= MAX_CASES, 'INVALID_CASE_COUNT')
    require(type(manifest['policy']) is dict and set(manifest['policy'])==set(DEFAULT_POLICY), 'INVALID_POLICY')
    for k,v in manifest['policy'].items():
        require(type(v) is int and 0 <= v <= MAX_CASES, 'INVALID_POLICY')
    require(manifest['policy']['max_false_passes']==0 and
            manifest['policy']['max_false_attributions']==0,
            'UNSAFE_RELEASE_POLICY')
    ids=set()
    for c in cases:
        require(type(c) is dict and CASE_KEYS.issubset(c) and set(c).issubset(CASE_KEYS|{'phase5','receipt_files'}),
                'INVALID_CASE_SCHEMA')
        case_id=name(c['id'])
        require(case_id not in ids, 'DUPLICATE_CASE_ID')
        ids.add(case_id)
        require(c['cohort'] in COHORTS, 'INVALID_COHORT')
        source_key(c['source'])
        for key in ('phase6','phase5'):
            if key in c:
                record=c[key]
                require(type(record) is dict and set(record)=={'path','sha256'}, 'INVALID_EVIDENCE_REFERENCE')
                relpath(record['path'])
                require(type(record['sha256']) is str and HASH.fullmatch(record['sha256']), 'INVALID_SHA256')
        receipts=c.get('receipt_files',[])
        require(type(receipts) is list and len(receipts)<=48, 'INVALID_RECEIPT_FILES')
        receipts_seen=set()
        for receipt in receipts:
            require(type(receipt) is dict and set(receipt)=={'id','path'}, 'INVALID_RECEIPT_FILES')
            rid=name(receipt['id'])
            require(rid not in receipts_seen, 'DUPLICATE_RECEIPT_ID')
            receipts_seen.add(rid)
            relpath(receipt['path'])
        g=c['ground_truth']
        require(type(g) is dict and set(g)==TRUTH_KEYS and g['reviewed'] is True,
                'GROUND_TRUTH_NOT_REVIEWED')
        name(g['reviewer_id'])
        require(g['basis'] in REVIEW_BASES, 'INVALID_REVIEW_BASIS')
        try:
            require(type(g['reviewed_at']) is str and date.fromisoformat(g['reviewed_at']).isoformat()==g['reviewed_at'],
                    'INVALID_REVIEW_DATE')
        except (ValueError,TypeError):
            raise BenchmarkError('INVALID_REVIEW_DATE') from None
        require(g['expected_report_status'] in REPORT_STATES, 'INVALID_EXPECTED_STATUS')
        codes=g['expected_codes']
        require(type(codes) is list and len(codes)<=100 and len(set(codes))==len(codes) and
                all(type(code) is str and code in CATALOG for code in codes), 'INVALID_EXPECTED_CODES')
        events=g['expected_events']
        require(type(events) is list and len(events)<=MAX_EVENTS_PER_CASE, 'INVALID_EXPECTED_EVENTS')
        pairs=set()
        for ev in events:
            require(type(ev) is dict and set(ev)=={'run','test_id','outcome','known_cause'}, 'INVALID_EXPECTED_EVENT')
            require(type(ev['run']) is str and EVENT_ID.fullmatch(ev['run']) is not None and
                    type(ev['test_id']) is str and EVENT_ID.fullmatch(ev['test_id']) is not None,
                    'INVALID_EXPECTED_EVENT_ID')
            require(ev['outcome'] in OUTCOMES, 'INVALID_EXPECTED_EVENT_OUTCOME')
            cause=ev['known_cause']
            require(cause is None or (type(cause) is str and CODE.fullmatch(cause)), 'INVALID_KNOWN_CAUSE')
            require(ev['outcome']=='FAIL' or cause is None, 'CAUSE_WITHOUT_FAILURE')
            key=(ev['run'],ev['test_id'])
            require(key not in pairs, 'DUPLICATE_EXPECTED_EVENT')
            pairs.add(key)
        comps=g['expected_comparisons']
        require(type(comps) is list and len(comps)<=100, 'INVALID_EXPECTED_COMPARISONS')
        seen=set()
        for comp in comps:
            require(type(comp) is dict and set(comp)=={'test_id','status','environment_equivalent'}, 'INVALID_EXPECTED_COMPARISON')
            require(type(comp['test_id']) is str and EVENT_ID.fullmatch(comp['test_id']) is not None,
                    'INVALID_EXPECTED_COMPARISON_ID')
            require(comp['status'] in COMPARISONS and type(comp['environment_equivalent']) is bool,
                    'INVALID_EXPECTED_COMPARISON')
            require(comp['test_id'] not in seen, 'DUPLICATE_EXPECTED_COMPARISON')
            seen.add(comp['test_id'])
        rating=g['maintainability_rating']
        require(rating is None or (type(rating) in (int,float) and 0 <= rating <= 100),
                'INVALID_MAINTAINABILITY_RATING')
        require(rating is None or 'phase5' in c, 'RATING_WITHOUT_PHASE5')


def record_source(src:dict) -> tuple:
    kind=src.get('kind')
    if kind in ('github_pinned','local_unverified'):
        src={**src,'kind':{'github_pinned':'github','local_unverified':'local'}[kind]}
    return source_key(src)
