"""Phase 2C QEMU pilot orchestration. All app execution happens inside a VM.

The host uses ONLY trusted QEMU/SSH tooling and static snapshot inspection.
Launch is explicitly restricted to operator-approved pinned fixture SHAs.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import uuid
from pathlib import Path

from ...github_client import GitHubClient
from ...models import RepositorySource
from ...safe_archive import extract_snapshot
from .contracts import VMPlanError, build_vm_plan, load_json
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key, verify_host_toolchain)


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
                         *, reviewed_fixture: bool = False, work_root: Path | None = None) -> dict:
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
    toolchain = verify_host_toolchain(config)
    if toolchain['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
        result['reason'] = 'QEMU_TOOLS_OR_IMMUTABLE_BASE_IMAGE_NOT_VERIFIED'
        result['toolchain'] = {k: bool(v) for k, v in toolchain['toolchain'].items()}
        result['base_sha256_matches'] = toolchain['template_sha256_matches']
        _save(result, output)
        return result

    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    vm_started = False
    worker_stopped = False
    expired = threading.Event()
    watchdog = None
    # TemporaryDirectory also removes SSH keys and any VM disk mutations.
    with tempfile.TemporaryDirectory(prefix='projectiq_vm_', dir=parent) as temp:
        work = Path(temp)
        worker = None
        try:
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
            }
            (work / 'request.json').write_text(json.dumps(request), 'utf-8')
            key, pubkey = make_ssh_key(config, work)
            seed = make_nocloud_iso(work, pubkey, 'projectiq-' + uuid.uuid4().hex)
            port = _free_loopback_port()
            limits = candidate['vm_limits']
            worker = QemuWorker(config, work, port, ram_mib=limits['ram_mib'],
                                cpus=effective_vm_vcpus(config, limits['vcpu']))
            worker.start(seed)
            vm_started = True
            def expire():
                if worker is not None and worker.proc is not None and worker.proc.poll() is None:
                    expired.set()
                    worker.proc.kill()
            watchdog = threading.Timer(candidate['vm_limits']['wall_seconds'], expire)
            watchdog.daemon = True
            watchdog.start()
            result['execution'] = 'VM_BOOT_REQUESTED'
            ssh = SSHControl(config, work, port, key)
            ssh.wait_ssh(worker, seconds=85)
            ssh.copy(archive, 'snapshot.zip')
            ssh.copy(Path(__file__).with_name('guest_agent.py'), 'guest_agent.py')
            ssh.copy(work / 'request.json', 'request.json')
            result['execution'] = 'GUEST_AGENT_STARTED'
            command = ssh.ssh('python3', '/home/projectiq/guest_agent.py',
                              timeout=125 if candidate['adapter'] == 'php_mysql_v1' else 75)
            output_path = work / 'result.json'
            ssh.receive('result.json', output_path)
            guest = load_json(output_path, limit=64_000)
            # Only allow a strict, small data subset into the persisted report.
            status = guest.get('status')
            result['guest_result'] = {
                'status': status if status in ('STARTUP_VERIFIED', 'STARTUP_FAILED') else 'INVALID_REPORT',
                'http_status': guest.get('http_status') if isinstance(guest.get('http_status'), int) else None,
                'readiness_path': guest.get('readiness_path') if guest.get('readiness_path') == candidate['probe_path'] else None,
                'readiness': guest.get('readiness') if guest.get('readiness') == 'HTML_RESPONSE_OK' else 'UNVERIFIED',
                'notes': [x[:160] for x in guest.get('notes', [])[:5]
                          if isinstance(x, str)] if isinstance(guest.get('notes'), list) else [],
            }
            allowed_guest_errors = {
                'PHP PDO connection adapter not recognized',
                'No recognizable PDO connection',
                'PDO connection field not uniquely identifiable',
                'Expected one bounded SQL dump',
                'SQL binary content is not supported',
                'SQL dump uses operations outside the pilot allowlist',
                'MySQL disposable database bootstrap failed',
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
                'UNEXPECTED_GUEST_FAILURE',
            }
            if status == 'STARTUP_FAILED':
                reported = guest.get('error_code')
                result['guest_error_code'] = reported if reported in allowed_guest_errors else 'UNCLASSIFIED_GUEST_FAILURE'
            if status == 'STARTUP_VERIFIED' and command.returncode == 0:
                result.update(status='STARTUP_VERIFIED', execution='PERFORMED_IN_DISPOSABLE_VM',
                              readiness='HTTP_200_HTML_PROBE_PASSED')
            else:
                result.update(status='STARTUP_FAILED', execution='ATTEMPTED_IN_DISPOSABLE_VM',
                              readiness='NOT_VERIFIED', reason='GUEST_ADAPTER_FAILED')
        except Exception as exc:
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
                    worker_stopped = True
                except Exception:
                    worker_stopped = False
            result['cleanup'] = ('VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' if worker_stopped or not vm_started
                                 else 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')
            result['elapsed_seconds'] = round(time.monotonic() - started_at, 2)
    _save(result, output)
    return result
