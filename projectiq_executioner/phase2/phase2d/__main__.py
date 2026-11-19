"""CLI: python -m projectiq_executioner.phase2.phase2d --repo URL ..."""
from __future__ import annotations

import argparse
from pathlib import Path

from ...github_client import IngestionError
from ...safe_archive import ArchiveError
from ..planner import InvalidManifest
from ..preflight import InvalidPreflightInput
from ..vm_worker.contracts import VMPlanError
from .pipeline import run_pipeline


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Phase 2D: integrated plans; approved Phase 2C VM execution only')
    selection = p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--repo', help='Public GitHub repository root URL')
    selection.add_argument('--manifest', type=Path, help='Existing Phase 1 manifest (PLAN ONLY)')
    p.add_argument('--ref', help='GitHub SHA/ref; running requires a full 40-character SHA')
    p.add_argument('--out', type=Path, default=Path('outputs/executioner/phase2d'))
    p.add_argument('--vm-config', type=Path, help='Operator-owned pinned image and approved SHA list')
    p.add_argument('--run-reviewed-fixture', action='store_true', help='Launch only a reviewed, explicitly SHA-pinned fixture')
    p.add_argument('--work-root', type=Path, help='Directory for temporary VM disks')
    args = p.parse_args(argv)
    try:
        result = run_pipeline(
            repository_url=args.repo, ref=args.ref, manifest_path=args.manifest,
            output_dir=args.out, vm_config=args.vm_config,
            run_reviewed_fixture=args.run_reviewed_fixture, work_root=args.work_root,
        )
    except (IngestionError, ArchiveError, InvalidManifest, InvalidPreflightInput,
            VMPlanError, ValueError, OSError) as exc:
        p.error(str(exc))
    print('ProjectIQ — Phase 2D foundation')
    print('  Stage:      ' + result['stage'])
    print('  Adapter:    ' + str(result['phase2c_adapter']))
    print('  Status:     ' + result['status'])
    print('  Execution:  ' + result['execution'])
    if result.get('cleanup'):
        print('  Cleanup:    ' + result['cleanup'])
    print('  Output:     ' + str(args.out / 'pipeline_summary.json'))
    if result['status'] in ('PLANNED_NO_EXECUTION', 'STARTUP_VERIFIED'):
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
