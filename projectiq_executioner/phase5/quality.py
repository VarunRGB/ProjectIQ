"""Evidence-aware, provisional static-maintainability indicator (v0.3).

No functions ≠ perfect quality and no function coverage ≠ zero quality.
Unsupported dimensions are excluded; weights are renormalized and disclosed.
Secrets are a separate risk surface, never diluted into maintainability.
"""
from __future__ import annotations

WEIGHTS={'lint':0.30,'complexity':0.30,'duplication':0.20,'structure':0.20}
_STRUCTURAL={'LONG_FUNCTION','VERY_LONG_FUNCTION','HIGH_CYCLOMATIC','DEEP_NESTING',
             'POTENTIAL_BRACE_NESTING','POTENTIAL_COMPLEX_BRANCHING'}

def _bound(value: float) -> int:
    return max(0,min(100,round(value)))

def compute_quality(*, code_lines:int, functions:list[dict], findings:list[dict],
                    duplications:list[dict], analyzed:int, candidates:int,
                    source_read_ratio:float=1.0,function_file_ratio:float=1.0,
                    function_body_ratio:float=1.0,truncated:bool=False,
                    production_code_lines:int|None=None,
                    production_files:list[dict]|None=None,
                    duplicated_production_lines:int=0,
                    production_functions:list[dict]|None=None) -> dict:
    """Keep a comparable 0-100 *indicator*, not a student grade.

    Function-poor projects receive a limited score from file-level evidence.
    Complexity is excluded if function bodies cover <60% of executable source.
    """
    prod_code=code_lines if production_code_lines is None else production_code_lines
    prod_files=production_files or []
    prod_funcs=functions if production_functions is None else production_functions
    reasons=[]
    if prod_code<10:reasons.append('INSUFFICIENT_CODE')
    if source_read_ratio<0.85:reasons.append('LOW_SOURCE_READ_COVERAGE')
    if truncated:reasons.append('TRUNCATED_EVIDENCE')
    if reasons:
        return {'status':'INSUFFICIENT_COVERAGE','score':None,
                'dimensions':dict.fromkeys(WEIGHTS),
                'basis':'HEURISTIC_NOT_A_GRADE','reason_codes':reasons,
                'scoring_version':'0.3','weights_used':{},'confidence':'NONE'}

    lint_codes=[f for f in findings if f['code'] not in _STRUCTURAL and not f['code'].startswith('SEC_')
                and f.get('scope','PRODUCTION')=='PRODUCTION']
    severity={'HIGH':7.0,'MEDIUM':3.0,'LOW':1.0,'ADVISORY':0.0}
    # Stronger per-1kLOC penalty avoids 100 on otherwise warning-heavy projects.
    lint=_bound(100-1000*sum(severity[x['severity']] for x in lint_codes)/max(prod_code,100)*0.18)

    duplicated_ratio=min(1.0,duplicated_production_lines/max(prod_code,1))
    # Only exact matched lines count, not number of duplicate groups per file.
    duplication=_bound(100-100*duplicated_ratio)

    long_lines=sum(x['lines']>400 for x in prod_files)
    # File-level structure exists even for procedural PHP, HTML or SQL-only apps.
    file_penalty=100*long_lines/max(len(prod_files),1)*0.30
    long_funcs=sum(1 for f in prod_funcs if f['lines']>=60)
    func_penalty=(100*long_funcs/max(len(prod_funcs),1)*0.40) if prod_funcs else 0
    structure=_bound(100-file_penalty-func_penalty)

    known=[f for f in prod_funcs if f['method']=='PYTHON_AST']
    # Brace metrics are advisory and not strong enough to affect grade.
    ast_share=sum(f['lines'] for f in known)/max(sum(f['lines'] for f in prod_funcs),1)
    complex_eligible=bool(known) and ast_share>=0.70 and function_body_ratio>=0.60 and function_file_ratio>=0.80
    cc=None
    if complex_eligible:
        # Only AST-backed decision paths; other languages require real parsers.
        excess=sum(max(0,f['cyclomatic']-9) for f in known)
        cc=_bound(100-8*excess/max(len(known),1))

    dims={'lint':lint,'complexity':cc,'duplication':duplication,'structure':structure}
    used={k:v for k,v in WEIGHTS.items() if dims[k] is not None}
    denom=sum(used.values())
    # Half-up rather than bankers rounding for predictable report semantics.
    score=_bound(sum(dims[k]*w for k,w in used.items())/denom)
    limited=(not complex_eligible or function_body_ratio<0.60 or
             function_file_ratio<0.80 or source_read_ratio<0.98)
    if limited:
        reasons.append('LIMITED_COMPLEXITY_ANALYSIS')
    return {'status':'PROVISIONAL_LIMITED' if limited else 'PROVISIONAL',
            'score':score,'dimensions':dims,'basis':'HEURISTIC_NOT_A_GRADE',
            'reason_codes':reasons,'scoring_version':'0.3',
            'weights_used':{k:round(w/denom,6) for k,w in used.items()},
            'confidence':'LOW' if limited else 'MEDIUM'}
