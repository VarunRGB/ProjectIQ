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
