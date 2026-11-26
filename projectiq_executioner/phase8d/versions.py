"""Conservative npm engine subset; unknown syntax is never treated as compatible."""
import re
from packaging.specifiers import SpecifierSet
from packaging.version import Version


def node_constraint(version, constraint):
    """True/False, or None for unsupported ranges. Supports AND/OR, partials,
    wildcards, comparators, caret, tilde, inclusive hyphen ranges."""
    try:
        if not isinstance(constraint,str) or len(constraint)>160 or not re.fullmatch(r'[-0-9vVxX.*^~<>=| \t]+',constraint):return None
        if not re.fullmatch(r'v?\d+\.\d+\.\d+',version):return None
        target=tuple(map(int,version.lstrip('v').split('.')))
        def partial(s):
            s=s.lstrip('vV');bits=s.split('.')
            if not 1<=len(bits)<=3:raise ValueError()
            nums=[];wild=False
            for b in bits:
                if b in ('x','X','*'):wild=True
                elif wild or not b.isdecimal() or len(b)>6:raise ValueError()
                else:nums.append(int(b))
            return tuple(nums+[0]*(3-len(nums))),len(nums)
        def upper(v,n):
            if n==0:return None
            return tuple(list(v[:n-1])+[v[n-1]+1]+[0]*(3-n))
        def term(op,raw):
            v,n=partial(raw);hi=upper(v,n)
            if n==0:
                if op not in ('','=', '~','^'):raise ValueError()
                return True
            if op in ('','=' ):return target==v if n==3 else v<=target<hi
            if op=='>=':return target>=v
            if op=='<':return target<v
            if op=='>':return target>v if n==3 else target>=hi
            if op=='<=':return target<=v if n==3 else target<hi
            if op=='~':return v<=target<upper(v,min(n,2))
            if op=='^':
                j=next((i for i,x in enumerate(v[:n]) if x),n-1)
                return v<=target<upper(v,j+1)
            raise ValueError()
        branches=[]
        for raw in constraint.strip().split('||'):
            raw=raw.strip()
            if not raw:raise ValueError()
            hyphen=re.fullmatch(r'(\S+)\s+-\s+(\S+)',raw)
            if hyphen:
                lo,n=partial(hyphen[1]);hi,m=partial(hyphen[2]);branches.append(target>=lo and (target<=hi if m==3 else upper(hi,m) is None or target<upper(hi,m)));continue
            raw=re.sub(r'([<>=~^]+)\s+',r'\1',raw)
            vals=[]
            for token in raw.split():
                m=re.fullmatch(r'(>=|<=|>|<|=|\^|~)?([vV]?[0-9xX*]+(?:\.[0-9xX*]+){0,2})',token)
                if not m:raise ValueError()
                vals.append(term(m[1] or '',m[2]))
            if not vals:raise ValueError()
            branches.append(all(vals))
        return any(branches)
    except (ValueError,TypeError,AttributeError):return None


def python_constraint(version,constraint,*,file_pin=False):
    try:
        if not isinstance(constraint,str) or len(constraint)>160:return None
        if file_pin:
            if not re.fullmatch(r'\d+(?:\.\d+){0,2}',constraint):return None
            constraint='=='+constraint+('.*' if constraint.count('.')<2 else '')
        return SpecifierSet(constraint).contains(Version(version),prereleases=False)
    except (ValueError,TypeError):return None
