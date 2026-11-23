"""Phase 2C QEMU pilot orchestration. All app execution happens inside a VM.

The host uses ONLY trusted QEMU/SSH tooling and static snapshot inspection.
Launch is explicitly restricted to operator-approved pinned fixture SHAs.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from ...github_client import GitHubClient
from ...models import RepositorySource
from ...safe_archive import extract_snapshot
from .contracts import VMPlanError, build_vm_plan, load_json
from .offline_assets import (OfflineAssetError, approved_node_vite_assets,
                             approved_python_web_assets, approved_node_express_assets,
                             approved_node_fullstack_assets)
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key, verify_host_toolchain)
from .d5c_runtime_workspace import ManagedRuntimeRun, RuntimeWorkspaceError


def load_config(path: Path) -> dict:
    config = load_json(path, limit=100_000)
    image = config.get('base_image')
    sha = config.get('base_sha256')
    if not isinstance(image, str) or not image or not isinstance(sha, str) or len(sha) != 64:
        raise VMPlanError('VM config needs base_image and pinned base_sha256')
    if not isinstance(config.get('approved_snapshots'), list):
        raise VMPlanError('VM config needs approved_snapshots: ["Owner/Repo@40-character-SHA"]')
    return config


def inspect_inputs(p1: Path, p2a: Path, p2b: Path) -> dict:
    return build_vm_plan(load_json(p1), load_json(p2a), load_json(p2b))


def _save(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', 'utf-8')


def execute_vm_candidate(candidate: dict, config: dict, output: Path,
                         *, reviewed_fixture: bool = False, work_root: Path | None = None,
                         test_bundle: dict | None = None, api_bundle: dict | None = None,
                         db_fixture_bundle: dict | None = None,
                         auth_bundle: dict | None = None,
                         postcondition_bundle: dict | None = None,
                         workflow_bundle: dict | None = None,
                         crud_bundle: dict | None = None,
                         browser_bundle: dict | None = None,
                         login_bundle: dict | None = None) -> dict:
    """Runs only approved source SHAs. Does not use Docker/Podman for any stage.

    'reviewed_fixture=True' signifies human approval of a particular pinned
    source SHA in a nonproduction VM pilot, NOT blanket sandbox attestation.
    """
    source = candidate['source']
    identity = source['repository'] + '@' + source['commit_sha']
    result = {
        'schema_version': '0.3', 'stage': 'vm_dev_fixture_execution',
        'source': source, 'adapter': candidate.get('adapter'),
        'backend': 'QEMU_DISPOSABLE_QCOW2_VM',
        'status': 'DENIED', 'execution': 'NOT_PERFORMED',
        'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
        'cleanup': 'NOT_STARTED', 'readiness': 'NOT_VERIFIED',
    }
    if (not reviewed_fixture or identity not in config.get('approved_snapshots', [])
            or candidate.get('status') != 'CANDIDATE_NEEDS_VM_VERIFICATION'):
        result['reason'] = 'PINNED_FIXTURE_NOT_OPERATOR_APPROVED_OR_ADAPTER_MISSING'
        _save(result, output)
        return result
    if sum(x is not None for x in (test_bundle, api_bundle, db_fixture_bundle, auth_bundle,
                                    postcondition_bundle, workflow_bundle, crud_bundle,
                                     browser_bundle, login_bundle)) > 1:
        result['reason'] = 'PHASE4_MULTIPLE_BUNDLE_MODES_DENIED'
        _save(result, output)
        return result
    # 4D-D: separate, exact source/fixture approval; never share 4D-C mode.
    login_guest_bundle = None
    if login_bundle is not None:
        from ...phase4.browser_login_contracts import (
            LoginReviewError, validate_reviewed_login)
        try:
            login_guest_bundle = validate_reviewed_login(login_bundle, candidate, config)
        except (LoginReviewError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4DD_LOGIN_NOT_OPERATOR_APPROVED'
            _save(result, output)
            return result
    browser_guest_bundle = None
    if browser_bundle is not None:
        from ...phase4.browser_ui_contracts import (
            BrowserReviewError, validate_reviewed_browser)
        try:
            browser_guest_bundle = validate_reviewed_browser(browser_bundle, candidate, config)
        except (BrowserReviewError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4DC_BROWSER_NOT_OPERATOR_APPROVED'
            _save(result, output)
            return result
    # The operator's config explicitly opts reviewed browser VMs into
    # the 384/512 profile. All other VMs retain the 128/192 baseline.
    browser_profile_name = config.get('browser_resource_profile', 'standard_128_192_v1')
    if browser_guest_bundle is not None and browser_profile_name not in (
            'standard_128_192_v1', 'reviewed_browser_384_512_v1'):
        result['reason'] = 'PHASE4DC_BROWSER_RESOURCE_POLICY_DENIED'
        _save(result, output)
        return result
    browser_cgroup_pids = (384 if (browser_guest_bundle is not None or
                                  login_guest_bundle is not None) and
                            browser_profile_name == 'reviewed_browser_384_512_v1' else 128)
    crud_guest_bundle = None
    if crud_bundle is not None:
        from ...phase4.crud_workflow_contracts import CrudReviewError, validate_reviewed_crud
        try:
            crud_guest_bundle = validate_reviewed_crud(crud_bundle, candidate, config)
        except (CrudReviewError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4DB_CRUD_NOT_OPERATOR_APPROVED'
            _save(result, output)
            return result
    dependent_guest_bundle = None
    if workflow_bundle is not None:
        from ...phase4.dependent_workflow_contracts import (
            DependentWorkflowReviewError, validate_reviewed_dependent_workflow)
        try:
            dependent_guest_bundle = validate_reviewed_dependent_workflow(
                workflow_bundle, candidate, config)
        except (DependentWorkflowReviewError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4DA_DEPENDENT_WORKFLOW_NOT_APPROVED'
            _save(result, output)
            return result
    postcondition_guest_bundle = None
    if postcondition_bundle is not None:
        from ...phase4.db_postcondition_contracts import (
            PostconditionReviewError, validate_reviewed_postcondition_bundle)
        try:
            postcondition_guest_bundle = validate_reviewed_postcondition_bundle(
                postcondition_bundle, candidate, config)
        except (PostconditionReviewError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4D3_POSTCONDITION_NOT_OPERATOR_APPROVED'
            _save(result, output)
            return result
    auth_guest_bundle = None
    if auth_bundle is not None:
        from ...phase4.reviewed_auth import AuthReviewError, validate_reviewed_auth_bundle
        try:
            auth_guest_bundle = validate_reviewed_auth_bundle(auth_bundle, candidate, config)
        except (AuthReviewError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4D2_AUTH_BUNDLE_NOT_OPERATOR_APPROVED'
            _save(result, output)
            return result
    if db_fixture_bundle is not None:
        from ...phase4.guest_db_fixture import DbFixtureError, validate_fixture_bundle
        try:
            validate_fixture_bundle(db_fixture_bundle, expected_source=source,
                                    expected_adapter=candidate['adapter'])
        except (DbFixtureError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4D1_DB_FIXTURE_BUNDLE_REJECTED'
            _save(result, output)
            return result
        allowed_db = config.get('approved_database_fixtures')
        permission = identity + '#' + db_fixture_bundle['fixture_catalog_sha256']
        if (not isinstance(allowed_db, list) or len(allowed_db) > 256
                or permission not in allowed_db):
            result['reason'] = 'PHASE4D1_FIXTURE_NOT_OPERATOR_APPROVED'
            _save(result, output)
            return result
    if api_bundle is not None:
        from ...phase4.guest_api import validate_api_bundle, GuestApiError
        try:
            validate_api_bundle(api_bundle, expected_source=source,
                                expected_adapter=candidate['adapter'])
        except (GuestApiError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4C_API_BUNDLE_REJECTED'
            _save(result, output)
            return result
    if test_bundle is not None:
        # Operator-approved execution still rejects arbitrary guest instructions.
        from ...phase4.guest_http import GuestTestError, validate_guest_bundle
        try:
            validate_guest_bundle(test_bundle, expected_source=source,
                                  expected_adapter=candidate['adapter'])
        except (GuestTestError, TypeError, ValueError, KeyError):
            result['reason'] = 'PHASE4_TEST_BUNDLE_REJECTED'
            _save(result, output)
            return result
    # The Node/Vite branch is opt-in per exact repository SHA. Validate both
    # archives and their hashes before source download or VM startup.
    node_assets = None
    python_assets = None
    if candidate.get('adapter') == 'node_vite_offline_v1':
        try:
            node_assets = approved_node_vite_assets(config, identity)
        except OfflineAssetError as exc:
            result['reason'] = str(exc)
            _save(result, output)
            return result
    if candidate.get('adapter') == 'node_express_offline_v1':
        try:
            node_assets = approved_node_express_assets(config, identity)
        except OfflineAssetError as exc:
            result['reason'] = str(exc)
            _save(result, output)
            return result
    if candidate.get('adapter') == 'node_fullstack_offline_v1':
        try:
            node_assets = approved_node_fullstack_assets(config, identity)
            if node_assets['service_roots'] != candidate.get('service_roots'):
                raise OfflineAssetError('NODE_FULLSTACK_SERVICE_ROOT_PROFILE_MISMATCH')
        except OfflineAssetError as exc:
            result['reason'] = str(exc)
            _save(result, output)
            return result
    if candidate.get('adapter') == 'python_web_offline_v1':
        try:
            python_assets = approved_python_web_assets(config, identity)
            if python_assets['framework'] != candidate.get('python_framework'):
                raise OfflineAssetError('PYTHON_WEB_FRAMEWORK_PROFILE_MISMATCH')
        except OfflineAssetError as exc:
            result['reason'] = str(exc)
            _save(result, output)
            return result
    toolchain = verify_host_toolchain(config)
    if toolchain['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
        result['reason'] = 'QEMU_TOOLS_OR_IMMUTABLE_BASE_IMAGE_NOT_VERIFIED'
        result['toolchain'] = {k: bool(v) for k, v in toolchain['toolchain'].items()}
        result['base_sha256_matches'] = toolchain['template_sha256_matches']
        _save(result, output)
        return result

    # Apply the same operator-owned disk budget to every runtime adapter.
    # Never accept a repository-provided budget or silently disable this gate.
    workspace_mib = config.get('workspace_disk_mib', 1536)
    if type(workspace_mib) is not int or not 64 <= workspace_mib <= 2048:
        result['reason'] = 'INVALID_WORKSPACE_DISK_BUDGET'
        _save(result, output)
        return result
    # D5c Gate 4: the real reviewed-fixture runner MUST use a registered,
    # controller-leased workspace. A failed registry preflight cannot fall back
    # to untracked TemporaryDirectory and cannot start a VM.
    try:
        runtime = ManagedRuntimeRun(config, work_root)
        runtime.open()
    except (RuntimeWorkspaceError, OSError, ValueError):
        result.update(status='DENIED', reason='MANAGED_VM_RUNTIME_UNAVAILABLE')
        _save(result, output)
        return result
    started_at = time.monotonic()
    worker_stage = 'WORKSPACE_PREPARE'
    vm_started = False
    worker_stopped = False
    expired = threading.Event()
    watchdog = None
    with runtime.hold() as work:
        worker = None
        try:
            worker_stage = 'SNAPSHOT_FETCH'
            source_record = RepositorySource(kind='github', repository=source['repository'],
                         url='https://github.com/' + source['repository'], requested_ref=None,
                         resolved_ref=source['commit_sha'], commit_sha=source['commit_sha'])
            archive = work / 'snapshot.zip'
            GitHubClient(timeout=25).download(source_record, archive)
            if archive.stat().st_size > candidate['vm_limits']['max_snapshot_zip_bytes']:
                raise WorkerFailure('Snapshot archive exceeds operator limit')
            # Host-side safe *inspection only*. Never run the repository on host.
            extract_snapshot(archive, work / 'inspection')
            request = {
                'adapter': candidate['adapter'], 'probe_path': candidate['probe_path'],
                'schema_path': candidate.get('schema_path'),
                'project_root': candidate.get('project_root'),
                'python_framework': candidate.get('python_framework'),
                'workspace_disk_mib': workspace_mib,
                'app_cgroup': True,
            }
            if test_bundle is not None:
                request['phase4_source'] = source
                request['phase4_test_bundle'] = test_bundle
            if api_bundle is not None:
                request['phase4c_source'] = source
                request['phase4c_api_bundle'] = api_bundle
            if db_fixture_bundle is not None:
                request['phase4d1_source'] = source
                request['phase4d1_fixture_bundle'] = db_fixture_bundle
            if auth_guest_bundle is not None:
                request['phase4d2_source'] = source
                request['phase4d2_auth_bundle'] = auth_guest_bundle
            if postcondition_guest_bundle is not None:
                request['phase4d3_source'] = source
                request['phase4d3_auth_bundle'] = postcondition_guest_bundle['auth']
                request['phase4d3_postcondition_bundle'] = postcondition_guest_bundle['postconditions']
            if browser_guest_bundle is not None:
                request['phase4dc_source'] = source
                request['phase4dc_browser_bundle'] = browser_guest_bundle
                request['phase4dc_browser_resource_pids'] = browser_cgroup_pids
            if login_guest_bundle is not None:
                request['phase4dd_source'] = source
                request['phase4dd_login_bundle'] = login_guest_bundle
                request['phase4dc_browser_resource_pids'] = browser_cgroup_pids
            if crud_guest_bundle is not None:
                request['phase4db_source'] = source
                request['phase4db_auth_bundle'] = crud_guest_bundle['auth']
                request['phase4db_crud_bundle'] = crud_guest_bundle['crud']
            if dependent_guest_bundle is not None:
                request['phase4d1_source'] = source
                request['phase4d1_fixture_bundle'] = dependent_guest_bundle['fixture']
                request['phase4da_source'] = source
                request['phase4da_workflow_bundle'] = dependent_guest_bundle['workflow']
            if node_assets is not None:
                request.update({
                    'node_version': node_assets['node_version'],
                    'node_sha256': node_assets['node_sha256'],
                    'npm_cache_sha256': node_assets['npm_cache_sha256'],
                    **({'service_roots': node_assets['service_roots'],
                        'frontend_lockfile_sha256': node_assets['frontend_lockfile_sha256'],
                        'backend_lockfile_sha256': node_assets['backend_lockfile_sha256'],
                        'backend_probe_path': candidate['backend_probe_path']}
                       if candidate['adapter'] == 'node_fullstack_offline_v1' else
                       {'lockfile_sha256': node_assets['lockfile_sha256']}),
                })
            if python_assets is not None:
                request.update({
                    'python_version': python_assets['python_version'],
                    'requirements_sha256': python_assets['requirements_sha256'],
                    'wheelhouse_sha256': python_assets['wheelhouse_sha256'],
                    'pip_sha256': python_assets['pip_sha256'],
                })
            (work / 'request.json').write_text(json.dumps(request), 'utf-8')
            key, pubkey = make_ssh_key(config, work)
            seed = make_nocloud_iso(work, pubkey, 'projectiq-' + uuid.uuid4().hex,
                                    mysql_bootstrap=(candidate['adapter'] == 'php_mysql_v1'),
                                    workspace_disk_mib=request['workspace_disk_mib'],
                                    app_cgroup=True,
                                    app_cgroup_pids=browser_cgroup_pids)
            port = _free_loopback_port()
            limits = candidate['vm_limits']
            worker = QemuWorker({**config, 'workspace_disk_mib': request['workspace_disk_mib']},
                                work, port, ram_mib=limits['ram_mib'],
                                cpus=effective_vm_vcpus(config, limits['vcpu']))
            runtime.mark_launching()
            worker_stage = 'VM_LAUNCH'
            worker.start(seed)
            vm_started = True
            runtime.mark_launched(worker.proc)
            def expire():
                if worker is not None and worker.proc is not None and worker.proc.poll() is None:
                    expired.set()
                    worker.proc.kill()
            watchdog = threading.Timer(candidate['vm_limits']['wall_seconds'], expire)
            watchdog.daemon = True
            watchdog.start()
            result['execution'] = 'VM_BOOT_REQUESTED'
            ssh = SSHControl(config, work, port, key)
            worker_stage = 'VM_SSH_READY'
            ssh.wait_ssh(worker, seconds=85)
            runtime.mark_booted()
            worker_stage = 'SNAPSHOT_TRANSFER'
            ssh.copy(archive, 'snapshot.zip')
            worker_stage = 'GUEST_AGENT_TRANSFER'
            ssh.copy(Path(__file__).with_name('guest_agent.py'), 'guest_agent.py')
            worker_stage = 'GUEST_REQUEST_TRANSFER'
            ssh.copy(work / 'request.json', 'request.json')
            if test_bundle is not None:
                # Trusted runner, not code from the student checkout.
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_http.py', 'guest_http.py')
            if api_bundle is not None or auth_guest_bundle is not None or postcondition_guest_bundle is not None:
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_api.py', 'guest_api.py')
            if db_fixture_bundle is not None:
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_db_fixture.py',
                         'guest_db_fixture.py')
            if postcondition_guest_bundle is not None:
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_db_postconditions.py',
                         'guest_db_postconditions.py')
            if browser_guest_bundle is not None:
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_browser_ui.py',
                         'guest_browser_ui.py')
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_browser_cdp.py',
                         'guest_browser_cdp.py')
                if ssh.ssh('chmod', '0644', '/home/projectiq/guest_browser_cdp.py',
                           timeout=8).returncode != 0:
                    raise WorkerFailure('Trusted CDP helper permissions unavailable')
            if login_guest_bundle is not None:
                for module in ('guest_browser_login.py', 'guest_browser_login_cdp.py',
                               'guest_browser_cdp.py'):
                    worker_stage = {
                        'guest_browser_login.py': 'LOGIN_CONTROLLER_TRANSFER',
                        'guest_browser_login_cdp.py': 'LOGIN_CDP_TRANSFER',
                        'guest_browser_cdp.py': 'BASE_CDP_TRANSFER',
                    }[module]
                    ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / module,
                             module)
                    worker_stage = {
                        'guest_browser_login.py': 'LOGIN_CONTROLLER_PERMISSIONS',
                        'guest_browser_login_cdp.py': 'LOGIN_CDP_PERMISSIONS',
                        'guest_browser_cdp.py': 'BASE_CDP_PERMISSIONS',
                    }[module]
                    if ssh.ssh('chmod', '0644', '/home/projectiq/' + module,
                               timeout=8).returncode != 0:
                        raise WorkerFailure('Trusted login helper permissions unavailable')
            if crud_guest_bundle is not None:
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_api.py',
                         'guest_api.py')
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_crud.py',
                         'guest_crud.py')
            if dependent_guest_bundle is not None:
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_db_fixture.py',
                         'guest_db_fixture.py')
                ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / 'guest_workflow.py',
                         'guest_workflow.py')
            if node_assets is not None:
                ssh.copy_offline_asset(node_assets['node_tarball'], 'node_runtime.tar.xz')
                ssh.copy_offline_asset(node_assets['npm_cache_zip'], 'npm_cache.zip')
            if python_assets is not None:
                ssh.copy_offline_asset(python_assets['pip_wheel'], 'python_pip.whl')
                ssh.copy_offline_asset(python_assets['wheelhouse_zip'], 'python_wheelhouse.zip')
            worker_stage = 'GUEST_EXECUTION'
            result['execution'] = 'GUEST_AGENT_STARTED'
            command = ssh.ssh('python3', '/home/projectiq/guest_agent.py',
                              timeout=710 if candidate['adapter'] == 'node_fullstack_offline_v1' else
                              415 if candidate['adapter'] in ('node_vite_offline_v1', 'node_express_offline_v1') else
                              (240 if candidate['adapter'] == 'python_web_offline_v1' else
                                ((190 if login_guest_bundle is not None else 125)
                                 if candidate['adapter'] == 'php_mysql_v1' else 75)))
            output_path = work / 'result.json'
            worker_stage = 'GUEST_REPORT_RETRIEVAL'
            ssh.receive('result.json', output_path)
            worker_stage = 'GUEST_REPORT_PARSING'
            guest = load_json(output_path, limit=64_000)
            # Only allow a strict, small data subset into the persisted report.
            status = guest.get('status')
            result['guest_result'] = {
                'status': status if status in ('STARTUP_VERIFIED', 'STARTUP_FAILED') else 'INVALID_REPORT',
                'http_status': guest.get('http_status') if isinstance(guest.get('http_status'), int) else None,
                'readiness_path': guest.get('readiness_path') if guest.get('readiness_path') == candidate['probe_path'] else None,
                'readiness': (guest.get('readiness') if guest.get('readiness') in ('HTML_RESPONSE_OK', 'HTTP_RESPONSE_OK') else 'UNVERIFIED'),
                'notes': [x[:160] for x in guest.get('notes', [])[:5]
                          if isinstance(x, str)] if isinstance(guest.get('notes'), list) else [],
            }
            if test_bundle is not None:
                from ...phase4.report_contracts import GuestReportError, validate_guest_report
                try:
                    validate_guest_report(test_bundle, guest.get('phase4_test_report'))
                except (GuestReportError, TypeError, ValueError):
                    result['phase4_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4_status'] = 'VALIDATED'
                    # This contract contains only bounded safe evidence fields.
                    result['phase4_test_results'] = guest['phase4_test_report']
            if api_bundle is not None:
                from ...phase4.api_report_contracts import ApiReportError, validate_api_report
                try:
                    validate_api_report(api_bundle, guest.get('phase4c_api_report'))
                except (ApiReportError, TypeError, ValueError):
                    result['phase4c_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4c_status'] = 'VALIDATED'
                    result['phase4c_test_results'] = guest['phase4c_api_report']
            if db_fixture_bundle is not None:
                from ...phase4.db_fixture_contracts import FixtureReviewError, validate_fixture_report
                try:
                    validate_fixture_report(db_fixture_bundle, guest.get('phase4d1_fixture_report'))
                except (FixtureReviewError, TypeError, ValueError, KeyError):
                    result['phase4d1_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4d1_status'] = 'VALIDATED'
                    result['phase4d1_fixture_report'] = guest['phase4d1_fixture_report']
            if auth_guest_bundle is not None:
                from ...phase4.auth_report_contracts import AuthReportError, validate_auth_report
                try:
                    validate_auth_report(auth_guest_bundle, guest.get('phase4d2_auth_report'))
                except (AuthReportError, TypeError, ValueError, KeyError):
                    result['phase4d2_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4d2_status'] = 'VALIDATED'
                    result['phase4d2_auth_report'] = guest['phase4d2_auth_report']
            if postcondition_guest_bundle is not None:
                from ...phase4.auth_report_contracts import AuthReportError, validate_auth_report
                from ...phase4.db_postcondition_contracts import (
                    PostconditionReviewError, validate_guest_postcondition_report)
                try:
                    validate_auth_report(postcondition_guest_bundle['auth'],
                                         guest.get('phase4d3_auth_report'))
                    validate_guest_postcondition_report(
                        postcondition_guest_bundle['postconditions'],
                        guest['phase4d3_auth_report'], guest.get('phase4d3_postcondition_report'))
                except (AuthReportError, PostconditionReviewError, TypeError, ValueError, KeyError):
                    result['phase4d3_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4d3_status'] = 'VALIDATED'
                    result['phase4d3_auth_report'] = guest['phase4d3_auth_report']
                    result['phase4d3_postcondition_report'] = guest['phase4d3_postcondition_report']
            if browser_guest_bundle is not None:
                from ...phase4.guest_browser_ui import (
                    BrowserUiError, validate_browser_ui_report)
                try:
                    validate_browser_ui_report(browser_guest_bundle,
                                               guest.get('phase4dc_browser_report'))
                except (BrowserUiError, KeyError, TypeError, ValueError):
                    result['phase4dc_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4dc_status'] = 'VALIDATED'
                    result['phase4dc_browser_report'] = guest['phase4dc_browser_report']
            if login_guest_bundle is not None:
                from ...phase4.browser_login_contracts import (
                    LoginReviewError, validate_login_report)
                try:
                    validate_login_report(login_guest_bundle, guest.get('phase4dd_login_report'))
                except (LoginReviewError, KeyError, TypeError, ValueError):
                    result['phase4dd_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4dd_status'] = 'VALIDATED'
                    result['phase4dd_login_report'] = guest['phase4dd_login_report']
            if crud_guest_bundle is not None:
                from ...phase4.auth_report_contracts import AuthReportError, validate_auth_report
                from ...phase4.guest_crud import CrudError, validate_crud_report
                try:
                    validate_auth_report(crud_guest_bundle['auth'], guest.get('phase4db_auth_report'))
                    validate_crud_report(crud_guest_bundle['crud'], guest.get('phase4db_crud_report'))
                except (AuthReportError, CrudError, ValueError, TypeError, KeyError):
                    result['phase4db_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4db_status'] = 'VALIDATED'
                    result['phase4db_auth_report'] = guest['phase4db_auth_report']
                    result['phase4db_crud_report'] = guest['phase4db_crud_report']
            if dependent_guest_bundle is not None:
                from ...phase4.db_fixture_contracts import FixtureReviewError, validate_fixture_report
                from ...phase4.guest_workflow import (
                    DependentWorkflowError, validate_guest_workflow_report)
                try:
                    validate_fixture_report(dependent_guest_bundle['fixture'],
                                            guest.get('phase4d1_fixture_report'))
                    validate_guest_workflow_report(dependent_guest_bundle['workflow'],
                                                   guest.get('phase4da_workflow_report'))
                except (FixtureReviewError, DependentWorkflowError, TypeError, ValueError, KeyError):
                    result['phase4da_status'] = 'INVALID_GUEST_REPORT'
                else:
                    result['phase4da_status'] = 'VALIDATED'
                    result['phase4da_fixture_report'] = guest['phase4d1_fixture_report']
                    result['phase4da_workflow_report'] = guest['phase4da_workflow_report']
            # Compact, typed, guest-controller-collected kernel observations.
            # Startup alone is not accepted as evidence of process placement.
            placement = guest.get('cgroup_placement')
            expected_servers = 2 if candidate['adapter'] == 'node_fullstack_offline_v1' else 1
            if (isinstance(placement, dict) and
                    set(placement) == {'status', 'server_count', 'builds_verified',
                                       'database_service_verified'} and
                    placement.get('status') == 'SERVER_PIDS_VERIFIED' and
                    type(placement.get('server_count')) is int and
                    placement['server_count'] == expected_servers and
                    placement.get('builds_verified') is False and
                    placement.get('database_service_verified') is False):
                result['guest_result']['cgroup_placement'] = placement
            else:
                result['guest_result']['cgroup_placement'] = {'status': 'NOT_VERIFIED'}
            # Typed pre-exec handoff observation. Never copy raw PID/argv data.
            evidence = guest.get('build_launch_evidence')
            expected_builds = {
                'node_vite_offline_v1': (1, 1, 0),
                'node_express_offline_v1': (1, 0, 0),
                'python_web_offline_v1': (0, 0, 1),
                'node_fullstack_offline_v1': (2, 1, 0),
            }.get(candidate['adapter'], (0, 0, 0))
            required_state = 'PREEXEC_KERNEL_HANDOFF_VERIFIED' if any(expected_builds) else 'NOT_APPLICABLE'
            if (isinstance(evidence, dict) and
                    set(evidence) == {'status', 'npm_ci', 'vite_build', 'pip_install'} and
                    evidence.get('status') == required_state and
                    tuple(evidence.get(k) if type(evidence.get(k)) is int else None
                          for k in ('npm_ci', 'vite_build', 'pip_install')) == expected_builds):
                result['guest_result']['build_launch_evidence'] = evidence
            else:
                result['guest_result']['build_launch_evidence'] = {'status': 'NOT_VERIFIED'}
            if candidate['adapter'] == 'node_fullstack_offline_v1':
                services = guest.get('service_results')
                if isinstance(services, dict):
                    result['guest_result']['service_results'] = {
                        name: (services.get(name) if type(services.get(name)) is bool else
                               services.get(name) if type(services.get(name)) is int
                               and services[name] in (200, 502) else None)
                        for name in ('frontend_http', 'backend_http', 'frontend_proxy_api_http',
                                     'built_asset_http', 'sqlite_verified')}

            allowed_guest_errors = {
                'PHP PDO connection adapter not recognized',
                'No recognizable PDO connection',
                'PDO connection field not uniquely identifiable',
                'Expected one bounded SQL dump',
                'SQL binary content is not supported',
                'SQL dump uses operations outside the pilot allowlist',
                'MySQL disposable database bootstrap failed',
                'MySQL trusted cloud-init bootstrap unavailable',
                'MYSQL_RESOURCE_ENFORCEMENT_UNAVAILABLE',
                'RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE',
                'APP_CGROUP_SERVER_PLACEMENT_NOT_VERIFIED',
                'APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED',
                'APP_CGROUP_BUILD_RESUME_TIMEOUT',
                'APP_CGROUP_BUILD_EXEC_TIMEOUT',
                'APP_CGROUP_BUILD_GATE_RELEASE_FAILED',
                'APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT',
                'RUNTIME_SHARED_PATH_UNSUPPORTED',
                'WORKSPACE_DISK_QUOTA_UNAVAILABLE',
                'WORKSPACE_BOOTSTRAP_NOT_READY',
                'WORKSPACE_DISK_QUOTA_EXCEEDED',
                'MySQL guest admin permission unavailable',
                'MySQL service not ready before bootstrap',
                'MySQL bootstrap failed at database',
                'MySQL bootstrap failed at local_user',
                'MySQL bootstrap failed at loopback_user',
                'MySQL bootstrap failed at local_grant',
                'MySQL bootstrap failed at loopback_grant',
                'Schema import failed (SQL compatibility or permissions)',
                'SQL import produced no accessible tables',
                'PHP PDO failed to connect to disposable MySQL',
                'Trusted guest tool missing or timed out',
                'Application HTTP readiness not established: CONNECTION_UNAVAILABLE',
                'Application HTTP readiness not established: UNEXPECTED_HTTP_RESPONSE',
                'NODE_VITE_INVALID_REQUEST',
                'NODE_VITE_INVALID_PROJECT_ROOT',
                'NODE_VITE_NODE_ARCHIVE_INVALID',
                'NODE_VITE_NODE_HASH_MISMATCH',
                'NODE_VITE_RUNTIME_EXTRACTION_FAILED',
                'NODE_VITE_RUNTIME_VERSION_MISMATCH',
                'NODE_VITE_CACHE_ARCHIVE_INVALID',
                'NODE_VITE_CACHE_HASH_MISMATCH',
                'NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED',
                'NODE_VITE_LOCKFILE_HASH_MISMATCH',
                'NODE_VITE_OFFLINE_DEPENDENCY_INSTALL_FAILED',
                'NODE_VITE_BUILD_FAILED',
                'NODE_VITE_BUILD_OUTPUT_MISSING',
                'NODE_VITE_ASSET_PROBE_FAILED',
                'NODE_EXPRESS_INVALID_REQUEST',
                'NODE_EXPRESS_INVALID_PROJECT_ROOT',
                'NODE_EXPRESS_NODE_HASH_MISMATCH',
                'NODE_EXPRESS_RUNTIME_VERSION_MISMATCH',
                'NODE_EXPRESS_CACHE_HASH_MISMATCH',
                'NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED',
                'NODE_EXPRESS_LOCKFILE_HASH_MISMATCH',
                'NODE_EXPRESS_ENTRYPOINT_UNSUPPORTED',
                'NODE_EXPRESS_OFFLINE_DEPENDENCY_INSTALL_FAILED',
                'NODE_EXPRESS_HTTP_PROBE_FAILED',
                'NODE_FULLSTACK_INVALID_REQUEST', 'NODE_FULLSTACK_INVALID_SERVICE_ROOTS',
                'NODE_FULLSTACK_UNSUPPORTED_MANIFEST',
                'NODE_FULLSTACK_NODE_HASH_MISMATCH', 'NODE_FULLSTACK_NODE_VERSION_MISMATCH',
                'NODE_FULLSTACK_CACHE_HASH_MISMATCH', 'NODE_FULLSTACK_CACHE_ARCHIVE_INVALID',
                'NODE_FULLSTACK_OFFLINE_DEPENDENCY_INSTALL_FAILED',
                'NODE_FULLSTACK_VITE_BUILD_FAILED', 'NODE_FULLSTACK_BUILD_OUTPUT_INVALID',
                'NODE_FULLSTACK_BACKEND_NOT_READY', 'NODE_FULLSTACK_FRONTEND_NOT_READY',
                'NODE_FULLSTACK_CROSS_SERVICE_PROBE_FAILED',
                'NODE_FULLSTACK_SQLITE_CHECK_FAILED',
                'PYTHON_WEB_INVALID_REQUEST',
                'PYTHON_WEB_INVALID_PROJECT_ROOT',
                'PYTHON_WEB_REQUIREMENTS_UNSUPPORTED',
                'PYTHON_WEB_REQUIREMENTS_HASH_MISMATCH',
                'PYTHON_WEB_PIP_BOOTSTRAP_INVALID',
                'PYTHON_WEB_PIP_VERSION_MISMATCH',
                'PYTHON_WEB_RUNTIME_VERSION_MISMATCH',
                'PYTHON_WEB_WHEELHOUSE_HASH_MISMATCH',
                'PYTHON_WEB_WHEELHOUSE_INVALID',
                'PYTHON_WEB_OFFLINE_DEPENDENCY_INSTALL_FAILED',
                'PYTHON_WEB_ENTRYPOINT_UNSUPPORTED',
                'PYTHON_WEB_IMPORT_OR_SQLITE_FAILED',
                'PYTHON_WEB_HTTP_PROBE_FAILED',
                'PHASE4_TRUSTED_TEST_DISPATCH_FAILED',
                'PHASE4C_TRUSTED_API_DISPATCH_FAILED',
                'DATABASE_FIXTURE_SEEDING_NOT_VERIFIED',
                'PHASE4D2_TRUSTED_AUTH_DISPATCH_FAILED',
                'PHASE4D3_TRUSTED_DB_PROBE_FAILED',
                'PHASE4DA_TRUSTED_DEPENDENT_DISPATCH_FAILED',
                'PHASE4DB_TRUSTED_CRUD_DISPATCH_FAILED',
                'PHASE4DC_TRUSTED_BROWSER_DISPATCH_FAILED',
                'PHASE4DD_TRUSTED_LOGIN_DISPATCH_FAILED',
                'REVIEWED_BROWSER_RESOURCE_POLICY_DENIED',
                'UNEXPECTED_GUEST_FAILURE',
            }
            if status == 'STARTUP_FAILED':
                reported = guest.get('error_code')
                result['guest_error_code'] = reported if reported in allowed_guest_errors else 'UNCLASSIFIED_GUEST_FAILURE'
            if (status == 'STARTUP_VERIFIED' and command.returncode == 0
                    and (test_bundle is None or result.get('phase4_status') == 'VALIDATED')
                    and (api_bundle is None or result.get('phase4c_status') == 'VALIDATED')
                    and (db_fixture_bundle is None or result.get('phase4d1_status') == 'VALIDATED')
                    and (auth_guest_bundle is None or result.get('phase4d2_status') == 'VALIDATED')
                    and (postcondition_guest_bundle is None or result.get('phase4d3_status') == 'VALIDATED')
                    and (dependent_guest_bundle is None or result.get('phase4da_status') == 'VALIDATED')
                    and (crud_guest_bundle is None or result.get('phase4db_status') == 'VALIDATED')
                    and (browser_guest_bundle is None or result.get('phase4dc_status') == 'VALIDATED')
                    and (login_guest_bundle is None or result.get('phase4dd_status') == 'VALIDATED')):
                result.update(status='STARTUP_VERIFIED', execution='PERFORMED_IN_DISPOSABLE_VM',
                              readiness=('HTTP_200_FULLSTACK_PROBE_PASSED' if candidate['adapter'] == 'node_fullstack_offline_v1' else
                                         'HTTP_200_API_PROBE_PASSED' if candidate['adapter'] in ('python_web_offline_v1', 'node_express_offline_v1') else 'HTTP_200_HTML_PROBE_PASSED'))
            else:
                bad_test_report = ((test_bundle is not None or api_bundle is not None or db_fixture_bundle is not None
                                    or auth_guest_bundle is not None or postcondition_guest_bundle is not None
                                    or dependent_guest_bundle is not None or crud_guest_bundle is not None
                                    or browser_guest_bundle is not None or login_guest_bundle is not None)
                                   and status == 'STARTUP_VERIFIED' and command.returncode == 0)
                result.update(status='PHASE4_REPORT_INVALID' if bad_test_report else 'STARTUP_FAILED',
                              execution='ATTEMPTED_IN_DISPOSABLE_VM', readiness='NOT_VERIFIED',
                              reason=('GUEST_PHASE4_REPORT_NOT_VERIFIED' if bad_test_report
                                      else 'GUEST_ADAPTER_FAILED'))
        except Exception as exc:
            # Only trusted, fixed messages map to fixed diagnostic codes.
            # Never export exception text, argv, stderr, URL or HTML.
            if isinstance(exc, WorkerFailure):
                result['worker_failure_stage'] = worker_stage
                result['worker_failure_code'] = {
                    'Unapproved destination filename': 'UNAPPROVED_VM_TRANSFER_DESTINATION',
                    'Unable to copy trusted input into disposable VM': 'VM_TRANSFER_FAILED',
                    'Trusted login helper permissions unavailable': 'LOGIN_HELPER_PERMISSIONS_FAILED',
                    'Trusted CDP helper permissions unavailable': 'CDP_HELPER_PERMISSIONS_FAILED',
                    'Invalid or oversized VM report': 'VM_REPORT_UNAVAILABLE_OR_OVERSIZED',
                    'Worker VM SSH readiness timed out': 'VM_SSH_READINESS_TIMEOUT',
                    'Worker VM exited before SSH became ready': 'VM_EXITED_BEFORE_SSH',
                    'Worker VM startup cancelled': 'VM_BOOT_CANCELLED',
                    'Windows VM lifetime guard unavailable': 'WINDOWS_VM_LIFETIME_GUARD_MISSING',
                    'Unable to create disposable QEMU disk overlay': 'VM_OVERLAY_CREATION_FAILED',
                    'Could not create bounded disposable workspace disk': 'VM_WORKSPACE_DISK_CREATION_FAILED',
                    'Snapshot archive exceeds operator limit': 'SNAPSHOT_TOO_LARGE',
                    'Unapproved VM output name': 'UNAPPROVED_VM_OUTPUT',
                    'Invalid trusted SSH command arguments': 'INVALID_TRUSTED_SSH_ARGS',
                }.get(str(exc), 'OTHER_WORKER_FAILURE')
            # No user source text, token, database password or remote stderr in logs.
            result.update(status='WALL_TIME_LIMIT' if expired.is_set() else 'INFRASTRUCTURE_OR_ADAPTER_FAILURE',
                          reason='VM_WALL_TIME_EXCEEDED' if expired.is_set() else type(exc).__name__)
            result['execution'] = 'ATTEMPTED_VM' if vm_started else 'NOT_PERFORMED'
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if worker is not None:
                try:
                    worker.stop()
                    if runtime._phase in ('LAUNCHED', 'BOOTED'):
                        runtime.mark_stopped()
                    worker_stopped = True
                except Exception:
                    worker_stopped = False
            result['elapsed_seconds'] = round(time.monotonic() - started_at, 2)
    # The lease has been released and the recovery engine has independently
    # verified actual deletion; a successful worker.stop() alone is NOT cleanup.
    cleaned = runtime.cleanup_verified and (worker_stopped or not vm_started)
    result['cleanup'] = ('VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' if cleaned
                         else 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')
    if not cleaned:
        result['status'] = 'CLEANUP_FAILED'
        result['cleanup_failure_code'] = runtime.cleanup_reason[:100]
        if runtime.folder is not None and runtime.folder.exists():
            result['manual_review_workspace'] = str(runtime.folder)
    result['managed_runtime'] = ('SIGNED_LEASED_WORKSPACE_REMOVED' if cleaned
                                 else 'MANAGED_CLEANUP_NOT_VERIFIED')
    result['managed_run_id'] = runtime.folder.name if runtime.folder is not None else None
    _save(result, output)
    return result
