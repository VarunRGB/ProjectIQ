"""Source-grounded compiler for the narrow EJS in-memory record profile.
Reuses Phase 9A discovery; does not authorize execution or prove absence of side effects.
"""
import re
from collections import Counter
from ..phase9a import planner as p
from .adaptive_runner import validate_workflow
from .compiler import require


def reject(ok):require(ok,'ADAPTIVE_SOURCE_PROFILE_UNSUPPORTED')


def handler(text,method,path,line):
    tok=p.tokens(text);ts=[t for t,_ in tok];hits=[]
    for i in range(len(ts)-13):
        if ts[i+1:i+4]!=['.',method.lower(),'('] or p.plain_string(ts[i+4])!=path or ts[i+5]!=',':continue
        if text.count('\n',0,tok[i][1])+1!=line:continue
        header=ts[i+6:i+13]
        if len(header)!=7 or header[0]!='(' or header[2]!=',' or header[4:]!=[')','=>','{']:continue
        close=p.matching(ts,i+12);end=p.matching(ts,i+3)
        if close is not None and end==close+1:hits.append((header[1],header[3],ts[i+13:close]))
    reject(len(hits)==1);return hits[0]


def render_binding(body,res):
    matches=[]
    for i in range(len(body)-8):
        if body[i:i+4]!=[res,'.','render','(']:continue
        name=p.plain_string(body[i+4])
        if name and re.fullmatch(r'[A-Za-z0-9_-]+\.ejs',name) and body[i+5:i+7]==[',','{'] and p.FIELD.fullmatch(body[i+7]) and body[i+8:i+10]==['}',')']:
            matches.append((name,body[i+7]))
    reject(len(matches)==1);return matches[0]


def template(root,prefix,name):
    raw=p.source_bytes(root,prefix+'views/'+name,limit=p.MAX_TEMPLATE);reject(raw is not None)
    try:return raw.decode('utf-8-sig')
    except UnicodeError:raise ValueError('ADAPTIVE_SOURCE_PROFILE_UNSUPPORTED') from None


def compile_workflow(root,proposal,component):
    reject(component['adapter']=='NODE_HTTP' and component['status']=='REVIEWED_CANDIDATE' and not component['blockers'] and not component.get('service_hints') and not proposal['issues'])
    matches=[w for w in proposal['workflows'] if w['component_id']==component['id'] and not w['runtime_blockers']]
    reject(len(matches)==1);w=matches[0];steps=w['steps'];reject([s['role'] for s in steps]==['CREATE','LIST_CREATED','READ_CREATED','UPDATE','READ_UPDATED','DELETE','VERIFY_REMOVED'])
    create,listing,read,update,_,delete,_=steps
    reject(len(create['input_fields'])==len(update['input_fields'])==1)
    cf,uf=create['input_fields'][0],update['input_fields'][0]
    reject(len(w['matching_create_forms'])==1 and w['matching_create_forms'][0]['fields']==[cf])
    prefix='' if component['root']=='.' else component['root']+'/'
    entry=prefix+component['entrypoint'];raw=p.source_bytes(root,entry,limit=512_000);reject(raw is not None)
    try:text=raw.decode('utf-8-sig')
    except UnicodeError:raise ValueError('ADAPTIVE_SOURCE_PROFILE_UNSUPPORTED') from None
    direct=p.express_routes(root,raw,entry,component['root']);counts=Counter((r['method'],r['path']) for r in direct)
    routes={(r['method'],r['path']):r for r in direct};handlers={}
    for s in (create,listing,read,update,delete):
        key=(s['method'],s['path_template']);reject(counts[key]==1);r=routes[key]
        ev=r['evidence'][0];reject(ev['path']==entry)
        handlers[s['role']]=handler(text,*key,ev['line'])
        if s['role'] in ('CREATE','UPDATE','DELETE'):reject(r['redirect']==listing['path_template'])
    _,lr,lb=handlers['LIST_CREATED'];lt,collection=render_binding(lb,lr)
    rq,rr,rb=handlers['READ_CREATED'];rt,record=render_binding(rb,rr)
    # Conservative source profile: declared array, urlencoded forms, direct find by id.
    tokens=[t for t,_ in p.tokens(text)];joined=' '.join(tokens)
    reject(bool(re.search(r'\b(?:const|let|var) '+re.escape(collection)+r' = \[',joined)))
    reject(bool(re.search(r'\burlencoded \(',joined)))
    read_body=' '.join(rb)
    direct_id=' '.join([rq,'.','params','.','id']) in read_body
    destructured_id=bool(re.search(r'\b(?:const|let|var) \{ id \} = '+re.escape(rq)+r' \. params\b',read_body))
    reject(' '.join([collection,'.','find','(']) in read_body and (direct_id or destructured_id))
    list_text=template(root,prefix,lt);detail=template(root,prefix,rt)
    # Exact escaped interpolation only: no raw HTML or computed values in observation cells.
    loop=re.compile(r'<%\s*for\s*\(\s*(?:(?:const|let|var)\s+)?([A-Za-z_]\w*)\s+of\s+'+re.escape(collection)+r'\s*\)\s*\{\s*%>(.*?)<%\s*\}\s*%>',re.S)
    loops=list(loop.finditer(list_text));reject(len(loops)==1);m=loops[0];item=m[1];inside=m[2]
    row_pattern=r'<h3>\s*([A-Za-z0-9 _-]{1,32}:)\s*<%=\s*'+re.escape(item)+r'\.id\s*%>\s*</h3>\s*<p>\s*([A-Za-z0-9 _-]{1,32}:)\s*<%=\s*'+re.escape(item)+r'\.([A-Za-z_]\w*)\s*%>\s*</p>'
    row=list(re.finditer(row_pattern,inside));reject(len(row)==1);row=row[0];id_label,value_label,value_field=row[1],row[2],row[3]
    # No other candidate record tags inside or outside the loop.
    remaining=list_text[:m.start()]+inside[:row.start()]+inside[row.end():]+list_text[m.end():]
    reject(not re.search(r'</?(?:h3|p)(?:\s|>)',remaining,re.I))
    detail_pattern=r'<h1>\s*<%=\s*'+re.escape(record)+r'\.id\s*%>\s*</h1>\s*<p>\s*<%=\s*'+re.escape(record)+r'\.'+re.escape(value_field)+r'\s*%>\s*</p>'
    ds=list(re.finditer(detail_pattern,detail));reject(len(ds)==1);d=ds[0]
    reject(not re.search(r'</?(?:h1|p)(?:\s|>)',detail[:d.start()]+detail[d.end():],re.I))
    # Same field observed by both list and detail; no claim that implementation is correct.
    reject(cf==value_field)
    return validate_workflow({'workflow_id':w['id'],'list_path':listing['path_template'],'create_path':create['path_template'],'read_template':read['path_template'],'update_template':update['path_template'],'update_method':update['method'],'delete_template':delete['path_template'],'create_field':cf,'update_field':uf,'id_label':id_label,'value_label':value_label,'format':'EJS_H3_P_H1_P_V1'})
