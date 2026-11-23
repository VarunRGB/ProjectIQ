"""Evidence-based quality diagnostics without executing linter configurations."""
from __future__ import annotations

import ast
import re

from .functions import FunctionSpan
from .lexical import mask_cstyle

# Messages are fixed and bounded; no repository content is copied into output.
_MESSAGES={
 'LONG_FUNCTION':('MEDIUM','Long function may be harder to maintain.',
                  'Consider splitting responsibilities into smaller functions.'),
 'VERY_LONG_FUNCTION':('HIGH','Very long function is a maintainability hotspot.',
                       'Extract cohesive operations and reduce shared mutable state.'),
 'HIGH_CYCLOMATIC':('MEDIUM','Function has many independent decision paths.',
                    'Simplify branching or extract well-named helper functions.'),
 'POTENTIAL_COMPLEX_BRANCHING':('ADVISORY','Approximate branching count is high.',
                    'Review complex conditional logic with a full language-aware tool.'),
 'DEEP_NESTING':('MEDIUM','Deeply nested control flow is hard to follow.',
                 'Consider early returns or smaller helper functions.'),
 'POTENTIAL_BRACE_NESTING':('ADVISORY','Brace nesting is deep; object literals may be included.',
                 'Review whether the nested statements are actually complex.'),
 'PY_BARE_EXCEPT':('MEDIUM','Bare except catches system-exiting exceptions.',
                   'Catch the specific expected exception types.'),
 'PY_MUTABLE_DEFAULT':('MEDIUM','Mutable parameter default may retain state across calls.',
                       'Use None as the default and create a fresh container per call.'),
 'PY_WILDCARD_IMPORT':('LOW','Wildcard import makes dependencies harder to inspect.',
                       'Import explicitly named symbols.'),
 'PY_EVAL_EXEC':('HIGH','Dynamic evaluation complicates reasoning about behavior.',
                 'Prefer explicit functions or a constrained parser.'),
 'JS_VAR_DECLARATION':('LOW','Function-scoped var declarations can obscure scope.',
                       'Consider let or const where semantics permit.'),
 'JS_DEBUGGER':('LOW','Debugger statement remains in source.',
                'Remove debugging statements before release.'),
 'JS_LOOSE_EQUALITY':('ADVISORY','Loose equality may involve implicit coercion.',
                      'Prefer explicit type handling when strict equality is intended.'),
 'PHP_DYNAMIC_EVAL':('HIGH','Dynamic evaluation complicates auditability.',
                      'Replace dynamic evaluation with explicit data handling.'),
 'PHP_LEGACY_MYSQL':('MEDIUM','Legacy mysql API may not work on modern PHP.',
                     'Prefer supported PDO or MySQLi APIs.'),
 'C_UNSAFE_GETS':('HIGH','Unbounded gets can overflow a destination buffer.',
                  'Use bounded input handling.'),
 'PY_EMPTY_EXCEPT':('MEDIUM','Exception handler silently discards errors.',
                      'Handle the expected exception or document intentional suppression.'),
 'PY_UNREACHABLE_STATEMENT':('LOW','Statement appears after an unconditional return or raise.',
                             'Remove unreachable statements or fix control flow.'),
 'PY_NONE_COMPARISON':('ADVISORY','Equality comparison to None can be confusing.',
                       'Use is None or is not None where identity is intended.'),
 'JS_EVAL':('HIGH','Dynamic JavaScript evaluation complicates security review.',
            'Avoid eval; use explicit data parsing and safe APIs.'),
 'JS_CONSOLE_LOG':('ADVISORY','Console logging remains in application source.',
                   'Check whether runtime logging exposes sensitive information.'),
 'PHP_ERROR_SUPPRESSION':('ADVISORY','PHP error suppression hides failures.',
                          'Handle the expected error explicitly.'),
 'C_STRCPY':('ADVISORY','Unbounded strcpy may overflow a destination buffer.',
             'Use a bounded copy with explicit destination size checks.'),
 'C_SPRINTF':('ADVISORY','Unbounded sprintf may overflow a destination buffer.',
              'Use snprintf with validated bounds.'),
 'LONG_SOURCE_LINE':('ADVISORY','Very long source line reduces readability.',
                     'Consider splitting the statement into smaller expressions.'),
 'TRAILING_WHITESPACE':('LOW','Trailing whitespace in source file.',
                        'Remove unnecessary trailing whitespace.'),
}


def _add(out:list, code:str,path:str,line:int,confidence='HIGH'):
    sev,msg,rec=_MESSAGES[code]
    out.append({'code':code,'severity':sev,'path':path,'line':line,'message':msg,
                'confidence':confidence,'recommendation':rec})


def _ast_lints(node:ast.AST,path:str,out:list):
    for item in ast.walk(node):
        line=getattr(item,'lineno',1)
        if isinstance(item,ast.ExceptHandler) and item.type is None:
            _add(out,'PY_BARE_EXCEPT',path,line)
        if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)):
            all_defaults=[*item.args.defaults,*[v for v in item.args.kw_defaults if v is not None]]
            for d in all_defaults:
                if isinstance(d,(ast.List,ast.Dict,ast.Set)):
                    _add(out,'PY_MUTABLE_DEFAULT',path,getattr(d,'lineno',line))
        if isinstance(item,ast.ImportFrom) and any(x.name=='*' for x in item.names):
            _add(out,'PY_WILDCARD_IMPORT',path,line)
        if isinstance(item,ast.Call) and isinstance(item.func,ast.Name) and item.func.id in {'eval','exec'}:
            _add(out,'PY_EVAL_EXEC',path,line,'MEDIUM')
        if isinstance(item,ast.ExceptHandler) and len(item.body)==1 and isinstance(item.body[0],ast.Pass):
            _add(out,'PY_EMPTY_EXCEPT',path,line)
        if isinstance(item,ast.Compare) and any(isinstance(c,ast.Constant) and c.value is None for c in [item.left,*item.comparators]) and \
                any(isinstance(op,(ast.Eq,ast.NotEq)) for op in item.ops):
            _add(out,'PY_NONE_COMPARISON',path,line,'MEDIUM')
        if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef,ast.If,ast.For,ast.While,ast.With,ast.Try)):
            for block in ([item.body] if hasattr(item,'body') else []):
                for a,b in zip(block,block[1:]):
                    if isinstance(a,(ast.Return,ast.Raise)):
                        _add(out,'PY_UNREACHABLE_STATEMENT',path,getattr(b,'lineno',line))
                        break


def language_lints(text:str,lang:str,path:str)->list:
    found=[]
    if lang=='Python':
        try:_ast_lints(ast.parse(text),path,found)
        except (SyntaxError,ValueError,RecursionError):pass
    else:
        mask,_=mask_cstyle(text,php=(lang=='PHP'))
        patterns=[]
        if lang in ('JavaScript','TypeScript'):
            patterns=[('JS_VAR_DECLARATION',r'\bvar\s+[A-Za-z_$]'),
                      ('JS_DEBUGGER',r'\bdebugger\s*;'),
                      ('JS_EVAL',r'\beval\s*\('),
                      ('JS_CONSOLE_LOG',r'\bconsole\s*\.\s*log\s*\('),
                      ('JS_LOOSE_EQUALITY',r'(?<![=!])==(?!=)|(?<![!=])!=(?!=)')]
        elif lang=='PHP':patterns=[('PHP_DYNAMIC_EVAL',r'\beval\s*\('),
                                   ('PHP_ERROR_SUPPRESSION',r'(?<![\w$])@[A-Za-z_][\w]*\s*\('),
                                   ('PHP_LEGACY_MYSQL',r'\bmysql_query\s*\(')]
        elif lang in ('C','C++','C/C++'):
            patterns=[('C_UNSAFE_GETS',r'\bgets\s*\('),
                      ('C_STRCPY',r'\bstrcpy\s*\('),
                      ('C_SPRINTF',r'\bsprintf\s*\(')]
        for code,rx in patterns:
            for m in list(re.finditer(rx,mask))[:40]:
                _add(found,code,path,mask.count('\n',0,m.start())+1,
                     'MEDIUM' if code in ('JS_LOOSE_EQUALITY','C_UNSAFE_GETS') else 'HIGH')
    trailing=[i for i,row in enumerate(text.splitlines(),1) if row.endswith((' ','\t'))]
    # Only one finding per file avoids polluting the report with formatting noise.
    if trailing:_add(found,'TRAILING_WHITESPACE',path,trailing[0])
    long=[i for i,row in enumerate(text.splitlines(),1) if len(row)>180 and not row.lstrip().startswith(('http','//','#'))]
    if long:_add(found,'LONG_SOURCE_LINE',path,long[0],'LOW')
    return found


def structural_findings(spans:list[FunctionSpan],metrics:list[tuple[int,int]],path:str)->list:
    out=[]
    for fn,(cc,nest) in zip(spans,metrics):
        lines=fn.end_line-fn.start_line+1
        if lines>=100:_add(out,'VERY_LONG_FUNCTION',path,fn.start_line)
        elif lines>=60:_add(out,'LONG_FUNCTION',path,fn.start_line)
        if cc>=12:
            code='HIGH_CYCLOMATIC' if fn.method=='PYTHON_AST' else 'POTENTIAL_COMPLEX_BRANCHING'
            _add(out,code,path,fn.start_line,
                 'HIGH' if fn.method=='PYTHON_AST' else 'LOW')
        if nest>=5:
            code='DEEP_NESTING' if fn.method=='PYTHON_AST' else 'POTENTIAL_BRACE_NESTING'
            _add(out,code,path,fn.start_line,
                 'HIGH' if fn.method=='PYTHON_AST' else 'LOW')
    return out


def stable_findings(items:list[dict],max_findings:int=250, *, with_truncation:bool=False):
    # Dedupe and stable-order across input inventory permutations.
    seen=set();ordered=[]
    for x in sorted(items,key=lambda d:(d['path'],d['line'],d['code'])):
        key=(x['path'],x['line'],x['code'])
        if key in seen:continue
        seen.add(key);ordered.append(x)
    return (ordered[:max_findings],len(ordered)>max_findings) if with_truncation else ordered[:max_findings]
