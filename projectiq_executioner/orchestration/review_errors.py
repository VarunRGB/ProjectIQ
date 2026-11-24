"""Redacted, closed-vocabulary review failures. No raw exception text in reports."""
from __future__ import annotations

_CODES = {
    'D4A_REQUIRES_APPROVED_D1_FIXTURE', 'INVALID_D4A_WORKFLOW_CATALOG',
    'D4A_REQUIRES_ONE_SYNTHETIC_FIXTURE_ROW', 'D4A_SELECTOR_NOT_GROUNDED_IN_D1_FIXTURE',
    'D4A_ONLY_BOUNDED_INTEGER_ID_PASSING_SUPPORTED', 'D4A_THREE_DEPENDENT_STEPS_REQUIRED',
    'D4A_INVALID_WORKFLOW_STEP', 'D4A_TARGET_NOT_DISCOVERED_IN_PHASE3',
    'D4A_TARGET_METHOD_MISMATCH', 'D4A_WORKFLOW_DIGEST_NOT_OPERATOR_APPROVED',
    'D4A_GUEST_BUNDLE_REJECTED', 'CRUD_REQUIRES_APPROVED_D2_AUTH',
    'CRUD_REQUIRES_PHP_MYSQL_ADAPTER', 'CRUD_CATALOG_INVALID', 'CRUD_SIX_STEPS_REQUIRED',
    'CRUD_CATALOG_STEP_INVALID', 'CRUD_ENDPOINT_NOT_ESTABLISHED_IN_PHASE3',
    'CRUD_DIGEST_NOT_OPERATOR_APPROVED', 'CRUD_GUEST_CONTRACT_INVALID',
    'DATABASE_FIXTURE_NOT_OPERATOR_APPROVED', 'INVALID_FIXTURE_MANIFEST_OR_SOURCE',
    'INVALID_FIXTURE_POLICY', 'INVALID_FIXTURE_ROWS', 'PINNED_SOURCE_NOT_OPERATOR_APPROVED',
    'ORACLE_CATALOG_NOT_OPERATOR_APPROVED', 'PINNED_SOURCE_NOT_APPROVED',
    'INVALID_ORACLE_STAGING', 'API_GUEST_BUNDLE_REJECTED',
    'MISSING_OR_INVALID_ORACLE_APPROVAL_LIST', 'API_SCENARIO_LIMIT',
    'D2_AUTH_APPROVAL_REQUIRED', 'D3_REQUIRES_APPROVED_PHP_MYSQL_ADAPTER',
    'INVALID_D3_POSTCONDITION_CATALOG', 'POSTCONDITION_NOT_GROUNDED_IN_REGISTRATION_INPUT',
    'D3_POSTCONDITION_DIGEST_NOT_OPERATOR_APPROVED', 'UNSAFE_CATALOG_PATH',
    'UNSAFE_OPERATOR_REVIEW_ROOT', 'MISSING_INPUT_FILE', 'INVALID_INPUT_JSON',
    'DUPLICATE_JSON_KEY', 'INPUT_SIZE_LIMIT', 'INVALID_INPUT_ROOT',
    'UNSAFE_INPUT_PATH', 'NONFINITE_JSON_NUMBER',
}
_TRUSTED = {
    'projectiq_executioner.orchestration.contracts',
    'projectiq_executioner.phase4.dependent_workflow_contracts',
    'projectiq_executioner.phase4.crud_workflow_contracts',
    'projectiq_executioner.phase4.db_fixture_contracts',
    'projectiq_executioner.phase4.reviewed_api',
    'projectiq_executioner.phase4.reviewed_auth',
    'projectiq_executioner.phase4.db_postcondition_contracts',
}


def review_error_codes(exc):
    """Expose only known, constant codes from trusted host-side compiler types."""
    found = []
    seen = set()
    while exc is not None and id(exc) not in seen and len(seen) < 8:
        seen.add(id(exc))
        if type(exc).__module__ in _TRUSTED and len(exc.args) == 1:
            code = exc.args[0]
            if isinstance(code, str) and code in _CODES and code not in found:
                found.append(code)
        exc = exc.__cause__ or exc.__context__
    return found
