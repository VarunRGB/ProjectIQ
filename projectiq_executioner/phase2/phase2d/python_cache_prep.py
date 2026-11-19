"""D2 operator-reviewed, disposable Linux Python wheelhouse preparation.

No student code, pip, or package command runs on the host. The approved
preparation VM alone may use PyPI networking, and ONLY to download wheels.
Normal backend execution uses a separate VM with egress denied.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from ...analyzer import analyze_directory
from ...github_client import GitHubClient, IngestionError
from ...safe_archive import extract_snapshot, ArchiveError
from ..vm_worker.guest_agent import (_check_python_requirements,
                                     _check_python_entry, GuestFailure)
from ..vm_worker.offline_assets import OfflineAssetError
from ..vm_worker.orchestrator import load_config
from ..vm_worker.qemu import (QemuWorker, SSHControl, WorkerFailure,
                              _free_loopback_port, effective_vm_vcpus,
                              make_nocloud_iso, make_ssh_key, sha256_file,
                              verify_host_toolchain)
from .pipeline import plan_from_manifest

_SHA40 = re.compile(r'^[0-9a-fA-F]{40}$')
_SHA64 = re.compile(r'^[0-9a-fA-F]{64}$')


class PythonCacheError(ValueError):
    pass


def prepare_python_cache(*, repo_url: str, commit_sha: str, vm_config: Path,
                         pip_wheel: Path, pip_sha256: str, output_dir: Path,
                         allow_trusted_pypi_network: bool = False) -> dict:
    """Prepare immutable wheelhouse for exactly one approved pinned repository."""
    if not allow_trusted_pypi_network:
        raise PythonCacheError('Explicit --allow-trusted-pypi-network approval required')
    if not isinstance(commit_sha, str) or not _SHA40.fullmatch(commit_sha):
        raise PythonCacheError('Expected exact 40-character pinned GitHub commit SHA')
    if not isinstance(pip_sha256, str) or not _SHA64.fullmatch(pip_sha256):
        raise PythonCacheError('Pinned pip wheel SHA-256 is invalid')
    pip_wheel = Path(pip_wheel).expanduser().resolve()
    if (pip_wheel.name != 'pip-25.2-py3-none-any.whl'
            or not pip_wheel.is_file() or not 0 < pip_wheel.stat().st_size <= 5_000_000
            or sha256_file(pip_wheel) != pip_sha256.lower()):
        raise PythonCacheError('Official pip 25.2 wheel missing or SHA-256 mismatched')
    config = load_config(vm_config)
    client = GitHubClient(timeout=25)
    source = client.resolve(repo_url, commit_sha)
    if source.commit_sha.lower() != commit_sha.lower():
        raise PythonCacheError('GitHub source differs from requested pinned commit')
    identity = source.repository + '@' + source.commit_sha.lower()
    if identity not in config['approved_snapshots']:
        raise PythonCacheError('Pinned repository not approved in local VM config')
    if verify_host_toolchain(config)['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
        raise PythonCacheError('QEMU toolchain or pinned guest image not verified')
    output_dir = Path(output_dir).expanduser().resolve()
    if output_dir.exists() and not output_dir.is_dir():
        raise PythonCacheError('Output exists but is not a directory')
    cache_path = output_dir / 'python-wheelhouse.zip'
    profile_path = output_dir / 'python_web_profile.json'
    if cache_path.exists() or profile_path.exists():
        raise PythonCacheError('Refusing to overwrite existing Python wheelhouse or profile')

    with tempfile.TemporaryDirectory(prefix='projectiq_python_prep_') as tmp:
        work = Path(tmp)
        snapshot = work / 'snapshot.zip'
        client.download(source, snapshot)
        if snapshot.stat().st_size > 30_000_000:
            raise PythonCacheError('Pinned repository archive exceeds pilot limit')
        checkout = extract_snapshot(snapshot, work / 'inspection')
        manifest = analyze_directory(checkout, source).to_dict()
        candidate, _ = plan_from_manifest(manifest, work / 'plan')
        if candidate.get('adapter') != 'python_web_offline_v1':
            raise PythonCacheError('Repository not recognized as standalone Flask/FastAPI pilot')
        framework = candidate['python_framework']
        project_root = candidate['project_root']
        subdir = checkout if project_root == '.' else checkout.joinpath(*project_root.split('/'))
        req = subdir / 'requirements.txt'
        if not req.is_file():
            raise PythonCacheError('Pinned Python requirements missing')
        requirements_sha256 = sha256_file(req)
        try:
            _check_python_requirements(subdir, requirements_sha256, framework)
            _check_python_entry(subdir, framework)
        except GuestFailure as exc:
            raise PythonCacheError('Python requirements or fixed entrypoint not supported') from exc
        request = {
            'operation': 'prepare_python_wheels', 'adapter': 'python_web_offline_v1',
            'project_root': project_root, 'python_framework': framework,
            'python_version': '3.12', 'requirements_sha256': requirements_sha256,
            'pip_sha256': pip_sha256.lower(),
        }
        request_path = work / 'request.json'
        request_path.write_text(json.dumps(request), 'utf-8')
        key, pubkey = make_ssh_key(config, work)
        seed = make_nocloud_iso(work, pubkey, 'projectiq-python-prep-' + uuid.uuid4().hex)
        port = _free_loopback_port()
        vm = QemuWorker(config, work, port, ram_mib=2048,
                        cpus=effective_vm_vcpus(config, 2), trusted_dependency_fetch=True)
        expired = threading.Event()
        timer = None
        stopped = False
        try:
            vm.start(seed)
            def expire():
                if vm.proc is not None and vm.proc.poll() is None:
                    expired.set()
                    vm.proc.kill()
            timer = threading.Timer(450, expire)
            timer.daemon = True
            timer.start()
            ssh = SSHControl(config, work, port, key)
            ssh.wait_ssh(vm, seconds=110)
            ssh.copy(snapshot, 'snapshot.zip')
            ssh.copy(Path(__file__).resolve().parents[1] / 'vm_worker' / 'guest_agent.py',
                     'guest_agent.py')
            ssh.copy(request_path, 'request.json')
            ssh.copy_offline_asset(pip_wheel, 'python_pip.whl')
            command = ssh.ssh('python3', '/home/projectiq/guest_agent.py', timeout=305)
            result_path = work / 'result.json'
            ssh.receive('result.json', result_path)
            guest = json.loads(result_path.read_text('utf-8'))
            if (command.returncode or guest.get('status') != 'PYTHON_WHEELHOUSE_PREPARED'
                    or guest.get('framework') != framework
                    or guest.get('python_version') != '3.12'
                    or guest.get('requirements_sha256') != requirements_sha256
                    or not _SHA64.fullmatch(str(guest.get('wheelhouse_sha256', '')))):
                approved_errors = {
                    'PYTHON_WEB_INVALID_REQUEST', 'PYTHON_WEB_RUNTIME_VERSION_MISMATCH',
                    'PYTHON_WEB_REQUIREMENTS_UNSUPPORTED',
                    'PYTHON_WEB_REQUIREMENTS_HASH_MISMATCH',
                    'PYTHON_WEB_PIP_BOOTSTRAP_INVALID',
                    'PYTHON_WEB_PIP_VERSION_MISMATCH',
                    'PYTHON_WEB_ENTRYPOINT_UNSUPPORTED',
                    'PYTHON_WEB_INVALID_PROJECT_ROOT',
                    'PYTHON_WEB_WHEEL_DOWNLOAD_FAILED',
                    'PYTHON_WEB_WHEELHOUSE_INVALID',
                }
                code = guest.get('error_code')
                safe_code = code if isinstance(code, str) and code in approved_errors else 'UNCLASSIFIED_GUEST_FAILURE'
                raise PythonCacheError('Disposable Python wheel preparation failed: ' + safe_code)
            retrieved = work / 'retrieved-python-wheelhouse.zip'
            ssh.receive_offline_cache(retrieved, remote_filename='python_wheelhouse.zip')
            result_sha = sha256_file(retrieved)
            if result_sha != guest['wheelhouse_sha256']:
                raise PythonCacheError('Downloaded offline Python wheelhouse hash mismatch')
        finally:
            if timer is not None:
                timer.cancel()
            try:
                vm.stop()
                stopped = True
            except Exception:
                stopped = False
        if not stopped or expired.is_set():
            raise PythonCacheError('Python preparation VM cleanup or time limit failed')
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(retrieved, cache_path)
        if sha256_file(cache_path) != result_sha:
            cache_path.unlink(missing_ok=True)
            raise PythonCacheError('Saved Python wheelhouse hash mismatch')
        profile = {
            'python_version': '3.12', 'framework': framework,
            'pip_wheel': str(pip_wheel), 'pip_sha256': pip_sha256.lower(),
            'wheelhouse_zip': str(cache_path), 'wheelhouse_sha256': result_sha,
            'requirements_sha256': requirements_sha256,
        }
        profile_path.write_text(json.dumps({'python_web_offline': {identity: profile}}, indent=2) + '\n',
                                'utf-8')
        return {
            'status': 'OFFLINE_PYTHON_WHEELHOUSE_PREPARED_NOT_SANDBOX_ATTESTED',
            'source': identity, 'framework': framework, 'python_version': '3.12',
            'network': 'EXPLICITLY_AUTHORIZED_PREPARATION_VM_ONLY',
            'execution_vm_egress': 'DENY',
            'student_package_builds': 'DISABLED_BINARY_WHEELS_ONLY',
            'cleanup': 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
            'wheelhouse_zip': str(cache_path), 'profile_json': str(profile_path),
            'requirements_sha256': requirements_sha256,
            'wheelhouse_sha256': result_sha,
        }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='D2: approved offline Python wheel preparation in disposable Ubuntu VM')
    p.add_argument('--repo', required=True)
    p.add_argument('--ref', required=True)
    p.add_argument('--vm-config', type=Path, required=True)
    p.add_argument('--pip-wheel', type=Path, required=True)
    p.add_argument('--pip-sha256', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--allow-trusted-pypi-network', action='store_true')
    args = p.parse_args(argv)
    try:
        result = prepare_python_cache(
            repo_url=args.repo, commit_sha=args.ref, vm_config=args.vm_config,
            pip_wheel=args.pip_wheel, pip_sha256=args.pip_sha256,
            output_dir=args.out,
            allow_trusted_pypi_network=args.allow_trusted_pypi_network,
        )
    except (PythonCacheError, OfflineAssetError, WorkerFailure, IngestionError,
            ArchiveError, ValueError, OSError) as exc:
        p.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
