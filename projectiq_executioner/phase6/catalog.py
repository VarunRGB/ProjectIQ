"""Fixed diagnosis and action vocabulary; nothing from untrusted source becomes prose."""
from __future__ import annotations

# code -> category, priority, audience, title, explanation, suggested action
CATALOG = {
 'VM_CLEANUP_UNVERIFIED': ('SECURITY_OR_ISOLATION','P0','OPERATOR','VM cleanup was not verified','The worker did not prove disposable storage was removed.','Stop further VM work; inspect managed leases and storage.'),
 'VM_PROVENANCE_INVALID': ('SECURITY_OR_ISOLATION','P0','OPERATOR','VM/source evidence is inconsistent','Execution evidence contradicts the requested source or required runtime metadata.','Investigate evidence integrity; do not attribute project defects.'),
 'VM_EXECUTION_UNVERIFIED': ('INFRASTRUCTURE_FAILURE','P1','OPERATOR','VM execution not verified','The runner did not establish a valid execution and startup witness.','Inspect trusted worker diagnostic codes and reviewed configuration.'),
 'VM_LAUNCH_BLOCKED': ('SETUP_OR_AUTHORIZATION','P2','OPERATOR','VM launch was blocked','The runtime could not launch under existing reviewed authorization.','Review approved SHA, runtime adapter and offline dependency readiness.'),
 'STARTUP_NOT_READY': ('STARTUP_FAILURE','P1','OPERATOR','Application readiness not established','The approved runtime did not establish required service readiness.','Check verified startup observations, adapter expectations and setup.'),
 'TEST_ASSERTION_MISMATCH': ('APPLICATION_OBSERVATION','P2','REVIEWER','Observed functional assertion mismatch','The validated run reports a response that did not match a reviewed assertion.','Verify the oracle and test fixture, then examine the relevant application route.'),
 'TEST_INCONCLUSIVE': ('INSUFFICIENT_EVIDENCE','P2','OPERATOR','Test result is inconclusive','The test could not establish a trustworthy pass or failure.','Inspect bounded diagnostic codes and retry only through approved workflows.'),
 'TEST_BLOCKED': ('TEST_UNSUPPORTED','P3','REVIEWER','Test not executed','The test was blocked, unsupported or lacked a grounded oracle.','Review test-oracle evidence and runner support.'),
 'ORACLE_NOT_ESTABLISHED': ('INSUFFICIENT_EVIDENCE','P3','REVIEWER','Expected behavior not yet established','One or more planned specifications do not yet have grounded expected outcomes.','Review and approve bounded scenario expectations; do not attribute missing coverage to the student.'),
 'INVALID_TEST_ORACLE': ('INVALID_TEST_ORACLE','P2','REVIEWER','Test oracle not established','The proposed expectation lacks sufficient evidence for a valid assertion.','Review the source contract and provide a grounded expected outcome.'),
 'INVALID_TEST_EVIDENCE': ('INVALID_TEST_ORACLE','P1','OPERATOR','Test evidence inconsistent','Reported results or counts contradict the allowed report structure.','Reject this result and inspect the trusted producing stage.'),
 'BROWSER_CHECK_FAILURE': ('APPLICATION_OBSERVATION','P2','REVIEWER','Browser check mismatch','The reviewed UI/login check failed according to the validated report.','Verify selectors and setup, then examine the reported UI state.'),
 'DATABASE_POSTCONDITION_FAILURE': ('APPLICATION_OBSERVATION','P2','REVIEWER','Database postcondition mismatch','The reviewed database observation did not match its expected state.','Inspect approved fixtures, transaction ordering and application persistence.'),
 'DEPENDENT_TEST_BLOCKED': ('INSUFFICIENT_EVIDENCE','P3','REVIEWER','Downstream test blocked','A declared prerequisite did not pass and this test was not independently assessed.','Resolve the prerequisite first; do not count a separate application defect.'),
 'RERUN_OUTCOME_INCONSISTENT': ('FLAKY_OR_NONDETERMINISTIC','P2','REVIEWER','Controlled rerun outcomes differ','Matching source/oracle reports returned differing decisive outcomes; runtime and fixture equivalence have not been established.','Inspect determinism, fixture reset and environmental stability.'),
 'RERUN_NOT_COMPARABLE': ('INSUFFICIENT_EVIDENCE','P3','REVIEWER','Rerun evidence cannot be compared','An input differs or lacks the provenance required for a controlled comparison.','Use matching source commit, reviewed oracle digest and verified execution context.'),
 'PHASE5_SECRET_INDICATOR': ('POTENTIAL_SECURITY_EXPOSURE','P1','STUDENT','Potential credential exposure','Static analysis found a credential exposure indicator; actual validity is unknown.','Review the reported file; rotate confirmed exposed credentials and inspect Git history.'),
 'PHASE5_SECURITY_SCAN_INCOMPLETE': ('POTENTIAL_SECURITY_EXPOSURE','P2','REVIEWER','Security indicator scan incomplete','Phase 5 could not examine every eligible file for credential patterns.','Review security scan coverage before relying on a clean result.'),
 'PHASE5_QUALITY_ADVISORY': ('MAINTAINABILITY_ADVISORY','P3','STUDENT','Static maintainability finding','Phase 5 reported a supported source-quality concern, not a proven runtime cause.','Review the Phase 5 evidence and recommendation separately from functional failures.'),
 'NO_EXECUTION_EVIDENCE': ('INSUFFICIENT_EVIDENCE','P3','REVIEWER','No verified execution available','Only planning or static data was supplied; student runtime behavior was not evaluated.','Supply a valid existing reviewed execution report if available.'),
 'UNSUPPORTED_STAGE': ('TEST_UNSUPPORTED','P3','OPERATOR','Unrecognized report stage','Phase 6 does not have a validated adapter for this result shape.','Add a reviewed parser for this exact report version before claiming findings.'),
 'MISSING_CONFIGURATION_EVIDENCE': ('CONFIGURATION_OR_CREDENTIALS','P2','OPERATOR','Configuration not verified','Evidence explicitly identifies a missing required setup input or fixture.','Check the approved environment and fixture configuration without exposing values.'),
}

# These codes cannot establish a concrete root cause unless extra evidence exists.
SOURCE_ISSUES = {
 'VM_WORKER_EXCEPTION':'VM_EXECUTION_UNVERIFIED',
 'VM_LAUNCH_DENIED':'VM_LAUNCH_BLOCKED',
 'VM_CLEANUP_NOT_VERIFIED':'VM_CLEANUP_UNVERIFIED',
 'LOGIN_CLEANUP_NOT_VERIFIED':'VM_CLEANUP_UNVERIFIED',
 'VM_OR_GUEST_API_NOT_VERIFIED':'VM_EXECUTION_UNVERIFIED',
 'VM_OR_GUEST_TESTS_NOT_VERIFIED':'VM_EXECUTION_UNVERIFIED',
 'LOGIN_GUEST_EVIDENCE_INVALID':'INVALID_TEST_EVIDENCE',
 'GUEST_REPORT_NOT_VERIFIED':'INVALID_TEST_EVIDENCE',
 'API_GUEST_REPORT_NOT_VERIFIED':'INVALID_TEST_EVIDENCE',
 'PINNED_FIXTURE_NOT_OPERATOR_APPROVED_OR_ADAPTER_MISSING':'VM_LAUNCH_BLOCKED',
 'NO_SUPPORTED_ADAPTER':'VM_LAUNCH_BLOCKED',
 'PINNED_SHA_NOT_APPROVED':'VM_LAUNCH_BLOCKED',
 'PREPARATION_NOT_ENABLED':'VM_LAUNCH_BLOCKED',
 'BROWSER_CDP_UNAVAILABLE':'VM_EXECUTION_UNVERIFIED',
 'BROWSER_CDP_NET_ERR_SOCKET_NOT_CONNECTED':'VM_EXECUTION_UNVERIFIED',
 'MISSING_REQUIRED_ENV':'MISSING_CONFIGURATION_EVIDENCE',
 'REQUIRED_CONFIGURATION_ABSENT':'MISSING_CONFIGURATION_EVIDENCE',
 'OFFLINE_DEPENDENCY_VALIDATION_FAILED':'VM_LAUNCH_BLOCKED',
 'PINNED_NODE_ARTIFACT_REQUIRED':'VM_LAUNCH_BLOCKED',
 'PINNED_PIP_ARTIFACT_REQUIRED':'VM_LAUNCH_BLOCKED',
 'TRUSTED_DEPENDENCY_NETWORK_NOT_AUTHORIZED':'VM_LAUNCH_BLOCKED',
 'NO_SUPPORTED_ADAPTER':'VM_LAUNCH_BLOCKED',
}


def diagnosis(code: str, *, evidence: list[str], confidence: str = 'MEDIUM',
              linked_tests: list[str] | None = None, causal_status: str = 'UNPROVEN') -> dict:
    category, priority, audience, title, explanation, action = CATALOG[code]
    return dict(code=code, category=category, priority=priority,
                audience=audience, title=title, explanation=explanation,
                recommended_action=action, confidence=confidence,
                causal_status=causal_status, evidence=sorted(set(evidence)),
                test_ids=sorted(set(linked_tests or [])), student_defect='NOT_ESTABLISHED')
