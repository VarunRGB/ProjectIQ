"""Trusted, offline agent for a disposable Ubuntu worker VM.

Intended to be copied in by the controller and executed inside the guest ONLY.
Never execute this guest agent on the ProjectIQ/Windows host.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import shutil
import stat
import tarfile
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote
import zipfile
from pathlib import Path, PurePosixPath


class GuestFailure(RuntimeError):
    pass


class NpmPreparationDiagnostic(GuestFailure):
    """Trusted preparation-only npm failure with optional sanitized context."""

    def __init__(self, code: str, safe_hint: str):
        super().__init__(code)
        self.safe_hint = safe_hint


DENIED_SQL = re.compile(
    r'\b(?:CREATE\s+(?:USER|FUNCTION|PROCEDURE|TRIGGER|EVENT|DATABASE|SERVER)|'
    r'DROP\s+DATABASE|ALTER\s+USER|GRANT|REVOKE|INTO\s+OUTFILE|INTO\s+DUMPFILE|'
    r'LOAD\s+DATA|INSTALL\s+PLUGIN|SET\s+GLOBAL|USE\s+|DELIMITER\b|'
    r'SOURCE\b|SYSTEM\b|EXECUTE\b|PREPARE\b)\b|\\[!.]|\b(?:SLEEP|BENCHMARK)\s*\(',
    re.IGNORECASE,
)


def safe_extract(archive: Path, into: Path, *, max_total: int = 120_000_000) -> Path:
    """Extract bounded GitHub ZIP with no symlinks, zip-slip or executable host hooks."""
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        if len(entries) > 12000:
            raise GuestFailure('Too many source files')
        expanded = 0
        root = None
        seen = set()
        targets = []
        for entry in entries:
            path = entry.filename
            if (not path or '\\' in path or '\x00' in path or path.startswith('/') or '//'
                    in path or len(path) > 350):
                raise GuestFailure('Unsafe source archive path')
            parts = path.rstrip('/').split('/')
            if len(parts) < 2 or any(p in {'.', '..', ''} or ':' in p for p in parts):
                if entry.is_dir() and len(parts) == 1:
                    continue
                raise GuestFailure('Unsafe source archive structure')
            if root is None:
                root = parts[0]
            if parts[0] != root:
                raise GuestFailure('Multiple source roots')
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise GuestFailure('Symlinks and special files are not allowed')
            if entry.is_dir():
                continue
            if entry.file_size > 20_000_000:
                raise GuestFailure('Oversized source member')
            expanded += entry.file_size
            if expanded > max_total:
                raise GuestFailure('Expanded snapshot exceeds limit')
            sub = '/'.join(parts[1:])
            if sub.casefold() in seen:
                raise GuestFailure('Duplicate source paths')
            seen.add(sub.casefold())
            targets.append((entry, sub))
        if not root or not targets:
            raise GuestFailure('Empty or invalid repository')
        into.mkdir(parents=True, exist_ok=False)
        for entry, sub in targets:
            target = into.joinpath(*PurePosixPath(sub).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, target.open('xb') as out:
                copied = 0
                while data := src.read(65536):
                    copied += len(data)
                    if copied > entry.file_size:
                        raise GuestFailure('Archive size mismatch')
                    out.write(data)
                if copied != entry.file_size:
                    raise GuestFailure('Truncated archive')
        return into


def _trusted_process(args: list[str], *, input: bytes | None = None, timeout: int = 15,
                     env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False, env=env, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuestFailure('Trusted guest tool missing or timed out') from exc


def _php_pdo_rebind(checkout: Path) -> None:
    """Generalized, conservative PDO 4-variable adapter; VM copy ONLY."""
    candidate = checkout / 'db' / 'db.php'
    if not candidate.is_file() or candidate.stat().st_size > 30_000:
        raise GuestFailure('PHP PDO connection adapter not recognized')
    content = candidate.read_text(encoding='utf-8-sig')
    if not re.search(r'new\s+PDO\s*\(', content):
        raise GuestFailure('No recognizable PDO connection')
    mapping = {
        'host': 'PROJECTIQ_DB_HOST', 'dbname': 'PROJECTIQ_DB_NAME',
        'user': 'PROJECTIQ_DB_USER', 'pass': 'PROJECTIQ_DB_PASSWORD',
    }
    for var, env in mapping.items():
        pattern = re.compile(r'(?m)^(\s*\$' + var + r'\s*=\s*)([^;\r\n]{1,250})(;\s*)$')
        matches = list(pattern.finditer(content))
        if len(matches) != 1:
            raise GuestFailure('PDO connection field not uniquely identifiable')
        content = pattern.sub(lambda m: m.group(1) + "getenv('" + env + "')" + m.group(3), content)
    candidate.write_text(content, encoding='utf-8')


def _check_sql(path: Path) -> bytes:
    if not path.is_file() or path.stat().st_size > 5_000_000:
        raise GuestFailure('Expected one bounded SQL dump')
    contents = path.read_bytes()
    # MySQL dumps may be UTF-16 with a byte-order mark (for example,
    # exports produced by a Windows client). A byte-level NUL check rejects
    # valid UTF-16 text, so decode *strictly* before checking text content.
    # Require the BOM for UTF-16; do not guess encodings for binary inputs.
    encoding = 'utf-16' if contents.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        decoded = contents.decode(encoding)
    except UnicodeError as exc:
        raise GuestFailure('SQL dump text encoding is not supported') from exc
    if '\x00' in decoded:
        raise GuestFailure('SQL binary content is not supported')
    # MySQL mysqldump version comments are accepted but the regex also scans them.
    if DENIED_SQL.search(decoded):
        raise GuestFailure('SQL dump uses operations outside the pilot allowlist')
    return decoded.encode('utf-8')


def _wait_mysql_runtime_ready(*, seconds: int = 45) -> None:
    """Wait for the sealed guest's MySQL socket and local admin access.

    SSH/cloud-init can become ready before mysqld on a cold single-vCPU VM.
    The runtime account is permitted to invoke ONLY /usr/bin/mysql via sudo;
    do not start services or expand its sudo permissions here.
    """
    if seconds <= 0:
        raise GuestFailure('Invalid MySQL readiness window')
    deadline = time.monotonic() + seconds
    while True:
        result = _trusted_process(['sudo', '-n', '/usr/bin/mysql', '--batch',
                                   '--skip-column-names', '-e', 'SELECT 1;'], timeout=10)
        if result.returncode == 0 and result.stdout.strip() == b'1':
            return
        # Distinguish missing runtime privilege from a service that is still
        # starting, without returning shell output or credentials to the host.
        stderr = result.stderr.decode('utf-8', errors='replace').lower()
        if ('not in the sudoers' in stderr or 'not allowed to execute' in stderr
                or 'a password is required' in stderr):
            raise GuestFailure('MySQL guest admin permission unavailable')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GuestFailure('MySQL service not ready before bootstrap')
        time.sleep(min(2, remaining))


def _provision_mysql(checkout: Path, schema_relpath: str) -> tuple[dict, str]:
    # This privileged step is allowed only INSIDE the disposable guest image.
    # Database credentials are generated per VM and never returned to the host.
    sql_path = checkout.joinpath(*PurePosixPath(schema_relpath).parts)
    sql_data = _check_sql(sql_path)
    # Readiness was verified in the trusted template baker, but each fresh
    # disposable boot must wait for MySQL independently.
    _wait_mysql_runtime_ready()
    passwd = secrets.token_urlsafe(27)
    dbname = 'projectiq_run'
    username = 'projectiq_app'
    # Report which trusted administrative step fails, never its SQL text:
    # CREATE USER contains a per-VM password that must stay guest-private.
    admin_steps = (
        ('database', 'CREATE DATABASE `projectiq_run` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;'),
        ('local_user', f"CREATE USER '{username}'@'localhost' IDENTIFIED BY '{passwd}';"),
        ('loopback_user', f"CREATE USER '{username}'@'127.0.0.1' IDENTIFIED BY '{passwd}';"),
        ('local_grant', f"GRANT ALL ON `{dbname}`.* TO '{username}'@'localhost';"),
        ('loopback_grant', f"GRANT ALL ON `{dbname}`.* TO '{username}'@'127.0.0.1';"),
    )
    for stage, statement in admin_steps:
        resp = _trusted_process(['sudo', '-n', '/usr/bin/mysql', '--batch',
                                 '--skip-column-names', '-e', statement])
        if resp.returncode:
            raise GuestFailure(f'MySQL bootstrap failed at {stage}')
    env = dict(os.environ)
    env.update({'PROJECTIQ_DB_HOST': '127.0.0.1', 'PROJECTIQ_DB_NAME': dbname,
                'PROJECTIQ_DB_USER': username, 'PROJECTIQ_DB_PASSWORD': passwd})
    with tempfile.NamedTemporaryFile(mode='w', prefix='projectiq_mysql_', delete=False, dir='/tmp') as cfg:
        cfg.write('[client]\nuser=' + username + '\npassword=' + passwd + '\nhost=127.0.0.1\n')
        config_path = Path(cfg.name)
    try:
        config_path.chmod(0o600)
        imported = _trusted_process(['/usr/bin/mysql', f'--defaults-extra-file={config_path}',
                                     '--binary-mode', '--batch', '--database', dbname],
                                    input=sql_data, timeout=30)
        if imported.returncode:
            raise GuestFailure('Schema import failed (SQL compatibility or permissions)')
        tables = _trusted_process(['/usr/bin/mysql', f'--defaults-extra-file={config_path}',
                                   '--batch', '--skip-column-names', '-D', dbname, '-e', 'SHOW TABLES;'])
        count = len([x for x in tables.stdout.decode('utf-8', 'replace').splitlines() if x.strip()])
        if tables.returncode or count < 1:
            raise GuestFailure('SQL import produced no accessible tables')
    finally:
        config_path.unlink(missing_ok=True)
    _php_pdo_rebind(checkout)
    # Verify the *actual PHP PDO connection* using test credentials, not merely
    # that MySQL accepts a standalone CLI query. This runs only within the VM.
    check_env = dict(env)
    check_env['PROJECTIQ_PDO_CONNECTOR'] = str(checkout / 'db' / 'db.php')
    php_check = _trusted_process([
        '/usr/bin/php', '-r',
        'require getenv("PROJECTIQ_PDO_CONNECTOR"); '
        'if (!isset($pdo) || (int)$pdo->query("SELECT 1")->fetchColumn() !== 1) exit(3);',
    ], env=check_env, timeout=15)
    if php_check.returncode:
        raise GuestFailure('PHP PDO failed to connect to disposable MySQL')
    return env, f'{count} SQL table(s) initialized; PHP PDO connectivity verified'



def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _node_tar_extract(archive: Path, version: str, base: Path) -> tuple[Path, Path]:
    """Unpack only an operator-pinned official linux-x64 Node.js tarball."""
    expected_root = f'node-{version}-linux-x64'
    if not (archive.is_file() and 0 < archive.stat().st_size <= 65_000_000):
        raise GuestFailure('NODE_VITE_NODE_ARCHIVE_INVALID')
    try:
        with tarfile.open(archive, 'r:xz') as bundle:
            members = bundle.getmembers()
            if (not 0 < len(members) <= 20000
                    or sum(x.size for x in members) > 350_000_000
                    or any((x.name != expected_root and not x.name.startswith(expected_root + '/'))
                           or x.isdev() or x.isfifo() for x in members)):
                raise GuestFailure('NODE_VITE_NODE_ARCHIVE_INVALID')
            # Python 3.12+ 'data' filter rejects traversal and unsafe links.
            bundle.extractall(base, filter='data')
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise GuestFailure('NODE_VITE_RUNTIME_EXTRACTION_FAILED') from exc
    node_dir = base / expected_root
    node = node_dir / 'bin' / 'node'
    npm = node_dir / 'bin' / 'npm'
    if not node.is_file() or not npm.is_file() or node.is_symlink():
        raise GuestFailure('NODE_VITE_RUNTIME_EXTRACTION_FAILED')
    return node, npm


def _check_vite_manifest(root: Path, expected_lock_sha: str) -> None:
    pkg = root / 'package.json'
    lock = root / 'package-lock.json'
    if (not pkg.is_file() or pkg.stat().st_size > 150_000 or
            not lock.is_file() or lock.stat().st_size > 4_000_000
            or (root / '.npmrc').exists() or (root / 'npm-shrinkwrap.json').exists()):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    if _sha256(lock) != expected_lock_sha:
        raise GuestFailure('NODE_VITE_LOCKFILE_HASH_MISMATCH')
    try:
        package = json.loads(pkg.read_text('utf-8'))
        locked = json.loads(lock.read_text('utf-8'))
    except (UnicodeError, ValueError, OSError) as exc:
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED') from exc
    if (not isinstance(package, dict) or not isinstance(locked, dict)
            or locked.get('lockfileVersion') not in (2, 3)
            or not isinstance(locked.get('packages'), dict)
            or len(locked['packages']) > 12000 or 'workspaces' in package):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    direct = package.get('dependencies') or {}
    dev = package.get('devDependencies') or {}
    if not isinstance(direct, dict) or not isinstance(dev, dict):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    deps = {**direct, **dev}
    if 'vite' not in deps or any(x in deps for x in ('next', 'nuxt')):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    # No git:, file:, link:, HTTP or alternate registries during the pilot.
    # Offline cache still contains third-party packages and the build executes
    # reviewed code, exclusively inside the disposable guest.
    for value in locked['packages'].values():
        if not isinstance(value, dict) or value.get('link') is True:
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        version = value.get('version')
        if isinstance(version, str) and version.startswith(('file:', 'link:', 'git:', 'git+', 'http:', 'https:')):
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        resolved = value.get('resolved')
        if resolved is not None and (not isinstance(resolved, str) or
                                     not resolved.startswith('https://registry.npmjs.org/')):
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        if resolved and (not isinstance(value.get('integrity'), str)
                         or not value['integrity'].startswith(('sha512-', 'sha384-', 'sha256-'))):
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')



def _check_express_manifest(root: Path, expected_lock_sha: str) -> str:
    """Verify supported Express/npm-lock project and choose a fixed JS entry.

    No package.json scripts are executed. Other entrypoint layouts are a future
    adapter capability, not permission to run a user-supplied command.
    """
    pkg, lock = root / 'package.json', root / 'package-lock.json'
    if (not pkg.is_file() or pkg.stat().st_size > 150_000
            or not lock.is_file() or lock.stat().st_size > 4_000_000
            or (root / '.npmrc').exists() or (root / 'npm-shrinkwrap.json').exists()):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    if not isinstance(expected_lock_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_lock_sha):
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    if _sha256(lock) != expected_lock_sha:
        raise GuestFailure('NODE_EXPRESS_LOCKFILE_HASH_MISMATCH')
    try:
        package = json.loads(pkg.read_text('utf-8'))
        locked = json.loads(lock.read_text('utf-8'))
    except (UnicodeError, ValueError, OSError) as exc:
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED') from exc
    if (not isinstance(package, dict) or not isinstance(locked, dict)
            or locked.get('lockfileVersion') not in (2, 3)
            or not isinstance(locked.get('packages'), dict)
            or not 0 < len(locked['packages']) <= 12000
            or 'workspaces' in package
            or package.get('type', 'commonjs') not in ('module', 'commonjs')):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    direct = package.get('dependencies') or {}
    dev = package.get('devDependencies') or {}
    if (not isinstance(direct, dict) or not isinstance(dev, dict)
            or not isinstance(direct.get('express'), str)
            or any(x in {**direct, **dev} for x in ('vite', 'next', 'nuxt'))):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    # Accept only ordinary registry semver specifications. Even with an
    # apparently valid lockfile, do not let npm resolve URL/git/file/alias specs.
    all_direct = {**direct, **dev}
    version_spec = re.compile(r'^[~^]?[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$')
    if (not all(isinstance(k, str) and isinstance(v, str)
                and version_spec.fullmatch(v) for k, v in all_direct.items())
            or not isinstance(locked['packages'].get(''), dict)
            or locked['packages'][''].get('dependencies', {}) != direct
            or locked['packages'][''].get('devDependencies', {}) != dev):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    for value in locked['packages'].values():
        if not isinstance(value, dict) or value.get('link') is True:
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        version, resolved = value.get('version'), value.get('resolved')
        if isinstance(version, str) and version.startswith(('file:', 'link:', 'git:', 'git+', 'http:', 'https:')):
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        if resolved is not None and (not isinstance(resolved, str) or
                                     not resolved.startswith('https://registry.npmjs.org/')):
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        if resolved and (not isinstance(value.get('integrity'), str)
                         or not value['integrity'].startswith(('sha512-', 'sha384-', 'sha256-'))):
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    candidates = ('server.js', 'app.js', 'index.js')
    explicit = package.get('main')
    if explicit is not None:
        if not isinstance(explicit, str) or explicit not in candidates:
            raise GuestFailure('NODE_EXPRESS_ENTRYPOINT_UNSUPPORTED')
        chosen = explicit
    else:
        present = [name for name in candidates if (root / name).is_file()]
        if len(present) != 1:
            raise GuestFailure('NODE_EXPRESS_ENTRYPOINT_UNSUPPORTED')
        chosen = present[0]
    entry = root / chosen
    if (not entry.is_file() or entry.is_symlink()
            or not 0 < entry.stat().st_size <= 300_000):
        raise GuestFailure('NODE_EXPRESS_ENTRYPOINT_UNSUPPORTED')
    return chosen


def _node_express_root(checkout: Path, req: dict) -> Path:
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str)
            and re.fullmatch(r'[A-Za-z0-9_./-]{1,180}', root_rel)
            and all(s not in ('.', '..', '') for s in root_rel.split('/'))):
        raise GuestFailure('NODE_EXPRESS_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir() or (checkout / '.npmrc').exists():
        raise GuestFailure('NODE_EXPRESS_INVALID_PROJECT_ROOT')
    return root


def _prepare_express(checkout: Path, base: Path, req: dict, *, port: int = 8765) -> tuple[list[str], dict, str]:
    if req.get('adapter') != 'node_express_offline_v1':
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    root = _node_express_root(checkout, req)
    keys = ('node_sha256', 'npm_cache_sha256', 'lockfile_sha256')
    if any(not isinstance(req.get(k), str) or not re.fullmatch(r'[0-9a-f]{64}', req[k]) for k in keys):
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    entry = _check_express_manifest(root, req['lockfile_sha256'])
    node_archive, cache_archive = base / 'node_runtime.tar.xz', base / 'npm_cache.zip'
    if not node_archive.is_file() or not cache_archive.is_file():
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    if _sha256(node_archive) != req['node_sha256']:
        raise GuestFailure('NODE_EXPRESS_NODE_HASH_MISMATCH')
    if _sha256(cache_archive) != req['npm_cache_sha256']:
        raise GuestFailure('NODE_EXPRESS_CACHE_HASH_MISMATCH')
    runtime_dir = base / 'node_runtime'
    runtime_dir.mkdir(mode=0o700)
    node, npm = _node_tar_extract(node_archive, version, runtime_dir)
    node_archive.unlink(missing_ok=True)
    check = _trusted_process([str(node), '--version'], timeout=15)
    if check.returncode or check.stdout.decode('utf-8', 'replace').strip() != version:
        raise GuestFailure('NODE_EXPRESS_RUNTIME_VERSION_MISMATCH')
    if not 0 < cache_archive.stat().st_size <= 170_000_000:
        raise GuestFailure('NODE_EXPRESS_CACHE_HASH_MISMATCH')
    try:
        npm_cache = safe_extract(cache_archive, base / 'npm_cache', max_total=250_000_000)
    except (GuestFailure, zipfile.BadZipFile) as exc:
        raise GuestFailure('NODE_EXPRESS_CACHE_HASH_MISMATCH') from exc
    cache_archive.unlink(missing_ok=True)
    # Do not allow inherited npm / Node injection knobs or host credentials.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_')
           and k.upper() not in ('NODE_OPTIONS', 'NODE_PATH')}
    env['PATH'] = str(node.parent) + ':/usr/local/bin:/usr/bin:/bin'
    env.update({'HOME': str(base), **_isolated_npm_config_paths(base),
                'NPM_CONFIG_CACHE': str(npm_cache), 'NPM_CONFIG_OFFLINE': 'true',
                'NPM_CONFIG_IGNORE_SCRIPTS': 'true', 'NPM_CONFIG_AUDIT': 'false',
                'NPM_CONFIG_FUND': 'false', 'NPM_CONFIG_UPDATE_NOTIFIER': 'false',
                'CI': 'true', 'PORT': str(port), 'HOST': '127.0.0.1',
                'NODE_ENV': 'production'})
    installed = _trusted_process([str(npm), 'ci', '--offline', '--ignore-scripts',
                                  '--no-audit', '--no-fund', '--no-progress',
                                  '--cache', str(npm_cache)], timeout=185, env=env, cwd=root)
    if installed.returncode:
        raise GuestFailure('NODE_EXPRESS_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    if not (root / 'node_modules' / 'express' / 'package.json').is_file():
        raise GuestFailure('NODE_EXPRESS_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    return [str(node), str(root / entry)], env, f'Express offline npm ci completed with {version}; fixed entrypoint {entry}'


def _probe_express(path: str, *, port: int = 8765) -> tuple[int, str]:
    if path not in ('/health', '/api/health', '/'):
        raise GuestFailure('NODE_EXPRESS_HTTP_PROBE_FAILED')
    for _ in range(30):
        try:
            request = urllib.request.Request(
                f'http://127.0.0.1:{port}{path}',
                headers={'User-Agent': 'ProjectIQ-Express-Probe'})
            with urllib.request.urlopen(request, timeout=2) as response:
                data = response.read(65_536)
                if response.status != 200 or not data:
                    continue
                # D3 API readiness requires JSON, but no invented business
                # assertions or test-generated expected responses.
                parsed = json.loads(data)
                if isinstance(parsed, (dict, list)):
                    return 200, 'HTTP_RESPONSE_OK'
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    raise GuestFailure('NODE_EXPRESS_HTTP_PROBE_FAILED')


def _isolated_npm_config_paths(base: Path) -> dict[str, str]:
    """Give npm distinct, empty user/global config files outside the checkout.

    npm 11 rejects loading the same path for both scopes, even when that path
    is /dev/null. Both files are guest-local and cannot contain host secrets.
    """
    config_dir = base / '.projectiq-npm-config'
    config_dir.mkdir(mode=0o700)
    result = {}
    for setting, filename in (
        ('NPM_CONFIG_USERCONFIG', 'user.npmrc'),
        ('NPM_CONFIG_GLOBALCONFIG', 'global.npmrc'),
    ):
        path = config_dir / filename
        with path.open('xb'):
            pass
        path.chmod(0o600)
        result[setting] = str(path)
    return result


def _prepare_vite(checkout: Path, base: Path, req: dict) -> tuple[Path, str]:
    """Offline npm ci and Vite build; NEVER execute student commands on host."""
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str) and
            re.fullmatch('[A-Za-z0-9_./-]{1,180}', root_rel)
            and not any(x in ('.', '..', '') for x in root_rel.split('/'))):
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir() or (checkout / '.npmrc').exists():
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    digests = ('node_sha256', 'npm_cache_sha256', 'lockfile_sha256')
    if any(not isinstance(req.get(k), str) or not re.fullmatch('[0-9a-f]{64}', req[k]) for k in digests):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    node_archive = base / 'node_runtime.tar.xz'
    cache_archive = base / 'npm_cache.zip'
    if not node_archive.is_file() or not cache_archive.is_file():
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    if _sha256(node_archive) != req['node_sha256']:
        raise GuestFailure('NODE_VITE_NODE_HASH_MISMATCH')
    if _sha256(cache_archive) != req['npm_cache_sha256']:
        raise GuestFailure('NODE_VITE_CACHE_HASH_MISMATCH')
    _check_vite_manifest(root, req['lockfile_sha256'])
    runtime_dir = base / 'node_runtime'
    runtime_dir.mkdir(mode=0o700)
    node, npm = _node_tar_extract(node_archive, version, runtime_dir)
    node_archive.unlink(missing_ok=True)
    node_version = _trusted_process([str(node), '--version'], timeout=15)
    if node_version.returncode or node_version.stdout.decode('utf-8', 'replace').strip() != version:
        raise GuestFailure('NODE_VITE_RUNTIME_VERSION_MISMATCH')
    if not 0 < cache_archive.stat().st_size <= 170_000_000:
        raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
    try:
        npm_cache = safe_extract(cache_archive, base / 'npm_cache', max_total=250_000_000)
    except (GuestFailure, zipfile.BadZipFile) as exc:
        raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID') from exc
    cache_archive.unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_')
           and k.upper() not in ('NODE_OPTIONS', 'NODE_PATH')}
    env['PATH'] = str(node.parent) + ':/usr/local/bin:/usr/bin:/bin'
    env.update({'HOME': str(base), **_isolated_npm_config_paths(base), 'NPM_CONFIG_CACHE': str(npm_cache),
                'NPM_CONFIG_OFFLINE': 'true', 'NPM_CONFIG_IGNORE_SCRIPTS': 'true',
                'NPM_CONFIG_AUDIT': 'false', 'NPM_CONFIG_FUND': 'false',
                'NPM_CONFIG_UPDATE_NOTIFIER': 'false', 'CI': 'true'})
    installed = _trusted_process([str(npm), 'ci', '--offline', '--ignore-scripts',
                                  '--no-audit', '--no-fund', '--no-progress',
                                  '--cache', str(npm_cache)], timeout=185, env=env, cwd=root)
    if installed.returncode:
        raise GuestFailure('NODE_VITE_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    vite = root / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    if not vite.is_file() or not vite.resolve().is_relative_to(root.resolve()):
        raise GuestFailure('NODE_VITE_BUILD_FAILED')
    dist = root / 'dist'
    if dist.is_dir():
        shutil.rmtree(dist)
    built = _trusted_process([str(node), str(vite), 'build', '--outDir', 'dist'],
                             timeout=125, env=env, cwd=root)
    if built.returncode:
        raise GuestFailure('NODE_VITE_BUILD_FAILED')
    index = dist / 'index.html'
    if not index.is_file() or index.stat().st_size > 2_000_000 or b'<html' not in index.read_bytes().lower():
        raise GuestFailure('NODE_VITE_BUILD_OUTPUT_MISSING')
    return dist, f'Vite offline npm ci + build completed with {version}; dist/index.html generated'



def _classify_npm_cache_failure(stderr: bytes) -> str:
    """Return only a fixed, non-sensitive diagnostic for failed npm ci.

    npm output may contain package URLs, server messages, token-bearing URLs,
    or arbitrary text from an approved repository. It MUST NOT be sent to the
    host or persisted in reports. Inspect a bounded tail and return fixed codes.
    """
    sample = (stderr or b'')[-24_000:].decode('utf-8', errors='replace').lower()
    categories = (
        ('NODE_VITE_NPM_TLS_CERTIFICATE',
         ('self_signed_cert_in_chain', 'unable_to_verify_leaf_signature',
          'unable_to_get_issuer_cert', 'cert_has_expired', 'err_tls_',
          'certificate verify failed', 'depth_zero_self_signed_cert')),
        ('NODE_VITE_NPM_NETWORK_DNS', ('eai_again', 'enotfound', 'getaddrinfo',
                                      'name or service not known', 'temporary failure in name resolution')),
        ('NODE_VITE_NPM_NETWORK_TIMEOUT', ('etimedout', 'esockettimedout', 'fetch timeout')),
        ('NODE_VITE_NPM_NETWORK_CONNECTIVITY',
         ('econnreset', 'econnrefused', 'enetunreach', 'ehostunreach', 'econnaborted')),
        ('NODE_VITE_NPM_DISK_SPACE', ('enospc', 'no space left on device')),
        ('NODE_VITE_NPM_FILE_PERMISSION', ('eacces', 'eperm', 'permission denied')),
        ('NODE_VITE_NPM_INTEGRITY', ('eintegrity', 'integrity checksum failed')),
        ('NODE_VITE_NPM_LOCKFILE_INCONSISTENT',
         ('npm ci can only install packages when your package.json',
          'missing from lock file', 'invalid: ', 'npm error code eusage',
          'npm err! code eusage')),
        ('NODE_VITE_NPM_DEPENDENCY_CONFLICT', ('eresolve', 'unable to resolve dependency tree')),
        ('NODE_VITE_NPM_REGISTRY_DENIED', ('npm error code e403', 'npm err! code e403',
                                          '403 forbidden')),
        ('NODE_VITE_NPM_REGISTRY_NOT_FOUND', ('npm error code e404', 'npm err! code e404',
                                             '404 not found')),
        ('NODE_VITE_NPM_PLATFORM_UNSUPPORTED', ('ebadplatform', 'ebadengine')),
        ('NODE_VITE_NPM_AUTH_REQUIRED', ('npm error code e401', 'npm err! code e401')),
        ('NODE_VITE_NPM_MISSING_FILE', ('npm error code enoent', 'npm err! code enoent',
                                        'cannot find module', 'err_module_not_found')),
        ('NODE_VITE_NPM_INVALID_PACKAGE', ('ejsonparse', 'invalid version:',
                                           'invalid tag name', 'invalid package name')),
        ('NODE_VITE_NPM_UNSUPPORTED_PROTOCOL', ('eunsupportedprotocol', 'unsupported url type')),
    )
    for code, indicators in categories:
        if any(indicator in sample for indicator in indicators):
            return code
    return 'NODE_VITE_NPM_FAILURE_UNCLASSIFIED'


def _npm_operator_safe_hint(proc: subprocess.CompletedProcess, cache: Path) -> str:
    """Opt-in, bounded npm process diagnostics for an approved fetch-only VM.

    The old filter required an explicit `npm error` prefix and discarded shell,
    loader, signal and Node.js runtime failures. Return fixed numeric metadata
    plus at most three aggressively scrubbed *error-like* line fragments.
    Never return complete logs, URLs, paths, credentials or source snippets.
    """
    logdir = cache / '_logs'
    channels = [('stderr', bytes(proc.stderr or b'')[-12_000:]),
                ('stdout', bytes(proc.stdout or b'')[-6_000:])]
    logs = 0
    if logdir.is_dir() and not logdir.is_symlink():
        try:
            for log in sorted(logdir.glob('*-debug-0.log'), reverse=True)[:2]:
                if log.is_symlink() or not log.is_file():
                    continue
                with log.open('rb') as f:
                    f.seek(0, os.SEEK_END)
                    f.seek(max(0, f.tell() - 24_000))
                    channels.append(('log', f.read(24_000)))
                    logs += 1
        except OSError:
            pass
    try:
        exit_status = int(proc.returncode)
    except (ValueError, TypeError):
        exit_status = 999
    # Numeric metadata is safe even if npm writes nothing recognizable.
    facts = (f'npm exit {max(-999, min(exit_status, 999))} '
             f'stdout bytes {len(proc.stdout or b"")} '
             f'stderr bytes {len(proc.stderr or b"")} debug logs {logs}')
    seen = set()
    highlights = []
    for channel, data in channels:
        for raw in data.decode('utf-8', 'replace').splitlines()[-80:]:
            # Only infrastructure/process error phrases, not arbitrary source
            # output, are candidates for the opt-in diagnostic.
            if not re.search(r'(?i)\b(?:npm|error|err!|fatal|fail(?:ed|ure)?|killed|'
                             r'exception|exit|heap|module|libc|denied|signal|segfault)\b', raw):
                continue
            if re.search(r'(?i)token|password|secret|credential|authorization|bearer|'
                         r'api[_-]?key|_auth|npmrc|private', raw):
                continue
            if 'complete log of this run' in raw.lower():
                continue
            phrase = re.sub(r'(?i)https?://\S+', '[URL]', raw)
            phrase = re.sub(r'(?i)(?:[a-z]:[\\/]|/(?:home|tmp|var|root|etc|mnt|usr)/)\S*',
                            '[PATH]', phrase)
            phrase = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', phrase)
            phrase = re.sub(r'\x22[^\x22]{1,180}\x22|\x27[^\x27]{1,180}\x27|\x60[^\x60]{1,180}\x60',
                            '[QUOTED]', phrase)
            phrase = re.sub(r'[^A-Za-z0-9 _.,!?:;()\[\]\-|]', ' ', phrase)
            phrase = ' '.join(phrase.split())[:115]
            if phrase and phrase not in seen:
                highlights.append((channel, phrase))
                seen.add(phrase)
    # Preserve last error-like lines where npm normally places the summary.
    selected = highlights[-3:]
    parts = [facts] + [f'{channel} {line}' for channel, line in selected]
    return ' | '.join(parts)[:640]


def _npm_failure_with_debug_context(proc: subprocess.CompletedProcess, cache: Path) -> str:
    """Classify npm's three possible output channels without exporting log text.

    npm can write meaningful diagnostics only to its local _logs/*debug*.log.
    Read at most two bounded log tails, which remain entirely inside the VM.
    All return values are fixed literals; never include package names, URLs,
    process output, or credentials in the host-visible result.
    """
    output = [bytes(proc.stderr or b'')[-24_000:], bytes(proc.stdout or b'')[-8_000:]]
    logdir = cache / '_logs'
    if logdir.is_dir() and not logdir.is_symlink():
        try:
            candidates = sorted(logdir.glob('*-debug-0.log'), reverse=True)[:2]
            for path in candidates:
                if path.is_symlink() or not path.is_file():
                    continue
                with path.open('rb') as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    stream.seek(max(0, size - 32_000))
                    output.append(stream.read(32_000))
        except OSError:
            # Logging is a diagnostic only; it must not cause the fetch to pass.
            pass
    for sample in output:
        category = _classify_npm_cache_failure(sample)
        if category != 'NODE_VITE_NPM_FAILURE_UNCLASSIFIED':
            return category
    if proc.returncode in (-9, 137):
        return 'NODE_VITE_NPM_PROCESS_TERMINATED'
    # The operator explicitly allowed network ONLY in this preparation VM.
    # A fixed public registry reachability probe separates basic network
    # failure from npm/package issues without leaking any untrusted output.
    try:
        with urllib.request.urlopen('https://registry.npmjs.org/-/ping', timeout=10) as response:
            if 200 <= response.status < 400:
                return 'NODE_VITE_NPM_UNCLASSIFIED_REGISTRY_REACHABLE'
            return 'NODE_VITE_NPM_REGISTRY_PROBE_HTTP_ERROR'
    except urllib.error.HTTPError:
        return 'NODE_VITE_NPM_REGISTRY_PROBE_HTTP_ERROR'
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            return 'NODE_VITE_NPM_NETWORK_DNS'
        if isinstance(exc.reason, (ssl.SSLError, ssl.CertificateError)):
            return 'NODE_VITE_NPM_TLS_CERTIFICATE'
        return 'NODE_VITE_NPM_REGISTRY_PROBE_CONNECTION_FAILED'
    except (OSError, ValueError, TimeoutError):
        return 'NODE_VITE_NPM_REGISTRY_PROBE_CONNECTION_FAILED'


def prepare_npm_cache(request_path: Path, *, base_dir: Path = Path('/home/projectiq')) -> dict:
    """Separate, explicitly authorized disposable VM: downloads npm tarballs.

    No student build or lifecycle scripts run here. Unlike runtime VMs this
    trusted fetch VM has outbound network, but still no host filesystem mounts,
    no real credentials, no root sudo, and only a reviewed pinned fixture.
    """
    req = json.loads(request_path.read_text('utf-8'))
    if req.get('operation') != 'prepare_npm_cache' or req.get('adapter') not in ('node_vite_offline_v1', 'node_express_offline_v1'):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    snapshot = base_dir / 'snapshot.zip'
    if not snapshot.is_file() or snapshot.stat().st_size > 30_000_000:
        raise GuestFailure('Pinned snapshot missing or oversized')
    checkout = safe_extract(snapshot, base_dir / 'checkout')
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str) and
            re.fullmatch('[A-Za-z0-9_./-]{1,180}', root_rel) and
            not any(x in ('.', '..', '') for x in root_rel.split('/'))):
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir() or (checkout / '.npmrc').exists():
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    node_sha = req.get('node_sha256')
    lock_sha = req.get('lockfile_sha256')
    if any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in (node_sha, lock_sha)):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    node_archive = base_dir / 'node_runtime.tar.xz'
    if not node_archive.is_file() or _sha256(node_archive) != node_sha:
        raise GuestFailure('NODE_VITE_NODE_HASH_MISMATCH')
    if req['adapter'] == 'node_express_offline_v1':
        _check_express_manifest(root, lock_sha)
    else:
        _check_vite_manifest(root, lock_sha)
    runtime = base_dir / 'node_runtime'
    runtime.mkdir(mode=0o700)
    node, npm = _node_tar_extract(node_archive, version, runtime)
    node_archive.unlink(missing_ok=True)
    ver = _trusted_process([str(node), '--version'], timeout=15)
    if ver.returncode or ver.stdout.decode('utf-8', errors='replace').strip() != version:
        raise GuestFailure('NODE_VITE_RUNTIME_VERSION_MISMATCH')
    cache = base_dir / 'npm-cache'
    cache.mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_')
           and k.upper() not in ('NODE_OPTIONS', 'NODE_PATH')}
    env['PATH'] = str(node.parent) + ':/usr/local/bin:/usr/bin:/bin'
    env.update({'HOME': str(base_dir), **_isolated_npm_config_paths(base_dir), 'NPM_CONFIG_CACHE': str(cache),
                'NPM_CONFIG_IGNORE_SCRIPTS': 'true', 'NPM_CONFIG_AUDIT': 'false',
                'NPM_CONFIG_FUND': 'false', 'NPM_CONFIG_UPDATE_NOTIFIER': 'false',
                'NPM_CONFIG_REGISTRY': 'https://registry.npmjs.org/', 'CI': 'true'})
    proc = _trusted_process([str(npm), 'ci', '--ignore-scripts', '--no-audit',
                             '--no-fund', '--no-progress', '--cache', str(cache)],
                            timeout=210, env=env, cwd=root)
    if proc.returncode:
        code = _npm_failure_with_debug_context(proc, cache)
        if req['adapter'] == 'node_express_offline_v1':
            code = code.replace('NODE_VITE_', 'NODE_EXPRESS_')
        # Only the specifically authorized preparation VM can return a short,
        # filtered, operator-only diagnostic. The normal report remains a code.
        if req.get('include_npm_diagnostic') is True:
            # Verify npm's own CLI and HTTPS registry access, not just the
            # Python urllib probe. These fixed commands cannot run project
            # install scripts and are limited to the authorized fetch VM.
            try:
                cli = _trusted_process([str(npm), '--version'], timeout=15, env=env, cwd=root)
                cli_status = 'PASS' if cli.returncode == 0 else 'FAIL'
            except GuestFailure:
                cli_status = 'TIMEOUT_OR_MISSING'
            ping_status = 'SKIPPED'
            if cli_status == 'PASS':
                try:
                    ping = _trusted_process([str(npm), 'ping', '--no-progress',
                                             '--fetch-retries=0', '--fetch-timeout=15000'],
                                            timeout=30, env=env, cwd=root)
                    ping_status = 'PASS' if ping.returncode == 0 else 'FAIL'
                except GuestFailure:
                    ping_status = 'TIMEOUT_OR_MISSING'
            meta = (' | npm cli ' + cli_status + ' | npm registry ping ' + ping_status)
            raise NpmPreparationDiagnostic(code, (_npm_operator_safe_hint(proc, cache) + meta)[:640])
        raise GuestFailure(code)
    archive = base_dir / 'npm_cache.zip'
    total = 0
    count = 0
    # Store cache only: no node_modules, checkout source, credentials, logs or
    # outputs are transferred to the Windows host.
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=5, allowZip64=False) as zf:
        for item in sorted(cache.rglob('*')):
            if item.is_dir():
                continue
            if item.is_symlink() or not item.is_file():
                raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
            rel = item.relative_to(cache)
            if '_logs' in rel.parts:
                continue
            if item.stat().st_size > 20_000_000:
                raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
            count += 1
            total += item.stat().st_size
            if count > 12000 or total > 250_000_000:
                raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
            zf.write(item, arcname='npm-cache/' + rel.as_posix())
    if count < 1 or archive.stat().st_size > 170_000_000:
        raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
    return {'status': 'CACHE_PREPARED', 'adapter': req['adapter'],
            'lockfile_sha256': lock_sha, 'npm_cache_sha256': _sha256(archive),
            'node_version': version, 'notes': ['Offline npm cache archived; no student scripts executed']}


# D2 Python/SQLite pilot: invoked only in the disposable Linux guest.
# pip is imported directly from a SHA-pinned wheel, not installed into Ubuntu.
_PYTHON_SHA = re.compile(r'^[0-9a-f]{64}$')
_PYTHON_REQ = re.compile(r'^([A-Za-z][A-Za-z0-9._-]{0,63})==([0-9]+(?:\.[0-9]+){1,3}(?:[a-zA-Z0-9.]*)?)$')
_PYTHON_WHEEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.+\-]{0,180}\.whl$')
_PIP_WHEEL_FILE = 'pip-25.2-py3-none-any.whl'


def _check_python_requirements(root: Path, sha256: str, framework: str) -> Path:
    """Conservative single-file requirements: pinned versions, no pip options or URLs."""
    req = root / 'requirements.txt'
    if (not isinstance(sha256, str) or not _PYTHON_SHA.fullmatch(sha256)
            or framework not in ('flask', 'fastapi') or not req.is_file()
            or req.is_symlink() or req.stat().st_size > 16_000):
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
    content = req.read_bytes()
    if _sha256(req) != sha256:
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_HASH_MISMATCH')
    try:
        lines = content.decode('utf-8-sig').splitlines()
    except UnicodeError as exc:
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED') from exc
    packages = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = _PYTHON_REQ.fullmatch(line)
        if not match:
            raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
        name = re.sub('[-_.]+', '-', match.group(1)).lower()
        if name in packages:
            raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
        packages.add(name)
    if (not 1 <= len(packages) <= 25 or framework not in packages
            or framework == 'fastapi' and 'uvicorn' not in packages):
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
    return req


def _python_guest_root(checkout: Path, req: dict) -> Path:
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str) and
            re.fullmatch(r'[A-Za-z0-9_./-]{1,180}', root_rel) and
            not any(p in ('.', '..', '') for p in root_rel.split('/'))):
        raise GuestFailure('PYTHON_WEB_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir():
        raise GuestFailure('PYTHON_WEB_INVALID_PROJECT_ROOT')
    return root


def _check_guest_python312() -> None:
    if sys.version_info[:2] != (3, 12):
        raise GuestFailure('PYTHON_WEB_RUNTIME_VERSION_MISMATCH')


def _python_pip_wheel(base: Path, req: dict) -> Path:
    pip_file = base / 'python_pip.whl'
    expected = req.get('pip_sha256')
    if (not isinstance(expected, str) or not _PYTHON_SHA.fullmatch(expected)
            or not pip_file.is_file() or pip_file.stat().st_size > 5_000_000
            or _sha256(pip_file) != expected):
        raise GuestFailure('PYTHON_WEB_PIP_BOOTSTRAP_INVALID')
    try:
        with zipfile.ZipFile(pip_file) as z:
            names = set(z.namelist())
            if (len(names) > 4000 or 'pip/__main__.py' not in names
                    or 'pip/__init__.py' not in names
                    or not any(n.startswith('pip-25.2.dist-info/') for n in names)):
                raise GuestFailure('PYTHON_WEB_PIP_BOOTSTRAP_INVALID')
    except zipfile.BadZipFile as exc:
        raise GuestFailure('PYTHON_WEB_PIP_BOOTSTRAP_INVALID') from exc
    return pip_file


def _python_pip_env(base: Path, pip_wheel: Path) -> dict:
    """No host or project Python/pip environment variables; distinct config scope."""
    return {'PATH': '/usr/bin:/bin', 'HOME': str(base),
            'PYTHONPATH': str(pip_wheel), 'PYTHONNOUSERSITE': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'PIP_CONFIG_FILE': '/dev/null',
            'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'LC_ALL': 'C.UTF-8'}


def _verify_python_pip(base: Path, pip_wheel: Path) -> dict:
    env = _python_pip_env(base, pip_wheel)
    check = _trusted_process(['/usr/bin/python3', '-m', 'pip', '--version'],
                             timeout=15, env=env)
    if check.returncode or not check.stdout.startswith(b'pip 25.2 '):
        raise GuestFailure('PYTHON_WEB_PIP_VERSION_MISMATCH')
    return env


def _check_python_entry(root: Path, framework: str) -> None:
    entry = root / ('main.py' if framework == 'fastapi' else 'app.py')
    if not entry.is_file() or entry.is_symlink() or entry.stat().st_size > 150_000:
        raise GuestFailure('PYTHON_WEB_ENTRYPOINT_UNSUPPORTED')
    try:
        body = entry.read_text('utf-8-sig')
    except UnicodeError as exc:
        raise GuestFailure('PYTHON_WEB_ENTRYPOINT_UNSUPPORTED') from exc
    marker = 'FastAPI' if framework == 'fastapi' else 'Flask'
    if not re.search(r'\bapp\s*=\s*' + marker + r'\s*\(', body):
        raise GuestFailure('PYTHON_WEB_ENTRYPOINT_UNSUPPORTED')


def _unpack_python_wheelhouse(archive: Path, into: Path) -> list[Path]:
    if not archive.is_file() or not 0 < archive.stat().st_size <= 120_000_000:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    into.mkdir(mode=0o700)
    files = []
    total = 0
    seen = set()
    try:
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            if not 1 <= len(infos) <= 250:
                raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
            for entry in infos:
                if entry.is_dir() and entry.filename == 'wheels/':
                    continue
                name = entry.filename
                if (not name.startswith('wheels/') or name.count('/') != 1
                        or not _PYTHON_WHEEL.fullmatch(name[7:])
                        or entry.is_dir() or entry.flag_bits & 1
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                        or entry.file_size > 35_000_000 or entry.file_size <= 0
                        or name.casefold() in seen):
                    raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                seen.add(name.casefold())
                total += entry.file_size
                if total > 180_000_000:
                    raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                target = into / name[7:]
                with z.open(entry) as source, target.open('xb') as dest:
                    copied = 0
                    while block := source.read(65536):
                        copied += len(block)
                        if copied > entry.file_size:
                            raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                        dest.write(block)
                    if copied != entry.file_size:
                        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                files.append(target)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID') from exc
    if not files:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    return files


def prepare_python_wheelhouse(request_path: Path, *, base_dir: Path = Path('/home/projectiq')) -> dict:
    """Operator-approved, networked, fetch-only VM. Only wheel downloads."""
    req = json.loads(request_path.read_text('utf-8'))
    if (req.get('operation') != 'prepare_python_wheels'
            or req.get('adapter') != 'python_web_offline_v1'
            or req.get('python_version') != '3.12'
            or req.get('python_framework') not in ('flask', 'fastapi')):
        raise GuestFailure('PYTHON_WEB_INVALID_REQUEST')
    _check_guest_python312()
    snapshot = base_dir / 'snapshot.zip'
    if not snapshot.is_file() or snapshot.stat().st_size > 30_000_000:
        raise GuestFailure('Pinned snapshot missing or oversized')
    checkout = safe_extract(snapshot, base_dir / 'checkout')
    root = _python_guest_root(checkout, req)
    requirements = _check_python_requirements(root, req.get('requirements_sha256'),
                                             req['python_framework'])
    _check_python_entry(root, req['python_framework'])
    pip_file = _python_pip_wheel(base_dir, req)
    env = _verify_python_pip(base_dir, pip_file)
    wheels = base_dir / 'python-wheels'
    wheels.mkdir(mode=0o700)
    command = ['/usr/bin/python3', '-m', 'pip', '--isolated', 'download',
               '--only-binary=:all:', '--no-cache-dir', '--disable-pip-version-check',
               '--dest', str(wheels), '--index-url', 'https://pypi.org/simple',
               '-r', str(requirements)]
    result = _trusted_process(command, timeout=210, env=env, cwd=base_dir)
    if result.returncode:
        # Do not send package names, URLs, logs or requirement contents to host.
        raise GuestFailure('PYTHON_WEB_WHEEL_DOWNLOAD_FAILED')
    members = sorted(wheels.iterdir())
    if not members or len(members) > 150 or any(
        not p.is_file() or p.is_symlink() or not _PYTHON_WHEEL.fullmatch(p.name)
        or not 0 < p.stat().st_size <= 35_000_000 for p in members
    ) or sum(p.stat().st_size for p in members) > 180_000_000:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    archive = base_dir / 'python_wheelhouse.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=5, allowZip64=False) as z:
        for member in members:
            z.write(member, arcname='wheels/' + member.name)
    if archive.stat().st_size > 120_000_000:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    return {'status': 'PYTHON_WHEELHOUSE_PREPARED',
            'python_version': '3.12', 'framework': req['python_framework'],
            'requirements_sha256': req['requirements_sha256'],
            'wheelhouse_sha256': _sha256(archive),
            'notes': ['Offline Python wheelhouse prepared; no project installed or executed']}


def _prepare_python_web(checkout: Path, base_dir: Path, req: dict,
                        *, port: int = 8765) -> tuple[list[str], dict, str]:
    if (req.get('adapter') != 'python_web_offline_v1' or req.get('python_version') != '3.12'
            or req.get('python_framework') not in ('flask', 'fastapi')):
        raise GuestFailure('PYTHON_WEB_INVALID_REQUEST')
    _check_guest_python312()
    root = _python_guest_root(checkout, req)
    framework = req['python_framework']
    requirements = _check_python_requirements(root, req.get('requirements_sha256'), framework)
    _check_python_entry(root, framework)
    pip_wheel = _python_pip_wheel(base_dir, req)
    env = _verify_python_pip(base_dir, pip_wheel)
    expected = req.get('wheelhouse_sha256')
    archive = base_dir / 'python_wheelhouse.zip'
    if (not isinstance(expected, str) or not _PYTHON_SHA.fullmatch(expected)
            or not archive.is_file() or _sha256(archive) != expected):
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_HASH_MISMATCH')
    wheels = base_dir / 'python-wheels'
    _unpack_python_wheelhouse(archive, wheels)
    archive.unlink(missing_ok=True)
    target = base_dir / 'python-site'
    target.mkdir(mode=0o700)
    cmd = ['/usr/bin/python3', '-m', 'pip', '--isolated', 'install',
           '--no-index', '--find-links', str(wheels), '--no-cache-dir',
           '--only-binary=:all:', '--no-compile', '--disable-pip-version-check',
           '--target', str(target), '-r', str(requirements)]
    result = _trusted_process(cmd, timeout=150, env=env, cwd=base_dir)
    if result.returncode:
        raise GuestFailure('PYTHON_WEB_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    env = {**env, 'PYTHONPATH': str(target), 'PYTHONUNBUFFERED': '1'}
    check = _trusted_process(['/usr/bin/python3', '-c',
                              'import sqlite3; import flask' if framework == 'flask'
                              else 'import sqlite3; import fastapi, uvicorn'],
                             timeout=15, env=env, cwd=base_dir)
    if check.returncode:
        raise GuestFailure('PYTHON_WEB_IMPORT_OR_SQLITE_FAILED')
    if framework == 'flask':
        program = ['/usr/bin/python3', '-m', 'flask', '--app', 'app', 'run',
                   '--no-reload', '--host', '127.0.0.1', '--port', str(port)]
    else:
        program = ['/usr/bin/python3', '-m', 'uvicorn', 'main:app',
                   '--host', '127.0.0.1', '--port', str(port), '--workers', '1']
    # The caller must start in the validated backend root.
    return program, env, 'Offline Python 3.12 wheels installed; SQLite module available'


def _probe_python_web(path: str, framework: str, *, port: int = 8765) -> tuple[int, str]:
    if path != ('/openapi.json' if framework == 'fastapi' else '/'):
        raise GuestFailure('PYTHON_WEB_HTTP_PROBE_FAILED')
    for _ in range(30):
        try:
            request = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                                             headers={'User-Agent': 'ProjectIQ-Python-Probe'})
            with urllib.request.urlopen(request, timeout=2) as response:
                data = response.read(65_536)
                if response.status != 200 or not data:
                    continue
                if framework == 'fastapi':
                    try:
                        obj = json.loads(data)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(obj, dict) or 'openapi' not in obj or 'paths' not in obj:
                        continue
                return 200, 'HTTP_RESPONSE_OK'
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    raise GuestFailure('PYTHON_WEB_HTTP_PROBE_FAILED')


def _probe_vite_asset(dist: Path, port: int) -> str:
    """Verify a built JS bundle can actually be served, not only index.html."""
    candidates = [p for p in (dist / 'assets').rglob('*.js')
                  if p.is_file() and p.resolve().is_relative_to(dist.resolve())
                  and p.stat().st_size > 0]
    if not candidates:
        raise GuestFailure('NODE_VITE_BUILD_OUTPUT_MISSING')
    asset = sorted(candidates, key=lambda x: str(x))[0]
    path = '/' + quote(asset.relative_to(dist).as_posix(), safe='/')
    try:
        with urllib.request.urlopen('http://127.0.0.1:' + str(port) + path, timeout=5) as response:
            if response.status == 200 and response.read(128):
                return 'Built JS asset HTTP 200 verified'
    except (urllib.error.URLError, OSError, ValueError):
        pass
    raise GuestFailure('NODE_VITE_ASSET_PROBE_FAILED')


def _probe(url_path: str, port: int = 8765, deadline: int = 25) -> tuple[int, str]:
    last = 'CONNECTION_UNAVAILABLE'
    for _ in range(deadline):
        try:
            req = urllib.request.Request('http://127.0.0.1:' + str(port) + url_path,
                                         headers={'User-Agent': 'ProjectIQ-Readiness-Probe'})
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = resp.read(8192)
                status = int(resp.status)
            if status == 200 and b'<html' in data.lower():
                return status, 'HTML_RESPONSE_OK'
            last = 'UNEXPECTED_HTTP_RESPONSE'
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    raise GuestFailure('Application HTTP readiness not established: ' + last)


def run(request_path: Path, *, base_dir: Path = Path('/home/projectiq'), port: int = 8765) -> dict:
    req = json.loads(request_path.read_text('utf-8'))
    adapter = req.get('adapter')
    if adapter not in ('static_html_v1', 'php_mysql_v1', 'node_vite_offline_v1', 'node_express_offline_v1', 'python_web_offline_v1'):
        raise GuestFailure('Unapproved guest adapter')
    path = req.get('probe_path')
    if not isinstance(path, str) or not (path == '/' and adapter in ('python_web_offline_v1', 'node_express_offline_v1') or re.fullmatch(r'/[A-Za-z0-9_./-]{1,150}', path)) or '..' in path.split('/'):
        raise GuestFailure('Invalid readiness path')
    snapshot = base_dir / 'snapshot.zip'
    if not snapshot.is_file() or snapshot.stat().st_size > 30_000_000:
        raise GuestFailure('Pinned snapshot missing or oversized')
    workspace = base_dir / 'checkout'
    checkout = safe_extract(snapshot, workspace)
    if not (checkout / 'index.html').is_file() and adapter == 'static_html_v1':
        raise GuestFailure('Static site has no index.html')
    env = dict(os.environ)
    notes = []
    if adapter == 'node_vite_offline_v1':
        dist, note = _prepare_vite(checkout, base_dir, req)
        notes.append(note)
        program = [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1',
                   '--directory', str(dist)]
    elif adapter == 'node_express_offline_v1':
        program, env, note = _prepare_express(checkout, base_dir, req, port=port)
        notes.append(note)
    elif adapter == 'python_web_offline_v1':
        program, env, note = _prepare_python_web(checkout, base_dir, req, port=port)
        notes.append(note)
    elif adapter == 'php_mysql_v1':
        schema_path = req.get('schema_path')
        if not isinstance(schema_path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,180}', schema_path) or '..' in schema_path.split('/'):
            raise GuestFailure('Invalid schema path')
        env, note = _provision_mysql(checkout, schema_path)
        notes.append(note)
        program = ['/usr/bin/php', '-S', '127.0.0.1:' + str(port), '-t', str(checkout)]
    else:
        program = [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1',
                   '--directory', str(checkout)]
    server_log = base_dir / 'server.log'
    with server_log.open('wb') as log:
        server = subprocess.Popen(program, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, cwd=(_python_guest_root(checkout, req)
                                  if adapter == 'python_web_offline_v1' else
                                  _node_express_root(checkout, req) if adapter == 'node_express_offline_v1' else checkout),
                                  env=env, start_new_session=True)
    try:
        status, detail = (_probe_python_web(path, req['python_framework'], port=port)
                          if adapter == 'python_web_offline_v1' else
                          _probe_express(path, port=port) if adapter == 'node_express_offline_v1' else
                          _probe(path, port=port, deadline=35 if adapter in ('php_mysql_v1', 'node_vite_offline_v1') else 20))
        if adapter == 'node_vite_offline_v1':
            notes.append(_probe_vite_asset(dist, port))
        result = {'status': 'STARTUP_VERIFIED', 'adapter': adapter, 'http_status': status,
                  'readiness_path': path, 'readiness': detail, 'notes': notes}
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    # Return only a bounded, redacted tail; guest credentials must never be logged.
    return result


def main() -> int:
    result = {'status': 'STARTUP_FAILED', 'error_code': 'UNKNOWN_FAILURE'}
    try:
        request = Path('/home/projectiq/request.json')
        meta = json.loads(request.read_text('utf-8'))
        result = (prepare_npm_cache(request) if meta.get('operation') == 'prepare_npm_cache'
                  else prepare_python_wheelhouse(request) if meta.get('operation') == 'prepare_python_wheels'
                  else run(request))
    except GuestFailure as exc:
        # Intentionally avoid returning source strings, SQL, credentials or tracebacks.
        result = {'status': 'STARTUP_FAILED', 'error_code': str(exc)[:120]}
        if isinstance(exc, NpmPreparationDiagnostic):
            result['npm_operator_hint'] = exc.safe_hint
    except Exception:
        result = {'status': 'STARTUP_FAILED', 'error_code': 'UNEXPECTED_GUEST_FAILURE'}
    Path('/home/projectiq/result.json').write_text(json.dumps(result, indent=2) + '\n', 'utf-8')
    return 0 if result['status'] in ('STARTUP_VERIFIED', 'CACHE_PREPARED', 'PYTHON_WHEELHOUSE_PREPARED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
