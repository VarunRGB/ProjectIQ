"""Phase L: conservative grading checks over verified report/native evidence.

Heuristics establish review prerequisites, not semantic proof or misconduct.
No untrusted text becomes an instruction, a rubric, or a source of marks.
"""
from __future__ import annotations
import re
from collections import Counter

VERSION = 'L2'
# Broad evaluator-directed patterns; false positives trigger review, not penalties.
_INSTRUCTIONS = re.compile(r'(?is)\b(?:ignore|disregard|override|forget|bypass)\b[^\n.!?]{0,110}\b(?:instruction|rubric|system|previous|policy|criteria|grading)s?\b|\b(?:grader|evaluator|assessor|judge|grading (?:agent|system)|summari[sz]er)\b[^\n.!?]{0,180}\b(?:must|should|always|return|award|assign|give|select|output|mark|score|ignore|follow|instruction)\b|\b(?:award|assign|give|return|select|output)\b[^\n.!?]{0,100}\b(?:full marks|maximum marks|excellent|highest (?:level|score)|100\s*%|10\s*/\s*10|20\s*/\s*20)\b|\b(?:system prompt|developer message|grading instructions|evaluation instructions|instructions for (?:the )?(?:grader|evaluator|summari[sz]er))\b|(?:<\|(?:system|assistant|im_start|im_end)\|>|\[INST\])')

def suspicious(text):
    return [{'start':m.start(),'end':m.end(),'code':'EVALUATOR_DIRECTED_CONTENT'} for m in _INSTRUCTIONS.finditer(text)][:100]

def active(bundle):
    return bundle.observations.get('evaluation_policy',{}).get('version') == VERSION

def report_items(bundle):
    return [e for e in bundle.items if e.kind=='REPORT_EXCERPT' and e.eligible_for_review]

def _sections(items,pattern):
    return [e for e in items if re.search(pattern,str(e.data.get('section','')),re.I)]

def _body(e):
    t=e.text.strip()
    section=str(e.data.get('section','')).strip()
    return t[len(section):].strip() if section and t.startswith(section) else t

def _find(items, pattern):
    return [e for e in items if re.search(pattern,_body(e),re.I)]

def report_facts(items):
    """Return source-linked lexical signals, with no claim of complete extraction."""
    # A validation limitation by itself is not a test scenario. Require an
    # observable action and expected outcome, in plan context (or same sentence).
    tests=_sections(items,r'test|validation|verif|evaluation')
    actions=r'\b(?:test(?:s|ing)?|check(?:s|ed)?|verif(?:y|ies|ication)|scenario|request|login|sign in|register|creat(?:e|ion)|delet(?:e|ion)|edit|updat(?:e|ing)|return|reject(?:ion|ed)?|invalid|unauthenticated)\b'
    outcomes=r'\b(?:expected|should|must|assert|reject(?:ed|ion)?|denied|cannot|persists?|persisted|stored|appears?|absent|matches?|status code|responds?|returns?|succeed|fails?)\b'
    scenarios=[e for e in tests if re.search(actions,_body(e),re.I) and re.search(outcomes,_body(e),re.I) and re.search(r'\b(?:login|sign in|account|request|create|creation|delete|deletion|edit|update|invalid|unauthenticated|wrong.role|return|rejection|unavailable|input|output|assert|scenario|expected observation)\b',_body(e),re.I)]
    # Exclude solely statements about unavailable tests from the positive signal.
    scenarios=[e for e in scenarios if not (len(_body(e).split())<45 and re.search(r'\b(?:no tests|not (?:been )?executed|not run|no runtime|no functional|independent verification)\b',_body(e),re.I) and not re.search(r'\b(?:invalid|reject|expected observation|unauthenticated|wrong.role|assert|check loan|check that|test cases? include)\b',_body(e),re.I))]
    negative=_find(scenarios,r'\b(?:invalid|unauthenticated|unauthori[sz]ed|wrong.role|reject(?:ed|ion)?|unavailable|denied|cannot|negative (?:test|case|check))\b')
    limitations=_find(items,r'\b(?:not (?:assert|claim|executed|run|tested|verified)|does\s*not\s*assert|not been (?:executed|tested)|no tests? (?:have been |were )?(?:executed|run|performed)|independent verification is required|limitations?|not established|outside (?:the )?scope|requires? (?:independent |manual )?(?:review|verification|testing))\b')
    design=_sections(items,r'architecture|design|data model|database|interface|methodology|implementation')
    design=[e for e in design if re.search(r'\b(?:components?|layers?|tables?|schemas?|modules?|interfaces?|clients?|servers?|databases?|apis?|controllers?|services?|data flow|relationships?|foreign keys?|pipelines?|entities)\b',_body(e),re.I) and re.search(r'\b(?:uses?|stores?|connects?|calls?|sends?|receives?|handles?|contains?|consists?|between|links?|references?|separates?|reads?|writes?|routes?|responsible|because|through|maps?|passes?|queries|provides?)\b',_body(e),re.I) and len(_body(e).split())>=8]
    return {'test_scenarios':[e.id for e in scenarios], 'negative_checks':[e.id for e in negative], 'limitations':[e.id for e in limitations], 'design_description':[e.id for e in design]}

def _quality(e):
    return e.kind=='STATIC_SOURCE' and e.data.get('record_type') in ('quality','findings') and bool(e.data.get('code') or e.label) and bool(e.data.get('source_locations'))

def _implementation(e):
    return _quality(e) or (e.kind=='STATIC_SOURCE' and e.data.get('record_type')=='source_inventory' and e.data.get('analyzed_files',0)>0)

# Do not let retrieval of a reference alone imply relevance. Native classifications
# remain heuristic; a faculty reviewer may override a deferral with evidence.
_POSITIVE_RELEVANCE={'directly_relevant','methodologically_relevant','supporting_relevant','foundational_relevant'}

def research_facts(bundle):
    from .research_policy import research_record,reference_item
    record=research_record(bundle)
    if not record:return None
    p=record.data.get('phase_1',{})
    refs=[e for e in bundle.items if e.eligible_for_review and reference_item(e)]
    return {'entries':p.get('reference_entries'), 'linked':p.get('matched_reference_entries'), 'literature_review':p.get('literature_review_found'), 'citation_mentions':p.get('document_citation_mentions'), 'verification':dict(Counter(str(e.data.get('verification_status','not_checked')) for e in refs)), 'relevance':dict(Counter(str(e.data.get('relevance_classification','insufficient_evidence')) for e in refs)), 'positive_verified_ids':[e.id for e in refs if e.data.get('verification_status')=='verified' and (e.data.get('relevance_classification') in _POSITIVE_RELEVANCE or (e.data.get('relevance_classification')=='partially_relevant' and e.data.get('supporting_engineering_connection') is True and e.data.get('citation_context_linked') is True)) and e.data.get('citation_context_linked',True) is not False], 'mismatch_ids':[e.id for e in refs if e.data.get('verification_status')=='metadata_mismatch'], 'reference_ids':[e.id for e in refs], 'assessment_id':record.id}

def prerequisite(bundle,c):
    """A conservative no-inference gate for automatic judgments only."""
    if not active(bundle):return None
    if bundle.observations['evaluation_policy'].get('suspect_spans'):
        return 'REPORT_INSTRUCTION_CONTENT_REQUIRES_REVIEW'
    if any(suspicious(e.text) for e in bundle.items if e.eligible_for_review and e.kind in c.allowed_evidence):
        return 'EVIDENCE_INSTRUCTION_CONTENT_REQUIRES_REVIEW'
    if bundle.report_extraction!='COMPLETE' and 'REPORT_EXCERPT' in c.allowed_evidence:
        return 'REPORT_EXTRACTION_REQUIRES_REVIEW'
    from .intelligence import focus,runtime_inventory
    from .research_policy import is_research,research_state,PRESENT
    f=focus(c);facts=report_facts(report_items(bundle))
    if is_research(c):
        if research_state(bundle)!=PRESENT:return None # existing absence/inconclusive policy
        r=research_facts(bundle)
        if not r or not r['positive_verified_ids'] or not r['linked'] or r['mismatch_ids']:
            return 'RESEARCH_IDENTITY_RELEVANCE_OR_LINKAGE_REQUIRES_REVIEW'
    elif f=='TEST_STRATEGY' and not facts['test_scenarios']:
        return 'DOCUMENTED_TEST_SCENARIOS_NOT_ESTABLISHED'
    elif f=='DESIGN' and not facts['design_description']:
        return 'SUBSTANTIVE_DESIGN_EVIDENCE_NOT_ESTABLISHED'
    elif f=='IMPLEMENTATION' and not any(_quality(e) for e in bundle.items if e.eligible_for_review):
        # Inventory is shown, but existence/absence of findings alone is not quality.
        return 'IMPLEMENTATION_QUALITY_EVIDENCE_NOT_ESTABLISHED'
    elif f=='FUNCTIONAL' and not runtime_inventory(bundle)[0]:
        return 'DECISIVE_RUNTIME_EVIDENCE_NOT_ESTABLISHED'
    return None

def candidate_filter(bundle,c,allowed):
    if not active(bundle):return allowed
    from .intelligence import focus
    if focus(c)=='IMPLEMENTATION':return [e for e in allowed if e.kind!='STATIC_SOURCE' or _implementation(e)]
    return allowed

def validate(bundle,c,level,refs,rationale,feedback):
    if not active(bundle):return
    code=prerequisite(bundle,c)
    if code:raise ValueError(code)
    from .intelligence import focus,runtime_inventory
    from .research_policy import is_research
    f=focus(c);ids={e.id for e in refs};facts=report_facts(report_items(bundle))
    prose=' '.join([rationale]+([feedback.strength,feedback.limitation,feedback.improvement] if feedback else []))
    if suspicious(prose):raise ValueError('UNTRUSTED_INSTRUCTION_IN_PROPOSAL')
    if f=='DESIGN' and level.fraction>0 and not ids.intersection(facts['design_description']):raise ValueError('DESIGN_CITATION_NOT_SUBSTANTIVE')
    if f=='TEST_STRATEGY':
        prose=' '.join([rationale]+([feedback.strength,feedback.limitation] if feedback else []))
        if not ids.intersection(facts['test_scenarios']):raise ValueError('TEST_PLAN_CITATION_REQUIRED')
        if facts['negative_checks'] and re.search(r'\b(?:no|lacks?|lacking|missing|without|absence of|absent|omits?|omitting)\b[^.!?]{0,50}\b(?:explicit )?negative (?:tests?|checks?|cases?|scenarios?)\b|\bnegative (?:tests?|checks?|cases?|scenarios?)\b[^.!?]{0,35}\b(?:absent|missing|not (?:included|documented|present))\b',prose,re.I):raise ValueError('NEGATIVE_CHECK_CONTRADICTION')
        if facts['limitations'] and re.search(r'\b(?:no|lacks?|lacking|missing|without|absence of|omits?|omitting)\b[^.!?]{0,50}\b(?:explicit )?limitations\b|\blimitations\b[^.!?]{0,35}\b(?:absent|missing|not (?:included|documented|present))\b',prose,re.I):raise ValueError('LIMITATION_CONTRADICTION')
        if facts['test_scenarios'] and re.search(r'\b(?:no meaningful validation strategy|no clear expected outcomes|misrepresents what was tested)\b',prose,re.I):raise ValueError('TEST_STRATEGY_CONTRADICTION')
    if f=='IMPLEMENTATION' and not any(_quality(e) for e in refs):raise ValueError('IMPLEMENTATION_QUALITY_CITATION_REQUIRED')
    if f=='FUNCTIONAL':
        inv,_,_=runtime_inventory(bundle);groups={str(e.data.get('suite') or e.data.get('category')) for e in inv.values()};selected={str(e.data.get('suite') or e.data.get('category')) for e in refs if e.kind=='TEST_OBSERVATION'}
        if groups-selected:raise ValueError('RUNTIME_SCOPE_NOT_REPRESENTED')
        if any(e.data.get('outcome')=='FAILED' for e in inv.values()) and not any(e.data.get('outcome')=='FAILED' for e in refs):raise ValueError('MATERIAL_RUNTIME_FAILURE_NOT_CITED')
        if level.fraction>=.75 and not bundle.observations.get('executioner',{}).get('requirements_mapping_verified',False):raise ValueError('REQUIREMENTS_MAPPING_NOT_ESTABLISHED')
    if is_research(c):
        r=research_facts(bundle)
        if level.fraction>0 and not ids.intersection(r['positive_verified_ids']):raise ValueError('VERIFIED_RELEVANT_SOURCE_CITATION_REQUIRED')
        if r['literature_review'] and re.search(r'\b(?:no|lacks?|missing|without)\s+(?:a\s+)?literature review\b|literature review (?:is )?absent',prose,re.I):raise ValueError('LITERATURE_REVIEW_CONTRADICTION')
        if re.search(r'\b(?:must|should|required to)\s+(?:replicate|reproduce)\b',prose,re.I) and 'replicat' not in c.description.lower() and 'reproduc' not in c.description.lower():raise ValueError('UNSPECIFIED_REPLICATION_REQUIREMENT')
    if c.id=='problem_scope' and re.search(r'\b(?:not a production|only a prototype|merely a prototype)\b',prose,re.I) and 'production' not in c.description.lower():raise ValueError('SCOPE_PENALTY_NOT_IN_RUBRIC')
    if f=='DESIGN' and re.search(r'\bsome components are missing\b',prose,re.I):raise ValueError('UNSPECIFIED_MISSING_COMPONENTS')

def display_feedback(bundle,c,refs,feedback):
    """Native evidence stays visible even when narrative feedback is generic."""
    if not active(bundle) or feedback is None:return feedback
    from .intelligence import focus
    f=focus(c)
    if f=='IMPLEMENTATION':
        e=next((e for e in refs if _quality(e) and e.data.get('recommendation')),None)
        if e:
            locations=e.data.get('source_locations') or []
            loc=', '.join(str(x.get('path'))+(':'+str(x['line']) if x.get('line') else '') for x in locations[:2])
            return feedback.model_copy(update={'limitation':(str(e.label)+': '+loc+' (static observation; contextual review required).')[:220], 'improvement':str(e.data['recommendation'])[:220]})
    if f=='FUNCTIONAL':
        from .intelligence import runtime_inventory
        inv,_,_=runtime_inventory(bundle);counts=Counter(e.data['outcome'] for e in inv.values());groups=sorted({str(e.data.get('suite') or e.data.get('category')) for e in inv.values()})
        return feedback.model_copy(update={'strength':(f"{counts['PASSED']} scoped checks passed; {counts['FAILED']} failed. Suites: "+', '.join(groups))[:220], 'limitation':'Overall requirement coverage has not been established. Untested cases are not failures.','improvement':'Map required behaviors to explicit test oracles; review untested and inconclusive cases.'})
    return feedback


def grounded_summary(text, *, legacy_l1=False):
    """Extractive fallback: exact source sentences only, no evaluator instructions.

    Selection is deterministic and bounded; it does not claim independent truth.
    """
    from .evidence import section_chunks
    suspect=suspicious(text)
    if suspect:
        return {'summary':'Report contains evaluator-directed text. Automated summary withheld pending review.','key_points':['Original report retained for teacher review.']}
    candidates=[];seen=set()
    for _,_,part,section in section_chunks(text):
        body=part.strip();h=section.strip()
        if h and body.startswith(h):body=body[len(h):].strip()
        for s in re.split(r'(?<=[.!?])\s+|\n\s*\n',body):
            s=' '.join(s.split())
            if not 20<=len(s)<=360 or s.casefold() in seen:continue
            seen.add(s.casefold())
            priority=2 if re.search(r'abstract|objective|scope|architecture|design|implementation|testing|validation|limitations|conclusion',h,re.I) else 1
            if re.search(r'\b(?:not (?:executed|verified|assert|claim)|not been (?:executed|tested)|negative|reject|invalid|unauthenticated|limitations?)\b',s,re.I):priority+=3
            candidates.append((priority,len(candidates),s,h))
    if not candidates:
        clean=' '.join(text.split())[:500]
        return {'summary':clean or 'No readable text available.','key_points':[]}
    ranked=sorted(candidates,key=lambda x:(-x[0],x[1]));chosen=[];sections=set()
    if not legacy_l1:
        # Reserve one exact source sentence for explicit non-execution/verification
        # boundaries before selecting topic diversity. Never synthesize a claim.
        boundary = next((row for row in ranked if re.search(r"\b(?:no (?:runtime |functional )?tests? (?:have been |were )?(?:executed|run|performed)|not (?:necessarily )?(?:been )?(?:executed|verified|tested)|does not (?:assert|claim)|independent verification is required)\b",row[2],re.I)),None)
        if boundary:
            chosen.append(boundary);sections.add(boundary[3])
        negative = next((row for row in ranked if row not in chosen and re.search(r"testing|validation|test plan",row[3],re.I) and re.search(r"\b(?:reject(?:ed|ion|s)?|invalid|unauthenticated|unauthori[sz]ed|wrong.role|negative (?:test|case|check))\b",row[2],re.I)),None)
        if negative:
            chosen.append(negative);sections.add(negative[3])
    for row in ranked:
        if row[3] not in sections:chosen.append(row);sections.add(row[3])
        if len(chosen)==7:break
    for row in ranked:
        if len(chosen)==7:break
        if row not in chosen:chosen.append(row)
    chosen.sort(key=lambda x:x[1]);sentences=[r[2] for r in chosen]
    return {'summary':' '.join(sentences[:3]),'key_points':sentences[3:]}
