"""4D-D reviewed, immutable MiniCollabApp login catalog generator (NO VM)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .browser_login_contracts import login_catalog
from .oracle_stage import _load_strict_json


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Stage pinned MiniCollab login session catalog; no VM')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    plan = _load_strict_json(args.plan, 2_000_000)
    if plan.get('execution_context', {}).get('adapter') != 'php_mysql_v1':
        p.error('MiniCollab PHP Phase 3 plan required')
    catalog = login_catalog(plan['source'])
    if args.out.exists():
        p.error('Output must be a fresh file')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as f:
        json.dump(catalog, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')
    print('4D-D LOGIN CATALOG STAGED: NO VM; OPERATOR APPROVAL STILL REQUIRED')
    print('Catalog:', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
