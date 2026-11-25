"""Sequential local collection, or explicit import of operator-selected JSON artifacts.

All outputs are private local data. This is not a server or a sandbox. STATIC is
 the only live Executioner mode exposed by this phase.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from hashlib import sha256
from urllib.parse import urlparse
import json, os, re
from typing import Callable
from .contracts import AGENTS, Evaluation, Submission, Receipt
from .adapters import normalize, skipped, AdapterError

MAX_JSON = 64 * 1024 * 1024
MAX_REPORT = 64 * 1024 * 1024
REPO = re.compile(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)/?\Z')
SHA = re.compile(r'[a-fA-F0-9]{40}\Z')


def read_bounded(path: Path, limit: int) -> bytes:
    with path.open('rb') as f:
        data=f.read(limit+1)
    if len(data)>limit: raise ValueError('INPUT_SIZE_LIMIT')
    return data


def _pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise ValueError('DUPLICATE_JSON_KEY')
        out[k]=v
    return out


def _constant(_): raise ValueError('NONFINITE_JSON_NUMBER')


def load_json(path: Path):
    return json.loads(read_bounded(path, MAX_JSON).decode('utf-8-sig'),object_pairs_hook=_pairs,parse_constant=_constant)


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')


def write_new(path: Path, data: bytes):
    with path.open('xb') as stream: stream.write(data)


def validate_identity(agent, raw, submission, *, imported=False):
    if not isinstance(raw,dict): raise AdapterError('EXPECTED_OBJECT')
    if agent=='executioner':
        if raw.get('stage')!='executioner_evaluation': raise AdapterError('NOT_EXECUTIONER_RESULT')
        if not imported and raw.get('submission_id')!=submission.id: raise AdapterError('SUBMISSION_ID_MISMATCH')
        # Imported run IDs often differ from the combined evaluation ID. Record native ID.
        src=raw.get('source')
        if src is None and raw.get('status')=='FAILED' and not imported: return
        m=REPO.fullmatch(submission.repository_url or '')
        if not m or not isinstance(src,dict) or src.get('kind')!='github': raise AdapterError('MISSING_REPOSITORY_IDENTITY')
        if str(src.get('repository','')).lower()!='/'.join(m.groups()).lower(): raise AdapterError('REPOSITORY_IDENTITY_MISMATCH')
        commit=src.get('commit_sha')
        if not isinstance(commit,str) or not SHA.fullmatch(commit): raise AdapterError('MISSING_PINNED_COMMIT')
        if submission.requested_ref and commit.lower()!=submission.requested_ref.lower(): raise AdapterError('COMMIT_MISMATCH')
        if not imported and raw.get('effective_mode')!='STATIC': raise AdapterError('LIVE_EXECUTIONER_NOT_STATIC')
    else:
        key={'summarizer':'name','integrity':'document','research':'source'}[agent]
        name=raw.get(key)
        if not isinstance(name,str) or PureWindowsPath(name.replace('/','\\')).name.casefold()!=submission.report_name.casefold():
            raise AdapterError('REPORT_FILENAME_MISMATCH')


def make_submission(report: Path, title: str, submission_id: str, repo: str | None, ref: str | None):
    if report.suffix.lower() not in ('.txt','.md','.pdf','.docx'): raise ValueError('UNSUPPORTED_REPORT_TYPE')
    if repo and (not REPO.fullmatch(repo) or '..' in repo): raise ValueError('INVALID_GITHUB_URL')
    if ref and (not repo or not SHA.fullmatch(ref)): raise ValueError('REF_MUST_BE_EXACT_SHA')
    data=read_bounded(report,MAX_REPORT)
    if not data: raise ValueError('EMPTY_REPORT')
    submission=Submission(id=submission_id,project_title=title,report_name=report.name,
        report_sha256=sha256(data).hexdigest(),repository_url=repo,requested_ref=ref.lower() if ref else None)
    return submission,data


def collect(*, submission: Submission, report_bytes: bytes, out: Path, mode: str,
            sources: dict[str,Callable[[Path,Path],dict]], progress=print) -> Evaluation:
    """Call each configured source once, sequentially. A failed module does not erase other results."""
    if mode not in ('LIVE','IMPORTED') or set(sources)-set(AGENTS): raise ValueError('INVALID_COLLECTION')
    if sha256(report_bytes).hexdigest()!=submission.report_sha256: raise ValueError('REPORT_DIGEST_MISMATCH')
    if Path(submission.report_name).name!=submission.report_name or '\\' in submission.report_name: raise ValueError('INVALID_REPORT_NAME')
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    native=out/'native';native.mkdir()
    snapshot=out/'input';snapshot.mkdir();report=snapshot/submission.report_name
    write_new(report,report_bytes)
    results={}
    cancelled=False
    for agent in AGENTS:
        progress(f'{agent}: starting' if agent in sources else f'{agent}: not requested')
        if agent not in sources:
            results[agent]=skipped(agent,note='Agent not requested or required input unavailable.')
            continue
        receipt=None
        try:
            raw=sources[agent](report,out)
            validate_identity(agent,raw,submission,imported=mode=='IMPORTED')
            data=json_bytes(raw)
            if len(data)>MAX_JSON: raise ValueError('NATIVE_RESULT_SIZE_LIMIT')
            path=native/f'{agent}.json';write_new(path,data)
            receipt=Receipt(path=f'native/{agent}.json',sha256=sha256(data).hexdigest(),origin=mode)
            result=normalize(agent,raw,receipt)
            results[agent]=result
        except KeyboardInterrupt:
            cancelled=True
            results[agent]=skipped(agent,'FAILED','CANCELLED','Agent interrupted; completed native receipts remain available.',receipt)
            break
        except Exception as exc:
            # No arbitrary exception text in teacher-facing JSON.
            code=str(exc) if isinstance(exc,AdapterError) and re.fullmatch('[A-Z0-9_]{1,80}',str(exc)) else type(exc).__name__
            results[agent]=skipped(agent,'FAILED',code,'Agent result unavailable or incompatible; inspect locally.',receipt)
        progress(f'{agent}: {results[agent].status}')
    if cancelled:
        for agent in AGENTS:
            results.setdefault(agent,skipped(agent,note='Collection cancelled before this agent started.'))
    limitations=['No final project grade is calculated in Phase 1.', 'Native receipts attest saved bytes only, not agent correctness or authenticity.',
                 'Private evaluation contains student text and code evidence; do not publish the output directory.']
    if mode=='IMPORTED': limitations.append('Imported results are operator-associated by filename/repository; report bytes at their original execution are not independently established.')
    result=Evaluation(submission=submission,created_utc=datetime.now(timezone.utc).isoformat(),mode=mode,agents=results,limitations=limitations)
    write_new(out/'evaluation.json',json_bytes(result.model_dump(mode='json')))
    if cancelled:
        write_new(out/'collection_cancelled.json',json_bytes({'state':'CANCELLED','completed_receipts':[a for a,v in results.items() if v.status not in ('NOT_RUN','FAILED')],'recovery':'Use saved evaluation in a new output directory; unavailable agents require explicit recollection.'}))
        raise KeyboardInterrupt
    return result


def live_sources(submission, *, references: Path|None=None, agents=AGENTS, rubric: Path|None=None,
                 samples: int=1, verify_research=False, summary_only: bool=False):
    """Lazy imports keep missing model dependencies isolated. No reviewed VM path."""
    if summary_only and rubric is not None: raise ValueError('LEGACY_RUBRIC_NOT_ALLOWED_IN_SUMMARY_ONLY')
    if not 1<=samples<=5: raise ValueError('JUDGE_SAMPLES_OUT_OF_RANGE')
    # Local model weights should be prepared by the operator; don't silently download.
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    def summarize(report,out):
        if summary_only:
            from time import perf_counter
            from projectiq_summarizer.loaders import load_text
            from .phase2.hardening import grounded_summary
            start=perf_counter();text=load_text(report)
            if not isinstance(text,str) or not text.strip():raise ValueError('EMPTY_REPORT_TEXT')
            print('summarizer: grounded extractive summary (no legacy judge or model request)',flush=True)
            write_new(out/'ollama_release.json',json_bytes({'status':'NOT_USED'}))
            return {'result_kind':'SUMMARY_ONLY','name':report.name,'model':'grounded-extractive-L1',
                    'summary':grounded_summary(text),'word_count':len(text.split()),'chunks':1,
                    'elapsed_s':round(perf_counter()-start,4),'collector_resource_lifecycle':{'ollama_release':'NOT_USED'}}
        from projectiq_summarizer.config import Settings
        from projectiq_summarizer.llm import OllamaClient
        from projectiq_summarizer.loaders import load_text
        s=Settings(judge_samples=samples)
        if urlparse(s.ollama_url).hostname not in ('localhost','127.0.0.1','::1'): raise AdapterError('OLLAMA_MUST_BE_LOCAL')
        from .resources import release_ollama_model
        llm=OllamaClient(s)
        checked=False
        try:
            llm.check();checked=True
            from projectiq_summarizer.pipeline import evaluate
            from projectiq_summarizer.rubric import load_rubric
            raw=evaluate(load_text(report),report.name,llm,s,load_rubric(str(rubric) if rubric else None),progress=print).model_dump(mode='json')
        finally:
            release=release_ollama_model(llm) if checked else 'RELEASE_NOT_ATTEMPTED'
            print('summarizer: Ollama '+release,flush=True)
            try:
                write_new(out/'ollama_release.json',json_bytes({'status':release}))
            except OSError:
                print('summarizer: resource receipt unavailable',flush=True)
            try:
                llm.http.close()
            except Exception:
                pass
        # Collector annotation, not an LLM-generated finding.
        return {**raw,'collector_resource_lifecycle':{'ollama_release':release}}
    def integrity(report,out):
        from projectiq_integrity.document_check import check_document
        # No bibliography PDFs are required for AI-writing checks. Use a private,
        # empty corpus directory so the native agent can still analyze the report.
        corpus = references
        if corpus is None:
            corpus = out / 'empty_reference_corpus'
            corpus.mkdir(exist_ok=False)
        return check_document(report,corpus).to_dict()
    def research(report,out):
        from projectiq_research.agent import analyze_research_document
        return analyze_research_document(report,verify=verify_research).to_dict()
    def executioner(report,out):
        from projectiq_executioner.orchestration import evaluate
        return evaluate({'submission_id':submission.id,'repository_url':submission.repository_url,'ref':submission.requested_ref,
                         'mode':'STATIC','max_vm_launches':0},output_dir=out/'executioner_artifacts')
    funcs={'summarizer':summarize,'integrity':integrity,'research':research,'executioner':executioner}
    return {a:f for a,f in funcs.items() if a in agents and (a!='executioner' or submission.repository_url)}
