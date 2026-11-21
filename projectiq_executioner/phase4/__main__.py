"""Phase 4A CLI: stage eligible test requests. Does not execute a VM."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .handoff import (
    Phase4HandoffError, _new_result, load_bounded_json, prepare_handoff, save_handoff,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Phase 4A trusted VM test handoff (never launches VM)')
    p.add_argument('--plan', type=Path, required=True, help='Phase 3 test_plan.json')
    p.add_argument('--candidate', type=Path, required=True, help='Matching Phase 2C candidate')
    p.add_argument('--vm-config', type=Path, required=True, help='Operator-owned VM config')
    p.add_argument('--out', type=Path, required=True, help='New output directory')
    args = p.parse_args(argv)
    result_path = args.out / 'phase4_handoff.json'
    if result_path.exists():
        p.error('Refusing to overwrite prior Phase 4 evidence')
    try:
        plan = load_bounded_json(args.plan, 2_000_000)
        candidate = load_bounded_json(args.candidate, 200_000)
        config = load_bounded_json(args.vm_config, 100_000)
        result = prepare_handoff(plan, candidate, config)
        save_handoff(result, result_path)
    except (Phase4HandoffError, OSError, ValueError):
        # Always attempt a fixed-schema diagnostic, never include raw exceptions.
        result = _new_result(
            {'kind': 'unknown', 'repository': 'NOT_RESOLVED', 'commit_sha': None}, None
        )
        result['status'] = 'INPUT_ERROR'
        result['issues'] = [{'code': 'INVALID_PHASE4_HANDOFF_INPUT', 'stage': 'input'}]
        try:
            save_handoff(result, result_path)
        except (OSError, ValueError):
            print('Phase 4A cannot persist a safe input-error report', file=sys.stderr)
        print('Phase 4A handoff failed safely: INVALID_PHASE4_HANDOFF_INPUT', file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4A — Trusted VM Test Handoff')
    print('  Status:     ' + result['status'])
    print('  Selected:   ' + str(result['summary']['selected']))
    print('  Excluded:   ' + str(result['summary']['excluded']))
    print('  Execution:  NOT PERFORMED')
    print('  JSON:       ' + str(result_path))
    return 0 if result['status'] in ('STAGED_NOT_EXECUTED', 'NO_RUNNABLE_TESTS') else 2


if __name__ == '__main__':
    raise SystemExit(main())
