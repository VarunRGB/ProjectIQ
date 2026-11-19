"""Operator-authorized, disposable, networked npm *fetch-only* VM.

NEVER use the offline runtime VM for network access. This program creates a
separate, operator-reviewed preparation VM that runs npm ci --ignore-scripts
with temporary internet access and returns only a bounded cache ZIP. No npm
commands or student code are launched on the Windows/Linux controller.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from ...analyzer import analyze_directory
from ...github_client import GitHubClient, IngestionError
from ...safe_archive import extract_snapshot, ArchiveError
from ..vm_worker.guest_agent import _check_vite_manifest, GuestFailure
from ..vm_worker.offline_assets import OfflineAssetError
from ..vm_worker.orchestrator import load_config
from ..vm_worker.qemu import (QemuWorker, SSHControl, WorkerFailure,
                              _free_loopback_port, effective_vm_vcpus,
                              make_nocloud_iso, make_ssh_key, sha256_file,
                              verify_host_toolchain)
from .pipeline import plan_from_manifest

_FULL_SHA = re.compile(r'^[0-9a-fA-F]{40}$')
_NODE_VERSION = re.compile(r'^v24\.[0-9]{1,3}\.[0-9]{1,3}$')
_HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')


class CachePreparationError(ValueError):
    pass


def prepare_cache(*, repo_url: str, commit_sha: str, vm_config: Path,
                  node_tarball: Path, node_sha256: str, node_version: str,
                  output_dir: Path, allow_trusted_npm_network: bool = False,
                  include_npm_diagnostic: bool = False) -> dict:
    if not allow_trusted_npm_network:
        raise CachePreparationError('Explicit --allow-trusted-npm-network approval required')
    if not isinstance(commit_sha, str) or not _FULL_SHA.fullmatch(commit_sha):
        raise CachePreparationError('Expected exact 40-character pinned GitHub commit SHA')
    if not isinstance(node_version, str) or not _NODE_VERSION.fullmatch(node_version):
        raise CachePreparationError('Only pinned official Node 24 Linux x64 is enabled')
    if not _HEX64.fullmatch(node_sha256):
        raise CachePreparationError('Official Node SHA256 is invalid')
    node_tarball = Path(node_tarball).expanduser().resolve()
    if (node_tarball.name != f'node-{node_version}-linux-x64.tar.xz'
            or not node_tarball.is_file() or not 0 < node_tarball.stat().st_size <= 65_000_000
            or sha256_file(node_tarball) != node_sha256.lower()):
        raise CachePreparationError('Official Linux Node archive is missing or SHA256 mismatched')
    config = load_config(vm_config)
    source = GitHubClient(timeout=25).resolve(repo_url, commit_sha)
    if source.commit_sha.lower() != commit_sha.lower():
        raise CachePreparationError('GitHub source is not the requested pinned commit')
    identity = source.repository + '@' + source.commit_sha.lower()
    if identity not in config['approved_snapshots']:
        raise CachePreparationError('Operator has not approved the pinned fixture in vm_config.json')
    if verify_host_toolchain(config)['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
        raise CachePreparationError('QEMU or pinned guest image is not verified')
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and not output_dir.is_dir():
        raise CachePreparationError('Output path is not a directory')
    cache_dest = output_dir / 'node-vite-npm-cache.zip'
    profile_dest = output_dir / 'node_vite_profile.json'
    if cache_dest.exists() or profile_dest.exists():
        raise CachePreparationError('Refusing to overwrite prepared offline cache or profile')

    with tempfile.TemporaryDirectory(prefix='projectiq_node_fetch_') as tmp:
        work = Path(tmp)
        snapshot = work / 'snapshot.zip'
        client = GitHubClient(timeout=25)
        client.download(source, snapshot)
        if snapshot.stat().st_size > 30_000_000:
            raise CachePreparationError('Pinned snapshot archive exceeds approved size')
        checkout = extract_snapshot(snapshot, work / 'inspection')
        analyzed = analyze_directory(checkout, source)
        manifest = analyzed.to_dict()
        c, _ = plan_from_manifest(manifest, work / 'plan')
        if c.get('adapter') != 'node_vite_offline_v1':
            raise CachePreparationError('Repository is not a standalone Vite/npm-lock fixture')
        root_path = checkout if c['project_root'] == '.' else checkout.joinpath(*c['project_root'].split('/'))
        lock = root_path / 'package-lock.json'
        if not lock.is_file():
            raise CachePreparationError('Approved fixture has no npm lockfile')
        lock_sha = sha256_file(lock)
        try:
            _check_vite_manifest(root_path, lock_sha)
        except GuestFailure as exc:
            raise CachePreparationError('Vite manifest/lockfile is not supported by offline pilot') from exc
        request = {
            'operation': 'prepare_npm_cache', 'adapter': 'node_vite_offline_v1',
            'project_root': c['project_root'], 'node_version': node_version,
            'include_npm_diagnostic': include_npm_diagnostic is True,
            'node_sha256': node_sha256.lower(), 'lockfile_sha256': lock_sha,
        }
        request_path = work / 'request.json'
        request_path.write_text(json.dumps(request), 'utf-8')
        key, pubkey = make_ssh_key(config, work)
        seed = make_nocloud_iso(work, pubkey, 'projectiq-node-fetch-' + uuid.uuid4().hex)
        port = _free_loopback_port()
        vm = QemuWorker(config, work, port, ram_mib=3072,
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
            timer = threading.Timer(540, expire)
            timer.daemon = True
            timer.start()
            ssh = SSHControl(config, work, port, key)
            ssh.wait_ssh(vm, seconds=110)
            ssh.copy(snapshot, 'snapshot.zip')
            ssh.copy(Path(__file__).resolve().parents[1] / 'vm_worker' / 'guest_agent.py', 'guest_agent.py')
            ssh.copy(request_path, 'request.json')
            ssh.copy_offline_asset(node_tarball, 'node_runtime.tar.xz')
            command = ssh.ssh('python3', '/home/projectiq/guest_agent.py', timeout=365)
            result_path = work / 'result.json'
            ssh.receive('result.json', result_path)
            guest = json.loads(result_path.read_text('utf-8'))
            if (command.returncode != 0 or guest.get('status') != 'CACHE_PREPARED'
                    or guest.get('node_version') != node_version
                    or guest.get('lockfile_sha256') != lock_sha
                    or not _HEX64.fullmatch(str(guest.get('npm_cache_sha256', '')))):
                approved_codes = {
                    'NODE_VITE_CACHE_PREPARATION_FAILED', 'NODE_VITE_CACHE_ARCHIVE_INVALID',
                    'NODE_VITE_NPM_TLS_CERTIFICATE', 'NODE_VITE_NPM_NETWORK_DNS',
                    'NODE_VITE_NPM_NETWORK_TIMEOUT', 'NODE_VITE_NPM_NETWORK_CONNECTIVITY',
                    'NODE_VITE_NPM_DISK_SPACE', 'NODE_VITE_NPM_FILE_PERMISSION',
                    'NODE_VITE_NPM_INTEGRITY', 'NODE_VITE_NPM_LOCKFILE_INCONSISTENT',
                    'NODE_VITE_NPM_DEPENDENCY_CONFLICT', 'NODE_VITE_NPM_REGISTRY_DENIED',
                    'NODE_VITE_NPM_REGISTRY_NOT_FOUND', 'NODE_VITE_NPM_PLATFORM_UNSUPPORTED',
                    'NODE_VITE_NPM_FAILURE_UNCLASSIFIED',
                    'NODE_VITE_NPM_AUTH_REQUIRED', 'NODE_VITE_NPM_MISSING_FILE',
                    'NODE_VITE_NPM_INVALID_PACKAGE', 'NODE_VITE_NPM_UNSUPPORTED_PROTOCOL',
                    'NODE_VITE_NPM_PROCESS_TERMINATED',
                    'NODE_VITE_NPM_UNCLASSIFIED_REGISTRY_REACHABLE',
                    'NODE_VITE_NPM_REGISTRY_PROBE_HTTP_ERROR',
                    'NODE_VITE_NPM_REGISTRY_PROBE_CONNECTION_FAILED',
                    'NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED',
                    'NODE_VITE_LOCKFILE_HASH_MISMATCH',
                    'NODE_VITE_RUNTIME_EXTRACTION_FAILED',
                    'NODE_VITE_RUNTIME_VERSION_MISMATCH',
                    'NODE_VITE_NODE_HASH_MISMATCH',
                }
                code = guest.get('error_code')
                detail = code if isinstance(code, str) and code in approved_codes else 'UNCLASSIFIED_GUEST_FAILURE'
                if include_npm_diagnostic:
                    hint = guest.get('npm_operator_hint')
                    # Redundant host-side character/length gate. The guest
                    # already redacts and bounds the selected npm-error lines.
                    if (isinstance(hint, str) and 0 < len(hint) <= 680 and
                            re.fullmatch(r'[A-Za-z0-9 _.,!?:;()\[\]\-|]+', hint)):
                        detail += '\nOperator-only sanitized npm error: ' + hint
                raise CachePreparationError('Disposable npm preparation failed: ' + detail)
            local_cache = work / 'retrieved-cache.zip'
            ssh.receive_offline_cache(local_cache)
            calculated = sha256_file(local_cache)
            if calculated != guest['npm_cache_sha256']:
                raise CachePreparationError('Returned offline npm cache SHA256 mismatched')
        finally:
            if timer is not None:
                timer.cancel()
            try:
                vm.stop()
                stopped = True
            except Exception:
                stopped = False
        if not stopped or expired.is_set():
            raise CachePreparationError('Preparation VM cleanup or wall-time gate failed')
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_cache, cache_dest)
        if sha256_file(cache_dest) != calculated:
            cache_dest.unlink(missing_ok=True)
            raise CachePreparationError('Copied offline npm cache SHA256 mismatched')
        profile = {
            'node_version': node_version, 'node_tarball': str(node_tarball),
            'node_sha256': node_sha256.lower(),
            'npm_cache_zip': str(cache_dest), 'npm_cache_sha256': calculated,
            'lockfile_sha256': lock_sha,
        }
        profile_dest.write_text(json.dumps({'node_vite_offline': {identity: profile}}, indent=2) + '\n', 'utf-8')
        return {
            'status': 'OFFLINE_CACHE_PREPARED_NOT_SANDBOX_ATTESTED', 'source': identity,
            'network': 'EXPLICITLY_AUTHORIZED_PREPARATION_VM_ONLY',
            'student_install_scripts': 'DISABLED',
            'execution_vm_egress': 'DENY',
            'cleanup': 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
            'cache_zip': str(cache_dest), 'profile_json': str(profile_dest),
            'node_version': node_version, 'lockfile_sha256': lock_sha,
            'npm_cache_sha256': calculated,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D1: prepare Linux offline npm cache in an approved disposable networked VM')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--vm-config', type=Path, required=True)
    parser.add_argument('--node-tarball', type=Path, required=True)
    parser.add_argument('--node-sha256', required=True)
    parser.add_argument('--node-version', default='v24.22.3')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--allow-trusted-npm-network', action='store_true')
    parser.add_argument('--diagnose-npm', action='store_true',
                        help='Opt in to a bounded, redacted npm-error hint on preparation failures')
    args = parser.parse_args(argv)
    try:
        result = prepare_cache(
            repo_url=args.repo, commit_sha=args.ref, vm_config=args.vm_config,
            node_tarball=args.node_tarball, node_sha256=args.node_sha256,
            node_version=args.node_version, output_dir=args.out,
            allow_trusted_npm_network=args.allow_trusted_npm_network,
            include_npm_diagnostic=args.diagnose_npm,
        )
    except (CachePreparationError, OfflineAssetError, WorkerFailure, IngestionError,
            ArchiveError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
