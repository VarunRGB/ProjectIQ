"""One-call ProjectIQ Executioner orchestration for a reviewed GitHub snapshot.

The agent is a *deterministic workflow*, not an LLM with shell access. It
reuses Phase 2D analysis and the Phase 2C allowlisted VM worker. An unfamiliar
commit is analyzed, but NEVER approved or executed implicitly.

Optional offline dependency preparation is a separate, explicitly authorized
networked preparation VM. Normal project execution retains egress restrictions.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...github_client import IngestionError
from ...safe_archive import ArchiveError
from ..vm_worker.contracts import VMPlanError, load_json
from ..vm_worker.offline_assets import (
    OfflineAssetError, approved_node_express_assets,
    approved_node_fullstack_assets, approved_node_vite_assets,
    approved_python_web_assets,
)
from ..vm_worker.orchestrator import execute_vm_candidate, load_config
from .pipeline import run_pipeline
from .agent_diagnostics import issue, classify_error, classify_vm_result

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
SHA40 = re.compile(r'^[0-9a-f]{40}$')
NODE_ADAPTERS = {
    'node_vite_offline_v1': ('node_vite_offline', approved_node_vite_assets),
    'node_express_offline_v1': ('node_express_offline', approved_node_express_assets),
    'node_fullstack_offline_v1': ('node_fullstack_offline', approved_node_fullstack_assets),
}
PYTHON_ADAPTER = 'python_web_offline_v1'
STAGES = ('analysis', 'adapter', 'operator_approval', 'offline_dependencies',
          'vm_execution', 'vm_cleanup')


@dataclass(frozen=True)
class DependencyPreparation:
    """Opt-in networked-cache preparation for *already approved* exact SHAs.

    The official Node and pip artifacts are pinned by operator-supplied hashes;
    this class never accepts a runtime download URL from the student repo.
    """
    enabled: bool = False
    allow_trusted_network: bool = False
    node_tarball: Path | None = None
    node_sha256: str | None = None
    node_version: str = 'v24.22.3'
    pip_wheel: Path | None = None
    pip_sha256: str | None = None


def _new_report() -> dict[str, Any]:
    return {
        'schema_version': '0.1',
        'stage': 'executioner_one_call_orchestration',
        'status': 'NOT_STARTED',
        'execution': 'NOT_PERFORMED',
        'cleanup': 'NOT_STARTED',
        'security_attestation': ATTESTATION,
        'source': None,
        'adapter': None,
        'http_status': None,
        'failure_code': None,
        'issues': [],  # Stable, curated dashboard diagnostics; never raw exceptions.
        'steps': [{'name': name, 'status': 'PENDING'} for name in STAGES],
        'artifacts': {'plan': 'plan/phase2c_candidate.json'},
        'limitations': [
            'ARBITRARY_UNTRUSTED_SUBMISSIONS_NOT_AUTHORIZED',
            'OPERATOR_APPROVAL_BOUND_TO_EXACT_GITHUB_COMMIT',
            'HTTP_READINESS_IS_NOT_FUNCTIONAL_CORRECTNESS',
            'NO_HYPERVISOR_SECURITY_ATTESTATION',
        ],
    }


def _step(report: dict, stage: str, value: str) -> None:
    if value not in {'PASS', 'SKIPPED', 'BLOCKED', 'FAILED'}:
        raise ValueError('Invalid orchestration step status')
    for entry in report['steps']:
        if entry['name'] == stage:
            entry['status'] = value
            return
    raise ValueError('Unknown orchestration step')


def _add_issue(report: dict, code: str, stage: str) -> None:
    """Use a fixed, structured issue; never persist raw exception output."""
    report['issues'].append(issue(code, stage))


def _finish(report: dict, out: Path) -> dict:
    for entry in report['steps']:
        if entry['status'] == 'PENDING':
            entry['status'] = 'SKIPPED'
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'orchestration_result.json'
    # Never overwrite evidence from a previous attempt.
    with target.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return report


def _existing_assets(config: dict, identity: str, adapter: str) -> bool:
    if adapter in ('static_html_v1', 'php_mysql_v1'):
        return True
    if adapter in NODE_ADAPTERS:
        _, check = NODE_ADAPTERS[adapter]
    elif adapter == PYTHON_ADAPTER:
        check = approved_python_web_assets
    else:
        return False
    try:
        check(config, identity)
        return True
    except (OfflineAssetError, OSError, ValueError):
        return False


def _prepare_assets(*, repo: str, sha: str, adapter: str,
                    identity: str, config_path: Path, config: dict,
                    out: Path, options: DependencyPreparation) -> tuple[dict, str]:
    """Return validated ephemeral config + fixed-category outcome.

    The preparation helpers themselves re-check the SHA allowlist, verify the
    official runtime artifact and run npm/pip fetch in a separate disposable VM.
    The resulting profile is validated with the normal offline-assets contract
    before it can reach the egress-denied execution VM.
    """
    if not options.enabled:
        return config, 'PREPARATION_NOT_ENABLED'
    if not options.allow_trusted_network:
        return config, 'TRUSTED_DEPENDENCY_NETWORK_NOT_AUTHORIZED'

    prepared = out / 'prepared_assets'
    if adapter in NODE_ADAPTERS:
        if not options.node_tarball or not options.node_sha256:
            return config, 'PINNED_NODE_ARTIFACT_REQUIRED'
        from .cache_prep import prepare_cache
        record = prepare_cache(
            repo_url=repo, commit_sha=sha, vm_config=config_path,
            node_tarball=options.node_tarball, node_sha256=options.node_sha256,
            node_version=options.node_version, output_dir=prepared,
            allow_trusted_npm_network=True, adapter=adapter,
        )
        key = NODE_ADAPTERS[adapter][0]
    elif adapter == PYTHON_ADAPTER:
        if not options.pip_wheel or not options.pip_sha256:
            return config, 'PINNED_PIP_ARTIFACT_REQUIRED'
        from .python_cache_prep import prepare_python_cache
        record = prepare_python_cache(
            repo_url=repo, commit_sha=sha, vm_config=config_path,
            pip_wheel=options.pip_wheel, pip_sha256=options.pip_sha256,
            output_dir=prepared, allow_trusted_pypi_network=True,
        )
        key = 'python_web_offline'
    else:
        return config, 'ADAPTER_NOT_SUPPORTED_FOR_PREPARATION'

    profile = load_json(Path(record['profile_json']), limit=20_000)
    # Accept only the exact SHA profile produced by trusted preparation; never
    # merge arbitrary or neighboring approvals from a downloaded JSON file.
    if (type(profile.get(key)) is not dict or
            set(profile[key]) != {identity} or type(profile[key][identity]) is not dict):
        raise VMPlanError('Prepared dependency profile has an unexpected identity')
    current = config.get(key)
    merged = dict(current) if type(current) is dict else {}
    merged[identity] = profile[key][identity]
    candidate = {**config, key: merged}
    if not _existing_assets(candidate, identity, adapter):
        raise OfflineAssetError('Prepared offline assets failed SHA contract')
    return candidate, 'PREPARED_AND_VERIFIED'


def run_submission(*, repo_url: str, output_dir: Path,
                   vm_config: Path | None = None, ref: str | None = None,
                   execute: bool = True, work_root: Path | None = None,
                   preparation: DependencyPreparation | None = None) -> dict[str, Any]:
    """Single Python-call workflow for orchestrators.

    An unknown source still receives an adapter plan, but cannot execute. The
    operator must separately review and authorize `owner/repo@exact_sha` in
    vm_config.json. The function never edits that persistent policy file.
    """
    output = Path(output_dir)
    if output.exists() and not output.is_dir():
        raise VMPlanError('Output path is not a directory')
    if any((output / name).exists() for name in (
            'plan', 'vm_execution.json', 'orchestration_result.json',
            'prepared_assets')):
        raise VMPlanError('Refusing to overwrite an existing orchestration run')
    if execute and vm_config is None:
        raise VMPlanError('Execution requires an operator VM configuration')
    prep = preparation or DependencyPreparation()
    report = _new_report()
    report['artifacts']['result'] = 'orchestration_result.json'
    stage = 'analysis'
    try:
        # Planning resolves HEAD/ref into an immutable SHA, downloads one
        # bounded archive and performs the existing inert stack analysis.
        planned = run_pipeline(
            repository_url=repo_url, ref=ref,
            output_dir=output / 'plan', run_reviewed_fixture=False,
        )
        candidate = load_json(output / 'plan' / 'phase2c_candidate.json', limit=200_000)
        source = candidate.get('source')
        if (type(source) is not dict or source != planned.get('source') or
                type(source.get('repository')) is not str or
                type(source.get('commit_sha')) is not str or
                not SHA40.fullmatch(source['commit_sha'])):
            raise VMPlanError('Inconsistent pipeline snapshot provenance')
        if ref and SHA40.fullmatch(ref.lower()) and source['commit_sha'] != ref.lower():
            raise VMPlanError('Resolved commit differs from explicit SHA')
        report['source'] = dict(source)
        report['adapter'] = candidate.get('adapter')
        _step(report, 'analysis', 'PASS')
        stage = 'adapter'
        if (candidate.get('status') != 'CANDIDATE_NEEDS_VM_VERIFICATION' or
                report['adapter'] not in (
                    'static_html_v1', 'php_mysql_v1', *NODE_ADAPTERS.keys(),
                    PYTHON_ADAPTER)):
            _step(report, stage, 'BLOCKED')
            report.update(status='NEEDS_ADAPTER', failure_code='NO_SUPPORTED_ADAPTER',
                          next_action={'type': 'IMPLEMENT_OR_SELECT_ADAPTER'})
            _add_issue(report, 'NO_SUPPORTED_ADAPTER', stage)
            return _finish(report, output)
        _step(report, stage, 'PASS')

        if not execute:
            _step(report, 'operator_approval', 'SKIPPED')
            report['status'] = 'PLANNED_NO_EXECUTION'
            return _finish(report, output)

        stage = 'operator_approval'
        config_path = Path(vm_config)
        config = load_config(config_path)
        identity = source['repository'] + '@' + source['commit_sha']
        report['review_identity'] = identity
        if identity not in config['approved_snapshots']:
            _step(report, stage, 'BLOCKED')
            report.update(status='NEEDS_OPERATOR_APPROVAL',
                          failure_code='PINNED_SHA_NOT_APPROVED',
                          next_action={'type': 'REVIEW_AND_APPROVE_EXACT_SHA',
                                       'identity': identity})
            _add_issue(report, 'PINNED_SHA_NOT_APPROVED', stage)
            return _finish(report, output)
        _step(report, stage, 'PASS')

        stage = 'offline_dependencies'
        if _existing_assets(config, identity, report['adapter']):
            _step(report, stage, 'PASS')
            report['dependency_state'] = 'ALREADY_VERIFIED_OR_NOT_REQUIRED'
        else:
            updated, state = _prepare_assets(
                repo=repo_url, sha=source['commit_sha'], adapter=report['adapter'],
                identity=identity, config_path=config_path, config=config,
                out=output, options=prep,
            )
            if state != 'PREPARED_AND_VERIFIED':
                _step(report, stage, 'BLOCKED')
                report.update(status='NEEDS_OFFLINE_DEPENDENCIES', failure_code=state,
                              next_action={'type': 'PREPARE_APPROVED_OFFLINE_ASSETS',
                                           'adapter': report['adapter']})
                _add_issue(report, state, stage)
                return _finish(report, output)
            config = updated
            _step(report, stage, 'PASS')
            report['dependency_state'] = state
            report['artifacts']['prepared_assets'] = 'prepared_assets'

        stage = 'vm_execution'
        # This is the existing Phase 2C worker: it independently downloads the
        # same pinned source, enforces the operator allowlist, and launches a
        # restricted disposable VM. No student commands run on the host.
        vm = execute_vm_candidate(
            candidate, config, output / 'vm_execution.json',
            reviewed_fixture=True, work_root=work_root,
        )
        report['artifacts']['vm_execution'] = 'vm_execution.json'
        report['vm_status'] = vm.get('status')
        report['execution'] = vm.get('execution', 'NOT_PERFORMED')
        report['cleanup'] = vm.get('cleanup', 'NOT_VERIFIED')
        guest = vm.get('guest_result')
        if type(guest) is dict and type(guest.get('http_status')) is int:
            report['http_status'] = guest['http_status']
        good = (vm.get('status') == 'STARTUP_VERIFIED' and
                vm.get('adapter') == report['adapter'] and
                vm.get('source') == source and
                vm.get('backend') == 'QEMU_DISPOSABLE_QCOW2_VM' and
                type(guest) is dict and guest.get('status') == 'STARTUP_VERIFIED' and
                report['execution'] == 'PERFORMED_IN_DISPOSABLE_VM' and
                report['http_status'] == 200 and
                report['cleanup'] == CLEAN and
                vm.get('security_attestation') == ATTESTATION)
        _step(report, 'vm_cleanup', 'PASS' if report['cleanup'] == CLEAN else 'FAILED')
        if good:
            _step(report, stage, 'PASS')
            report['status'] = 'STARTUP_VERIFIED'
            report['next_action'] = {'type': 'NONE'}
        else:
            _step(report, stage, 'FAILED')
            reason = classify_vm_result(vm, requested_source=source,
                                        requested_adapter=report['adapter'],
                                        clean=CLEAN, attestation=ATTESTATION)
            report.update(status='EXECUTION_NOT_VERIFIED',
                          failure_code=reason,
                          next_action={'type': 'REVIEW_VM_REPORT'})
            _add_issue(report, reason, stage)
        return _finish(report, output)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError,
            IngestionError, ArchiveError) as exc:
        # Never publish raw exception text (can include host paths or external
        # response data). Fixed-category error plus class is enough for logs.
        _step(report, stage, 'FAILED')
        code = classify_error(stage, exc)
        report.update(status='ORCHESTRATION_ERROR',
                      failure_code=code,
                      error_type=type(exc).__name__,
                      next_action={'type': 'REVIEW_ORCHESTRATION_FAILURE'})
        _add_issue(report, code, stage)
        return _finish(report, output)


class ExecutionerAgent:
    """Convenient orchestrator component; no state or mutable trust cache."""

    def __init__(self, *, vm_config: Path, preparation: DependencyPreparation | None = None):
        self.vm_config = Path(vm_config)
        self.preparation = preparation or DependencyPreparation()

    def run(self, repo_url: str, *, output_dir: Path, ref: str | None = None,
            work_root: Path | None = None) -> dict[str, Any]:
        return run_submission(
            repo_url=repo_url, output_dir=output_dir, vm_config=self.vm_config,
            ref=ref, work_root=work_root, preparation=self.preparation,
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='One-call ProjectIQ reviewed-fixture Executioner agent')
    p.add_argument('--repo', required=True, help='Public GitHub repository root URL')
    p.add_argument('--ref', help='Optional ref; execution approval always binds to resolved SHA')
    p.add_argument('--vm-config', type=Path, help='Existing reviewed SHA approvals and VM policy')
    p.add_argument('--out', type=Path, required=True, help='Fresh per-run output directory')
    p.add_argument('--plan-only', action='store_true', help='Analyze without execution')
    p.add_argument('--work-root', type=Path)
    p.add_argument('--auto-prepare', action='store_true',
                   help='Permit on-demand offline dependency preparation for approved SHA')
    p.add_argument('--allow-trusted-dependency-network', action='store_true',
                   help='Explicitly authorize networked preparation VM, not execution VM')
    p.add_argument('--node-tarball', type=Path)
    p.add_argument('--node-sha256')
    p.add_argument('--node-version', default='v24.22.3')
    p.add_argument('--pip-wheel', type=Path)
    p.add_argument('--pip-sha256')
    a = p.parse_args(argv)
    if not a.plan_only and a.vm_config is None:
        p.error('--vm-config required unless --plan-only')
    opts = DependencyPreparation(
        enabled=a.auto_prepare,
        allow_trusted_network=a.allow_trusted_dependency_network,
        node_tarball=a.node_tarball, node_sha256=a.node_sha256,
        node_version=a.node_version, pip_wheel=a.pip_wheel,
        pip_sha256=a.pip_sha256,
    )
    try:
        result = run_submission(
            repo_url=a.repo, output_dir=a.out, vm_config=a.vm_config,
            ref=a.ref, execute=not a.plan_only, work_root=a.work_root,
            preparation=opts,
        )
    except (OSError, VMPlanError, ValueError) as exc:
        p.error(str(exc))
    print('ProjectIQ Executioner Agent — ' + result['status'])
    print('  Adapter: ' + str(result['adapter']))
    print('  Commit: ' + str((result['source'] or {}).get('commit_sha', 'NOT_RESOLVED')))
    print('  Execution: ' + result['execution'])
    print('  Cleanup: ' + result['cleanup'])
    if result['issues']:
        problem = result['issues'][0]
        print('  Issue: ' + problem['code'] + ' — ' + problem['title'])
        print('  Action: ' + problem['recommended_action'])
    print('  Report: ' + str(a.out / 'orchestration_result.json'))
    return 0 if result['status'] in {'STARTUP_VERIFIED', 'PLANNED_NO_EXECUTION'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
