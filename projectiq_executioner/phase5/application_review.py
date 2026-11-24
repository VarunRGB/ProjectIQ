"""Conservative local source-review indicators, no execution or value disclosure.

These are review candidates, not a complete dataflow/security audit. Inherited
middleware, rewrites, and other runtime controls may change interpretation.
"""
from __future__ import annotations
from html.parser import HTMLParser
import posixpath
import re
from .lexical import mask_cstyle


def _finding(out, file, line, code, severity, message, recommendation, confidence='MEDIUM'):
    out.append({'path':file.path,'line':line,'code':code,'severity':severity,'message':message,
                'recommendation':recommendation,'confidence':confidence})

class _Forms(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.actions=[];self.base=False
    def handle_starttag(self,tag,attrs):
        if tag=='base':self.base=True
        if tag=='form':
            action=dict(attrs).get('action')
            if isinstance(action,str):self.actions.append((self.getpos()[0],action))

def review_application(files,index):
    out=[]; paths=set(index.files)
    for f in files:
        if len(out)>=200:break
        text=f.text
        if len(text)>350_000:continue
        if f.language=='PHP':
            mask,_=mask_cstyle(text,php=True)
            # Look only at file-backed handlers, and flag absent *local* guards.
            handler=bool(re.search(r'(?:^|/)(?:api|auth|logic|controllers)/',f.path,re.I))
            def line(m):return text.count('\n',0,m.start())+1
            def active(pattern, flags=0):
                return next((m for m in re.finditer(pattern,text,flags) if mask[m.start():m.start()+1].strip()),None)
            sql=active(r'\b(?:prepare|query)\s*\(\s*[\'\"]\s*(DELETE\s+FROM|SELECT\s+[^\'\"]{1,500}?\s+FROM|UPDATE\s+|INSERT\s+INTO)',re.I|re.S)
            if handler and sql and not re.search(r'\$_SESSION\b|\bsession_start\s*\(|\b(?:authorize|authenticate|requireAuth|checkAuth)\s*\(',mask,re.I):
                if not re.search(r'\bpassword_(?:hash|verify)\s*\(',mask):
                    _finding(out,f,line(sql),'SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN','HIGH',
                             'Database-backed handler has no recognized local session/authorization guard. External guards were not verified.',
                             'Review server-side access control and test unauthenticated and unauthorized requests.')
            role=active(r'\$_POST\s*\[\s*[\'\"]role[\'\"]\s*\]',re.I)
            if role and re.search(r'\bINSERT\s+INTO\b[^;]{0,500}\brole\b',text,re.I) and not re.search(r'\$_SESSION\b|\b(?:authorize|requireAuth)\s*\(',mask,re.I):
                _finding(out,f,line(role),'SEC_PHP_CLIENT_ROLE_INPUT','HIGH','Client-provided role is read in a user insertion handler; privileged role assignment requires independent review.',
                         'Set public registration roles server-side and restrict privileged role changes to authorized administrators.')
            exc=active(r'\b(?:echo|die|print)\b[^;]{0,250}->getMessage\s*\(',re.I)
            if exc:_finding(out,f,line(exc),'SEC_PHP_EXCEPTION_DISCLOSURE','MEDIUM','An exception message may be returned to the client.','Log detailed exceptions privately; return a generic client error.')
            if re.search(r'\bnew\s+PDO\s*\(',mask,re.I):
                literal=active(r'\$(?:pass(?:word)?|db_pass(?:word)?)\s*=\s*[\'\"][^\'\"\r\n]*[\'\"]',re.I)
                if literal:_finding(out,f,line(literal),'SEC_DB_LITERAL_CONFIG','MEDIUM','Literal database credential configuration found; validity was not checked.','Use operator-managed environment configuration; rotate real exposed credentials.')
            session_read=active(r'(\$[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\$_SESSION\s*\[\s*[\'\"]user_id[\'\"]\s*\]\s*;',re.I)
            if handler and sql and session_read and not re.search(r'\b(?:isset|empty|authorize|requireAuth|checkAuth)\s*\(|\bif\s*\([^\n;]{0,200}'+re.escape(session_read.group(1)),mask,re.I):
                _finding(out,f,line(session_read),'SEC_PHP_SESSION_GUARD_NOT_SEEN','MEDIUM','Direct session user identifier read without a recognized local guard. External enforcement was not verified.','Check session presence before using the identity and test anonymous requests.')
            # No claim that a successful execute proves rows were updated/deleted.
            if handler and sql and sql.group(1).upper().startswith(('UPDATE','DELETE')) and re.search(r'->execute\s*\(',mask) and not re.search(r'->rowCount\s*\(',mask,re.I) and re.search(r'[\'\"]success[\'\"]\s*=>\s*true',text,re.I):
                _finding(out,f,line(sql),'PHP_MUTATION_ROW_OUTCOME_UNCHECKED','MEDIUM','Handler reports success but no affected-row check was recognized. Zero rows can be a valid or misleading outcome.','Define the zero-row/idempotency contract and verify ownership and database effects.')
        elif f.language in ('JavaScript','TypeScript'):
            mask,_=mask_cstyle(text)
            for m in list(re.finditer(r'\.innerHTML\s*=\s*`[^`]{0,12000}\$\{',text,re.S))[:10]:
                if not mask[m.start():m.start()+1].strip():continue
                _finding(out,f,text.count('\n',0,m.start())+1,'SEC_DOM_TEMPLATE_HTML','HIGH','Interpolated template assigned to innerHTML; data provenance and escaping require review.','Use textContent or safe DOM construction for untrusted fields; verify context-appropriate sanitization.')
        elif f.language=='HTML':
            p=_Forms()
            try:p.feed(text)
            except (ValueError,RecursionError):continue
            if p.base:continue
            for n,a in p.actions[:20]:
                if not re.fullmatch(r'(?:\.\./|\./)*[A-Za-z0-9_./-]+\.php',a,re.I):continue
                resolved=posixpath.normpath(posixpath.join(posixpath.dirname(f.path),a))
                if resolved in paths or resolved.startswith('../'):continue
                alternatives=[x for x in paths if posixpath.basename(x)==posixpath.basename(resolved)]
                if len(alternatives)==1:
                    _finding(out,f,n,'HTML_FORM_TARGET_NOT_FOUND','MEDIUM','Relative PHP form target is absent in the indexed tree; a same-named handler exists elsewhere. Rewrites were not verified.','Check form action relative to the served page and verify registration/submission in a browser.')
    return out[:200]


def merge_credential_indicators(security, application_findings):
    """Add redacted literal DB config locations to the same security contract."""
    extra=[{'code':'LITERAL_DB_CONFIGURATION','path':f['path'],'line':f['line'],'confidence':'MEDIUM'}
           for f in application_findings if f['code']=='SEC_DB_LITERAL_CONFIG']
    if not extra:return security
    merged={(f['code'],f['path'],f['line']):f for f in security['findings']+extra}
    rows=sorted(merged.values(),key=lambda x:(x['path'],x['line'] or 0,x['code']))
    result=dict(security)
    result['findings']=rows[:200]
    result['truncated']=security['truncated'] or len(rows)>200
    result['tracked_sensitive_files']=sum(f['line'] is None for f in result['findings'])
    result['status']='PARTIAL' if result['truncated'] or security['status']=='PARTIAL' else 'INDICATORS_FOUND'
    return result


def reduce_generated_dump_noise(file, findings):
    """Do not treat a generated dump's INSERT line length as a code defect."""
    if file.language!='SQL' or not re.search(r'^--\s*(?:MySQL|MariaDB) dump\b',file.text[:2048],re.I|re.M):
        return findings
    lines=file.text.splitlines()
    return [f for f in findings if not (f.get('code')=='LONG_SOURCE_LINE' and
             1<=f.get('line',0)<=len(lines) and re.match(r'\s*INSERT\s+INTO\b',lines[f['line']-1],re.I))]
