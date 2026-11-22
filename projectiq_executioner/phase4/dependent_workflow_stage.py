"""4D-A inert stage: choose existing Phase 3 test IDs for a reviewed workflow.

Stages a *proposal*, not an oracle approval or permission to run; the operator
must review the pinned source handlers and the separately approved D1 fixture.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .oracle_stage import _load_strict_json


def stage(*, plan: dict, fixtures: dict, read_path: str,
          delete_path: str) -> dict:
    if plan.get('source') != fixtures.get('source'):
        raise ValueError('D4A_STAGE_SOURCE_MISMATCH')
    tables = fixtures.get('tables')
    if (not isinstance(tables, list) or len(tables) != 1
            or not isinstance(tables[0], dict)
            or not isinstance(tables[0].get('rows'), list) or len(tables[0]['rows']) != 1):
        raise ValueError('D4A_STAGE_REQUIRES_ONE_REVIEWED_D1_ROW')
    row = tables[0]['rows'][0]
    if type(row.get('id')) is not int or not isinstance(row.get('title'), str):
        raise ValueError('D4A_STAGE_REQUIRES_TITLE_AND_ID')
    ids = []
    for method, path in (('GET', read_path), ('POST', delete_path)):
        matches = [item for item in plan.get('test_cases', [])
                   if item.get('category') in ('HTTP_READINESS', 'HTTP_ROUTE', 'UI_PAGE')
                   and item.get('status') != 'UNSUPPORTED_BY_RUNNER'
                   and item.get('target', {}).get('method') == method
                   and item['target'].get('path') == path]
        if len(matches) != 1:
            raise ValueError('D4A_STAGE_DISCOVERED_ROUTE_NOT_UNIQUE')
        ids.append(matches[0]['id'])
    return {
        'schema_version':'0.1', 'source': plan['source'],
        'selector': {'field':'title', 'value':row['title']},
        'capture_field': 'id', 'body_field': 'id',
        'steps': [
            {'role':'DISCOVER','test_id':ids[0]},
            {'role':'DELETE_CAPTURED','test_id':ids[1]},
            {'role':'VERIFY_ABSENT','test_id':ids[0]},
        ],
        'expected_delete': {'status':200,'json_subset': {'success':True}},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='4D-A operator-reviewable proposal, no execution')
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--read-path',default='/logic/get_tasks.php')
    parser.add_argument('--delete-path',default='/logic/delete_task.php')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    plan=_load_strict_json(args.plan,2_000_000)
    fixtures=_load_strict_json(args.fixtures,64_000)
    proposal=stage(plan=plan,fixtures=fixtures,read_path=args.read_path,
                   delete_path=args.delete_path)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x',encoding='utf8') as dest:
        json.dump(proposal,dest,indent=2,sort_keys=True)
        dest.write('\n')
    print('D4A PROPOSAL STAGED (NO VM / NO APPROVAL)')
    print('Review pinned GET/POST source before approving:',args.out)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
