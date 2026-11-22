"""4D-B offline, no-auto-approval review proposal for six source-discovered API steps."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .oracle_stage import _load_strict_json


_OPERATIONS=(('CREATE','POST','/logic/add_task.php'),
             ('READ_CREATED','GET','/logic/get_tasks.php'),
             ('UPDATE_CAPTURED','POST','/logic/edit_task.php'),
             ('READ_UPDATED','GET','/logic/get_tasks.php'),
             ('DELETE_CAPTURED','POST','/logic/delete_task_user.php'),
             ('VERIFY_DELETED','GET','/logic/get_tasks.php'))


def stage(plan:dict)->dict:
    if (plan.get('source',{}).get('kind')!='github'
            or plan.get('execution_context',{}).get('adapter')!='php_mysql_v1'):
        raise ValueError('CRUD_REQUIRES_REVIEWED_PHP_PLAN')
    steps=[]
    for role,method,path in _OPERATIONS:
        matches=[c for c in plan.get('test_cases',[]) if isinstance(c,dict)
                 and c.get('category') in ('HTTP_ROUTE','HTTP_READINESS','UI_PAGE')
                 and c.get('status')!='UNSUPPORTED_BY_RUNNER'
                 and c.get('target',{}).get('method')==method
                 and c['target'].get('path')==path]
        if len(matches)!=1: raise ValueError('CRUD_REQUIRED_DISCOVERED_ROUTE_NOT_UNIQUE:'+role)
        steps.append({'role':role,'test_id':matches[0]['id']})
    return {'schema_version':'0.1','source':plan['source'],
            'create':{'title':'ProjectIQ CRUD Task','deadline':'2027-03-15','priority':'Low'},
            'update':{'title':'ProjectIQ CRUD Updated','deadline':'2027-03-16','priority':'High'},
            'steps':steps}


def main(argv=None):
    p=argparse.ArgumentParser(description='Stage reviewed MiniCollab CRUD proposal; no VM')
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    catalog=stage(_load_strict_json(args.plan,2_000_000))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x',encoding='utf8') as f:
        json.dump(catalog,f,indent=2,sort_keys=True)
        f.write('\n')
    print('D4B REVIEW PROPOSAL STAGED (NOT APPROVED / NO VM):',args.out)
    return 0

if __name__=='__main__': raise SystemExit(main())
