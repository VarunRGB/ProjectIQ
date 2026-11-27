import { expect, it } from 'vitest';
import { buildViewModel } from '../viewModel';
import { emptyReview } from '../../api/backend';
import type { EvaluationRecord } from '../../api/types';
import campus from '../../mock/fixtures/campusconnect.json';

function fixture(): EvaluationRecord {
  const fx: any = structuredClone(campus);
  return {id:'test', result:fx.result, evidence:fx.evidence, decisions:fx.decisions, meta:fx.meta, review:emptyReview(), source:'BACKEND'};
}
it('live dashboard uses engine assessment, preserving original proposal display', () => {
  const rec=fixture();
  rec.engineAssessment=structuredClone(rec.result.assessment);
  // A backend assessment differs from a naive local recomputation.
  rec.engineAssessment.proposed_points=12.34;
  rec.engineAssessment.criteria[0].reason='Authoritative engine feedback';
  const vm=buildViewModel(rec);
  expect(vm.assessment.proposed_points).toBe(12.34);
  expect(vm.assessment.criteria[0].reason).toBe('Authoritative engine feedback');
  expect(vm.assessment.criteria[0].proposal).toBeDefined();
});
it('mock scoring retains the existing frontend port', () => {
  const rec=fixture();rec.source='MOCK_FIXTURE';rec.engineAssessment={...rec.result.assessment,proposed_points:99999};
  expect(buildViewModel(rec).assessment.proposed_points).not.toBe(99999);
});
it('bridge job metadata does not promote imported receipt provenance', () => {
  const rec=fixture();rec.meta.bridge_job_actions={executioner:'COLLECTED_THIS_RUN'};
  const vm=buildViewModel(rec);
  expect(vm.provenance.perAgent.executioner.action).toBe('COLLECTED_THIS_RUN');
  expect(vm.provenance.perAgent.executioner.provenance).toBe(rec.result.agents.executioner.receipt_provenance);
});
