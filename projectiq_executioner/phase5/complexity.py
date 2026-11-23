"""Cyclomatic/nesting metrics and cautious Python Big-O subset estimation.

Big-O here is *not* a theorem prover. Dynamic dispatch, recursion, unknown
calls and comprehensions return UNKNOWN; data-dependent early exits are not
interpreted as improved complexity. Estimates use a unit-cost RAM model.
"""
from __future__ import annotations

import ast
import re

from .functions import FunctionSpan

_BRANCHES=(ast.If,ast.For,ast.AsyncFor,ast.While,ast.Match,ast.IfExp)
_NESTING=_BRANCHES+(ast.Try,ast.TryStar,ast.With,ast.AsyncWith)


class _PythonComplexity(ast.NodeVisitor):
    def __init__(self,root):self.root=root;self.cyclomatic=1;self.depth=0;self.maximum=0
    def visit_FunctionDef(self,node):
        if node is self.root:self.generic_visit(node)
    def visit_AsyncFunctionDef(self,node):
        if node is self.root:self.generic_visit(node)
    def visit_Lambda(self,node):pass
    def visit_ClassDef(self,node):pass
    def generic_visit(self,node):
        marked=isinstance(node,_NESTING)
        if isinstance(node,_BRANCHES):
            self.cyclomatic+=1
        if marked:
            self.depth+=1
            self.maximum=max(self.depth,self.maximum)
        if isinstance(node,ast.ExceptHandler):self.cyclomatic+=1
        if isinstance(node,ast.BoolOp):self.cyclomatic+=max(0,len(node.values)-1)
        if isinstance(node,ast.comprehension):
            self.cyclomatic+=1+len(node.ifs)
            self.maximum=max(self.maximum,self.depth+1)
        super().generic_visit(node)
        if marked:self.depth-=1


def python_complexity(node:ast.AST)->tuple[int,int]:
    v=_PythonComplexity(node);v.visit(node)
    return v.cyclomatic,v.maximum


def brace_complexity(body:str)->tuple[int,int]:
    tokens=re.findall(r'\b(?:if|else|for|while|case|catch|switch|foreach)\b|&&|\|\||\?\?|\?\.|\?|\{|\}',body)
    cc=1+sum(t in {'if','for','while','case','catch','foreach','&&','||','?'} for t in tokens)
    # Approximate structural depth from brace nesting, not proof of control nesting.
    depth=0;max_depth=0
    for t in tokens:
        if t=='{':
            depth+=1;max_depth=max(max_depth,depth)
        elif t=='}':depth=max(0,depth-1)
    return cc,max_depth


# Polynomial monomial: (degree_n,degree_m,log_n). Never infer from arbitrary calls.
ONE=frozenset({(0,0,0)})
N=frozenset({(1,0,0)})
M=frozenset({(0,1,0)})
SORT_N=frozenset({(1,0,1)})
SORT_M=frozenset({(0,1,1)})


def _add(*terms):
    if any(t is None for t in terms):return None
    return frozenset(x for t in terms for x in t)


def _multiply(a,b):
    if a is None or b is None:return None
    out=set()
    for x in a:
        for y in b:
            z=tuple(i+j for i,j in zip(x,y))
            if max(z)>4:return None
            out.add(z)
    return frozenset(out)


def _annotation_kind(annotation):
    """A narrow type-hint model, advisory not a proof of runtime types."""
    if isinstance(annotation,ast.Name) and annotation.id in {'int','float','bool'}:
        return 'NUMBER'
    if isinstance(annotation,ast.Subscript) and isinstance(annotation.value,ast.Name) and \
            annotation.value.id in {'list','tuple','set','dict'}:
        return 'COLLECTION' if annotation.value.id in {'list','tuple'} else 'UNORDERED'
    if isinstance(annotation,ast.Name) and annotation.id in {'list','tuple','str','dict','set','range'}:
        return 'COLLECTION'
    return None


def _scale_iter(node,params,types):
    if isinstance(node,ast.Name) and node.id in params and types.get(node.id) in ('COLLECTION','UNORDERED'):
        return N if params.index(node.id)==0 else M if params.index(node.id)==1 else None
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and not node.keywords:
        if node.func.id in ('enumerate','reversed') and len(node.args)==1:
            return _scale_iter(node.args[0],params,types)
        if node.func.id=='range' and 1<=len(node.args)<=3:
            args=node.args
            if len(args)==3 and (not isinstance(args[2],ast.Constant) or args[2].value!=1):return None
            if len(args)>=2 and (not isinstance(args[0],ast.Constant) or args[0].value!=0):return None
            bound=args[0] if len(args)==1 else args[1]
            if isinstance(bound,ast.Constant) and type(bound.value) is int and 0<=bound.value<=1000000:
                return ONE
            if isinstance(bound,ast.Name) and bound.id in params and types.get(bound.id)=='NUMBER':
                return N if params.index(bound.id)==0 else M if params.index(bound.id)==1 else None
            if isinstance(bound,ast.Call) and isinstance(bound.func,ast.Name) and bound.func.id=='len' \
                    and len(bound.args)==1 and not bound.keywords:
                return _scale_iter(bound.args[0],params,types)
    return None


def _scalar(node,scalars):
    if isinstance(node,ast.Constant):return type(node.value) in (int,float,bool)
    if isinstance(node,ast.Name):return node.id in scalars
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd,ast.Not)):
        return _scalar(node.operand,scalars)
    if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod)):
        return _scalar(node.left,scalars) and _scalar(node.right,scalars)
    return False


def _expression_cost(node,params,types,scalars,local_lists=frozenset()):
    if isinstance(node,(ast.Constant,ast.Name)):
        return ONE
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and not node.keywords:
        receiver=node.func.value
        if node.func.attr=='append' and isinstance(receiver,ast.Name) and \
                receiver.id in local_lists and len(node.args)==1:
            return _expression_cost(node.args[0],params,types,scalars,local_lists)
        return None
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and not node.keywords:
        if node.func.id in {'len','abs'} and len(node.args)==1:
            arg=node.args[0]
            if node.func.id=='abs' and _scalar(arg,scalars):return ONE
            if node.func.id=='len' and isinstance(arg,ast.Name) and types.get(arg.id) in ('COLLECTION','UNORDERED'):
                return ONE
        if node.func.id=='sorted' and len(node.args)==1:
            arg=node.args[0]
            if isinstance(arg,ast.Name) and types.get(arg.id)=='COLLECTION' and types.get(arg.id+'_item')=='NUMBER':
                return SORT_N if params.index(arg.id)==0 else SORT_M if params.index(arg.id)==1 else None
        # Method calls and unknown builtins may execute arbitrary code.
        return None
    if isinstance(node,ast.BinOp):
        # Python +,* support string/list concatenation and repetition. Non-scalar
        # operands cannot be presumed constant time. Big-int arithmetic is
        # also nonconstant: this is a fixed-width numeric approximation only.
        return ONE if _scalar(node,scalars) else None
    if isinstance(node,ast.Compare):
        if all(_scalar(v,scalars) for v in [node.left,*node.comparators]):return ONE
        if all(isinstance(op,(ast.Is,ast.IsNot)) for op in node.ops):return ONE
        return None
    if isinstance(node,ast.BoolOp):
        return ONE if all(_scalar(v,scalars) for v in node.values) else None
    if isinstance(node,ast.UnaryOp):
        return ONE if _scalar(node,scalars) else None
    if isinstance(node,(ast.Tuple,ast.List,ast.Set)):
        values=[_expression_cost(x,params,types,scalars) for x in node.elts]
        return _add(ONE,*values)
    if isinstance(node,ast.Dict):
        values=[_expression_cost(x,params,types,scalars) for x in [*node.keys,*node.values] if x is not None]
        return _add(ONE,*values) if all(x is not None for x in values) else None
    if isinstance(node,ast.IfExp):
        return _add(_expression_cost(node.test,params,types,scalars,local_lists),
                    _expression_cost(node.body,params,types,scalars,local_lists),
                    _expression_cost(node.orelse,params,types,scalars,local_lists))
    # Indexing, slicing, comprehensions, attributes, f-strings, format,
    # comparisons on arbitrary objects, etc. require deeper type analysis.
    return None


def _statement_cost(stmt,params,types,scalars,local_lists):
    if isinstance(stmt,(ast.Pass,ast.Break,ast.Continue)):return ONE
    if isinstance(stmt,ast.Return):
        return ONE if stmt.value is None else _expression_cost(stmt.value,params,types,scalars,local_lists)
    if isinstance(stmt,ast.Expr):return _expression_cost(stmt.value,params,types,scalars,local_lists)
    if isinstance(stmt,ast.Assign):
        if any(not isinstance(target,ast.Name) for target in stmt.targets):return None
        cost=_expression_cost(stmt.value,params,types,scalars,local_lists)
        for target in stmt.targets:
            if _scalar(stmt.value,scalars):scalars.add(target.id)
            else:scalars.discard(target.id)
        return cost
    if isinstance(stmt,ast.AnnAssign):
        if not isinstance(stmt.target,ast.Name):return None
        cost=ONE if stmt.value is None else _expression_cost(stmt.value,params,types,scalars,local_lists)
        if _annotation_kind(stmt.annotation)=='NUMBER' and stmt.value is not None and _scalar(stmt.value,scalars):
            scalars.add(stmt.target.id)
        else:scalars.discard(stmt.target.id)
        return cost
    if isinstance(stmt,ast.AugAssign):
        return ONE if isinstance(stmt.target,ast.Name) and stmt.target.id in scalars and \
              isinstance(stmt.op,(ast.Add,ast.Sub,ast.Mult,ast.FloorDiv)) and _scalar(stmt.value,scalars) else None
    if isinstance(stmt,ast.If):
        test=_expression_cost(stmt.test,params,types,scalars)
        a=_sequence_cost(stmt.body,params,types,set(scalars),local_lists)
        b=_sequence_cost(stmt.orelse,params,types,set(scalars),local_lists)
        return _add(test,a,b)
    if isinstance(stmt,ast.For):
        bound=_scale_iter(stmt.iter,params,types)
        if bound is None:return None
        scoped=set(scalars)
        if isinstance(stmt.target,ast.Name):
            # enumerate() index and range() element are numeric; arbitrary
            # iterable elements are numeric only with a builtin type hint.
            if (isinstance(stmt.iter,ast.Call) and isinstance(stmt.iter.func,ast.Name) and
                    stmt.iter.func.id=='range'):
                scoped.add(stmt.target.id)
            elif isinstance(stmt.iter,ast.Name) and types.get(stmt.iter.id+'_item')=='NUMBER':
                scoped.add(stmt.target.id)
        elif isinstance(stmt.target,ast.Tuple) and len(stmt.target.elts)==2 and \
             isinstance(stmt.iter,ast.Call) and isinstance(stmt.iter.func,ast.Name) and stmt.iter.func.id=='enumerate':
            if isinstance(stmt.target.elts[0],ast.Name):scoped.add(stmt.target.elts[0].id)
        body=_sequence_cost(stmt.body,params,types,scoped,local_lists)
        otherwise=_sequence_cost(stmt.orelse,params,types,set(scalars),local_lists)
        return _add(_multiply(bound,body),otherwise)
    # Recursion, while loops, generators and arbitrary calls remain unknown.
    return None


def _sequence_cost(stmts,params,types,scalars,local_lists):
    terms=[ONE]
    for stmt in stmts:
        cost=_statement_cost(stmt,params,types,scalars,local_lists)
        if cost is None:return None
        terms.append(cost)
    return _add(*terms)


def _describe(poly):
    if poly is None:return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    terms=[a for a in sorted(poly) if not any(a!=b and all(x<=y for x,y in zip(a,b)) for b in poly)]
    def format_one(t):
        a,b,log=t
        out=[]
        if a:out.append('n' if a==1 else f'n^{a}')
        if b:out.append('m' if b==1 else f'm^{b}')
        if log:out.append('log(n)')
        return '*'.join(out) or '1'
    if not terms:return 'O(1)','MEDIUM','CONSTANT_TIME_SUBSET'
    result='O('+ ' + '.join(format_one(t) for t in terms)+')'
    if len(result)>60:return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    reason='STANDARD_SORT' if any(t[2] for t in terms) else (
        'CONSTANT_TIME_SUBSET' if terms==[(0,0,0)] else 'SIMPLE_BOUNDED_LOOPS')
    return result,'MEDIUM',reason


def python_big_o(node:ast.AST)->tuple[str,str,str]:
    if not isinstance(node,ast.FunctionDef) or node.decorator_list:
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    args=[*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs]
    params=[arg.arg for arg in args if arg.arg not in {'self','cls'}]
    if len(params)>2 or node.args.vararg or node.args.kwarg:return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    types={}
    for arg in args:
        kind=_annotation_kind(arg.annotation)
        if kind:types[arg.arg]=kind
        if isinstance(arg.annotation,ast.Subscript) and isinstance(arg.annotation.value,ast.Name) and \
            arg.annotation.value.id in {'list','tuple'} and _annotation_kind(arg.annotation.slice)=='NUMBER':
            types[arg.arg+'_item']='NUMBER'
    if any(isinstance(z,(ast.Global,ast.Nonlocal,ast.Yield,ast.YieldFrom,ast.Await,ast.NamedExpr)) for z in ast.walk(node)):
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    scalars={p for p in params if types.get(p)=='NUMBER'}
    # A name can be considered a builtin list only if it has exactly one local
    # [] binding, and no global/nonlocal, alias rebinding, or loop binding.
    bindings={}
    for child in ast.walk(node):
        if isinstance(child,ast.Assign):
            for target in child.targets:
                if isinstance(target,ast.Name):
                    bindings.setdefault(target.id,[]).append(child.value)
        elif isinstance(child,ast.AnnAssign) and isinstance(child.target,ast.Name):
            bindings.setdefault(child.target.id,[]).append(child.value)
        elif isinstance(child,ast.For) and isinstance(child.target,ast.Name):
            bindings.setdefault(child.target.id,[]).append(None)
        elif isinstance(child,ast.AugAssign) and isinstance(child.target,ast.Name):
            bindings.setdefault(child.target.id,[]).append(None)
    local_lists=frozenset(name for name,values in bindings.items()
                          if len(values)==1 and isinstance(values[0],ast.List)
                          and not values[0].elts and name not in params)
    result=_describe(_sequence_cost(node.body,params,types,scalars,local_lists))
    if result[0]!='UNKNOWN' and any(isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute)
                                 and c.func.attr=='append' and isinstance(c.func.value,ast.Name)
                                 and c.func.value.id in local_lists for c in ast.walk(node)):
        return result[0],result[1],'AMORTIZED_APPEND'
    return result



# Narrow C-style loop-bound pattern (not arbitrary static cost analysis).
# Every loop must have a fixed-step, zero-origin counter and an exact n/m bound.
_FOR_HEADER=re.compile(r'\bfor\s*\(([^()]{1,160})\)\s*\{')
_C_FOR=re.compile(
    r'\s*(?:(?:int|size_t|long|unsigned|let|var|const)\s+)?(\$?[A-Za-z_]\w*)\s*=\s*0\s*;'
    r'\s*\1\s*<\s*(\$?[A-Za-z_]\w*)\s*;'
    r'\s*(?:\+\+\1|\1\+\+|\1\s*\+=\s*1)\s*\Z')

def _brace_numeric_only(stripped: str, bounds: list[str], counters: list[str]) -> bool:
    """Only scalar arithmetic statements; unknown type/operator ⇒ UNKNOWN.

    This is a conditional fixed-width primitive model, not C++ operator
    resolution, JavaScript type inference or a PHP interpreter.
    """
    source=stripped.replace('{',' ').replace('}',' ').strip()
    known=set(bounds+counters)
    number=r'(?:-?\d+(?:\.\d+)?)'
    ident=r'(?:\$?[A-Za-z_][A-Za-z0-9_]*)'
    declaration=r'(?:(?:int|long|short|size_t|unsigned|float|double|let|var|const)\s+)?'
    allowed_expr=re.compile(r'^[\s\w$+*/%()\-<>&|!.,]*$')
    def expr_ok(expr):
        if not expr or not allowed_expr.fullmatch(expr):return False
        if re.search(r'(?:\+\+|--|=>|<<|>>|\[|\]|\{|\}|\?|\:)',expr):return False
        names=re.findall(r'\$?[A-Za-z_][A-Za-z0-9_]*',expr)
        return all(n.lstrip('$') in known for n in names) and not re.search(r'[A-Za-z_]\w*\s*\(',expr)
    if not source:return True
    for piece in source.split(';'):
        stmt=piece.strip()
        if not stmt:continue
        if re.search(r'\b(?:if|else|switch|foreach|while|do|new|throw|try|catch|await|yield)\b',stmt):return False
        m=re.fullmatch(r'return\s+(.*)',stmt)
        if m:
            if not expr_ok(m.group(1)):return False
            continue
        m=re.fullmatch(declaration+r'(\$?[A-Za-z_]\w*)\s*=\s*('+number+r')',stmt)
        if m:
            known.add(m.group(1).lstrip('$'));continue
        m=re.fullmatch(r'(\$?[A-Za-z_]\w*)\s*(\+=|-=|\*=|/=|%=|=)\s*(.*)',stmt)
        if m and m.group(1).lstrip('$') in known and expr_ok(m.group(3)):
            continue
        return False
    return True


def brace_big_o(body:str)->tuple[str,str,str]:
    if re.search(r'\b(?:while|foreach|do|goto|yield|await|try|switch)\b',body):
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    loop_matches=list(_FOR_HEADER.finditer(body))
    # A malformed/unrecognized for-loop must not be mistaken for constant time.
    if len(re.findall(r'\bfor\s*\(',body))!=len(loop_matches):
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    if not loop_matches:
        # No loop is not enough to prove O(1): called functions/allocations vary.
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    if len(loop_matches)>12:
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    bounds=[];counters=[];opens=[];closes=[]
    for match in loop_matches:
        m=_C_FOR.fullmatch(match.group(1))
        if m is None:return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
        bound=m.group(2).lstrip('$')
        if bound not in ('n','m'):
            return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
        bounds.append(bound);counters.append(m.group(1).lstrip('$'))
        opening=match.end()-1
        closing=opening;depth=0
        for i in range(opening,len(body)):
            if body[i]=='{':depth+=1
            elif body[i]=='}':
                depth-=1
                if depth==0:closing=i;break
        if closing==opening:return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
        opens.append(opening);closes.append(closing)
    # Reject unknown calls to avoid claiming a simple bound despite complex work.
    # This intentionally excludes loop-header syntax already recognized above.
    stripped=body
    for match in reversed(loop_matches):
        stripped=stripped[:match.start()]+(' '*(match.end()-match.start()))+stripped[match.end():]
    # Reject updates to loop bounds or loop counters outside the approved
    # increment clause. Otherwise the loop may not terminate in O(n).
    for identifier in set(bounds+counters):
        if re.search(r'(?<![\w$])\$?'+re.escape(identifier)+
                r'\s*(?:\+\+|--|[+*/%-]?=(?!=))|(?:\+\+|--)\s*\$?'+
                re.escape(identifier)+r'\b',stripped):
            return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    if re.search(r'\b[A-Za-z_$][\w$]*\s*\(',stripped):
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    # Only nested loops contribute multiplicative factors.
    monomials=set()
    for i,bound in enumerate(bounds):
        factor_n=1 if bound=='n' else 0
        factor_m=1 if bound=='m' else 0
        for j in range(len(bounds)):
            if i!=j and opens[j]<opens[i]<closes[i]<closes[j]:
                if bounds[j]=='n':factor_n+=1
                else:factor_m+=1
        if factor_n+factor_m>3:return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
        monomials.add((factor_n,factor_m,0))
    if not _brace_numeric_only(stripped,bounds,counters):
        return 'UNKNOWN','UNKNOWN','UNSUPPORTED_CONSTRUCTS'
    return _describe(frozenset(monomials))


def complexity(span:FunctionSpan)->tuple[int,int,str,str,str]:
    if span.method=='PYTHON_AST':
        cc,depth=python_complexity(span.node)
        bo,conf,reason=python_big_o(span.node)
        return cc,depth,bo,conf,reason
    cc,depth=brace_complexity(span.body)
    bo,conf,reason=brace_big_o(span.body)
    return cc,depth,bo,conf,reason
