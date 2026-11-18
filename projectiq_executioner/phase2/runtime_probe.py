"""Phase 2B: OPTIONAL read-only Docker/Podman capability discovery.

Never starts a container, builds an image, pulls from registries, or executes
student code. A successful CLI probe is not a security attestation; a separate
trusted worker/VM must still be approved and tested by the operator.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping

MAX_RUNTIME_OUTPUT = 196_608
PROBE_TIMEOUT_S = 7


def _safe_version(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        s = str(value)
        if re.fullmatch(r'[a-zA-Z0-9._+:-]{1,40}', s):
            return s
    return None


def _context_is_safe_to_probe(runtime: str, environment: Mapping[str, str]) -> bool:
    # Never silently access a remote daemon because of an inherited environment.
    if runtime == 'docker':
        if environment.get('DOCKER_CONTEXT'):
            return False  # Context can refer to an SSH/TCP daemon.
        endpoint = environment.get('DOCKER_HOST', '')
    else:
        endpoint = environment.get('CONTAINER_HOST', '') or environment.get('PODMAN_HOST', '')
    return (not endpoint or endpoint.startswith(('unix://', 'npipe://', 'fd://')))


def _run_readonly_probe(executable: str, argv: list[str], timeout: int) -> dict[str, Any] | None:
    try:
        p = subprocess.run([executable] + argv, capture_output=True, text=True,
                           errors='replace', timeout=timeout, shell=False, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or len(p.stdout.encode('utf-8')) > MAX_RUNTIME_OUTPUT:
        return None
    try:
        obj = json.loads(p.stdout)
    except (json.JSONDecodeError, UnicodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _docker_assess(info: dict[str, Any]) -> dict[str, Any]:
    raw_security = info.get('SecurityOptions') or []
    opts = [str(s).lower() for s in raw_security if isinstance(s, str)] if isinstance(raw_security, list) else []
    rootless = any('rootless' in s for s in opts)
    seccomp = any('seccomp' in s for s in opts)
    cgroup2 = str(info.get('CgroupVersion', '')).strip().lower() in {'2', 'v2'}
    cgroup_manager = str(info.get('CgroupDriver', '')).strip().lower() == 'systemd'
    os_ok = str(info.get('OSType', '')).lower() == 'linux'
    # cgroup v2 alone is insufficient; resource enforcement must be checked
    # empirically on the future trusted worker.
    checks = {
        'linux_container_engine': os_ok,
        'rootless_engine_confirmed': rootless,
        'cgroup_v2_reported': cgroup2,
        'cgroup_systemd_manager_reported': cgroup_manager,
        'seccomp_reported': seccomp,
    }
    return {
        'engine': 'docker',
        'engine_version': _safe_version(info.get('ServerVersion')),
        'checks': checks,
        'missing_requirements': [k for k, v in checks.items() if not v],
        'capability_status': 'CANDIDATE_REQUIRES_ATTESTATION' if all(checks.values()) else 'POLICY_REQUIREMENTS_UNMET',
    }


def _podman_assess(info: dict[str, Any]) -> dict[str, Any]:
    host = info.get('host', {})
    host = host if isinstance(host, dict) else {}
    security = host.get('security', {})
    security = security if isinstance(security, dict) else {}
    os_info = host.get('os', '')
    os_ok = str(os_info).lower() == 'linux'
    cgroup2 = str(host.get('cgroupVersion', '')).lower() in {'v2', '2'}
    cgroup_manager = str(host.get('cgroupManager', '')).lower() == 'systemd'
    profile = bool(security.get('seccompEnabled') is True)
    checks = {
        'linux_container_engine': os_ok,
        'rootless_engine_confirmed': security.get('rootless') is True,
        'cgroup_v2_reported': cgroup2,
        'cgroup_systemd_manager_reported': cgroup_manager,
        'seccomp_reported': profile,
    }
    version = info.get('version', {})
    version = version if isinstance(version, dict) else {}
    return {
        'engine': 'podman',
        'engine_version': _safe_version(version.get('Version')),
        'checks': checks,
        'missing_requirements': [k for k, v in checks.items() if not v],
        'capability_status': 'CANDIDATE_REQUIRES_ATTESTATION' if all(checks.values()) else 'POLICY_REQUIREMENTS_UNMET',
    }


def probe_container_runtime(
    preferred: str = 'auto', *,
    locator: Callable[[str], str | None] | None = None,
    runner: Callable[[str, list[str], int], dict[str, Any] | None] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read-only, bounded CLI queries. Caller must explicitly opt in.

    `locator` and `runner` may be injected for offline unit tests.
    Network/daemon access through a custom or remote context is refused.
    """
    if preferred not in {'auto', 'docker', 'podman'}:
        raise ValueError('runtime must be auto, docker, or podman')
    locate = locator or shutil.which
    run = runner or _run_readonly_probe
    env = os.environ if environment is None else environment
    candidates = ['docker', 'podman'] if preferred == 'auto' else [preferred]
    observations: list[dict[str, Any]] = []
    for name in candidates:
        binary = locate(name)
        if not binary:
            observations.append({'engine': name, 'status': 'NOT_INSTALLED'})
            continue
        if not _context_is_safe_to_probe(name, env):
            observations.append({'engine': name, 'status': 'REMOTE_OR_CUSTOM_DAEMON_CONTEXT_NOT_PROBED'})
            continue
        if name == 'docker':
            # The active context can be remote even if DOCKER_HOST is unset.
            # This query uses local Docker CLI config, not a daemon operation.
            context = run(binary, ['context', 'inspect', '--format', '{{json .}}'], PROBE_TIMEOUT_S)
            endpoints = context.get('Endpoints', {}) if isinstance(context, dict) else {}
            endpoint = endpoints.get('docker', {}) if isinstance(endpoints, dict) else {}
            host = endpoint.get('Host', '') if isinstance(endpoint, dict) else ''
            if not isinstance(host, str) or not host.startswith(('unix://', 'npipe://', 'fd://')):
                observations.append({'engine': name, 'status': 'DAEMON_CONTEXT_ENDPOINT_NOT_VERIFIED_LOCAL'})
                continue
        args = ['info', '--format', '{{json .}}'] if name == 'docker' else ['info', '--format', 'json']
        info = run(binary, args, PROBE_TIMEOUT_S)
        if info is None:
            observations.append({'engine': name, 'status': 'NOT_RESPONDING_OR_INVALID_INFO'})
            continue
        assessment = _docker_assess(info) if name == 'docker' else _podman_assess(info)
        observations.append({'engine': name, 'status': 'PROBED', **assessment})
    viable = next((x for x in observations if x.get('capability_status') == 'CANDIDATE_REQUIRES_ATTESTATION'), None)
    if viable is None:
        viable = next((x for x in observations if x['status'] == 'PROBED'), None)
    return {
        'probe_status': 'PROBED' if viable else 'UNAVAILABLE_OR_UNVERIFIED',
        'selected_engine': viable['engine'] if viable else None,
        'capability_status': viable.get('capability_status') if viable else 'UNVERIFIED',
        'observations': observations,
        'host_platform': ('windows' if platform.system() == 'Windows' else 'macos' if platform.system() == 'Darwin' else 'linux' if platform.system() == 'Linux' else 'other'),
        'vm_or_worker_boundary_verified': False,
        'resource_limits_enforced_verified': False,
        'network_egress_isolation_verified': False,
        'policy_attested': False,
        'note': 'CLI reports capabilities only; privileged daemon access, VM hardening, resource enforcement, network policy and teardown require separate operator tests.',
    }


def unprobed_runtime() -> dict[str, Any]:
    return {
        'probe_status': 'NOT_REQUESTED',
        'selected_engine': None,
        'capability_status': 'UNVERIFIED',
        'observations': [],
        'vm_or_worker_boundary_verified': False,
        'resource_limits_enforced_verified': False,
        'network_egress_isolation_verified': False,
        'policy_attested': False,
        'note': 'Pass --probe-runtime to perform bounded read-only Docker/Podman CLI checks. No student code is executed.',
    }
