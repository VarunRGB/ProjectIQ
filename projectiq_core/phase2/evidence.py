"""Reconstruct Phase 1 evidence from receipts and a verified report snapshot.

No network, model calls, VM or execution of student source. The input directory
is a trusted local operator workspace, not an authenticated evidence store.
"""
from __future__ import annotations
import hashlib, json, re
from pathlib import Path, PureWindowsPath
from collections import Counter
from typing import Any
from ..contracts import Evaluation, AGENTS
from ..collector import read_bounded, load_json, validate_identity, MAX_JSON, MAX_REPORT
from ..adapters import normalize
from .contracts import Evidence, EvidenceBundle

MAX_TEXT=300_000
MAX_ITEMS=1500
MAX_ROWS=200
CLEAN='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
OUTCOMES=('PASSED','FAILED','INCONCLUSIVE','BLOCKED','NOT_TESTED')

def canonical(value):
    if hasattr(value,'model_dump'):value=value.model_dump(mode='json')
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')

def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def sha(data):return hashlib.sha256(data).hexdigest()
def obj(x):return x if isinstance(x,dict) else {}
def rows(x):return x if isinstance(x,list) else []
def clip(x,n=1200):return str(x)[:n] if isinstance(x,(str,int,float)) else ''
def valid_int(x):return type(x) is int and x>=0

def safe_child(root, relative):
    """Reject traversal and symlinks (including Windows junctions when available)."""
    p=Path(relative); w=PureWindowsPath(relative)
    if p.is_absolute() or w.drive or w.root or any(x in ('..','.') for x in p.parts) or '\\' in relative:
        raise ValueError('UNSAFE_ARTIFACT_PATH')
    candidate=root
    for part in p.parts:
        candidate=candidate/part
        if candidate.is_symlink() or getattr(candidate,'is_junction',lambda:False)():raise ValueError('LINKED_ARTIFACT')
    if not candidate.resolve().is_relative_to(root.resolve()):raise ValueError('ARTIFACT_OUTSIDE_RUN')
    return candidate


# Conservative heading detection: preserve the raw text and exact offsets.
_HEADING=re.compile(r"(?im)^\s*(?:(?:\d{1,2}\.)+\s+[^\n]{3,100}|(?:abstract|introduction|problem statement|objectives|scope|requirements|methodology|architecture|design|implementation|testing|validation|results|discussion|limitations|conclusion|references|bibliography|literature review)(?:\s*[:–-][^\n]{0,80})?)\s*$")

def section_chunks(text):
    heads=[(m.start(),m.group().strip()) for m in _HEADING.finditer(text) if m.group().strip()]
    boundaries=[(0,'Front matter')]+[(p,h) for p,h in heads if p>0]
    if heads and heads[0][0]==0:boundaries[0]=heads[0]
    for i,(start,heading) in enumerate(boundaries):
        stop=boundaries[i+1][0] if i+1<len(boundaries) else len(text)
        while start<stop:
            end=min(start+1600,stop)
            if end<stop:
                # Prefer paragraph, line, then word boundaries; never drop text.
                for separator in ('\n\n','\n',' '):
                    split=text.rfind(separator,start+500,end)
                    if split>=0:
                        end=split+len(separator);break
            if text[start:end].strip():yield start,end,text[start:end],heading[:120]
            start=end

def report_chunks(text):
    for start,end,excerpt,_ in section_chunks(text):yield start,end,excerpt


def safe_target(value):
    # Do not copy headers, cookies, credentials, request bodies or response bodies.
    v=obj(value)
    return {k:clip(v[k],240) for k in ('method','path','label','kind') if isinstance(v.get(k),str)}

def safe_assertions(value):
    result=[]
    for a in rows(value)[:30]:
        a=obj(a); entry={k:clip(a[k],160) for k in ('kind','type','result','status','operator') if isinstance(a.get(k),str)}
        # Numeric/boolean expectations and actuals are safe to retain; arbitrary payload values are not.
        for k in ('expected','actual','observed','passed'):
            if type(a.get(k)) in (int,float,bool):entry[k]=a[k]
        if entry:result.append(entry)
    return result

def observation_details(suites, matches, *, legacy_l=False):
    result=[]
    for si,oi,_,_ in matches[:20]:
        suite=obj(suites[si]);o=obj(rows(suite.get('observations'))[oi]);entry={'pointer':f'/suites/{si}/observations/{oi}','suite':clip(suite.get('kind'),80)}
        for k in ('test_id','id','step','result','status','method'):
            if isinstance(o.get(k),str):entry[k]=clip(o[k],120)
        for k in ('status_code','http_status','actual_status','expected_status','passed','affected_rows','row_count'):
            if type(o.get(k)) in (int,float,bool):entry[k]=o[k]
        if isinstance(o.get('target'),dict):entry['target']=safe_target(o['target'])
        if isinstance(o.get('path'),str):entry['path']=clip(o['path'],240)
        for k in ('assertions','expected_assertions','assertion_results','checks'):
            if isinstance(o.get(k),list):entry[k]=safe_assertions(o[k])
        if not legacy_l:
            # Strict numeric/boolean allowlist; no credentials, row contents, or raw responses.
            keys={'before_matches','after_matches','expected_fields_match','captured_matches_seed','discovery_http','discovery_matches','mutation_http','mutation_subset_match','remaining_matches','verification_http','before_count','create_http','create_success','created_db_count','created_db_fields_match','delete_http','delete_success','deleted_db_count','deleted_matches','id_is_valid','read_deleted_http','read_http','read_matches','read_updated_http','update_http','update_success','updated_db_count','updated_db_fields_match','updated_matches','updated_fields_match','read_updated_matches','read_updated_fields_match'}
            entry.update({k:o[k] for k in keys if type(o.get(k)) in (int,bool)})
            entry['suite_metrics']={k:v for k,v in obj(suite.get('observed_metrics')).items() if k in keys and type(v) in (int,bool)}
        result.append(entry)
    return result


def source_refs(row):
    result=[]
    for e in rows(row.get('evidence'))[:5]:
        e=obj(e);p=e.get('path');line=e.get('line')
        if isinstance(p,str) and p and len(p)<=500 and not PureWindowsPath(p).drive and not p.startswith(('/', '\\')) and '..' not in p.replace('\\','/').split('/'):
            result.append({'path':p,'line':line if type(line) is int and line>0 else None})
    # Phase 5 findings often carry top-level path and line.
    p=row.get('path');line=row.get('line')
    if isinstance(p,str) and p and len(p)<=500 and not PureWindowsPath(p).drive and not p.startswith(('/', '\\')) and '..' not in p.replace('\\','/').split('/'):
        result.append({'path':p,'line':line if type(line) is int and line>0 else None})
    return result


def build_evidence(evaluation_path: Path, *, acknowledge_imported=False, loader=None, legacy_research=False, legacy_l=False, legacy_l1=False):
    path=Path(evaluation_path).absolute();root=path.parent
    original=read_bounded(path,MAX_JSON)
    # Reuse Phase 1 duplicate-key/nonfinite parsing and typed contract.
    ev=Evaluation.model_validate(load_json(path))
    if sha(read_bounded(path,MAX_JSON))!=sha(original):raise ValueError('EVALUATION_CHANGED')
    if not ev.submission.report_name or Path(ev.submission.report_name).name!=ev.submission.report_name or PureWindowsPath(ev.submission.report_name).name!=ev.submission.report_name:
        raise ValueError('INVALID_REPORT_NAME')
    report=safe_child(root,'input/'+ev.submission.report_name)
    if sha(read_bounded(report,MAX_REPORT))!=ev.submission.report_sha256:raise ValueError('REPORT_DIGEST_MISMATCH')
    notes=['Receipt hashes verify saved bytes, not authenticity, accuracy or student authorship.',
           'Report excerpts establish what the extracted text says, not what the software actually does. Scans, tables and figures may be missed by the report loader.',
           'All output is private; treat displayed strings as untrusted text.']
    checks={};items=[];seen=set();observations={};raws={};provs={};commit=None
    def add(agent,kind,label,text,artifact,locator,provenance,eligible=False,limitations=None,data=None):
        if len(items)>=MAX_ITEMS:
            if 'EVIDENCE_ITEM_LIMIT_REACHED' not in notes:notes.append('EVIDENCE_ITEM_LIMIT_REACHED')
            return
        kw=dict(agent=agent,kind=kind,label=clip(label,240),text=clip(text,2000),artifact_sha256=artifact,
                locator=locator,provenance=provenance,eligible_for_review=eligible and provenance!='IMPORTED_UNCONFIRMED',
                limitations=limitations or [],data=data or {})
        e=Evidence(id='E-'+digest(kw)[:24],**kw)
        if e.id in seen:raise ValueError('DUPLICATE_EVIDENCE_ID')
        seen.add(e.id);items.append(e)
    from .hardening import suspicious, VERSION
    legacy_l=legacy_l or legacy_research
    text='';extracted_hash=None;extraction='UNAVAILABLE';suspect=[]
    try:
        if loader is None:
            from projectiq_summarizer.loaders import load_text
            loader=load_text
        full=loader(report)
        if not isinstance(full,str) or not full.strip():raise ValueError('EMPTY_EXTRACTED_TEXT')
        extracted_hash=sha(full.encode('utf-8'));text=full[:MAX_TEXT]
        if not legacy_l:suspect=suspicious(full)
        extraction='COMPLETE' if len(full)<=MAX_TEXT else 'TRUNCATED'
        if extraction=='TRUNCATED':notes.append('Report evidence capped; omitted text is not evidence of absence.')
        for start,end,excerpt,section in section_chunks(text):
            add('report','REPORT_EXCERPT',f'{section} | Report characters {start}–{end}',excerpt,ev.submission.report_sha256,
                f'extracted_text:{start}:{end}','REPORT_SNAPSHOT',True,
                ['Text claim, not independently verified fact.'],{'start':start,'end':end,'extracted_text_sha256':extracted_hash,'section':section})
    except Exception:
        notes.append('Report text extraction unavailable; report-dependent grading coverage may be incomplete.')
    if not legacy_l:
        observations['evaluation_policy']={'version':'L1' if legacy_l1 else VERSION,'suspect_spans':suspect,'automatic_judgment':'REVIEW_REQUIRED' if suspect else 'EVIDENCE_CONSTRAINED'}
        if suspect:notes.append('Evaluator-directed report content detected; automated qualitative judgments withheld. This is not a misconduct conclusion.')
    if sha(read_bounded(report,MAX_REPORT))!=ev.submission.report_sha256:raise ValueError('REPORT_CHANGED')
    for agent in AGENTS:
        a=ev.agents[agent]
        if a.status in ('FAILED','NOT_RUN'):
            checks[agent]=a.status;continue
        try:
            r=a.native
            if r is None or r.path!=f'native/{agent}.json' or r.origin!=ev.mode:raise ValueError('INVALID_RECEIPT')
            f=safe_child(root,r.path);b=read_bounded(f,MAX_JSON)
            if sha(b)!=r.sha256:raise ValueError('NATIVE_DIGEST_MISMATCH')
            raw=load_json(f)
            if sha(read_bounded(f,MAX_JSON))!=r.sha256:raise ValueError('NATIVE_CHANGED')
            if agent=='executioner' and ev.mode=='IMPORTED' and not ev.submission.requested_ref:raise ValueError('IMPORTED_EXECUTIONER_REQUIRES_PINNED_SHA')
            validate_identity(agent,raw,ev.submission,imported=ev.mode=='IMPORTED')
            # Re-derive envelope: a modified score, method, metric, or state cannot silently enter Phase 2.
            if normalize(agent,raw,r).model_dump(mode='json')!=a.model_dump(mode='json'):raise ValueError('ENVELOPE_NATIVE_MISMATCH')
            checks[agent]='MATCHED'
            raws[agent]=raw
            provs[agent]=('NATIVE_RECEIPT_MATCHED' if ev.mode=='LIVE' else 'IMPORTED_ACKNOWLEDGED' if acknowledge_imported else 'IMPORTED_UNCONFIRMED')
        except Exception:
            checks[agent]='RECEIPT_OR_CONTRACT_MISMATCH'
            notes.append(f'{agent}: native verification failed; its evidence is withheld.')
    if ev.mode=='IMPORTED':notes.append('Imported results do not establish original report-byte identity. '+('Operator acknowledged association; still not independent authentication.' if acknowledge_imported else 'Imported agent evidence is advisory until explicitly acknowledged.'))
    def native_add(agent,kind,label,txt,pointer,eligible=False,limits=None,data=None):
        add(agent,kind,label,txt,ev.agents[agent].native.sha256,pointer,provs[agent],eligible,limits,data)
    if 'summarizer' in raws:
        raw=raws['summarizer'];summary=obj(raw.get('summary'))
        native_add('summarizer','REPORT_SUMMARY','Generated summary',summary.get('summary',''),'/summary/summary',limits=['Model-generated paraphrase; not a grading evidence source.'])
        for i,p in enumerate(rows(summary.get('key_points'))[:30]):
            native_add('summarizer','REPORT_SUMMARY','Generated key point',p,f'/summary/key_points/{i}',limits=['Model-generated claim; check the report.'])
        legacy=[]
        for i,c in enumerate(rows(raw.get('criteria'))[:50]):
            c=obj(c);q=c.get('evidence_quote');found=False
            if isinstance(q,str) and 12<=len(q)<=2000 and text:
                parts=q.split()
                if parts:found=bool(re.search(r'\s+'.join(re.escape(part) for part in parts),text))
            legacy.append({'criterion_id':clip(c.get('criterion_id'),80),'quote_found':found if extraction=='COMPLETE' or found else None})
            native_add('summarizer','LEGACY_JUDGEMENT','Legacy report judge quote',q,f'/criteria/{i}/evidence_quote',limits=['Legacy score and recommendation excluded from Phase 2 grading.'],data={'quote_found':legacy[-1]['quote_found']})
        observations['summarizer']={'summary_only':raw.get('result_kind')=='SUMMARY_ONLY','legacy_quote_checks':legacy,'summary':summary}
    if 'integrity' in raws:
        raw=raws['integrity'];a=ev.agents['integrity'];words=raw.get('words_checked');comparable=bool(rows(raw.get('references'))) and valid_int(words) and words>0
        metrics={m.id:m.value for m in a.metrics}
        cats={k:(v if comparable and type(v) in (int,float) and 0<=v<=100 else None) for k,v in obj(raw.get('percent_by_category')).items()}
        observations['integrity']={'comparison_status':'AVAILABLE' if comparable and metrics.get('reference_overlap') is not None else 'NOT_AVAILABLE',
            'reference_overlap':metrics.get('reference_overlap') if comparable else None,'copied_chunks':metrics.get('copied_chunks') if comparable else None,
            'category_percentages':cats,'words_checked':words,'references_compared':metrics.get('references_compared'),
            'embedding_status':'NOT_APPLICABLE_NO_REFERENCE_CORPUS' if not comparable else clip(obj(raw.get('backends')).get('embeddings')),
            'ai_detection':a.ai_detection.model_dump(mode='json'),'advisory_only':True}
        native_add('integrity','INTEGRITY_ADVISORY','Integrity coverage','Reference overlap and AI indicators require human interpretation. No automatic deductions.','/',limits=['No authorship or misconduct conclusion.'],data={'comparison_available':comparable})
        for i,row in enumerate(rows(raw.get('ai_notes'))[:20]):native_add('integrity','INTEGRITY_ADVISORY','Native AI heuristic',row,f'/ai_notes/{i}',limits=['Advisory, not an AI percentage or proof.'])
    if 'research' in raws:
        raw=raws['research'];r=obj(raw.get('research_report'));p1=obj(r.get('phase_1'));p4=obj(r.get('phase_4'))
        observations['research']={'references_found':p1.get('references_found'),'literature_review_found':p1.get('literature_review_found'),
            'reference_entries':p1.get('reference_entries'),'recency_assessment':p4.get('recency_assessment'),
            'recent_reliable_percent':p4.get('recent_reliable_percent') if (p4.get('reliable_publication_year_count') or 0)>0 else None,
            'applicability':'TEACHER_RUBRIC_DECIDES'}
        if legacy_research:native_add('research','RESEARCH_RECORD','Bibliography extraction',f"Extracted entries: {p1.get('reference_entries')}; literature review found: {p1.get('literature_review_found')}",'/research_report/phase_1',True,['Extraction is heuristic; absence may be acceptable for this assignment.'])
        for field in (('reference_overview',) if legacy_research else ()):
            for i,row in enumerate(rows(r.get(field))[:MAX_ROWS]):
                native_add('research','RESEARCH_RECORD','Reference observation',canonical(row).decode()[:1500],f'/research_report/{field}/{i}',True,['Metadata, relevance and factual claim support are different.'])
    if 'research' in raws and not legacy_research:
        from .research_policy import normalize_research
        raw=raws['research'];r=raw['research_report']
        research=normalize_research(raw,report,text,extraction,ev.submission.repository_url,legacy_l=legacy_l)
        observations['research']['assessment']=research
        native_add('research','RESEARCH_RECORD','Native research assessment',research['reason'], '/research_report',True,
            research['limitations'],{'record_type':'research_assessment',**research})
        if len(rows(r.get('reference_overview')))>MAX_ROWS:notes.append('Research reference details capped; aggregate counts retained, not full citation coverage.')
        for i,row in enumerate(rows(r.get('reference_overview'))[:MAX_ROWS]):
            if not isinstance(row,dict):continue
            native_add('research','RESEARCH_RECORD','Reference '+str(i+1)+': '+clip(row.get('title'),160),canonical(row).decode()[:1500],f'/research_report/reference_overview/{i}',True,
                ['Native metadata and heuristic relevance; not proof of citation-to-claim support.'],{'record_type':'reference','reference_number':i+1,**{k:row[k] for k in ('verification_status','relevance_classification','confidence','publication_year','year_source','age_band','cited_in_literature_review',*(() if legacy_l else ('supporting_engineering_connection','citation_context_linked'))) if k in row}})
    if 'executioner' in raws:
        raw=raws['executioner'];p=obj(raw.get('project_intelligence'));quality=obj(raw.get('code_quality'));matrix=obj(raw.get('test_case_matrix'));source=obj(raw.get('source'));commit=source.get('commit_sha')
        hold=raw.get('status')=='SECURITY_HOLD' or raw.get('effective_mode') not in ('REVIEWED','RESUME') or not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit)
        observations['executioner']={'commit_sha':commit,'effective_mode':raw.get('effective_mode'),'server_endpoints':p.get('server_endpoint_count'),
            'features_discovered':p.get('features_discovered'),'planned_counts':matrix.get('counts'),
            'maintainability':obj(quality.get('quality')).get('score') if obj(quality.get('quality')).get('status')=='PROVISIONAL' else None,
            'security_attestation':raw.get('security_attestation'),'reviewed_check_counts':matrix.get('reviewed_check_counts')}
        if not legacy_l:
            qs=obj(quality.get('summary'));qc=obj(quality.get('coverage'));indicator=obj(quality.get('quality'))
            summary={k:qs[k] for k in ('analyzed_files','production_files','production_code_lines','functions_found','findings','source_candidates') if valid_int(qs.get(k))}
            observations['executioner']['source_analysis']={'summary':summary,'coverage':qc,'indicator_status':indicator.get('status'),'confidence':indicator.get('confidence'),'overall_score':indicator.get('score') if indicator.get('status')=='PROVISIONAL' and indicator.get('confidence')!='LOW' else None,'not_grade':True}
            if summary.get('analyzed_files',0)>0:
                native_add('executioner','STATIC_SOURCE','Source analysis inventory',canonical(summary).decode(),'/code_quality/summary',raw.get('status')!='SECURITY_HOLD',['Source inventory is not evidence of engineering quality or successful behavior.'],{'record_type':'source_inventory',**summary,'commit_sha':commit})
        for field,records,pointer in [('features',rows(p.get('features')),'/project_intelligence/features'),('endpoints',rows(p.get('server_endpoints')),'/project_intelligence/server_endpoints'),('findings',rows(p.get('findings')),'/project_intelligence/findings'),('quality',rows(quality.get('findings')),'/code_quality/findings')]:
            if len(records)>MAX_ROWS:notes.append(f'Executioner {field} capped; not complete coverage.')
            for i,row in enumerate(records[:MAX_ROWS]):
                row=obj(row);label=clip(row.get('name') or row.get('code') or row.get('path') or field)
                txt=clip(row.get('message') or row.get('name') or row.get('path') or label)
                native_add('executioner','STATIC_SOURCE',label,txt,f'{pointer}/{i}',raw.get('status')!='SECURITY_HOLD',
                    ['Native static observation or documented feature; not verified functional behavior.'],{'commit_sha':commit,'source_locations':source_refs(row),'method':clip(row.get('intended_method') or row.get('method')),'origin':clip(row.get('origin')),'record_type':field,'code':clip(row.get('code'),120),'severity':clip(row.get('severity'),40),'confidence':clip(row.get('confidence'),40),'category':clip(row.get('category'),80),'recommendation':clip(row.get('recommendation'),500),'target':safe_target(row),'description':clip(row.get('description'),500)})
        # Only exact test IDs plus a clean verified native suite observation can be decisive.
        suites=rows(raw.get('suites')); all_observations={}
        for sidx,s in enumerate(suites):
            s=obj(s)
            if hold or s.get('vm_verified') is not True or s.get('cleanup')!=CLEAN or s.get('state') not in ('PASS','FAIL','INCONCLUSIVE'):continue
            for oidx,o in enumerate(rows(s.get('observations'))):
                o=obj(o);tid=o.get('test_id');result=o.get('result',o.get('status'))
                if isinstance(tid,str):all_observations.setdefault(tid,[]).append((sidx,oidx,result,s.get('state')))
        plan_by_id={}
        for spec in rows(obj(raw.get('test_plan')).get('cases')):
            if isinstance(spec,dict) and isinstance(spec.get('id'),str):plan_by_id.setdefault(spec['id'],[]).append(spec)
        cases=rows(matrix.get('discovered_cases')); counts=Counter(c.get('test_id') for c in cases if isinstance(c,dict) and isinstance(c.get('test_id'),str))
        if len(cases)>MAX_ROWS:notes.append('Test case evidence capped; missing tests remain unresolved.')
        for i,row in enumerate(cases[:MAX_ROWS]):
            row=obj(row);tid=row.get('test_id');outcome=row.get('outcome');category=row.get('category')
            matching=all_observations.get(tid,[]) if isinstance(tid,str) else []
            expected={'PASSED':'PASS','FAILED':'FAIL'}.get(outcome) if isinstance(outcome,str) else None
            specs=plan_by_id.get(tid,[]) if isinstance(tid,str) else []
            exact_spec=(len(specs)==1 and specs[0].get('category')==category and specs[0].get('target')==row.get('target')
                        and (specs[0].get('assertions') or [])==(row.get('expected_assertions') or [])
                        and row.get('scope')=='PHASE3_DISCOVERED_CASE')
            decisive=bool(expected and exact_spec and counts.get(tid)==1 and matching and all(o[2]==expected and not (expected=='FAIL' and o[3]=='PASS') for o in matching))
            if decisive and category=='HTTP_READINESS':
                native_add('executioner','TEST_PLAN',clip(row.get('title') or tid),f'{outcome} — readiness assertion only',f'/test_case_matrix/discovered_cases/{i}',limits=['Verified startup/readiness is not functional evidence and cannot support functional marks.'],data={'test_id':tid,'reported_outcome':outcome,'category':category,'scope':'READINESS_ONLY'})
            elif decisive:
                native_add('executioner','TEST_OBSERVATION',clip(row.get('title') or tid),f'{outcome} — exact planned test',f'/test_case_matrix/discovered_cases/{i}',True,
                    ['Native verified suite and matching exact observation; assertion outcome, not whole-project correctness.'],{'test_id':tid,'category':category,'outcome':outcome,'commit_sha':commit,'scope':'EXACT_PLANNED_TEST','observations':[f'/suites/{s}/observations/{o}' for s,o,_,_ in matching],'target':safe_target(row.get('target')),'expected_assertions':safe_assertions(row.get('expected_assertions')),'observation_details':observation_details(suites,matching,legacy_l=legacy_l)})
            else:
                native_add('executioner','TEST_PLAN',clip(row.get('title') or tid),f'{outcome} — {clip(row.get("reason"))}',f'/test_case_matrix/discovered_cases/{i}',limits=['Unexecuted, inconclusive or not independently matched to a verified observation; no marks.'],data={'test_id':tid,'reported_outcome':outcome,'category':category})
        # Reviewed-only scenarios remain distinct. They can inform a reviewer, never exact test-ratio scoring.
        reviewed_rows=rows(matrix.get('reviewed_only_checks'))
        reviewed_counts=Counter((r.get('suite'),r.get('check_id')) for r in reviewed_rows if isinstance(r,dict) and isinstance(r.get('suite'),str) and isinstance(r.get('check_id'),str))
        if len(reviewed_rows)>MAX_ROWS:notes.append('Reviewed scenario evidence capped; missing tests remain unresolved.')
        for i,row in enumerate(reviewed_rows[:MAX_ROWS]):
            row=obj(row);outcome=row.get('outcome');check=row.get('check_id');kind=row.get('suite');expected={'PASSED':'PASS','FAILED':'FAIL'}.get(outcome) if isinstance(outcome,str) else None
            matches=[]
            if isinstance(check,str) and isinstance(kind,str) and expected and not hold:
                for si,s in enumerate(suites):
                    s=obj(s)
                    if s.get('kind')!=kind or s.get('vm_verified') is not True or s.get('cleanup')!=CLEAN or s.get('state') not in ('PASS','FAIL','INCONCLUSIVE'):continue
                    for oi,o in enumerate(rows(s.get('observations'))):
                        o=obj(o)
                        if o.get('test_id',o.get('id',o.get('step')))==check:matches.append((si,oi,o.get('result',o.get('status')),s.get('state')))
            good=bool(isinstance(kind,str) and isinstance(check,str) and 'readiness' not in kind.lower() and reviewed_counts[(kind,check)]==1 and matches and all(m[2]==expected and not(expected=='FAIL' and m[3]=='PASS') for m in matches))
            native_add('executioner','TEST_OBSERVATION' if good else 'TEST_PLAN',f'Reviewed scenario: {clip(check)}',f'{clip(outcome)} — {clip(kind)}',f'/test_case_matrix/reviewed_only_checks/{i}',good,
                ['Reviewed scenario only; never establishes whole-route or exact planned-test coverage.'],{'scope':'REVIEWED_SCENARIO_ONLY','check_id':check,'suite':kind,'outcome':outcome,'commit_sha':commit,'observations':[f'/suites/{s}/observations/{o}' for s,o,_,_ in matches],'target':safe_target(row.get('target')),'expected_assertions':safe_assertions(row.get('expected_assertions')),'observation_details':observation_details(suites,matches,legacy_l=legacy_l)})
    if not legacy_l and 'summarizer' in raws and extraction=='COMPLETE':
        from .hardening import grounded_summary
        observations['summarizer']['grounded_summary']=grounded_summary(text, legacy_l1=legacy_l1)
        observations['summarizer']['summary_display_method']='EXTRACTIVE_REPORT_CLAIMS'
    if not legacy_l and 'executioner' in raws:
        ex=observations['executioner'];raw=raws['executioner']
        ex['unavailable_suites']=[{'suite':clip(s.get('kind'),80),'state':clip(s.get('state'),80),'reason':clip(s.get('reuse_refusal') or s.get('reason') or s.get('vm_preflight_reason') or s.get('result_status'),160)} for s in rows(raw.get('suites')) if isinstance(s,dict) and s.get('state') not in ('PASS','FAIL')]
        ex['planned_counts_scope']='NATIVE_REPORTED_NOT_ELIGIBILITY'
        if 'trusted_demo' in raw:
            # Receipt normalization already checked the extension. Display only;
            # never emit an eligible TEST_OBSERVATION or modify reviewed counts.
            demo=raw['trusted_demo']
            inv=obj(demo.get('functional_inventory'))
            ex['trusted_demo']={'status':demo.get('status'),'selected_path':demo.get('selected_path'),
                'vm_start_attempts':demo.get('vm_start_attempts'),'vm_attempt_count_uncertain':demo.get('vm_attempt_count_uncertain'),
                'summary':inv.get('summary'),'workflow_status':obj(inv.get('workflow')).get('status'),
                'scope':'CHARACTERIZATION_DISPLAY_ONLY','grade_credit':None}
    return EvidenceBundle(evaluation_sha256=sha(original),submission_id=ev.submission.id,report_sha256=ev.submission.report_sha256,
        extracted_text_sha256=extracted_hash,report_extraction=extraction,repository_commit=commit,agent_checks=checks,items=items,observations=observations,limitations=notes)
