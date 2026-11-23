"""Function discovery with explicit AST vs approximate brace-parser provenance."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import re

from .lexical import line_at, mask_cstyle


@dataclass(frozen=True)
class FunctionSpan:
    name: str
    start_line: int
    end_line: int
    method: str
    node: ast.AST | None = None
    body: str = ''


class _PythonFunctions(ast.NodeVisitor):
    def __init__(self):self.items=[];self.stack=[]

    def visit_ClassDef(self,node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self,node):self._add(node)
    def visit_AsyncFunctionDef(self,node):self._add(node)

    def _add(self,node):
        name='.'.join([*self.stack,node.name])
        self.items.append(FunctionSpan(name[:120],node.lineno,node.end_lineno or node.lineno,
                                       'PYTHON_AST',node))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


def python_functions(text: str) -> tuple[list[FunctionSpan],bool]:
    try:tree=ast.parse(text,mode='exec')
    except (SyntaxError,ValueError,MemoryError,RecursionError):return [],False
    visitor=_PythonFunctions()
    try:visitor.visit(tree)
    except (RecursionError,MemoryError):return [],False
    return visitor.items,True


_IDENT=r'[A-Za-z_$][A-Za-z0-9_$]*'
# Deliberately strict: unsupported syntactic forms are left unreported, not guessed.
_JS_FUNC=re.compile(r'\b(?:async\s+)?function\s*\*?\s*('+_IDENT+r')\s*\(',re.M)
_JS_ARROW=re.compile(r'\b(?:const|let|var)\s+('+_IDENT+r')\s*=\s*(?:async\s*)?(?:\([^{};]*\)|'+_IDENT+r')\s*=>\s*\{',re.M)
_PHP_FUNC=re.compile(r'\bfunction\s+&?\s*('+_IDENT+r')\s*\(',re.M)
_METHOD=re.compile(r'(?m)^[ \t]*(?:(?:public|private|protected|static|final|abstract|async|override|readonly|virtual|inline|constexpr|friend)\s+)*('+_IDENT+r')\s*\([^;{}]{0,300}\)\s*(?:(?::\s*[^{};]{1,100})|(?:const|noexcept))?\s*\{')
_CPP_FUNC=re.compile(r'(?m)^[ \t]*(?:(?:template\s*<[^\n{};]{1,100}>\s*)?)(?:[\w:<>~,&*]+[ \t]+){1,8}([A-Za-z_~][\w:]*)\s*\([^\n{};]{0,280}\)\s*(?:(?:const|noexcept|override|final)\s*)?\{')
_METHOD_INLINE=re.compile(r'(?<=\{)[ \t]*(?:(?:public|private|protected|static|final|async)\s+)*('+_IDENT+r')\s*\([^;{}]{0,300}\)\s*(?:(?::\s*[^{};]{1,100})|(?:const|noexcept))?\s*\{')
_FORBID=frozenset({'if','else','while','for','foreach','switch','catch','with','try','return',
                   'new','do','sizeof','typeof','delete','await','function','throw','using',
                   'class','struct','namespace','namespace','match','select','case','yield'})


def _find_brace(m: str,start: int)->int|None:
    i=m.find('{',start,min(len(m),start+350))
    return i if i>=0 else None


def _closing_brace(mask: str,op: int) -> int | None:
    depth=0
    for i in range(op,len(mask)):
        if mask[i]=='{':depth+=1
        elif mask[i]=='}':
            depth-=1
            if depth==0:return i
    return None


def brace_functions(text: str,language: str) -> list[FunctionSpan]:
    if len(text)>350_000:return []
    masked,_=mask_cstyle(text,php=(language=='PHP'))
    expressions=[]
    if language in ('JavaScript','TypeScript'):
        expressions=[_JS_FUNC,_JS_ARROW,_METHOD,_METHOD_INLINE]
    elif language=='PHP':expressions=[_PHP_FUNC,_METHOD,_METHOD_INLINE]
    elif language in ('C','C++','C/C++'):expressions=[_CPP_FUNC]
    else:return []
    braces={};stack=[]
    for i,char in enumerate(masked):
        if char=='{':stack.append(i)
        elif char=='}' and stack:
            start=stack.pop();braces[start]=i
    candidates=[];used=set()
    for priority,pat in enumerate(expressions):
        for match in pat.finditer(masked):
            name=match.group(1)
            if name.lower() in _FORBID or (name.startswith('__') and len(name)>70):continue
            # Arrow-function regex already ends at its opening brace.
            brace=match.end()-1 if masked[match.end()-1]=='{' else _find_brace(masked,match.end())
            if brace is None or masked[brace]!='{':continue
            # A header must not have a semicolon before its opening brace.
            if ';' in masked[match.end():brace]:continue
            close=braces.get(brace)
            if close is None or close-brace>300_000:continue
            if brace in used:continue
            used.add(brace)
            line=line_at(masked,match.start())
            end=line_at(masked,close)
            if end<line:continue
            candidates.append((line,priority,FunctionSpan(name[:120],line,end,
                                            'BRACE_HEURISTIC',None,masked[brace+1:close])))
    candidates.sort(key=lambda x:(x[0],x[1],x[2].end_line))
    return [x[2] for x in candidates]


def discover(text: str,lang: str) -> tuple[list[FunctionSpan],str,str|None]:
    if lang=='Python':
        items,valid=python_functions(text)
        return (items,'AST',None) if valid else ([], 'NOT_ANALYZED','SYNTAX_ERROR')
    if lang in ('JavaScript','TypeScript','PHP','C++','C/C++','C'):
        return brace_functions(text,lang),'HEURISTIC_PARTIAL',None
    return [],'UNSUPPORTED','UNSUPPORTED_LANGUAGE'
