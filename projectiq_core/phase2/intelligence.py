"""Deterministic evidence intelligence; no inference, no automatic severity deductions."""
from collections import Counter, defaultdict
import re
from .research_policy import is_research, required_kinds, reference_item, relevant_report

_STOP=set('the and for with from that this are was were assess assessment project report evidence relevant whether quality'.split())
_SEVERITY={'CRITICAL':4,'HIGH':3,'MEDIUM':2,'LOW':1,'INFO':0,'WARNING':2,'ERROR':3}
_SECTION_HINTS={
 'REPORT':('abstract','introduction','problem','objective','scope','requirement','conclusion','limitation'),
 'DESIGN':('architecture','design','database','interface','methodology','technology','trade-off'),
 'IMPLEMENTATION':('implementation','architecture','database','security','limitation','improvement'),
 'FUNCTIONAL':('test','validation','result','requirement','limitation','scope'),
 'TEST_STRATEGY':('test','validation','result','requirement','limitation','scope'),
 'SECURITY':('security','authorization','authentication','limitation','implementation'),
 'MAINTAINABILITY':('implementation','architecture','design','limitation'),
 'STYLE':('implementation','coding','convention'),
 'RESEARCH':('literature','reference','bibliograph','methodology','related work'),
}

def tokens(s):return set(re.findall(r'[a-zA-Z][a-zA-Z0-9_]{2,}',s.lower()))-_STOP

def focus(c):
    if c.focus!='GENERAL':return c.focus
    return {'problem_scope':'REPORT','report_quality':'REPORT','technical_design':'DESIGN','implementation_quality':'IMPLEMENTATION','functional_validation':'FUNCTIONAL','research':'RESEARCH'}.get(c.id,'GENERAL')

def runtime_key(e):
    if e.kind!='TEST_OBSERVATION' or not e.eligible_for_review:return None
    data=e.data
    if data.get('outcome') not in ('PASSED','FAILED'):return None
    if data.get('scope')=='EXACT_PLANNED_TEST' and data.get('category')!='HTTP_READINESS' and isinstance(data.get('test_id'),str):return 'exact:'+data['test_id']
    if data.get('scope')=='REVIEWED_SCENARIO_ONLY' and isinstance(data.get('suite'),str) and isinstance(data.get('check_id'),str) and 'readiness' not in data['suite'].lower():return 'reviewed:'+data['suite']+':'+data['check_id']
    return None

def runtime_inventory(bundle):
    """Only uniquely identified, pinned, eligible decisive observations. Conflicts never counted."""
    groups=defaultdict(list)
    for e in bundle.items:
        key=runtime_key(e)
        if key and bundle.repository_commit and e.data.get('commit_sha')==bundle.repository_commit:groups[key].append(e)
    decisive={k:v[0] for k,v in groups.items() if len(v)==1}
    ambiguous=sorted(k for k,v in groups.items() if len(v)!=1)
    # Different namespaces may refer to the very same native assertion: do not double count.
    by_pointer=defaultdict(set)
    for k,e in decisive.items():
        for pointer in e.data.get('observations',[]):
            if isinstance(pointer,str):by_pointer[(e.artifact_sha256,pointer)].add(k)
    overlaps=sorted({k for keys in by_pointer.values() if len(keys)>1 for k in keys})
    # Keep exact and reviewed observations separate in display, but forbid reuse of the same native pointer for objective marks.
    return decisive,ambiguous,overlaps

def runtime_summary(bundle):
    items,ambiguous,overlaps=runtime_inventory(bundle)
    by_scope={}
    for scope in ('EXACT_PLANNED_TEST','REVIEWED_SCENARIO_ONLY'):
        selected=[e for e in items.values() if e.data['scope']==scope]
        counts=Counter(e.data['outcome'] for e in selected)
        by_scope[scope]={'passed':counts['PASSED'],'failed':counts['FAILED'],'decisive':len(selected),'pass_rate':counts['PASSED']/len(selected) if selected else None}
    groups=defaultdict(Counter)
    for e in items.values():groups[str(e.data.get('suite') or e.data.get('category') or 'unknown')][e.data['outcome']]+=1
    return {'by_scope':by_scope,'scenario_groups':{k:dict(v) for k,v in sorted(groups.items())},'ambiguous_keys':ambiguous,'overlapping_keys':overlaps,
            'planned_counts':bundle.observations.get('executioner',{}).get('planned_counts'),
            'requirements_coverage':None,'limitations':['Counts describe eligible scoped observations only; exact and reviewed scopes must not be summed as independent tests.','No agreed requirements mapping is implied. Untested does not mean failed.']}

def severity(e):return _SEVERITY.get(str(e.data.get('severity','')).upper(),-1)

def static_group(e):
    if e.kind!='STATIC_SOURCE':return None
    if e.data.get('record_type') in ('findings','quality') or e.data.get('code'):
        return str(e.data.get('code') or e.label).upper()
    return None

def select_candidates(bundle,c):
    allowed=[e for e in bundle.items if e.eligible_for_review and e.kind in c.allowed_evidence]
    f=focus(c);
    if is_research(c):
        f='RESEARCH'
        allowed=[e for e in allowed if reference_item(e) or relevant_report(e)]
    if f=='FUNCTIONAL':
        inventory,_,_=runtime_inventory(bundle);valid={e.id for e in inventory.values()}
        allowed=[e for e in allowed if e.kind!='TEST_OBSERVATION' or e.id in valid]
    terms=tokens(c.title+' '+c.description);hints=_SECTION_HINTS.get(f,())
    def rank(e):
        section=str(e.data.get('section','')).lower();relevance=len(terms & tokens(e.label+' '+e.text));heading=sum(h in section for h in hints)
        sev=severity(e) if e.kind=='STATIC_SOURCE' else -1
        if f in ('IMPLEMENTATION','SECURITY','MAINTAINABILITY','STYLE'):
            priority= (70+sev*10 if sev>=2 else 45 if e.kind=='STATIC_SOURCE' and static_group(e) is None else 0 if static_group(e) else 30) + heading*8
            code=static_group(e) or ''; desc=(code+' '+e.label+' '+e.text).lower()
            if e.kind=='STATIC_SOURCE':
                if f=='SECURITY':priority+=45 if re.search(r'sec_|security|auth|credential|secret|injection|exception|input|xss|html',desc) else -25
                if f=='MAINTAINABILITY':priority+=45 if re.search(r'complex|duplicat|maintain|structure|function|length|module',desc) else -15
                if f=='STYLE':priority=85 if re.search(r'whitespace|format|style|indent|naming|convention|lint',desc) else 25
                if e.data.get('record_type')=='endpoints':priority-=15
                if e.data.get('origin')=='readme':priority-=10
        elif f=='DESIGN':priority=(45 if e.kind=='REPORT_EXCERPT' else 35 if static_group(e) is None else 5)+heading*12
        elif f=='FUNCTIONAL':priority=100 if e.kind=='TEST_OBSERVATION' else 30+heading*10 if e.kind=='REPORT_EXCERPT' else 5
        elif f=='RESEARCH':priority=100 if reference_item(e) else 50+heading*12+relevance*3
        elif f in ('REPORT','TEST_STRATEGY'):
            priority=30+heading*12+relevance*3 if e.kind=='REPORT_EXCERPT' else 20
            if f=='TEST_STRATEGY' and e.kind=='REPORT_EXCERPT' and any(x in section for x in ('test','validation')):priority+=60
        else:priority=0
        return (-priority,-relevance,e.id)
    ranked=sorted(allowed,key=rank);best=[];locators=set();codes=set()
    def add(e):
        key=(e.artifact_sha256,e.locator);code=static_group(e)
        if key in locators or (code and code in codes):return
        best.append(e);locators.add(key)
        if code:codes.add(code)
    # All eligible decisive runtime evidence participates in the summary. Prefer all individual
    # references when bounded; prioritize failures and diverse suites if more than 20.
    if f=='FUNCTIONAL':
        inventory,_,_=runtime_inventory(bundle)
        tests=[e for e in inventory.values() if e in allowed]
        by_group=defaultdict(list)
        for e in sorted(tests,key=lambda x:(x.data['outcome']!='FAILED',x.id)):
            by_group[str(e.data.get('suite') or e.data.get('category'))].append(e)
        ordered=[]
        # Failed observations first, then round-robin suites (avoid one suite crowding out others).
        ordered.extend(e for e in tests if e.data['outcome']=='FAILED')
        for i in range(max(map(len,by_group.values()),default=0)):
            ordered.extend(v[i] for _,v in sorted(by_group.items()) if i<len(v))
        for e in ordered:
            if len(best)>=20:break
            add(e)
        # Keep space for required non-runtime kinds (if any).
        for kind in required_kinds(c):
            if kind!='TEST_OBSERVATION' and not any(e.kind==kind for e in best):
                if len(best)>=20:
                    removed=best.pop();locators.remove((removed.artifact_sha256,removed.locator))
                item=next((e for e in ranked if e.kind==kind),None)
                if item:add(item)
    for kind in required_kinds(c):
        if not any(e.kind==kind for e in best):
            item=next((e for e in ranked if e.kind==kind),None)
            if item:add(item)
    if f in ('IMPLEMENTATION','SECURITY','MAINTAINABILITY','STYLE'):
        # Keep the high-priority finding and limitations, but reserve a genuine
        # implementation section and a substantive source feature if available.
        positive_sections=('implementation','architecture','database','design','interface')
        report_context=[e for e in ranked if e.kind=='REPORT_EXCERPT' and any(x in str(e.data.get('section','')).lower() for x in positive_sections) and not any(x in str(e.data.get('section','')).lower() for x in ('limitation','future improvement'))]
        if report_context:
            report_context.sort(key=lambda e:(not ('implementation' in str(e.data.get('section','')).lower()),rank(e)))
            add(report_context[0])
        feature=next((e for e in ranked if e.kind=='STATIC_SOURCE' and static_group(e) is None and e.data.get('record_type')!='endpoints' and e.text.strip()!=e.label.strip()),None)
        if feature:add(feature)
    if f=='TEST_STRATEGY':
        plan=next((e for e in ranked if e.kind=='REPORT_EXCERPT' and any(x in str(e.data.get('section','')).lower() for x in ('test','validation'))),None)
        if plan:add(plan)
    if f=='FUNCTIONAL':limit=min(20,max(len(best)+1,5,c.minimum_evidence))
    else:limit=max(7 if f in ('IMPLEMENTATION','SECURITY','MAINTAINABILITY') else 6 if f=='STYLE' else 5,c.minimum_evidence,len(best))
    for e in ranked:
        if len(best)>=limit:break
        add(e)
    return allowed,best

def prompt_item(e,allowed,*,location_limit=6):
    result={'kind':e.kind,'label':e.label,'text':e.text}
    limits=[s for s in e.limitations if s!='Text claim, not independently verified fact.']
    if limits:result['limitations']=limits
    if e.kind=='STATIC_SOURCE':
        for k in ('record_type','severity','confidence','category','origin','method','recommendation','description','target'):
            if e.data.get(k):result[k]=e.data[k]
        code=static_group(e)
        if code:
            matches=[x for x in allowed if static_group(x)==code]
            result['finding_instances']=len(matches)
            # Representative locations do not turn duplicates into independent issues.
            result['source_locations']=[loc for x in matches for loc in x.data.get('source_locations',[])][:location_limit]
        elif e.data.get('source_locations'):result['source_locations']=e.data['source_locations'][:3]
    if e.kind=='RESEARCH_RECORD':
        result.update(e.data)
    if e.kind=='TEST_OBSERVATION':
        for k in ('scope','outcome','suite','category','check_id','test_id','target','expected_assertions','observation_details'):
            if e.data.get(k):result[k]=e.data[k]
        if 'observation_details' in result:
            details=result['observation_details'];result['observation_details']=[{**o,**{k:v[:4] for k,v in o.items() if k in ('assertions','expected_assertions','assertion_results','checks') and isinstance(v,list)}} for o in details[:3]]
            result['observation_details_note']='Bounded prompt detail; full normalized details in evidence.json.'
    return result

def failure_claim(text):
    # Deliberately narrow lexical check; semantic truth still requires teacher review.
    for sentence in re.split(r'[.!?;]',text.lower()):
        if re.search(r'\b(?:no|not|without|zero|cannot|unverified|unknown|potential|possible|whether)\b',sentence):continue
        if re.search(r'\b(?:tests? failed|failed tests?|failing behavio[u]?r|observed failures?|confirmed failures?|runtime failures?|functional failures?)\b',sentence):return True
    return False

def consistency(bundle,c,level,refs,rationale,feedback=None):
    if any(e.kind=='TEST_OBSERVATION' for e in refs):
        valid={e.id for e in runtime_inventory(bundle)[0].values()}
        if any(e.kind=='TEST_OBSERVATION' and e.id not in valid for e in refs):raise ValueError('AMBIGUOUS_RUNTIME_EVIDENCE')
    if focus(c)=='FUNCTIONAL' or level.requires_failed_observation or (feedback and feedback.observed_failure):
        observed_failed=any(e.kind=='TEST_OBSERVATION' and e.data.get('outcome')=='FAILED' for e in refs)
        claim=level.requires_failed_observation or bool(feedback and feedback.observed_failure) or failure_claim(rationale)
        if feedback:claim=claim or failure_claim(feedback.strength+';'+feedback.limitation)
        if claim and not observed_failed:raise ValueError('UNSUPPORTED_OBSERVED_FAILURE')
    if focus(c) in ('IMPLEMENTATION','SECURITY','MAINTAINABILITY') and level.fraction<=.5:
        static=[e for e in refs if e.kind=='STATIC_SOURCE']
        if static and all(static_group(e)=='TRAILING_WHITESPACE' or severity(e)==0 or severity(e)==1 for e in static):
            raise ValueError('MINOR_FINDINGS_ONLY')
    flags=[]
    if feedback:
        combined=' '.join((rationale,feedback.strength,feedback.limitation))
        for quote in re.findall(r'[“"]([^”"\n]{12,200})[”"]',combined):
            if not any(' '.join(quote.split()) in ' '.join(e.text.split()) for e in refs):
                raise ValueError('UNSUPPORTED_QUOTATION')
        if not all(v.strip() for v in (feedback.strength,feedback.limitation,feedback.improvement)):raise ValueError('EMPTY_STRUCTURED_FEEDBACK')
        descriptor=tokens(level.descriptor+' '+level.label)
        if tokens(rationale)<=descriptor:flags.append('RATIONALE_REPEATS_DESCRIPTOR')
        # Lexical checks are advisory: never replace faculty semantic judgment.
        words=tokens(feedback.strength+' '+feedback.limitation)
        specific=set().union(*(tokens(e.label+' '+e.text) for e in refs)) if refs else set()
        if len((words-descriptor)&specific)<2:flags.append('FEEDBACK_NEEDS_MORE_EVIDENCE_SPECIFIC_DETAIL')
        if re.search(r'(?i)\b(?:address (?:the )?(?:identified )?issues|improve (?:the )?quality|add more details)\b',feedback.improvement):flags.append('IMPROVEMENT_NEEDS_SPECIFIC_ACTION')
        if focus(c)=='TEST_STRATEGY' and level.fraction<=.25 and any('validation' in e.label.lower() or 'test' in e.label.lower() for e in refs):flags.append('REVIEW_LOW_TEST_STRATEGY_LEVEL_AGAINST_DOCUMENTED_PLAN')
    if focus(c) in ('IMPLEMENTATION','SECURITY','MAINTAINABILITY') and not any(e.kind=='TEST_OBSERVATION' and e.data.get('outcome')=='FAILED' for e in refs):
        text=rationale+' '+(feedback.limitation if feedback else '')
        if re.search(r'(?i)\b(?:confirmed exploit|exploit was confirmed|successfully exploited)\b',text):raise ValueError('UNSUPPORTED_RUNTIME_EXPLOIT_CLAIM')
    if focus(c)=='FUNCTIONAL':flags.append('REQUIREMENTS_COVERAGE_NOT_ESTABLISHED_BY_SCOPED_TESTS')
    if any(e.kind=='STATIC_SOURCE' and static_group(e) for e in refs):flags.append('STATIC_FINDINGS_REQUIRE_CONTEXTUAL_REVIEW')
    if feedback is None:flags.append('NO_STRUCTURED_FEEDBACK')
    return flags


def intelligence_report(bundle):
    inventory,_,_=runtime_inventory(bundle)
    findings=defaultdict(list)
    for e in bundle.items:
        code=static_group(e)
        if code and e.eligible_for_review:findings[code].append(e)
    integrity=bundle.observations.get('integrity',{})
    advisory={k:integrity[k] for k in ('comparison_status','reference_overlap','references_compared','words_checked','advisory_only') if k in integrity}
    return {'research':bundle.observations.get('research',{}).get('assessment'), 'integrity_advisory':advisory, 'runtime':runtime_summary(bundle),'runtime_observations':[{'key':k,'evidence_id':e.id,'outcome':e.data['outcome'],'details':e.data} for k,e in sorted(inventory.items())],
        'static_finding_groups':[{'code':k,'instances':len(es),'native_severities':sorted({str(e.data.get('severity') or 'UNSPECIFIED') for e in es}),'evidence_ids':[e.id for e in es]} for k,es in sorted(findings.items())],
        'limitations':['Static findings are not confirmed vulnerabilities; severity prioritizes review rather than directly deducting marks.','Report text extraction may omit figures or tables.']}


def evidence_details(bundle,ids):
    """Compact exact references for teacher-facing feedback; no new facts or marks."""
    by_id={e.id:e for e in bundle.items};result=[]
    for eid in ids:
        e=by_id[eid]
        row={'evidence_id':eid,'kind':e.kind,'label':e.label,'locator':e.locator,
             'artifact_sha256':e.artifact_sha256,'provenance':e.provenance,'text':e.text,
             'limitations':e.limitations}
        if e.kind=='STATIC_SOURCE':
            # Grouped siblings are contextual locations, not new selected citations.
            row.update({k:v for k,v in prompt_item(e,[x for x in bundle.items if x.eligible_for_review]).items() if k not in ('kind','label','text','limitations')})
        elif e.kind=='TEST_OBSERVATION':
            row.update({k:e.data[k] for k in ('scope','outcome','suite','check_id','test_id','target','observation_details') if k in e.data})
        elif e.kind=='RESEARCH_RECORD':
            row.update(e.data)
        elif e.kind=='REPORT_EXCERPT':
            row.update({k:e.data[k] for k in ('section','start','end') if k in e.data})
        result.append(row)
    return result
