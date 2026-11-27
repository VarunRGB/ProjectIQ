/** Plain-language labels for backend codes. Unknown codes fall back to a readable form. */
import type { AgentName, AgentStatus, CriterionStatus, EvidenceKind, Provenance } from '../api/types';

export function humanize(code: string | null | undefined): string {
  if (!code) return 'Unknown';
  const s = code.replace(/[_-]+/g, ' ').toLowerCase().trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const AGENT_INFO: Record<AgentName, { name: string; role: string }> = {
  summarizer: { name: 'Summary', role: 'Extracts the report’s own key sentences. Never contributes marks.' },
  integrity: { name: 'Integrity', role: 'Compares the report with supplied references and estimates AI-like writing. Advisory only.' },
  research: { name: 'Research', role: 'Extracts the bibliography, checks paper identity, relevance, citation links and recency.' },
  executioner: { name: 'Executioner', role: 'Inspects the repository and, when approved, runs reviewed tests in a disposable VM.' },
};

export const AGENT_STATUS: Record<AgentStatus, { label: string; tone: Tone; explain: string }> = {
  COMPLETED: { label: 'Completed', tone: 'good', explain: 'The agent finished and its saved output matched its receipt.' },
  PARTIAL: { label: 'Partial', tone: 'warn', explain: 'The agent finished, but some coverage is limited or not certified. Read its limitations.' },
  FAILED: { label: 'Failed', tone: 'bad', explain: 'The agent did not produce a usable result. Other agents are unaffected.' },
  NOT_RUN: { label: 'Not run', tone: 'muted', explain: 'This agent was not needed or not requested for this evaluation.' },
  REVIEW_REQUIRED: { label: 'Needs review', tone: 'warn', explain: 'The agent produced results that an operator or teacher should review.' },
};

export type Tone = 'good' | 'warn' | 'bad' | 'muted' | 'info' | 'pencil' | 'ink';

export const CRITERION_STATUS: Record<CriterionStatus | 'REVIEW_REQUIRED' | 'PREVIEW_PENDING', { label: string; tone: Tone; explain: string }> = {
  PROPOSED: { label: 'AI proposed', tone: 'pencil', explain: 'Suggested by the local rubric model from cited evidence. Not a mark until you approve it.' },
  TEACHER_REVIEWED: { label: 'Approved', tone: 'ink', explain: 'You approved or set this mark. It counts toward the reviewed grade.' },
  OBJECTIVE_PROVISIONAL: { label: 'Objective, provisional', tone: 'info', explain: 'Calculated from verified test results or a native check. Confirm it before it counts as reviewed.' },
  UNASSESSED: { label: 'Not assessed', tone: 'warn', explain: 'No defensible mark yet. It is not counted as zero; the complete grade is withheld until it is assessed.' },
  REVIEW_REQUIRED: { label: 'Needs your review', tone: 'warn', explain: 'The engine could not propose a mark safely. Score it yourself from the evidence.' },
  PREVIEW_PENDING: { label: 'Preview only', tone: 'muted', explain: 'Evaluated in preview mode; no model proposal was requested.' },
  EXCLUDED: { label: 'Not applicable', tone: 'muted', explain: 'Excluded from this rubric. Its weight is shared among the other criteria.' },
};

export const EVIDENCE_KIND: Record<EvidenceKind, { label: string; explain: string }> = {
  REPORT_EXCERPT: { label: 'Report excerpt', explain: 'What the student wrote. A claim, not a verified fact.' },
  STATIC_SOURCE: { label: 'Source code finding', explain: 'Observed by reading the repository. Not proof that the feature works.' },
  RESEARCH_RECORD: { label: 'Research record', explain: 'Bibliography metadata, relevance and linkage from the Research agent.' },
  TEST_OBSERVATION: { label: 'Verified test result', explain: 'Observed during a verified, cleaned-up VM run of a reviewed test.' },
  TEST_PLAN: { label: 'Planned test', explain: 'A test specification or unverified outcome. Cannot support marks.' },
  REPORT_SUMMARY: { label: 'Summary sentence', explain: 'Extracted from the report for orientation. Never used for marks.' },
  LEGACY_JUDGEMENT: { label: 'Legacy judge quote', explain: 'From the old report-only judge. Excluded from grading.' },
  INTEGRITY_ADVISORY: { label: 'Integrity note', explain: 'Advisory similarity or AI-style information. Never used for marks.' },
  METRIC: { label: 'Metric', explain: 'A measured value with its scope.' },
};

export const PROVENANCE: Record<Provenance, { label: string; tone: Tone; explain: string }> = {
  REPORT_SNAPSHOT: { label: 'Report snapshot', tone: 'info', explain: 'Taken from the private copy of the submitted report, identified by its SHA-256.' },
  NATIVE_RECEIPT_MATCHED: { label: 'Live', tone: 'good', explain: 'Collected in this evaluation; the saved agent output matches its receipt.' },
  IMPORTED_ACKNOWLEDGED: { label: 'Imported, acknowledged', tone: 'warn', explain: 'Imported from an earlier run. An operator confirmed it belongs to this submission. This is not independent authentication.' },
  IMPORTED_UNCONFIRMED: { label: 'Imported, not acknowledged', tone: 'bad', explain: 'Imported without acknowledgment. It is shown for information only and cannot support marks.' },
};

export const TEST_OUTCOME: Record<string, { label: string; tone: Tone; explain: string }> = {
  PASSED: { label: 'Passed', tone: 'good', explain: 'A verified observation matched the reviewed expectation.' },
  FAILED: { label: 'Failed', tone: 'bad', explain: 'A verified observation did not match the reviewed expectation. This alone does not prove student fault: the oracle or fixture could also be wrong.' },
  BLOCKED: { label: 'Blocked', tone: 'warn', explain: 'Execution could not be verified or a safety gate stopped it. Not a student defect.' },
  INCONCLUSIVE: { label: 'Inconclusive', tone: 'warn', explain: 'The run could not establish a trustworthy pass or fail.' },
  NOT_TESTED: { label: 'Not tested', tone: 'muted', explain: 'Planned but not executed, usually because no reviewed expectation exists, the feature is unsupported, or the run was STATIC. Not a failure.' },
};

export const TEST_REASON: Record<string, string> = {
  VERIFIED_OBSERVATION: 'Matched a verified observation',
  MULTIPLE_VERIFIED_OBSERVATIONS: 'Matched several consistent verified observations',
  MULTIPLE_DIFFERENT_OBSERVED_OUTCOMES: 'Verified observations disagree',
  REVIEWED_ORACLE_MISSING: 'No reviewed expected result yet',
  REVIEWED_SCENARIO_ONLY_NOT_FULL_ROUTE: 'Only a related reviewed scenario ran, not this exact test',
  REVIEWED_WORKFLOW_ONLY_NOT_FULL_ROUTE: 'Only an aggregate reviewed workflow ran, not this exact test',
  APPROVED_SCENARIO_NO_VERIFIED_OBSERVATION: 'Scenario approved but no verified observation',
  UNSUPPORTED_TEST_TARGET: 'Runner cannot test this kind of feature',
  NO_MATCHING_TEST_OBSERVATION: 'No matching observation in the selected suites',
  STATIC_MODE_NO_EXECUTION: 'STATIC mode: nothing was executed',
  READINESS_SUITE_NOT_VERIFIED: 'Readiness suite was not verified',
  NO_VERIFIED_TEST_OBSERVATION: 'No verified observation',
  EXECUTION_EVIDENCE_NOT_VERIFIED: 'Execution evidence not verified',
  SUITE_AND_CHECK_RESULT_CONFLICT: 'Suite and check results conflict',
  CHECK_RESULT_NOT_RECOGNIZED: 'Check result not recognised',
};

/** Phase 6 diagnosis categories -> who the problem belongs to. */
export const DIAG_CATEGORY: Record<string, { label: string; owner: 'student' | 'environment' | 'unsupported' | 'evidence' | 'security'; explain: string }> = {
  APPLICATION_OBSERVATION: { label: 'Observed application behaviour', owner: 'student', explain: 'A reviewed check observed behaviour that differs from the expectation. Verify the oracle before attributing it to the student.' },
  MAINTAINABILITY_ADVISORY: { label: 'Code quality advisory', owner: 'student', explain: 'A static source finding. Not a proven runtime cause.' },
  POTENTIAL_SECURITY_EXPOSURE: { label: 'Possible security exposure', owner: 'security', explain: 'A static indicator; actual validity is unknown.' },
  INFRASTRUCTURE_FAILURE: { label: 'Execution infrastructure', owner: 'environment', explain: 'The VM or runner did not produce verified evidence. Not a student defect.' },
  SETUP_OR_AUTHORIZATION: { label: 'Setup or authorisation', owner: 'environment', explain: 'The run was not authorised or prepared. Not a student defect.' },
  STARTUP_FAILURE: { label: 'Startup', owner: 'environment', explain: 'Readiness was not established under the approved runtime.' },
  CONFIGURATION_OR_CREDENTIALS: { label: 'Configuration', owner: 'environment', explain: 'A required setup input was missing.' },
  SECURITY_OR_ISOLATION: { label: 'VM isolation', owner: 'environment', explain: 'Cleanup or provenance could not be verified. Operator action required.' },
  TEST_UNSUPPORTED: { label: 'Unsupported by the runner', owner: 'unsupported', explain: 'The technology or test target is outside the supported adapters.' },
  INSUFFICIENT_EVIDENCE: { label: 'Not enough evidence', owner: 'evidence', explain: 'Expectations or observations are missing. Missing coverage is not attributed to the student.' },
  INVALID_TEST_ORACLE: { label: 'Test expectation problem', owner: 'evidence', explain: 'The expected outcome is not grounded well enough to judge.' },
  FLAKY_OR_NONDETERMINISTIC: { label: 'Inconsistent reruns', owner: 'evidence', explain: 'Matching reruns disagree; determinism not established.' },
};

export const OWNER_INFO: Record<string, { label: string; tone: Tone; explain: string }> = {
  student: { label: 'Student implementation', tone: 'bad', explain: 'Observed in the student’s code or behaviour. Still check the expectation before deducting.' },
  security: { label: 'Possible security risk', tone: 'warn', explain: 'Static indicator worth discussing with the student.' },
  environment: { label: 'Execution infrastructure', tone: 'info', explain: 'A limitation of the evaluation environment, not the student.' },
  unsupported: { label: 'Unsupported technology', tone: 'muted', explain: 'The runner has no adapter for this technology or test type.' },
  evidence: { label: 'Missing evidence', tone: 'muted', explain: 'Not enough grounded expectations or observations to judge.' },
};

export const RESEARCH_VERIFICATION: Record<string, { label: string; tone: Tone; explain: string }> = {
  verified: { label: 'Verified', tone: 'good', explain: 'Title, authors and year matched an academic metadata record.' },
  needs_review: { label: 'Needs review', tone: 'warn', explain: 'Metadata partly matched or the source is not an academic record (for example a web page).' },
  not_found: { label: 'Not found', tone: 'warn', explain: 'No matching academic record. This does not prove the paper is fabricated.' },
  metadata_mismatch: { label: 'Metadata conflict', tone: 'bad', explain: 'The DOI or metadata points to a different paper. Check the citation.' },
  lookup_error: { label: 'Lookup failed', tone: 'warn', explain: 'The metadata service could not be reached. Identity is unconfirmed.' },
  not_checked: { label: 'Not checked', tone: 'muted', explain: 'Online verification was skipped for this entry.' },
};

export const RESEARCH_RELEVANCE: Record<string, { label: string; tone: Tone; rank: number }> = {
  directly_relevant: { label: 'Directly relevant', tone: 'good', rank: 0 },
  methodologically_relevant: { label: 'Methodological', tone: 'good', rank: 1 },
  supporting_relevant: { label: 'Supporting', tone: 'info', rank: 2 },
  foundational_relevant: { label: 'Foundational', tone: 'info', rank: 2 },
  partially_relevant: { label: 'Partially relevant', tone: 'warn', rank: 3 },
  insufficient_evidence: { label: 'Uncertain', tone: 'muted', rank: 4 },
  possibly_unrelated: { label: 'Possibly unrelated', tone: 'bad', rank: 5 },
  not_assessed: { label: 'Not assessed', tone: 'muted', rank: 6 },
};

export const AGE_BAND: Record<string, string> = {
  within_5_years: 'Within 5 years', within_10_years: '5–10 years', foundational_or_older: 'Older than 10 years',
  future_dated: 'Dated in the future', unknown: 'Year unknown',
};

export const INTEGRITY_CATEGORY: Record<string, { label: string; tone: Tone; counted: boolean; explain: string }> = {
  verbatim: { label: 'Verbatim', tone: 'bad', counted: true, explain: 'Word-for-word the same as a reference sentence.' },
  'near-verbatim': { label: 'Near-verbatim', tone: 'bad', counted: true, explain: 'Almost the same wording, with small edits.' },
  paraphrase: { label: 'Confirmed paraphrase', tone: 'warn', counted: true, explain: 'Different words, same meaning in both directions (checked by an entailment model).' },
  'similar meaning (not confirmed)': { label: 'Similar meaning (advisory)', tone: 'muted', counted: false, explain: 'Semantically close but not confirmed as paraphrase. Not counted in the overlap.' },
};

export const FLAG_TEXT: Record<string, string> = {
  REQUIREMENTS_COVERAGE_NOT_ESTABLISHED_BY_SCOPED_TESTS: 'Passing tests cover only the reviewed scope, not every requirement.',
  RESEARCH_ABSENCE_HEURISTIC_TEACHER_REVIEW_REQUIRED: 'No sources were detected. Confirm this before it counts.',
};

export const WORKFLOW_STATUS: Record<string, { label: string; tone: Tone; explain: string }> = {
  COMPLETE: { label: 'Ready for review', tone: 'good', explain: 'All agents finished and every criterion has a proposed or objective mark.' },
  INCOMPLETE_ASSESSMENT: { label: 'Needs your input', tone: 'warn', explain: 'Some criteria could not be assessed automatically. The complete grade is withheld until you assess them.' },
  PARTIAL_ERROR: { label: 'Partial results', tone: 'bad', explain: 'At least one agent failed or its output did not match its receipt. Other results are preserved.' },
  CANCELLED: { label: 'Cancelled', tone: 'muted', explain: 'The run was cancelled. Completed artifacts were kept.' },
};

export function reasonText(reason: string): string {
  // Engine reasons sometimes start with a CODE; turn it into words, keep the rest.
  return reason.replace(/^([A-Z][A-Z0-9_]{6,})(;|:)?/, (_m, code, sep) => humanize(code) + (sep ? sep : ''));
}

/** Phase 2 engine validation codes returned when a teacher decision is rejected. */
export const ENGINE_ERRORS: Record<string, string> = {
  EVIDENCE_COVERAGE_NOT_MET: 'The cited evidence does not cover what this criterion requires. Cite at least one item of each required kind.',
  DUPLICATE_SOURCE_COVERAGE: 'The cited items point to the same source passage. Cite distinct evidence.',
  INELIGIBLE_EVIDENCE: 'One of the cited items cannot support marks for this criterion (wrong kind or unacknowledged import).',
  UNKNOWN_EVIDENCE_REFERENCE: 'A cited evidence item no longer exists in this evaluation.',
  UNKNOWN_RUBRIC_LEVEL: 'The selected level is not in the current rubric.',
  JUDGMENT_REQUIRES_LEVEL: 'Choose a level for this criterion.',
  ONLY_TEACHER_CAN_EXCLUDE_OPTIONAL: 'Only optional criteria can be marked not applicable.',
  RESEARCH_ABSENCE_REQUIRES_REVIEW_NOT_MODEL_SCORE: 'No sources were detected. Confirm the lowest level with the research record, or mark research not applicable in the rubric.',
  TEST_RATIO_REQUIRES_TEACHER_CONFIRMATION: 'Objective test criteria can only be confirmed, not scored by level.',
  CANNOT_CONFIRM_INCOMPLETE_TEST_RATIO: 'The selected tests are incomplete, so this objective result cannot be confirmed.',
  CANNOT_CONFIRM_INCOMPLETE_OBJECTIVE: 'The objective result is incomplete and cannot be confirmed.',
  STALE_DECISIONS: 'The evaluation changed since this page loaded. Reload and try again.',
  NO_APPLICABLE_CRITERIA: 'At least one criterion must apply.',
  REWEIGHT_POLICY_CHANGED_REASSESS_REQUIRED: 'This change alters how criteria are judged, so affected criteria need reassessment.',
};

export function engineMessage(e: unknown): string {
  const code = (e as any)?.code ?? (e as any)?.message ?? String(e);
  const key = Object.keys(ENGINE_ERRORS).find((k) => String(code).includes(k));
  return key ? `${ENGINE_ERRORS[key]} (engine: ${key})` : `The engine rejected this change: ${humanize(String(code))}.`;
}
