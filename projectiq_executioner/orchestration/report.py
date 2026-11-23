"""Comprehensive, sanitized, evidence-linked Executioner teacher handoff.

Raw guest logs and operator-reviewed catalogs are intentionally NOT rendered.
Machine consumers can read the full bounded Phase 1–6 JSON artifacts via the
hash-indexed report; this markdown emphasizes findings and provenance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import read_json


def esc(value: Any) -> str:
    return (str(value).replace('\\','\\\\').replace('|','\\|').replace('`','\\`')
            .replace('<','&lt;').replace('>','&gt;').replace('\r',' ').replace('\n',' ')
            .replace(']','\\]').replace('[','\\['))[:500]


def table(lines: list[str], columns:tuple[str,...], rows:list[tuple]) -> None:
    lines.append('| '+' | '.join(columns)+' |')
    lines.append('|'+'|'.join(['---']*len(columns))+'|')
    for row in rows:
        lines.append('| '+' | '.join(esc(v) for v in row)+' |')
    if not rows:lines.append('| '+' | '.join('—' for _ in columns)+' |')
    lines.append('')


def _kv(lines:list[str], key:str, value:Any) -> None:
    lines.append('- **'+key+':** '+esc(value))


def render_markdown(r:dict, root:Path|None=None) -> str:
    src=r.get('source') or {}
    intel=r.get('project_intelligence') or {}
    plan=r.get('test_plan') or {}
    quality=r.get('code_quality') or {}
    diagnosis=r.get('diagnostics') or {}
    suites=r.get('suites') or []
    lines=['# ProjectIQ Executioner — Comprehensive Evaluation','',
           '**Submission:** '+esc(r.get('submission_id'))+'  ',
           '**Workflow status:** '+esc(r.get('status'))+'  ',
           '**Mode:** '+esc(r.get('mode'))+' (effective '+esc(r.get('effective_mode'))+')  ',
           '**Security:** NOT ATTESTED for arbitrary untrusted student execution  ',
           '**Student-grade attribution:** NOT AUTOMATIC; faculty review required  ',
           '**Phase 7:** Release benchmarking only; not run on this submission','',
           '> A failed reviewed assertion is not proof of a student-code defect. '
           'Missing evidence is NOT_ASSESSED, not a zero mark.','',
           '## 1. Executive evaluation summary','']
    _kv(lines,'Repository',src.get('repository','NOT_RESOLVED'))
    _kv(lines,'Exact commit SHA',src.get('commit_sha','NOT_RESOLVED'))
    _kv(lines,'Architecture',intel.get('architecture','NOT_IDENTIFIED'))
    lines+=['','**Project synopsis (source-derived):** '+esc(intel.get('overview','NOT_AVAILABLE')),'']
    _kv(lines,'Source files indexed',(intel.get('inventory') or {}).get('indexed_files','UNKNOWN'))
    _kv(lines,'Discovered routes',intel.get('routes_discovered','UNKNOWN'))
    _kv(lines,'Planned test cases',(plan.get('summary') or {}).get('total','UNKNOWN'))
    for outcome, count in (r.get('test_case_matrix') or {}).get('counts',{}).items():
        _kv(lines,'Discovered tests '+outcome.lower().replace('_',' '),count)
    _kv(lines,'Code-quality finding count',(quality.get('summary') or {}).get('findings','NOT_AVAILABLE'))
    _kv(lines,'Functional suites selected',len(suites))
    _kv(lines,'Functional suites VM-verified',sum(bool(x.get('vm_verified')) for x in suites))
    _kv(lines,'VM attempts scheduled in this call',r.get('vm_attempts_scheduled',0))
    _kv(lines,'VM security attestation',r.get('security_attestation'))
    lines+=['','## 2. Repository intelligence (Phase 1)','']
    _kv(lines,'Project name',intel.get('project_name','UNKNOWN'))
    _kv(lines,'Architecture confidence',intel.get('architecture_confidence','UNKNOWN'))
    table(lines,('Language','Files'),[(k,v) for k,v in sorted((intel.get('languages') or {}).items())])
    table(lines,('Technology','Category','Confidence'),[
        (t.get('name'),t.get('category'),t.get('confidence')) for t in intel.get('technologies',[])])
    table(lines,('Service','Kind','Technology','Source root','Confidence'),[
        (s.get('name'),s.get('kind'),s.get('technology'),s.get('root'),s.get('confidence'))
        for s in intel.get('services',[])])
    lines+=['### Discovered project features','']
    table(lines,('Feature','Origin','Confidence'),[(x.get('name'),x.get('origin'),x.get('confidence'))
          for x in intel.get('features',[])])
    lines+=['### Discovered routes and endpoints (not verified)','']
    table(lines,('Method','Path','Evidence origin','Confidence'),[
          (x.get('method'),x.get('path'),x.get('kind'),x.get('confidence'))
          for x in intel.get('routes',[])])
    lines+=['### Existing source test artifacts','']
    table(lines,('Path','Kind','Framework'),[(x.get('path'),x.get('kind'),x.get('framework'))
          for x in intel.get('test_artifacts',[])])
    lines+=['### Setup and database inventory','']
    for k in ('setup_files','database_schema_files','environment_variable_names'):
        _kv(lines,k.replace('_',' ').title(),', '.join(map(str,intel.get(k,[]))) or 'NONE_DETECTED')
    lines+=['','### Source-analysis findings','']
    table(lines,('Code','Severity','Observation'),[(f.get('code'),f.get('severity'),f.get('message'))
          for f in intel.get('findings',[])])
    lines+=['### Remaining uncertainties','']
    for x in intel.get('unknowns',[]):lines.append('- '+esc(x))
    if not intel.get('unknowns'):lines.append('_No additional unknowns reported by Phase 1._')
    lines+=['','**Full detail:** `phase1/manifest.json` and `phase1/manifest.md`'
            ' (routes, features, setup/environment-variable names, source evidence, file roles).','',
            '## 3. Environment and runtime capability planning (Phase 2)','']
    for k,v in (r.get('runtime_plan') or {}).items():_kv(lines,k.replace('_',' ').title(),v)
    lines+=['','**Approval boundary:** Planning cannot authorize execution. All reviewed tests '
            'require an exact source SHA, operator-owned configuration, approved oracle digests, '
            'and the independently enforced disposable VM boundary.','',
            '**Full detail:** `phase2/phase2a_plan.json`, `phase2b_preflight.json`, '
            '`phase2c_candidate.json`, `phase2d_capabilities.json`.','',
            '## 4. Test planning and coverage inventory (Phase 3)','']
    _kv(lines,'Plan status',plan.get('status','NOT_AVAILABLE'))
    for k,v in (plan.get('summary') or {}).items():
        if type(v) in (str,int,float,bool):_kv(lines,k.replace('_',' ').title(),v)
    lines+=['','### Every generated test specification','']
    testrows=[]
    for c in plan.get('cases',[]):
        target=c.get('target') or {}
        oracle=c.get('oracle') or {}
        testrows.append((c.get('id'),c.get('category'),c.get('title'),
                         c.get('status'),str(target.get('method',''))+' '+str(target.get('path','')),
                         oracle.get('status','NOT_ESTABLISHED')))
    table(lines,('Test ID','Category','Purpose','Plan state','Target','Oracle'),testrows)
    lines+=['### Grounded Phase 3 assertions (specifications, not observed behavior)','']
    assertions=[]
    for c in plan.get('cases',[]):
        for a in c.get('assertions',[])[:32]:
            if type(a) is dict:
                assertions.append((c.get('id'),a.get('kind'),a.get('expected','—')))
    table(lines,('Test ID','Assertion','Expected'),assertions)
    lines+=['_A discovered route is not a verified working endpoint. `READY_FOR_PHASE4` '
            'is a test specification, not launch permission._','',
            '**Full detail:** `phase3/test_plan.json` includes assertions, inventory and source-evidence references.','',
            '## 5. Approved functional testing (Phase 4)','']
    matrix=r.get('test_case_matrix') or {}
    lines+=['### Consolidated test-case outcome matrix','']
    lines+=['_A discovered test case is PASSED or FAILED only when a verified Phase 4 '
            'observation explicitly matches its test ID. A ready plan is not a pass._','']
    testrows=[]
    for c in matrix.get('discovered_cases',[]):
        witnesses=c.get('evidence') or []
        refs=', '.join(str(w.get('report_path') or w.get('suite')) for w in witnesses)
        actual=', '.join(str(w.get('result',w.get('status','—')))+
                         (' (HTTP '+str(w['http_status'])+')' if w.get('http_status') is not None else '')
                         for w in witnesses)
        expected=', '.join(str(a.get('kind'))+'='+str(a.get('expected'))
                           for a in c.get('expected_assertions',[]) if type(a) is dict)
        testrows.append((c.get('test_id'),c.get('category'),c.get('planning_status'),
                         c.get('outcome'),expected or 'NOT_ESTABLISHED',actual or 'NOT_OBSERVED',
                         c.get('reason'),refs or '—'))
    table(lines,('Test ID','Category','Plan state','Final outcome','Expected','Observed','Reason','Evidence report'),testrows)
    counts=matrix.get('counts') or {}
    lines+=['**Discovered-case totals:** '+', '.join(str(counts.get(k,0))+' '+k
             for k in ('PASSED','FAILED','INCONCLUSIVE','BLOCKED','NOT_TESTED'))+'.','']
    lines+=['### Additional independently reviewed checks (not mapped to a discovered ID)','']
    table(lines,('Suite','Check ID','Outcome','Reason','Evidence report'),[
        (c.get('suite'),c.get('check_id'),c.get('outcome'),c.get('reason'),
         ', '.join(str(e.get('report_path') or '—') for e in c.get('evidence',[])))
        for c in matrix.get('reviewed_only_checks',[])])
    if matrix.get('reviewed_checks_truncated'):
        lines+=['_Additional reviewed check rows omitted from this bounded summary: '+
                esc(matrix['reviewed_checks_truncated'])+'; consult original hashed Phase 4 evidence._','']
    lines+=['_A reviewed check with no matching Phase 3 ID is deliberately listed separately '
            'rather than used to inflate discovered-case coverage._','']
    table(lines,('Suite','Outcome','Runner report status','VM verified','Cleanup','Source'),[
        (s.get('kind'),s.get('state'),s.get('result_status','—'),
         'YES' if s.get('vm_verified') else 'NO',s.get('cleanup','—'),
         'REUSED (not rerun)' if s.get('reused_from_prior') else 'CURRENT') for s in suites])
    if not suites:
        lines+=['_No approved Phase 4 test execution evidence supplied for this submission._','']
    for suite in suites:
        if suite.get('vm_preflight_reason'):
            _kv(lines,'VM preflight blocker ('+esc(suite.get('kind'))+')',
                suite['vm_preflight_reason'])
    if root is not None:
        for suite in suites:
            rel=suite.get('report_path')
            if not rel or suite.get('state') in ('REVIEW_REQUIRED','BLOCKED'):
                continue
            try:
                if not rel.startswith('phase4/') or '..' in rel.split('/'):
                    continue
                obj=read_json(root.joinpath(*rel.split('/')),limit=8_000_000)
            except (OSError,ValueError):continue
            lines+=['### Suite: '+esc(suite.get('kind')),'',
                    '- **Validated report status:** '+esc(obj.get('status')),
                    '- **Evidence report:** `'+esc(rel)+'`',
                    '- **VM execution:** '+esc(obj.get('execution')),
                    '- **Cleanup:** '+esc(obj.get('cleanup','NOT_REPORTED')),'']
            if suite.get('vm_preflight_reason'):
                _kv(lines,'VM preflight blocker',suite['vm_preflight_reason'])
            summary=obj.get('summary')
            if type(summary) is dict:
                for k,v in summary.items():
                    if type(v) in (int,str,bool):_kv(lines,k.replace('_',' ').title(),v)
            rows=obj.get('results') or obj.get('checks') or obj.get('scenarios') or []
            if type(rows) is list:
                table(lines,('Test / Check','Result','HTTP status','Issue / evidence'),[
                    (x.get('test_id',x.get('id',x.get('step','—'))),
                     x.get('result',x.get('status','UNKNOWN')),
                     x.get('http_status','—'),
                     x.get('issue_code',x.get('reason_code','—')))
                    for x in rows[:400] if type(x) is dict])
            for limitation in obj.get('limitations',[])[:12]:
                lines.append('- Limitation: '+esc(limitation))
            lines.append('')
    lines+=['**Important:** The saved reviewed oracle catalogs are deliberately not reproduced '
            'here because they may contain credentials or fixture values. '
            'Their SHA-256 approvals and bounded guest observations remain in the phase artifacts.','',
            '## 6. Static code quality (Phase 5)','']
    qscore=quality.get('quality') or {}
    _kv(lines,'Static analysis status',quality.get('status','NOT_AVAILABLE'))
    _kv(lines,'Maintainability indicator',qscore.get('score','NOT_ASSESSED'))
    _kv(lines,'Indicator confidence',qscore.get('confidence','NONE'))
    _kv(lines,'Indicator basis',qscore.get('basis','NOT_A_GRADE'))
    _kv(lines,'Coverage status',qscore.get('status','NOT_AVAILABLE'))
    _kv(lines,'Reason codes',', '.join(qscore.get('reason_codes') or []) or 'none')
    coverage=quality.get('coverage') or {}
    lines+=['','### Coverage and measuring limitations','']
    for key in ('source_ratio','function_file_ratio','function_body_ratio',
                'production_function_body_ratio','ast_complexity_share','duplicate_production_ratio'):
        if key in coverage:_kv(lines,key.replace('_',' ').title(),coverage[key])
    lines+=['','### Code-quality findings (file and line evidence)','']
    table(lines,('Severity','Code','Location','Confidence','Recommendation'),[
        (x.get('severity'),x.get('code'),str(x.get('path'))+':'+str(x.get('line')),
         x.get('confidence'),x.get('recommendation')) for x in quality.get('findings',[])])
    lines+=['### Recognized function/complexity inventory','']
    table(lines,('Function','Location','Complexity','Nesting','Big-O','Estimate basis'),[
        (x.get('name'),str(x.get('path'))+':'+str(x.get('start_line')),
         x.get('cyclomatic'),x.get('max_nesting'),x.get('big_o'),x.get('big_o_reason'))
        for x in quality.get('functions',[])[:100]])
    if len(quality.get('functions',[]))>100:
        lines.append('_Additional function records available in `phase5/code_quality.json`._')
    lines+=['','### Exact duplication indicators','']
    table(lines,('First location','Second location','Matched lines'),[
        (str(x.get('path_a'))+':'+str(x.get('line_a')),
         str(x.get('path_b'))+':'+str(x.get('line_b')),x.get('lines'))
        for x in quality.get('duplications',[])])
    lines+=['### Potential secret exposure indicators (values withheld)','']
    table(lines,('Pattern','File','Line','Confidence'),[
        (x.get('code'),x.get('path'),x.get('line','—'),x.get('confidence'))
        for x in (quality.get('security') or {}).get('findings',[])])
    lines+=['','**Scoring caution:** Phase 5 uses bounded heuristic analysis, not '
            'a comprehensive audit or a student rubric grade.','',
            '**Full detail:** `phase5/code_quality.json` and `phase5/code_quality.md`.','',
            '## 7. Evidence-backed diagnosis and triage (Phase 6)','']
    _kv(lines,'Diagnosis status',diagnosis.get('status','NOT_AVAILABLE'))
    table(lines,('Priority','Code','Category','Finding','Confidence','Causality'),[
        (d.get('priority'),d.get('code'),d.get('category'),d.get('title'),
         d.get('confidence'),d.get('causal_status')) for d in diagnosis.get('diagnoses',[])])
    lines+=['### Diagnosis details and recommended actions','']
    for d in diagnosis.get('diagnoses',[]):
        lines+=['#### '+esc(d.get('code')),'',
                '- **Interpretation:** '+esc(d.get('explanation')),
                '- **Action:** '+esc(d.get('recommended_action')),
                '- **Evidence IDs:** '+esc(', '.join(d.get('evidence',[]))),
                '- **Test IDs:** '+esc(', '.join(d.get('test_ids',[]))),
                '- **Student defect:** '+esc(d.get('student_defect','NOT_ESTABLISHED')),'']
    lines+=['### Unverified root-cause hypotheses','']
    table(lines,('Run','Test','Observed HTTP','Possibilities','Status'),[
        (h.get('run'),h.get('test_id'),h.get('observed_http_status'),
         ', '.join(h.get('candidate_codes',[])),h.get('status'))
         for h in diagnosis.get('root_cause_hypotheses',[])])
    lines+=['**Full detail:** `phase6/phase6_diagnosis.json` and '
            '`phase6/phase6_diagnosis.md`. Hypotheses are not proven defects.','',
            '## 8. Operator review requests','']
    table(lines,('Action needed','Stage','Exact approved identity'),[
        (i.get('type'),i.get('stage'),i.get('review_identity','—'))
        for i in r.get('review_requests',[])])
    lines+=['## 9. Consolidated recommendations (evidence-linked)','']
    table(lines,('Source','Priority','Issue','Proposed action','Causal status'),[
        (x.get('source'),x.get('priority'),x.get('code'),x.get('recommendation'),
         x.get('causal_status')) for x in r.get('recommendations',[])])
    lines+=['## 10. Workflow execution trace','']
    table(lines,('Step','Status','Reason'),[(x.get('stage'),x.get('status'),
          x.get('reason','—')) for x in r.get('workflow',[])])
    lines+=['## 11. Limitations, safety and interpretation','']
    for x in r.get('limitations',[]):lines.append('- '+esc(x))
    for x in r.get('error_codes',[]):lines.append('- Processing issue: `'+esc(x)+'`')
    lines+=['','- Evidence reports represent one exact Git commit, not all revisions or deployments.',
            '- An untested route, a missing oracle, and a failed reviewed assertion are distinct.',
            '- An approved VM pilot is not an independent attestation for arbitrary untrusted submissions.',
            '- Secret values, operator config, authentication cookies, fixture credentials and raw VM logs are not embedded.',
            '- Final rubric marks belong to the separate faculty-reviewed ProjectIQ Rubric Evaluator.','',
            '## 12. Evidence artifact inventory','']
    table(lines,('Artifact','SHA-256','Bytes'),[(k,v.get('sha256'),v.get('bytes'))
          for k,v in sorted((r.get('artifacts') or {}).items())])
    lines+=['','**Machine contract:** `executioner_result.json`; '
            'each Phase 1–6 subreport is retained and hash-indexed.','']
    return '\n'.join(lines)
