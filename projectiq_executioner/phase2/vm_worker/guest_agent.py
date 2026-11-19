"""Trusted, offline agent for a disposable Ubuntu worker VM.

Intended to be copied in by the controller and executed inside the guest ONLY.
Never execute this guest agent on the ProjectIQ/Windows host.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


class GuestFailure(RuntimeError):
    pass


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
                     env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False, env=env)
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
    if adapter not in ('static_html_v1', 'php_mysql_v1'):
        raise GuestFailure('Unapproved guest adapter')
    path = req.get('probe_path')
    if not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]{1,150}', path) or '..' in path.split('/'):
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
    if adapter == 'php_mysql_v1':
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
                                  stderr=subprocess.STDOUT, cwd=checkout, env=env, start_new_session=True)
    try:
        status, detail = _probe(path, port=port, deadline=35 if adapter == 'php_mysql_v1' else 20)
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
        result = run(Path('/home/projectiq/request.json'))
    except GuestFailure as exc:
        # Intentionally avoid returning source strings, SQL, credentials or tracebacks.
        result = {'status': 'STARTUP_FAILED', 'error_code': str(exc)[:120]}
    except Exception:
        result = {'status': 'STARTUP_FAILED', 'error_code': 'UNEXPECTED_GUEST_FAILURE'}
    Path('/home/projectiq/result.json').write_text(json.dumps(result, indent=2) + '\n', 'utf-8')
    return 0 if result['status'] == 'STARTUP_VERIFIED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
