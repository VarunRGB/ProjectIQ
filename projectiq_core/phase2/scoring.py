"""Pure deterministic weighted scoring. Never call a model or execute a project."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, localcontext
from collections import defaultdict
from .contracts import (Rubric, DecisionSet, EvidenceBundle, CriterionResult, Assessment)
from .evidence import digest
from .intelligence import runtime_inventory, consistency, evidence_details


def empty_decisions(bundle, rubric):
    return DecisionSet(evidence_sha256=digest(bundle),rubric_sha256=digest(rubric))


def D(value):return Decimal(str(value))

def round2(value):return float(value.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))


def assess(bundle: EvidenceBundle, rubric: Rubric, decisions: DecisionSet|None=None, *, include_details: bool=True) -> Assessment:
    """All applicable weights stay in the denominator until explicitly excluded.

    Any unresolved criterion withholds the complete mark; no missing evidence
    is treated as zero or silently reweighted. An observed failure is not by
    itself student fault, and deterministic test-ratio scores remain provisional.
    """
    decisions=decisions if decisions is not None else empty_decisions(bundle,rubric)
    if decisions.evidence_sha256!=digest(bundle) or decisions.rubric_sha256!=digest(rubric):raise ValueError('STALE_DECISIONS')
    known={c.id:c for c in rubric.criteria};selected={d.criterion_id:d for d in decisions.decisions}
    if set(selected)-set(known):raise ValueError('UNKNOWN_CRITERION')
    evidence={e.id:e for e in bundle.items}
    if len(evidence)!=len(bundle.items):raise ValueError('DUPLICATE_EVIDENCE_ID')
    excluded=set()
    for c in rubric.criteria:
        d=selected.get(c.id)
        if c.applicability=='EXCLUDED':
            if d:raise ValueError('DECISION_FOR_EXCLUDED_CRITERION')
            excluded.add(c.id)
        elif d and d.action=='EXCLUDE':
            if c.applicability!='OPTIONAL' or d.source!='TEACHER':raise ValueError('ONLY_TEACHER_CAN_EXCLUDE_OPTIONAL')
            excluded.add(c.id)
    if len(excluded)==len(rubric.criteria):raise ValueError('NO_APPLICABLE_CRITERIA')
    # One verified observation can belong to multiple qualitative dimensions but
    # explicit test ratio rules may not double-count the same exact test.
    used_tests=set()
    for c in rubric.criteria:
        if c.id not in excluded and c.method in ('TEST_RATIO','REVIEWED_RATIO'):
            keys={('exact:' if c.method=='TEST_RATIO' else 'reviewed:')+t for t in c.test_ids}
            if used_tests.intersection(keys):raise ValueError('OVERLAPPING_TEST_RATIO_RULES')
            used_tests.update(keys)
    inventory,ambiguous,overlaps=runtime_inventory(bundle)
    # Distinct ratio criteria may not reward the very same native assertion twice.
    used_pointers=set()
    for key in sorted(used_tests):
        if key in inventory:
            e=inventory[key];pointers={(e.artifact_sha256,p) for p in e.data.get('observations',[]) if isinstance(p,str)}
            if used_pointers & pointers:raise ValueError('OVERLAPPING_TEST_RATIO_OBSERVATIONS')
            used_pointers.update(pointers)
    tests=defaultdict(list)
    for e in bundle.items:
        if (e.kind=='TEST_OBSERVATION' and e.eligible_for_review and e.data.get('scope')=='EXACT_PLANNED_TEST'
                and e.data.get('outcome') in ('PASSED','FAILED') and e.data.get('category')=='HTTP_ROUTE'
                and e.data.get('commit_sha')==bundle.repository_commit and bundle.repository_commit
                and isinstance(e.data.get('test_id'),str)):
            tests[e.data['test_id']].append(e)
    with localcontext() as ctx:
        ctx.prec=40
        weights={c.id:D(c.weight) for c in rubric.criteria if c.id not in excluded}
        if rubric.group_weights:
            group_sums=defaultdict(Decimal)
            for c in rubric.criteria:
                if c.id in weights:group_sums[c.group]+=weights[c.id]
            weights={c.id:D(rubric.group_weights[c.group])*weights[c.id]/group_sums[c.group] for c in rubric.criteria if c.id in weights}
        total_weight=sum(weights.values(),Decimal(0))
        total=D(rubric.total_points);result=[];earned=Decimal(0);assessed_weight=Decimal(0);reviewed_weight=Decimal(0)
        groups=defaultdict(lambda:dict(weight=Decimal(0),assessed=Decimal(0),reviewed=Decimal(0),earned=Decimal(0),missing=[]))
        missing=[]
        for c in rubric.criteria:
            d=selected.get(c.id);f=None;level=None;ids=[];status='UNASSESSED';reason='No valid score decision yet.'
            w=weights.get(c.id,Decimal(0));flags=[];feedback=None;objective={}
            possible=total*w/total_weight
            if c.id in excluded:
                status='EXCLUDED';reason=d.rationale if d else 'Excluded by the teacher-defined rubric.'
            elif d and d.action=='DEFER':reason=d.rationale
            elif c.method=='JUDGMENT':
                if d:
                    if d.action!='SCORE':raise ValueError('JUDGMENT_REQUIRES_LEVEL')
                    matching=[x for x in c.levels if x.id==d.level_id]
                    if len(matching)!=1:raise ValueError('UNKNOWN_RUBRIC_LEVEL')
                    if any(i not in evidence for i in d.evidence_ids):raise ValueError('UNKNOWN_EVIDENCE_REFERENCE')
                    refs=[evidence[i] for i in d.evidence_ids]
                    if any(not e.eligible_for_review or e.kind not in c.allowed_evidence for e in refs):raise ValueError('INELIGIBLE_EVIDENCE')
                    if len(refs)<c.minimum_evidence or not set(c.required_evidence)<={e.kind for e in refs}:raise ValueError('EVIDENCE_COVERAGE_NOT_MET')
                    # Count distinct underlying locators rather than duplicate references.
                    if len({(e.artifact_sha256,e.locator) for e in refs})<c.minimum_evidence:raise ValueError('DUPLICATE_SOURCE_COVERAGE')
                    if d.source=='MODEL':
                        if c.feedback_required and d.feedback is None:raise ValueError('STRUCTURED_FEEDBACK_REQUIRED')
                        flags=consistency(bundle,c,matching[0],refs,d.rationale,d.feedback)
                    feedback=d.feedback
                    f=D(matching[0].fraction);level=matching[0].id;ids=d.evidence_ids;reason=d.rationale
                    status='TEACHER_REVIEWED' if d.source=='TEACHER' else 'PROPOSED'
                else:
                    available=[e for e in bundle.items if e.eligible_for_review and e.kind in c.allowed_evidence]
                    if len(available)<c.minimum_evidence or not set(c.required_evidence)<={e.kind for e in available}:reason='Required evidence coverage unavailable.'
            elif c.method=='TEST_RATIO':
                if d and (d.action!='CONFIRM_TEST_RATIO' or d.source!='TEACHER'):raise ValueError('TEST_RATIO_REQUIRES_TEACHER_CONFIRMATION')
                absent=[t for t in c.test_ids if len(tests[t])!=1]
                if absent:
                    reason='Missing, ambiguous or non-decisive exact HTTP route tests: '+', '.join(absent)
                    if d:raise ValueError('CANNOT_CONFIRM_INCOMPLETE_TEST_RATIO')
                else:
                    matches=[tests[t][0] for t in c.test_ids]
                    f=Decimal(sum(e.data['outcome']=='PASSED' for e in matches))/Decimal(len(matches));ids=[e.id for e in matches]
                    status='TEACHER_REVIEWED' if d else 'OBJECTIVE_PROVISIONAL'
                    reason=(d.rationale+' | ' if d else '')+f'{sum(e.data["outcome"]=="PASSED" for e in matches)}/{len(matches)} explicitly selected exact HTTP route tests passed. Assertion outcomes require attribution review.'
            elif c.method in ('REVIEWED_RATIO','REQUIREMENT_COVERAGE'):
                if d and (d.action!='CONFIRM_TEST_RATIO' or d.source!='TEACHER'):raise ValueError('OBJECTIVE_REQUIRES_TEACHER_CONFIRMATION')
                if c.method=='REVIEWED_RATIO':
                    keys=['reviewed:'+t for t in c.test_ids]
                    absent=[k for k in keys if k not in inventory]
                    if absent:
                        reason='Missing, ambiguous or non-decisive reviewed scenarios: '+', '.join(absent)
                    else:
                        matches=[inventory[k] for k in keys]
                        passed=sum(e.data['outcome']=='PASSED' for e in matches)
                        f=Decimal(passed)/Decimal(len(matches));ids=[e.id for e in matches]
                        objective={'method':c.method,'passed':passed,'failed':len(matches)-passed,'selected':len(matches),'scope':'REVIEWED_SCENARIO_ONLY'}
                        reason=f'{passed}/{len(matches)} teacher-selected reviewed scenarios passed; not full application correctness.'
                else:
                    # This measures evidence coverage, not successful behavior. Explicit mapped IDs
                    # must use a namespace; unmapped/unknown tests are not treated as failures.
                    mapping=c.requirement_map
                    truncated=any('capped' in x.lower() or 'limit_reached' in x.lower() for x in bundle.limitations)
                    if not inventory or truncated or ambiguous:
                        reason='Requirement coverage unavailable: no complete unambiguous executed evidence inventory.'
                    else:
                        covered=[rid for rid,keys in mapping.items() if all(k in inventory for k in keys)]
                        f=Decimal(len(covered))/Decimal(len(mapping))
                        ids=sorted({inventory[k].id for rid in covered for k in mapping[rid]})
                        objective={'method':c.method,'covered':covered,'unverified':sorted(set(mapping)-set(covered)),'total_requirements':len(mapping),'scope':'TEACHER_DEFINED_EVIDENCE_COVERAGE'}
                        reason=f'{len(covered)}/{len(mapping)} teacher-mapped requirements have all specified decisive observations. This measures testing coverage, not functional success.'
                if f is None and d:raise ValueError('CANNOT_CONFIRM_INCOMPLETE_OBJECTIVE')
                if f is not None:
                    status='TEACHER_REVIEWED' if d else 'OBJECTIVE_PROVISIONAL'
                    if d:reason=d.rationale+' | '+reason
            g=groups[c.group];g['weight']+=w
            if f is not None:
                points=possible*f;earned+=points;assessed_weight+=w;g['assessed']+=w;g['earned']+=points
                if status=='TEACHER_REVIEWED':reviewed_weight+=w;g['reviewed']+=w
            else:
                points=None
                if status!='EXCLUDED':missing.append(c.id);g['missing'].append(c.id)
            result.append(CriterionResult(id=c.id,title=c.title,group=c.group,applicability=c.applicability,status=status,
                configured_weight=c.weight,effective_weight_percent=round2(w*100/total_weight),max_points=round2(possible),
                earned_points=round2(points) if points is not None else None,fraction=float(f) if f is not None else None,
                level_id=level,evidence_ids=ids,reason=reason,feedback=feedback,review_flags=flags,objective_details=objective,evidence_details=evidence_details(bundle,ids) if include_details else []))
        complete=not missing;reviewed=complete and reviewed_weight==total_weight
        return Assessment(evidence_sha256=digest(bundle),rubric_sha256=digest(rubric),decisions_sha256=digest(decisions),
            status='TEACHER_REVIEWED' if reviewed else 'READY_FOR_TEACHER_REVIEW' if complete else 'INSUFFICIENT_EVIDENCE',
            max_points=rubric.total_points,proposed_points=round2(earned) if complete else None,reviewed_points=round2(earned) if reviewed else None,
            assessed_subtotal_points=round2(earned),assessed_max_points=round2(total*assessed_weight/total_weight),
            assessed_weight_percent=round2(assessed_weight*100/total_weight),reviewed_weight_percent=round2(reviewed_weight*100/total_weight),
            unresolved_criterion_ids=missing,criteria=result,
            groups=[dict(id=k,effective_weight_percent=round2(v['weight']*100/total_weight),max_points=round2(total*v['weight']/total_weight),
                assessed_max_points=round2(total*v['assessed']/total_weight),assessed_subtotal_points=round2(v['earned']),
                assessed_weight_percent=round2(v['assessed']*100/v['weight']) if v['weight'] else None,
                unresolved_criterion_ids=v['missing']) for k,v in groups.items()],
            limitations=['Integrity estimates and legacy Summarizer grades are advisory and never contribute to marks.',
                'Evidence IDs establish traceability, not correctness of a qualitative judgment. Teacher review is required.',
                'Excluded weights are redistributed proportionally; unresolved weights are not redistributed.',
                'Objective scenario success and explicitly mapped requirement evidence coverage measure distinct properties; neither proves unrestricted correctness.',
                'Display values are rounded independently; totals use unrounded arithmetic. Teacher-reviewed is a local operator status, not authenticated institutional approval.'])


def reweight(bundle, old_rubric, new_rubric, decisions):
    """Explicitly reuse decisions only when assessment semantics are unchanged."""
    assess(bundle,old_rubric,decisions,include_details=False)
    def policy(r):
        data=r.model_dump(mode='json')
        for k in ('title','revision','total_points','group_weights'):data.pop(k)
        for c in data['criteria']:c.pop('weight')
        return data
    if policy(old_rubric)!=policy(new_rubric):raise ValueError('REWEIGHT_POLICY_CHANGED_REASSESS_REQUIRED')
    rebound=DecisionSet(evidence_sha256=digest(bundle),rubric_sha256=digest(new_rubric),decisions=decisions.decisions)
    assess(bundle,new_rubric,rebound,include_details=False)
    return rebound
