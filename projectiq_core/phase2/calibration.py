"""Local, opt-in qualitative calibration; no real student submissions or VM runs.

A reference band is a deliberately authored acceptance expectation, not a
faculty-validated ground truth. This exercises the production proposal/scoring
path, not a second grading implementation. Preview never contacts Ollama.
"""
from __future__ import annotations
import argparse,json,time,sys
from pathlib import Path
from urllib.parse import urlparse
from .contracts import Evidence,EvidenceBundle,Rubric
from .evidence import sha,section_chunks,digest
from .scoring import assess,empty_decisions
from .suggest import propose,recover_rejected
from ..collector import write_new,json_bytes

IDS=('problem_scope','report_quality','testing_strategy')
# Same project, deliberately different quality of documentation. No facts about
# executed behavior are claimed; these fixtures test qualitative discrimination.
CASES={
'weak': '''1. Problem statement\nWe made a website. It helps people.\n2. Implementation\nThere is code and it works.\n3. Testing\nWe will test it someday.\n4. Conclusion\nIt is good.''',
'moderate': '''1. Problem statement\nStudents need a shared way to record tasks and deadlines. The application lets users create, edit and delete their own tasks. It is intended for a small class.\n2. Architecture\nA browser interface calls a server which stores users and tasks in a relational database.\n3. Testing\nCreate a task and check that it appears. Delete it and check that it disappears. Try invalid login credentials and expect rejection. No tests have been executed in this report.\n4. Conclusion\nThe project is a prototype. The report does not include performance targets or detailed test setup.''',
'strong': '''1. Problem statement\nStudents in a class currently track group tasks in separate messages, making deadlines and ownership difficult to discover. The intended users are students and course coordinators. The objective is to provide a shared list with ownership, deadlines and completion status; each user can modify only their own tasks and coordinators can view group progress. Acceptance targets are successful execution of each documented workflow and rejection of every unauthorized mutation in the agreed test matrix. The prototype is limited to a single course and synthetic accounts; offline access and notifications are out of scope.\n2. Architecture\nBrowser forms send JSON to server routes; server-side session and role checks precede mutations. The database separates users and tasks using a foreign key, and parameterized queries separate input from SQL. A relational model was chosen for ownership constraints rather than a document store; the trade-off is schema migration effort.\n3. Testing\nUse a fresh test database seeded with two students and one coordinator. Use separate sessions and reset state before each case. R1: create a task as student A; expect HTTP 201 and exactly one row owned by A. R2: edit and mark it complete; expect HTTP 200 and the intended fields changed, no other rows changed. R3: delete the task; expect HTTP 200 and no remaining matching row. R4: attempt invalid login; expect 401 and no session. R5: student B attempts to mutate A's task; expect 403 and unchanged database. R6: coordinator reads both students' tasks; expect the two seeded records. Record request status and row counts, repeat against the pinned revision. These are proposed cases, not executed results. Browser accessibility and load testing remain out of scope.\n4. Conclusion\nThe proposed requirements and test oracles cover the stated prototype scope. The report makes no claim of completed execution, performance benchmarks or production readiness.'''
}
# Deliberately broad acceptance bands, defined before inference. Fractions are
# tied to the bundled rubric's exact descriptors. No score is rewritten to fit.
BANDS={'weak':(0,.5),'moderate':(.25,.75),'strong':(.5,1)}


def calibration_rubric():
    raw=json.loads(Path(__file__).with_name('minicollab_hybrid.rubric.json').read_text(encoding='utf-8'))
    raw['id']='qualitative-calibration';raw['title']='Qualitative calibration (synthetic)';raw['group_weights']={}
    raw['criteria']=[c for c in raw['criteria'] if c['id'] in IDS]
    for c in raw['criteria']:c['weight']=1
    return Rubric.model_validate(raw)


def calibration_bundle(name):
    text=CASES[name];h=sha(text.encode('utf-8'));items=[]
    for a,z,t,heading in section_chunks(text):
        items.append(Evidence(id='E-'+digest([h,a,z])[:24],agent='report',kind='REPORT_EXCERPT',label=heading,text=t,
            artifact_sha256=h,locator=f'extracted_text:{a}:{z}',provenance='REPORT_SNAPSHOT',eligible_for_review=True,
            limitations=['Synthetic calibration text; no runtime claim.'],data={'section':heading,'start':a,'end':z}))
    return EvidenceBundle(evaluation_sha256=digest(['calibration-v1',name,h]),submission_id='calibration-'+name,
        report_sha256=h,extracted_text_sha256=h,report_extraction='COMPLETE',agent_checks={},items=items,observations={},
        limitations=['Authored synthetic calibration fixture; not a student submission or authenticated receipt.'])


def evaluate_calibration(results):
    checks=[]
    for cid in IDS:
        levels={name:next(c.fraction for c in results[name].criteria if c.id==cid) for name in CASES}
        complete=all(v is not None for v in levels.values())
        checks.append({'criterion_id':cid,'fractions':levels,'bands':BANDS,
            'band_pass':complete and all(BANDS[k][0]<=v<=BANDS[k][1] for k,v in levels.items()),
            'ordering_pass':complete and levels['weak']<=levels['moderate']<=levels['strong'] and levels['weak']<levels['strong'],
            'feedback_review_flags':{name:next(c.review_flags for c in results[name].criteria if c.id==cid) for name in CASES}})
    return {'status':'PASS' if all(x['band_pass'] and x['ordering_pass'] for x in checks) else 'CALIBRATION_REVIEW_REQUIRED','checks':checks,
            'limitation':'Synthetic contrast checks are not faculty calibration or proof of semantic accuracy.'}


def run_calibration(llm=None,*,preview=False,max_chars=18000,seed=42,progress=lambda _:None):
    rubric=calibration_rubric();results={};cases={};started=time.perf_counter();failure=None
    for name,text in CASES.items():
        b=calibration_bundle(name);log={};base=empty_decisions(b,rubric)
        progress('Calibration: '+name)
        try:
            d=propose(b,rubric,llm,existing=base,criterion_ids=list(IDS),preview=preview,diagnostics=log,max_chars=max_chars,seed=seed,progress=progress)
            if not preview:
                d,failed=recover_rejected(b,rubric,llm,d,log,max_chars=max_chars,seed=seed,progress=progress)
                if failed:failure='RECOVERY_INFERENCE_FAILED'
        except (ValueError,RuntimeError,OSError) as exc:
            d=base;failure=type(exc).__name__
        a=assess(b,rubric,d);results[name]=a
        cases[name]={'report':text,'assessment':a.model_dump(mode='json'),'diagnostics':log}
        if failure:break
    summary={'status':'INFERENCE_FAILED','error_type':failure} if failure else {'status':'PREVIEW_ONLY'} if preview else evaluate_calibration(results)
    return {**summary,'elapsed_seconds':round(time.perf_counter()-started,4),'cases':cases}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--preview',action='store_true');g.add_argument('--live',action='store_true');a=p.parse_args(argv)
    if a.out.exists():print('Output already exists.',file=sys.stderr);return 1
    llm=None;checked=False;release='NOT_REQUESTED'
    try:
        if a.live:
            from projectiq_summarizer.config import Settings
            from projectiq_summarizer.llm import OllamaClient
            from ..resources import release_ollama_model
            settings=Settings(judge_samples=1);u=urlparse(settings.ollama_url)
            if u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1','::1') or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:raise ValueError('OLLAMA_MUST_BE_LOCAL')
            llm=OllamaClient(settings);llm.check();checked=True
            report=run_calibration(llm,max_chars=min(18000,max(2000,(settings.num_ctx-2500)*3)),seed=settings.seed,progress=lambda s:print(s,flush=True))
        else:report=run_calibration(preview=True,progress=lambda s:print(s,flush=True))
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        report={'status':'INFERENCE_FAILED','error_type':type(exc).__name__}
    finally:
        if llm is not None:
            try:
                if checked:release=release_ollama_model(llm)
            finally:llm.http.close()
    report['model_release']=release
    a.out.mkdir(parents=True,exist_ok=False);write_new(a.out/'calibration.json',json_bytes(report))
    print('Calibration:',report['status']);print('Output:',a.out/'calibration.json')
    return 0 if report['status'] in ('PASS','PREVIEW_ONLY') else 1

if __name__=='__main__':raise SystemExit(main())
