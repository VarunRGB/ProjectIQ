"""Advisory plan consistency checks, deliberately not an execution admission gate."""
from __future__ import annotations
import hashlib
import json
from pathlib import PurePosixPath
import re

OPERATIONS={'RESOLVE_DEPENDENCIES','BUILD_DECLARED_SCRIPT','START_DECLARED_SCRIPT_CANDIDATE',
            'START_PYTHON_CANDIDATE','START_PHP_CANDIDATE','SERVE_STATIC_CANDIDATE',
            'BUILD_TOOLCHAIN_CANDIDATE','START_TOOLCHAIN_CANDIDATE','DISCOVER_HEALTH_PROBE'}
TOOLS={None,'npm','pnpm','yarn','bun','pip','uv','poetry','pipenv','composer','maven','gradle',
       'go','cargo','bundler','nuget','cmake','make','meson','pub','swiftpm','mix','stack','cabal','deno',
       'jupyter','python','php','static_http','java_or_kotlin','rust','ruby','dotnet','c_cpp','dart_or_flutter',
       'godot','swift','elixir','haskell','r','julia'}
SCRIPTS={'build','start','dev','preview','serve'}
OP_KEYS={'kind','tool','evidence','requires_isolated_preparation','script_key','requires_policy_validation','requires_independent_probe_validation'}


def _require(ok):
    if not ok:raise ValueError('INVALID_ADVISORY_PLAN')


def _safe_relative(path):
    return (isinstance(path,str) and 0<len(path)<=350 and '\\' not in path and ':' not in path
            and not PurePosixPath(path).is_absolute() and '..' not in PurePosixPath(path).parts
            and not any(ord(c)<32 for c in path))


def validate_plan(plan: dict) -> None:
    """Validate generated shape and checksum. A valid checksum is not authentication."""
    _require(isinstance(plan,dict) and plan.get('schema_version')=='8A.1' and plan.get('phase')=='8A')
    _require(plan.get('status') in {'PLANNED','PLANNED_WITH_LIMITATIONS'})
    _require(plan.get('execution_authorized') is False)
    for key in ('vm_launches','commands_executed','functional_tests_executed'):
        _require(type(plan.get(key)) is int and plan[key]==0)
    a=plan.get('admission');_require(isinstance(a,dict) and a.get('status')=='NOT_EVALUATED' and a.get('runtime_permission') is False and a.get('dependency_preparation_permission') is False)
    rows=plan.get('components');_require(isinstance(rows,list) and len(rows)<=150)
    ids=set()
    for c in rows:
        _require(isinstance(c,dict) and c.get('execution_authorized') is False and _safe_relative(c.get('root')))
        ident=c.get('id');_require(isinstance(ident,str) and re.fullmatch(r'component-[a-f0-9]{16}',ident) and ident not in ids);ids.add(ident)
        for k in ('evidence_paths','entrypoint_candidates'):
            _require(isinstance(c.get(k),list) and all(_safe_relative(v) for v in c[k]))
        _require(c.get('status') in {'ADAPTER_CANDIDATE','ADAPTER_CANDIDATE_WITH_BLOCKERS','EXPANSION_OR_REVIEW_REQUIRED'})
        ops=c.get('proposed_operations');_require(isinstance(ops,list) and len(ops)<=30)
        for op in ops:
            _require(isinstance(op,dict) and not set(op)-OP_KEYS and op.get('kind') in OPERATIONS and op.get('tool') in TOOLS)
            _require(isinstance(op.get('evidence'),list) and all(_safe_relative(v) for v in op['evidence']))
            if 'script_key' in op:_require(op['script_key'] in SCRIPTS)
            for k in OP_KEYS-{'kind','tool','evidence','script_key'}:
                if k in op:_require(op[k] is True)
    for edge in plan.get('relationship_hypotheses',[]):
        _require(edge['from'] in ids and edge['to'] in ids and edge['status']=='HYPOTHESIS_NOT_VERIFIED')
    for row in plan.get('combined_adapter_candidates',[]):
        _require(set(row['components'])<=ids and row['status']=='REQUIRES_EXISTING_ADAPTER_VALIDATION')
    raw={k:v for k,v in plan.items() if k!='plan_sha256'}
    expected=hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    _require(plan.get('plan_sha256')==expected)
