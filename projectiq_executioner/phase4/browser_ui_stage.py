"""4D-C offline UI test proposal. Never approves a source or launches a VM."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .oracle_stage import _load_strict_json


def stage(plan: dict, *, heading: str, loaded_text: str) -> dict:
    if (not isinstance(plan, dict) or not isinstance(plan.get('source'), dict)
            or plan.get('execution_context', {}).get('adapter') != 'node_fullstack_offline_v1'):
        raise ValueError('BROWSER_STAGE_REQUIRES_FULLSTACK_PLAN')
    return {'schema_version': '0.1', 'source': plan['source'],
            'page_path': '/index.html', 'checks': [
                {'tag': 'h1', 'exact_text': heading},
                {'tag': 'p', 'exact_text': loaded_text}]}


def main(argv=None):
    p = argparse.ArgumentParser(description='Stage reviewed real-browser DOM oracle; no execution')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--heading', required=True)
    p.add_argument('--loaded-text', required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    plan = _load_strict_json(args.plan, 2_000_000)
    catalog = stage(plan, heading=args.heading, loaded_text=args.loaded_text)
    # Structural validation does not confer review or runtime approval.
    from .guest_browser_ui import validate_guest_browser_bundle
    validate_guest_browser_bundle({**catalog, 'adapter': 'node_fullstack_offline_v1',
                                   'catalog_sha256': '0' * 64,
                                   'browser_binary': '/usr/bin/google-chrome-stable'},
                                  expected_source=plan['source'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as f:
        json.dump(catalog, f, sort_keys=True, indent=2)
        f.write('\n')
    print('BROWSER UI PROPOSAL STAGED; NOT APPROVED; NO VM')
    print('Review the pinned frontend source and catalog:', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
