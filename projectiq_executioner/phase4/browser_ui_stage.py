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


def stage_php_login(plan: dict) -> dict:
    """Proposal only: exact login heading/button from reviewed MiniCollab source."""
    if (not isinstance(plan, dict) or not isinstance(plan.get('source'), dict)
            or plan.get('execution_context', {}).get('adapter') != 'php_mysql_v1'):
        raise ValueError('BROWSER_PHP_STAGE_REQUIRES_PHP_PLAN')
    return {'schema_version': '0.1', 'source': plan['source'],
            'page_path': '/public/login.html', 'checks': [
                {'tag': 'h2', 'exact_text': 'Login'},
                {'tag': 'button', 'exact_text': 'Login'}]}


def main(argv=None):
    p = argparse.ArgumentParser(description='Stage reviewed real-browser DOM oracle; no execution')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--heading')
    p.add_argument('--loaded-text')
    p.add_argument('--php-login', action='store_true',
                   help='Propose only the reviewed MiniCollab PHP login page')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    plan = _load_strict_json(args.plan, 2_000_000)
    if args.php_login:
        if args.heading is not None or args.loaded_text is not None:
            p.error('--php-login cannot be combined with React text options')
        catalog = stage_php_login(plan)
        adapter = 'php_mysql_v1'
    else:
        if args.heading is None or args.loaded_text is None:
            p.error('React staging requires --heading and --loaded-text')
        catalog = stage(plan, heading=args.heading, loaded_text=args.loaded_text)
        adapter = 'node_fullstack_offline_v1'
    # Structural validation does not confer review or runtime approval.
    from .guest_browser_ui import validate_guest_browser_bundle
    validate_guest_browser_bundle({**catalog, 'adapter': adapter,
                                   'catalog_sha256': '0' * 64,
                                   'browser_binary': '/usr/bin/google-chrome-stable'},
                                  expected_source=plan['source'],
                                  expected_adapter=adapter)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as f:
        json.dump(catalog, f, sort_keys=True, indent=2)
        f.write('\n')
    print('BROWSER UI PROPOSAL STAGED; NOT APPROVED; NO VM')
    print('Review the pinned frontend source and catalog:', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
