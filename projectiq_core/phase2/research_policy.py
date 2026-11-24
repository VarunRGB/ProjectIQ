"""Research grading is driven by matched native evidence, not general project prose.

Absence is provisional and deliberately conservative. Unverified identity, age,
missing comparison corpora and heuristic relevance are not misconduct findings.
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit

ABSENT='NO_SOURCES_DETECTED'
PRESENT='SOURCES_PRESENT'
UNKNOWN='RESEARCH_EVIDENCE_INCONCLUSIVE'


def is_research(c):
    return c.method=='JUDGMENT' and (c.group=='research' or c.focus=='RESEARCH' or c.id=='research')


def same_repo(url, repo):
    try:
        u,r=urlsplit(url),urlsplit(repo or '')
        path=u.path.rstrip('/').removesuffix('.git').casefold();root=r.path.rstrip('/').removesuffix('.git').casefold()
        own=path==root or bool(re.fullmatch(re.escape(root)+r'/(?:tree|commit)/[a-f0-9]{40}',path))
        return (u.scheme in ('https','http') and u.hostname=='github.com' and
                r.hostname=='github.com' and own and not u.query and not u.fragment)
    except (ValueError,TypeError,AttributeError):return False


def source_signals(text,repo):
    # A bare project repository link is not a bibliography. Any other link,
    # DOI or citation-like marker prevents automatic absence classification.
    urls=re.findall(r'https?://[^\s<>\[\]()]+',text,re.I)
    return (bool(re.search(r'^\s*(?:#{1,6}\s+|\d+(?:\.\d+)*[.)]?\s+)?(?:works cited|references|bibliography|literature review|related work|sources|further reading)\s*:?[ \t]*$',text,re.I|re.M)) or any(not same_repo(u.rstrip('.,;:'),repo) for u in urls) or
            bool(re.search(r'\b(?:doi\s*:|10\.\d{4,9}/|www\.|arxiv\s*:|isbn\s*[:\d])|\[\s*\d+(?:\s*[,;–-]\s*\d+)*\s*\]|\([A-Z][^\n()]{1,70},\s*(?:19|20)\d{2}[a-z]?\)',text,re.I)))


def extraction_usable(report,text,extraction):
    if extraction!='COMPLETE' or not text.strip() or '\ufffd' in text:return False
    # A partially scanned PDF can have nonempty aggregate text. Never certify
    # absence where a page has no text. Figures/embedded images remain a caveat.
    if report.suffix.lower()=='.pdf':
        try:
            from pypdf import PdfReader
            return all((p.extract_text() or '').strip() for p in PdfReader(str(report)).pages)
        except Exception:return False
    return True


def normalize_research(raw,report,text,extraction,repo):
    r=raw['research_report'];p=r['phase_1'];v=r['phase_2'];rel=r['phase_3'];rec=r['phase_4']
    n=p.get('reference_entries');overview=r.get('reference_overview');papers=raw.get('papers');links=raw.get('hyperlinks')
    def count(x):return type(x) is int and x>=0
    fields=('reference_entries','document_citation_mentions','literature_review_citation_mentions','reference_match_records','matched_reference_entries','hyperlinks_found')
    complete=all(count(p.get(k)) for k in fields) and all(type(p.get(k)) is bool for k in ('references_found','literature_review_found'))
    pairs={'reference_entries':'papers','document_citation_mentions':'document_citations','literature_review_citation_mentions':'literature_citations','reference_match_records':'citation_reference_matches','hyperlinks_found':'hyperlinks'}
    complete=complete and all(isinstance(raw.get(key),list) and len(raw[key])==p[field] for field,key in pairs.items()) and p['matched_reference_entries']<=n
    usable=extraction_usable(report,text,extraction)
    state=UNKNOWN;reason='Native research extraction is incomplete, conflicting or unavailable.'
    # Positive records require actual matched per-reference observations, not
    # a count alone. Online verification is optional, not a precondition.
    if (usable and complete and n>0 and p['references_found'] is True and isinstance(overview,list) and len(overview)==n and
        isinstance(papers,list) and len(papers)==n and all(isinstance(x,dict) for x in papers) and
        all(isinstance(x,dict) and type(x.get('reference_number')) is int and x['reference_number']==i+1 and isinstance(x.get('title'),str) and x['title'].strip() for i,x in enumerate(overview))):
        state=PRESENT;reason='Native extracted reference records are available for contextual review.'
    elif (usable and complete and n==0 and p['references_found'] is False and p['literature_review_found'] is False
        and all(p[k]==0 for k in fields[1:-1]) and isinstance(papers,list) and not papers and overview==[]
        and all(raw.get(k)==[] for k in ('document_citations','literature_citations','citation_reference_matches'))
        and isinstance(links,list) and len(links)==p['hyperlinks_found'] and all(isinstance(x,dict) and same_repo(x.get('url'),repo) for x in links)
        and not source_signals(text,repo)
        and isinstance(raw.get('notes'),list)
        and not any(re.search(r'no text was extracted|image.only|scan(?:ned)?|ocr|extract(?:ion)? failed',str(x),re.I) for x in raw.get('notes',[]))):
        state=ABSENT;reason='Matched Research Agent detected no literature review, bibliography, reference entries or in-text citations; report text contains no conflicting source signals.'
    def pick(d,keys):return {k:d[k] for k in keys if k in d}
    recency=pick(rec,('recency_assessment','distinct_references','duplicate_reference_count','reliable_publication_year_count','recent_reliable_percent','verified_direct_count','verified_direct_recent_count','literature_review_reliable_count','literature_review_recent_percent'))
    if not count(rec.get('reliable_publication_year_count')) or rec['reliable_publication_year_count']==0:recency['recent_reliable_percent']=None
    if not count(rec.get('literature_review_reliable_count')) or rec['literature_review_reliable_count']==0:recency['literature_review_recent_percent']=None
    return {'state':state,'reason':reason,'text_extraction_usable':usable,
            'phase_1':pick(p,fields+('references_found','literature_review_found')),
            'verification':pick(v,('checked_references','status_counts')),
            'relevance':pick(rel,('assessed_references','classification_counts')),
            'recency':recency,
            'limitations':['Text extraction and source detection are heuristic; figures and image-only content can be missed.','Metadata identity, relevance and citation-to-claim support are distinct; unverified or older sources are not automatically invalid.']}


def research_record(bundle):
    if bundle.agent_checks.get('research')!='MATCHED':return None
    found=[e for e in bundle.items if e.agent=='research' and e.kind=='RESEARCH_RECORD' and e.eligible_for_review and e.data.get('record_type')=='research_assessment']
    return found[0] if len(found)==1 else None


def research_state(bundle):
    e=research_record(bundle)
    return e.data.get('state',UNKNOWN) if e else UNKNOWN


def required_kinds(c):
    return list(dict.fromkeys([*c.required_evidence,*(['RESEARCH_RECORD'] if is_research(c) else [])]))


def reference_item(e):
    return e.agent=='research' and e.kind=='RESEARCH_RECORD' and e.data.get('record_type')=='reference'


def relevant_report(e):
    if e.kind!='REPORT_EXCERPT':return False
    section=str(e.data.get('section','')).lower()
    return bool(re.search(r'literature|bibliograph|references|related work|prior work|sources|citations',section) or re.search(r'\[\s*\d+\s*\]|\bdoi\s*:|10\.\d{4,9}/|\([A-Z][^\n()]{1,70},\s*(?:19|20)\d{2}[a-z]?\)|\b(?:cited|citation|bibliography|literature review|prior work|related work)\b',e.text,re.I))


def validate_model_research(bundle,c,refs,rationale,feedback):
    if not is_research(c):return
    if research_state(bundle)!=PRESENT:raise ValueError('RESEARCH_NATIVE_EVIDENCE_UNAVAILABLE')
    if not any(reference_item(e) for e in refs):raise ValueError('RESEARCH_REFERENCE_EVIDENCE_REQUIRED')
    if any(e.kind=='REPORT_EXCERPT' and not relevant_report(e) for e in refs):raise ValueError('RESEARCH_REPORT_EVIDENCE_IRRELEVANT')
    texts=[rationale]+([feedback.improvement] if feedback else [])
    if not all(re.search(r'\b(?:research|sources?|citations?|cited|references?|bibliograph\w*|literature|papers?|metadata|relevance|recency|publication\w*|synthesis|doi|authors?)\b',txt,re.I) for txt in texts):raise ValueError('RESEARCH_FEEDBACK_OFF_TOPIC')
