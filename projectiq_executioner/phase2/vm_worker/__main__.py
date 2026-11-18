"""CLI: plan, toolchain check, and explicit reviewed-fixture VM execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import VMPlanError
from .orchestrator import execute_vm_candidate, inspect_inputs, load_config
from .qemu import verify_host_toolchain


def main() -> int:
    p = argparse.ArgumentParser(description='ProjectIQ Phase 2C: disposable Linux QEMU VM pilot (no Docker)')
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True, help='Phase 2A JSON')
    p.add_argument('--preflight', type=Path, required=True, help='Phase 2B JSON')
    p.add_argument('--out', type=Path, default=Path('outputs/executioner/vm_phase2c.json'))
    p.add_argument('--vm-config', type=Path, help='Operator-approved VM image SHA / reviewed snapshot list')
    p.add_argument('--probe-vm', action='store_true', help='Inspect QEMU toolchain and base image SHA (no launch)')
    p.add_argument('--run-reviewed-fixture', action='store_true', help='Launch ONLY an approved pinned fixture in a disposable VM')
    p.add_argument('--work-root', type=Path, help='Directory containing disposable VM disks during execution')
    a = p.parse_args()
    try:
        if a.probe_vm and a.run_reviewed_fixture:
            p.error('Choose only one execution mode')
        candidate = inspect_inputs(a.manifest, a.plan, a.preflight)
        if a.probe_vm or a.run_reviewed_fixture:
            if not a.vm_config:
                p.error('--vm-config required for a VM toolchain probe or run')
            config = load_config(a.vm_config)
            if a.probe_vm:
                candidate['host_vm_probe'] = verify_host_toolchain(config)
            else:
                output = execute_vm_candidate(candidate, config, a.out,
                    reviewed_fixture=True, work_root=a.work_root)
                print('ProjectIQ — Phase 2C disposable VM pilot')
                print('  Status:    ' + output['status'])
                print('  Execution: ' + output['execution'])
                print('  Cleanup:   ' + output['cleanup'])
                print('  Output:    ' + str(a.out))
                return 0 if output['status'] == 'STARTUP_VERIFIED' else 2
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(candidate, indent=2, sort_keys=True) + '\n', 'utf-8')
        print('ProjectIQ — Phase 2C VM plan (NO EXECUTION)')
        print('  Adapter: ' + str(candidate['adapter']))
        print('  Status:  ' + candidate['status'])
        print('  Docker:  NOT REQUIRED')
        print('  Output:  ' + str(a.out))
        return 0 if candidate['adapter'] else 2
    except (VMPlanError, OSError, ValueError) as exc:
        p.error(str(exc))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
