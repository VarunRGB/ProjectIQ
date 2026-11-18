"""Run an inert Phase 2A environment preflight from a Phase 1 manifest."""
from __future__ import annotations

import argparse
from pathlib import Path

from .planner import InvalidManifest, plan_from_file


def main() -> int:
    parser = argparse.ArgumentParser(description='ProjectIQ Executioner Phase 2A: safe plan-only preflight')
    parser.add_argument('manifest', type=Path, help='Path to Phase 1 manifest.json')
    parser.add_argument('--out', type=Path, default=Path('outputs/executioner/phase2_execution_plan.json'))
    args = parser.parse_args()
    try:
        plan = plan_from_file(args.manifest, args.out)
    except (InvalidManifest, ValueError, OSError) as exc:
        parser.error(str(exc))
    print('ProjectIQ Executioner — Phase 2A (plan only, NO EXECUTION)')
    print('  Status:   ' + plan['status'])
    print('  Services: ' + str(len(plan['proposed_services'])))
    print('  Blockers: ' + str(len(plan['blocking_conditions'])))
    print('  Plan:     ' + str(args.out))
    print('  Execution: NOT PERFORMED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
