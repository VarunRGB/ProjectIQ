"""Compile independently rederived 9A proposals into bounded HTTP test contracts.

No guest, host HTTP requests, process execution, dependency installation or approval.
A saved compiled report is data, never permission. A future runner must re-establish
source/plan identity and its own reviewed runtime admission before using it.
"""
from __future__ import annotations

import re
from collections import Counter
from hashlib import sha256
from pathlib import Path

from ..orchestration.contracts import validate_request, validate_source, source_matches_url
from ..phase8a.planner import canonical, digest
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8d.dispatch import fresh
from ..phase8c_final.dispatch import write
from ..phase9a import planner

MAX_PLAN_BYTES = 8_000_000
MAX_TESTS = 64
MAX_COMPONENT_TESTS = 32
MAX_ASSERTIONS = 5
ADAPTERS = frozenset({'NODE_HTTP', 'PY_FLASK', 'PY_ASGI', 'STATIC_HTTP'})
HEX = re.compile(r'[0-9a-f]{64}\Z')
SHA = re.compile(r'[0-9a-fA-F]{40}\Z')
CASE_ID = re.compile(r'FT-[0-9A-F]{16}\Z')
COMPONENT_ID = re.compile(r'component-[0-9a-f]{16}\Z')
BASIS = {
    'IMPLEMENTATION_LITERAL': {'BODY_TEXT_EQUALS', 'JSON_EQUALS'},
    'IMPLEMENTATION_TEMPLATE': {'HTML_TEXT_CONTAINS'},
    'STATIC_RESOURCE_BYTES': {'HTTP_BODY_SHA256_EQUALS'},
    'REPOSITORY_DECLARED_OPENAPI': set(),
}
POLICY = {'method': 'GET', 'target': 'GUEST_LOOPBACK_ONLY', 'follow_redirects': False,
          'request_body': 'NONE', 'caller_headers': 'NONE', 'cookies': 'DISABLED',
          'query_parameters': 'NONE', 'accept_encoding': 'identity',
          'timeout_seconds': 5, 'max_response_bytes': 262144,
          'max_requests_per_test': 1, 'max_tests_total': MAX_TESTS,
          'max_tests_per_component': MAX_COMPONENT_TESTS,
          'retry_count': 0, 'parallelism': 1}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read_object(path):
    """Read only a bounded regular local artifact; bind parsed bytes to observed hash."""
    path = Path(path).absolute()
    info = checked_file(path, MAX_PLAN_BYTES)
    with path.open('rb') as f:
        raw = f.read(MAX_PLAN_BYTES + 1)
    require(len(raw) == info['bytes'] and sha256(raw).hexdigest() == info['sha256'],
            'PHASE9B_ARTIFACT_CHANGED')
    return load_object(raw.decode('utf-8-sig'))


def verify_checksum(obj, field):
    require(type(obj) is dict and isinstance(obj.get(field), str) and
            bool(HEX.fullmatch(obj[field])) and
            obj[field] == digest({k: v for k, v in obj.items() if k != field}),
            'PHASE9B_CHECKSUM_MISMATCH')


def _assertions(case):
    """Restrict oracle vocabulary and types; a status-only oracle is declaration-only."""
    basis = case.get('oracle_basis')
    require(isinstance(basis, str) and basis in BASIS, 'PHASE9B_ORACLE_BASIS_INVALID')
    allowed = BASIS[basis]
    rows = case.get('assertions')
    require(type(rows) is list and 1 <= len(rows) <= MAX_ASSERTIONS, 'PHASE9B_ASSERTION_COUNT')
    kinds = []
    for row in rows:
        require(type(row) is dict and set(row) == {'kind', 'expected'}, 'PHASE9B_ASSERTION_SHAPE')
        k, v = row['kind'], row['expected']
        require(isinstance(k, str) and k in allowed | {'HTTP_STATUS_EQUALS'}, 'PHASE9B_ASSERTION_KIND')
        kinds.append(k)
        if k == 'HTTP_STATUS_EQUALS':
            require(type(v) is int and 200 <= v <= 599, 'PHASE9B_STATUS_EXPECTATION')
        elif k == 'HTTP_BODY_SHA256_EQUALS':
            require(isinstance(v, str) and bool(HEX.fullmatch(v)), 'PHASE9B_BODY_DIGEST')
        elif k == 'JSON_EQUALS':
            require(planner.literal(v), 'PHASE9B_JSON_EXPECTATION')
        else:
            require(isinstance(v, str) and bool(v.strip()) and planner.literal(v), 'PHASE9B_TEXT_EXPECTATION')
    require(kinds[0] == 'HTTP_STATUS_EQUALS' and kinds.count('HTTP_STATUS_EQUALS') == 1,
            'PHASE9B_STATUS_ASSERTION_REQUIRED')
    require(len({canonical(r) for r in rows}) == len(rows), 'PHASE9B_DUPLICATE_ASSERTION')
    if basis == 'REPOSITORY_DECLARED_OPENAPI':
        require(len(rows) == 1 and rows[0]['expected'] == 200 and case.get('declared_contract') is True,
                'PHASE9B_DECLARATION_ORACLE_INVALID')
    else:
        require(len(rows) >= 2 and case.get('declared_contract') is False, 'PHASE9B_CONTENT_ORACLE_REQUIRED')
        if basis != 'IMPLEMENTATION_TEMPLATE':
            require(len(rows) == 2, 'PHASE9B_CONTENT_ORACLE_AMBIGUOUS')
    return rows


def _compile_rederived(plan, runtime):
    """Internal compiler. Inputs MUST originate from fresh trusted planner, not saved JSON.

    Pure validation/compilation, intentionally no runtime authority even for genuine plans.
    """
    verify_checksum(plan, 'plan_sha256'); verify_checksum(runtime, 'plan_sha256')
    source = validate_source(plan.get('source'))
    require(plan.get('schema_version') == '9A.1' and runtime.get('schema_version') == '8D.1' and
            plan.get('runtime_plan_sha256') == runtime['plan_sha256'] and runtime.get('source') == source,
            'PHASE9B_RUNTIME_BINDING_MISMATCH')
    require(plan.get('runtime_permission') is False and plan.get('automatic_untrusted_execution') is False and
            runtime.get('runtime_permission') is False and runtime.get('automatic_untrusted_execution') is False and
            type(plan.get('functional_tests_executed')) is int and plan['functional_tests_executed'] == 0 and
            type(plan.get('vm_start_attempts')) is int and plan['vm_start_attempts'] == 0 and
            plan.get('grade_credit') is None, 'PHASE9B_PLANNING_SCOPE_INVALID')
    components = runtime['components']; by_id = {c['id']: c for c in components}
    require(len(by_id) == len(components), 'PHASE9B_COMPONENT_ID_DUPLICATE')
    cases = plan['cases']; workflows = plan['workflows']
    require(len(cases) <= planner.MAX_CASES and len(workflows) <= 24, 'PHASE9B_PLAN_LIMIT')
    require(len({c['id'] for c in cases}) == len(cases), 'PHASE9B_CASE_ID_DUPLICATE')
    require(len({(c['component_id'],c['method'],c['path_template']) for c in cases}) == len(cases), 'PHASE9B_TARGET_DUPLICATE')
    groups = {}; deferred = []; counts = Counter(); total = 0
    for c in cases:
        cid = c['component_id']; case_id = c['id']; target = c['path_template']; method = c['method']
        require(isinstance(case_id,str) and CASE_ID.fullmatch(case_id) and
                isinstance(cid,str) and COMPONENT_ID.fullmatch(cid) and cid in by_id and
                c['adapter'] == by_id[cid]['adapter'] and c.get('execution') == 'NOT_PERFORMED',
                'PHASE9B_CASE_BINDING_INVALID')
        component = by_id[cid]; reasons = set(c['blockers']) | set(component['blockers'])
        if c['status'] != 'ORACLE_PROPOSED': reasons.add('PROPOSAL_REQUIRES_REVIEW')
        if method != 'GET': reasons.add('METHOD_NOT_SUPPORTED_IN_9BA')
        if not planner.local_path(target) or ':' in target: reasons.add('STATIC_RELATIVE_PATH_REQUIRED')
        if c['input_fields']: reasons.add('INPUT_BINDING_REQUIRED')
        if component['adapter'] not in ADAPTERS or component['status'] != 'REVIEWED_CANDIDATE':
            reasons.add('RUNNER_PROFILE_NOT_SUPPORTED')
        if plan['issues'] or runtime['snapshot_inspection']['issues']: reasons.add('PLAN_ISSUES_REQUIRE_REVIEW')
        port = component.get('service_port')
        if type(port) is not int or not 1 <= port <= 65535: reasons.add('GUEST_SERVICE_PORT_INVALID')
        if total >= MAX_TESTS or counts[cid] >= MAX_COMPONENT_TESTS: reasons.add('TEST_BUDGET_REACHED')
        if reasons:
            deferred.append({'case_id':case_id,'component_id':cid,'method':method,'path_template':target,
                             'status':'DEFERRED_NOT_EXECUTED','reasons':sorted(reasons)})
            continue
        assertions = _assertions(c)
        group = groups.setdefault(cid, {'component_id':cid,'root':component['root'],'adapter':component['adapter'],
                           'guest_service_port':port,'tests':[]})
        group['tests'].append({'case_id':case_id,'method':'GET','path':target,'assertions':assertions,
                               'oracle_basis':c['oracle_basis'],'declared_contract':c['declared_contract'],
                               'source_evidence':c['evidence'],'status':'COMPILED_NOT_EXECUTED'})
        counts[cid] += 1; total += 1
    deferred_workflows = [{'workflow_id':w['id'],'component_id':w['component_id'],
                           'status':'DEFERRED_NOT_EXECUTED','reasons':sorted(set(w['binding_requirements']) |
                            set(w['runtime_blockers']) | {'ADAPTIVE_WORKFLOW_RUNNER_NOT_IMPLEMENTED'})}
                          for w in workflows]
    result = {'schema_version':'9BA.1','stage':'FUNCTIONAL_TEST_COMPILATION',
              'status':'COMPILED_TESTS_AVAILABLE' if total else 'NO_COMPILED_TESTS',
              'source':source,'functional_plan_sha256':plan['plan_sha256'],
              'runtime_plan_sha256':runtime['plan_sha256'],'phase3_plan_sha256':plan['phase3_plan_sha256'],
              'source_rederivation':'EXACT_CANONICAL_PLAN_MATCH', 'runtime_permission':False,
              'automatic_untrusted_execution':False,'functional_tests_executed':0,'vm_start_attempts':0,
              'grade_credit':None,'request_policy':dict(POLICY),'components':list(groups.values()),
              'deferred_cases':deferred,'deferred_workflows':deferred_workflows,
              'summary':{'proposed_cases':len(cases),'compiled_tests':total,'deferred_cases':len(deferred),
                         'deferred_workflows':len(deferred_workflows),'compiled_components':len(groups)},
              'limitations':['Compiled contracts are not executed tests or authorization. A future runner must independently admit the exact source and bind the actual guest service.',
               'GET is a bounded method restriction, not a guarantee of no application side effects. Run only in an isolated reviewed VM.',
               'Implementation-derived oracles characterize behavior; OpenAPI declarations are repository claims, not teacher-approved requirements.',
               'No dynamic inputs, mutations, workflows, authentication, browser or database checks are compiled here.']}
    result['compiled_sha256'] = digest(result)
    return result


def compile_repository(url, ref, proposed_path, out):
    """Operator-selected exact GitHub SHA, compare fresh 9A output, compile only on match."""
    require(isinstance(ref,str) and SHA.fullmatch(ref), 'PHASE9B_EXACT_REF_REQUIRED')
    req = validate_request({'submission_id':'PHASE9BA','repository_url':url,'ref':ref.lower(),
                            'mode':'STATIC','max_vm_launches':0})
    proposed = read_object(proposed_path); verify_checksum(proposed, 'plan_sha256')
    source = validate_source(proposed.get('source'))
    require(source_matches_url(source,req['repository_url']) and source['commit_sha'] == ref.lower(),
            'PHASE9B_SOURCE_MISMATCH')
    out = fresh(out)
    # Reuse the exact same planner (including manifest, Phase 3 and 8D compilation).
    # No trust in old sibling files, absolute paths, or supplied source trees.
    regenerated = planner.plan_repository(req['repository_url'],ref.lower(),out/'rederived')
    require(canonical(proposed) == canonical(regenerated), 'PHASE9B_PLAN_REDERIVATION_MISMATCH')
    runtime = read_object(out/'rederived'/'runtime_plan.json')
    result = _compile_rederived(regenerated,runtime)
    write(out/'compiled_plan.json',result)
    return result
