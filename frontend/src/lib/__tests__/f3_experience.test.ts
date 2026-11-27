import { describe, expect, it } from 'vitest';
import { filterEvaluations, evaluationModeLabel, runtimeLabel, reportStatusText } from '../facultyExperience';
const rows=[{title:'Express CRUD',submission_id:'F1-01',students:['Demo Student'],course:'Software Engineering',assessment_status:'INCOMPLETE_ASSESSMENT'}, {title:'MiniCollab',submission_id:'MC-01',students:['Sample'],course:'Web',assessment_status:'TEACHER_REVIEWED'}];
describe('F3 presentation helpers',()=>{
  it('searches case-insensitively across title, ID, roster and course',()=>expect(filterEvaluations(rows,'EXPRESS student engineering','ALL')).toEqual([rows[0]]));
  it('does not mutate saved records',()=>{const before=JSON.stringify(rows);filterEvaluations(rows,'mini','APPROVED');expect(JSON.stringify(rows)).toBe(before)});
  it('filters by actual assessment status',()=>{expect(filterEvaluations(rows,'','REVIEW')).toEqual([rows[0]]);expect(filterEvaluations(rows,'','APPROVED')).toEqual([rows[1]])});
  it('supports empty results',()=>expect(filterEvaluations(rows,'absent','ALL')).toEqual([]));
  it('preserves unknown mode and adapter labels',()=>{expect(evaluationModeLabel('FUTURE')).toBe('FUTURE');expect(runtimeLabel('FUTURE')).toBe('FUTURE');expect(runtimeLabel(null)).toBeNull()});
  it('labels automatic mode without changing the backend enum',()=>expect(evaluationModeLabel('TRUSTED_DEMO')).toBe('Automatic functional evaluation'));
  it('never treats an unassessed criterion as a zero',()=>expect(reportStatusText(0,6,0)).toContain('not zero marks'));
  it('does not label proposed marks final',()=>expect(reportStatusText(100,0,50)).toContain('not final'));
});
