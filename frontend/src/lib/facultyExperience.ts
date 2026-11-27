/** Presentation only. No inference of marks, new runtime permissions, or evidence eligibility. */
export type QueueFilter = 'ALL' | 'REVIEW' | 'APPROVED';
export interface QueueRow {
  title: string; submission_id: string; students: string[]; course?: string | null;
  assessment_status: string;
}
export function filterEvaluations<T extends QueueRow>(rows: readonly T[], query: string, filter: QueueFilter): T[] {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter(r => {
    const approved = r.assessment_status === 'TEACHER_REVIEWED';
    if (filter === 'REVIEW' && approved || filter === 'APPROVED' && !approved) return false;
    const text = [r.title, r.submission_id, ...r.students, r.course ?? ''].join(' ').toLocaleLowerCase();
    return words.every(word => text.includes(word));
  });
}
export function evaluationModeLabel(mode: string): string {
  return ({STATIC:'Source inspection', TRUSTED_DEMO:'Automatic functional evaluation', REVIEWED:'Advanced reviewed tests', RESUME:'Reuse previous evidence'} as Record<string,string>)[mode] ?? mode;
}
export function runtimeLabel(adapter: string | null): string | null {
  if (!adapter) return null;
  return ({NODE_HTTP:'Node.js HTTP application', PY_FLASK:'Python / Flask', PY_ASGI:'Python / ASGI', STATIC_HTTP:'Static website', PHP_MYSQL:'PHP / MySQL'} as Record<string,string>)[adapter] ?? adapter;
}
export function reportStatusText(assessed: number, unresolved: number, reviewed: number): string {
  if (unresolved > 0) return `${unresolved} ${unresolved === 1 ? 'criterion needs' : 'criteria need'} assessment. Unassessed criteria are not zero marks.`;
  if (reviewed < 100) return `All applicable criteria have a result; ${reviewed}% of the weight has been approved. The grade is not final until teacher review is complete.`;
  return `${assessed}% of the applicable weight assessed; ${reviewed}% approved. See the recorded assessment status.`;
}
