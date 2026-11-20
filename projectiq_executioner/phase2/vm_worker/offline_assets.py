"""Operator-owned SHA-pinned Node/Vite assets. No host-side npm or student execution.

Paths/hashes are supplied only by local operator configuration, never the
repository manifest, README, package.json or a generated agent command.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from .qemu import sha256_file

_HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')
_NODE_VERSION = re.compile(r'^v24\.[0-9]{1,3}\.[0-9]{1,3}$')


class OfflineAssetError(ValueError):
    pass


def approved_node_vite_assets(config: Mapping, identity: str) -> dict:
    """Verify pinned local assets before a VM or source transfer is started."""
    profiles = config.get('node_vite_offline')
    profile = profiles.get(identity) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise OfflineAssetError('NODE_VITE_OFFLINE_FIXTURE_ASSETS_NOT_APPROVED')
    for key in ('node_version', 'node_tarball', 'node_sha256', 'npm_cache_zip',
                'npm_cache_sha256', 'lockfile_sha256'):
        if not isinstance(profile.get(key), str) or not profile[key]:
            raise OfflineAssetError('NODE_VITE_OFFLINE_ASSET_CONFIGURATION_INVALID')
    if not _NODE_VERSION.fullmatch(profile['node_version']):
        raise OfflineAssetError('NODE_VITE_UNSUPPORTED_NODE_VERSION')
    for key in ('node_sha256', 'npm_cache_sha256', 'lockfile_sha256'):
        if not _HEX64.fullmatch(profile[key]):
            raise OfflineAssetError('NODE_VITE_ASSET_SHA256_INVALID')
    result = {}
    for path_key, sha_key, bound in (
        ('node_tarball', 'node_sha256', 65_000_000),
        ('npm_cache_zip', 'npm_cache_sha256', 170_000_000),
    ):
        path = Path(profile[path_key]).expanduser()
        if not path.is_file() or not 0 < path.stat().st_size <= bound:
            raise OfflineAssetError('NODE_VITE_OFFLINE_ASSET_MISSING_OR_OVERSIZED')
        if sha256_file(path) != profile[sha_key].lower():
            raise OfflineAssetError('NODE_VITE_OFFLINE_ASSET_SHA256_MISMATCH')
        result[path_key] = path
        result[sha_key] = profile[sha_key].lower()
    # The filename is advisory, but reject accidentally supplying a Windows
    # ZIP or a different CPU architecture as the official Linux runtime.
    expected = f"node-{profile['node_version']}-linux-x64.tar.xz"
    if result['node_tarball'].name != expected:
        raise OfflineAssetError('NODE_VITE_LINUX_NODE_TARBALL_EXPECTED')
    result['node_version'] = profile['node_version']
    result['lockfile_sha256'] = profile['lockfile_sha256'].lower()
    return result


_PIP_WHEEL_NAME = re.compile(r'^pip-25\.2-py3-none-any\.whl$')


def approved_python_web_assets(config: Mapping, identity: str) -> dict:
    """Validate operator-owned Python cache, pip bootstrap, and source hash.

    The pinned wheelhouse is reusable only with the exact approved GitHub SHA.
    """
    entries = config.get('python_web_offline')
    profile = entries.get(identity) if isinstance(entries, dict) else None
    if not isinstance(profile, dict):
        raise OfflineAssetError('PYTHON_WEB_OFFLINE_FIXTURE_ASSETS_NOT_APPROVED')
    if profile.get('python_version') != '3.12' or profile.get('framework') not in ('flask', 'fastapi'):
        raise OfflineAssetError('PYTHON_WEB_RUNTIME_PROFILE_INVALID')
    keys = ('pip_wheel', 'pip_sha256', 'wheelhouse_zip', 'wheelhouse_sha256',
            'requirements_sha256')
    if any(not isinstance(profile.get(k), str) or not profile[k] for k in keys):
        raise OfflineAssetError('PYTHON_WEB_ASSET_CONFIGURATION_INVALID')
    if any(not _HEX64.fullmatch(profile[k]) for k in keys if k.endswith('sha256')):
        raise OfflineAssetError('PYTHON_WEB_ASSET_SHA256_INVALID')
    results = {}
    for path_key, hash_key, max_bytes in (
        ('pip_wheel', 'pip_sha256', 5_000_000),
        ('wheelhouse_zip', 'wheelhouse_sha256', 120_000_000),
    ):
        file = Path(profile[path_key]).expanduser()
        if not file.is_file() or not 0 < file.stat().st_size <= max_bytes:
            raise OfflineAssetError('PYTHON_WEB_OFFLINE_ASSET_MISSING_OR_OVERSIZED')
        if sha256_file(file) != profile[hash_key].lower():
            raise OfflineAssetError('PYTHON_WEB_OFFLINE_ASSET_SHA256_MISMATCH')
        results[path_key] = file
        results[hash_key] = profile[hash_key].lower()
    if not _PIP_WHEEL_NAME.fullmatch(results['pip_wheel'].name):
        raise OfflineAssetError('PYTHON_WEB_PINNED_PIP_WHEEL_EXPECTED')
    results['requirements_sha256'] = profile['requirements_sha256'].lower()
    results['framework'] = profile['framework']
    results['python_version'] = '3.12'
    return results


def approved_node_express_assets(config: Mapping, identity: str) -> dict:
    """Validate D3 Express Node/npm assets without changing D1 profile semantics."""
    try:
        return approved_node_vite_assets(
            {'node_vite_offline': config.get('node_express_offline')}, identity)
    except OfflineAssetError as exc:
        # Same official Node tarball, npm cache and integrity model as D1, but
        # make denial reasons clearly identify the Express profile.
        raise OfflineAssetError(str(exc).replace('NODE_VITE_', 'NODE_EXPRESS_')) from exc


def approved_node_fullstack_assets(config: Mapping, identity: str) -> dict:
    """D4 two-lockfile profile: reuse D1's archive checks with distinct hashes."""
    profiles = config.get('node_fullstack_offline')
    profile = profiles.get(identity) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise OfflineAssetError('NODE_FULLSTACK_OFFLINE_FIXTURE_ASSETS_NOT_APPROVED')
    roots = profile.get('service_roots')
    if not isinstance(roots, dict) or set(roots) != {'frontend', 'backend'}:
        raise OfflineAssetError('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
    for rel in roots.values():
        if not (isinstance(rel, str) and rel and len(rel) <= 180
                and re.fullmatch(r'[A-Za-z0-9_./-]+', rel)
                and all(x not in ('', '.', '..') for x in rel.split('/'))):
            raise OfflineAssetError('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
    if roots['frontend'] == roots['backend']:
        raise OfflineAssetError('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
    for key in ('frontend_lockfile_sha256', 'backend_lockfile_sha256'):
        if not isinstance(profile.get(key), str) or not _HEX64.fullmatch(profile[key]):
            raise OfflineAssetError('NODE_FULLSTACK_LOCKFILE_SHA256_INVALID')
    try:
        result = approved_node_vite_assets(
            {'node_vite_offline': {identity: {**profile,
                'lockfile_sha256': profile['frontend_lockfile_sha256']}}}, identity)
    except OfflineAssetError as exc:
        raise OfflineAssetError(str(exc).replace('NODE_VITE_', 'NODE_FULLSTACK_')) from exc
    result['service_roots'] = dict(roots)
    result['frontend_lockfile_sha256'] = profile['frontend_lockfile_sha256'].lower()
    result['backend_lockfile_sha256'] = profile['backend_lockfile_sha256'].lower()
    return result
