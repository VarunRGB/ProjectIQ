"""Fixed, uncertainty-preserving root-cause *hypotheses*, never fault verdicts."""
from __future__ import annotations

# Hypotheses are distinct from observed failures and carry no numeric probability.
# A numeric HTTP status alone cannot identify a credential, source or oracle defect.
HTTP_HYPOTHESES = {
    401: ('AUTHENTICATION_CREDENTIAL_OR_STATE','REVIEWED_FIXTURE_MISMATCH','EXPECTED_STATUS_UNVERIFIED'),
    403: ('AUTHORIZATION_POLICY_OR_ROLE','REVIEWED_FIXTURE_MISMATCH','EXPECTED_STATUS_UNVERIFIED'),
    404: ('APPLICATION_ROUTE_MISSING','REQUEST_PATH_OR_BASE_URL','EXPECTED_STATUS_UNVERIFIED'),
    408: ('REQUEST_TIMEOUT_OR_LOAD','INFRASTRUCTURE_OR_READINESS','EXPECTED_STATUS_UNVERIFIED'),
    409: ('APPLICATION_STATE_CONFLICT','REVIEWED_FIXTURE_MISMATCH','EXPECTED_STATUS_UNVERIFIED'),
    422: ('INPUT_VALIDATION_REJECTION','REVIEWED_FIXTURE_MISMATCH','EXPECTED_STATUS_UNVERIFIED'),
    429: ('RATE_LIMITING_OR_STATE','REVIEWED_FIXTURE_MISMATCH','EXPECTED_STATUS_UNVERIFIED'),
    500: ('APPLICATION_RUNTIME_ERROR','UPSTREAM_OR_DATABASE_UNAVAILABLE','EXPECTED_STATUS_UNVERIFIED'),
    502: ('GATEWAY_OR_UPSTREAM_UNAVAILABLE','INFRASTRUCTURE_OR_READINESS','EXPECTED_STATUS_UNVERIFIED'),
    503: ('SERVICE_UNAVAILABLE','INFRASTRUCTURE_OR_READINESS','EXPECTED_STATUS_UNVERIFIED'),
    504: ('UPSTREAM_TIMEOUT','INFRASTRUCTURE_OR_READINESS','EXPECTED_STATUS_UNVERIFIED'),
}

CATEGORY_HYPOTHESES = {
    'reviewed_browser_ui': ('UI_RENDER_OR_SELECTOR_MISMATCH','TIMING_OR_ASYNC_LOADING','REVIEWED_EXPECTATION_MISMATCH'),
    'reviewed_browser_login': ('AUTHENTICATION_CREDENTIAL_OR_STATE','BROWSER_SESSION_OR_REDIRECT','REVIEWED_EXPECTATION_MISMATCH'),
    'reviewed_vm_database_postconditions': ('FIXTURE_OR_BASELINE_STATE','PERSISTENCE_OR_TRANSACTION_BEHAVIOR','REVIEWED_EXPECTATION_MISMATCH'),
    'reviewed_authenticated_crud': ('SESSION_OR_ROLE_STATE','APPLICATION_CRUD_BEHAVIOR','REVIEWED_EXPECTATION_MISMATCH'),
    'reviewed_vm_dependent_api_workflow': ('PREREQUISITE_OR_FIXTURE_STATE','APPLICATION_API_BEHAVIOR','REVIEWED_EXPECTATION_MISMATCH'),
}


def derive(runs: list[dict], *, security_hold: bool) -> list[dict]:
    if security_hold:
        return []
    result=[]
    seen=set()
    for run in runs:
        if not run['supported']:
            continue
        for row in run['rows']:
            if row['outcome']!='FAIL':
                continue
            status=row['http_status']
            codes=(HTTP_HYPOTHESES.get(status) if status is not None else None)
            if codes is None:
                codes=CATEGORY_HYPOTHESES.get(run['stage'],('APPLICATION_OR_TEST_SETUP','REVIEWED_EXPECTATION_MISMATCH'))
            key=(run['label'],row['id'])
            if key in seen:continue
            seen.add(key)
            result.append({'run':run['label'],'test_id':row['id'],
                           'observed_http_status':status,
                           'candidate_codes':list(codes),
                           'status':'UNVERIFIED_HYPOTHESES',
                           'evidence_strength':'VM_CORROBORATED_SYMPTOM' if run['verified'] else 'REPORT_ASSERTION_ONLY',
                           'cause_established':False})
    return result[:400]
