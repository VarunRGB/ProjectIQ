"""Small bounded lexical masks for physical LOC and evidence-scoped heuristics.

Not a substitute for a language parser. Template interpolation, regular
expressions, macros, raw strings and heredocs can limit confidence.
"""
from __future__ import annotations

import io
import tokenize


def python_lines(text: str) -> tuple[int,int,int,int]:
    rows=text.splitlines()
    blank=sum(not r.strip() for r in rows)
    comments=set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type==tokenize.COMMENT:
                line=tok.start[0]
                if 1<=line<=len(rows) and not rows[line-1][:tok.start[1]].strip():
                    comments.add(line)
    except (tokenize.TokenError,IndentationError,SyntaxError):
        # In malformed source, count only unmistakable standalone # lines.
        comments={i for i,r in enumerate(rows,1) if r.lstrip().startswith('#')}
    return len(rows),len(rows)-blank-len(comments),blank,len(comments)


def mask_cstyle(text: str, *, php: bool=False) -> tuple[str, set[int]]:
    """Blank strings and comments, retaining offsets and line breaks.

    For syntax unsupported by this mask, downstream function discovery is
    explicitly HEURISTIC_PARTIAL and complexity UNKNOWN where ambiguous.
    """
    out=list(text); standalone=set();i=0;n=len(text);line=1;state='code'
    saw_code=False;comment_line=None
    while i<n:
        c=text[i];nx=text[i+1] if i+1<n else ''
        if c=='\n':
            if state=='line':state='code'
            if comment_line==line and not saw_code:standalone.add(line)
            line+=1; saw_code=False;comment_line=None;i+=1;continue
        if state=='line':out[i]=' ';i+=1;continue
        if state=='block':
            out[i]=' '
            if c=='*' and nx=='/':out[i+1]=' ';i+=2;state='code';continue
            i+=1;continue
        if state in ('single','double','template'):
            out[i]=' '
            if c=='\\' and i+1<n:
                if text[i+1]!='\n':out[i+1]=' '
                i+=2;continue
            if c=={'single':"'",'double':'"','template':'`'}[state]:state='code'
            i+=1;continue
        # cstyle strings, including plain and template literals; PHP # comments
        if c=='/' and nx=='/':
            comment_line=line
            out[i]=out[i+1]=' ';i+=2;state='line';continue
        if c=='/' and nx=='*':
            comment_line=line
            out[i]=out[i+1]=' ';i+=2;state='block';continue
        if php and c=='#':
            comment_line=line;out[i]=' ';i+=1;state='line';continue
        if c in "'\"`":
            out[i]=' ';state={"'":'single','"':'double','`':'template'}[c];i+=1;continue
        if not c.isspace():saw_code=True
        i+=1
    if comment_line==line and not saw_code:standalone.add(line)
    return ''.join(out),standalone


def line_at(source:str,pos:int)->int:
    return source.count('\n',0,pos)+1


def _line_kinds(text: str, lang: str) -> list[str]:
    """Classify physical lines without importing or executing repository source.

    This is a bounded lexer for comments, not a syntax or quality checker.
    An ambiguous line is counted as code rather than falsely dropping it.
    """
    if lang == 'Python':
        rows = text.splitlines()
        comments: set[int] = set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT and 1 <= tok.start[0] <= len(rows):
                    if not rows[tok.start[0]-1][:tok.start[1]].strip():
                        comments.add(tok.start[0])
        except (tokenize.TokenError, SyntaxError, IndentationError):
            comments = {i for i,row in enumerate(rows,1) if row.lstrip().startswith('#')}
        return [('BLANK' if not row.strip() else 'COMMENT' if i in comments else 'CODE')
                for i,row in enumerate(rows,1)]

    rows = text.splitlines()
    result: list[str] = []
    state = 'normal'
    for row in rows:
        if not row.strip():
            result.append('BLANK')
            continue
        code = False
        i = 0
        while i < len(row):
            c = row[i]
            nx = row[i+1] if i+1 < len(row) else ''
            if state == 'block':
                if c == '*' and nx == '/':
                    state = 'normal'
                    i += 2
                else:
                    i += 1
                continue
            if state == 'html':
                if row.startswith('-->', i):
                    state = 'normal'
                    i += 3
                else:
                    i += 1
                continue
            if state in ('single','double','template'):
                code = True
                quote = {'single':"'", 'double':'"', 'template':'`'}[state]
                if c == '\\':
                    i += 2
                elif c == quote:
                    state = 'normal'
                    i += 1
                else:
                    i += 1
                continue
            if lang == 'HTML' and row.startswith('<!--',i):
                state = 'html'
                i += 4
                continue
            if lang != 'HTML' and c == '/' and nx == '*':
                state = 'block'
                i += 2
                continue
            if lang in {'Python','JavaScript','TypeScript','PHP','C','C++','C/C++','Java','Go','Rust','C#','Kotlin','Swift','Dart','Shell'} and c == '/' and nx == '/':
                break
            if lang == 'SQL' and c == '-' and nx == '-' and (i+2 == len(row) or row[i+2].isspace()):
                break
            if (lang in {'PHP','SQL','Shell'} and c == '#'):
                break
            if c in "'\"`" and lang != 'HTML':
                state = {"'":'single','"':'double','`':'template'}[c]
                code = True
                i += 1
                continue
            if not c.isspace():
                code = True
            i += 1
        result.append('CODE' if code else 'COMMENT')
    return result


def code_line_numbers(text: str, lang: str) -> set[int]:
    return {i for i, kind in enumerate(_line_kinds(text, lang),1) if kind=='CODE'}


# Preserve original public interface but use the corrected three-way classifier.
def source_lines(text: str,lang: str) -> tuple[int,int,int,int]:
    kinds = _line_kinds(text,lang)
    return (len(kinds), kinds.count('CODE'), kinds.count('BLANK'), kinds.count('COMMENT'))
