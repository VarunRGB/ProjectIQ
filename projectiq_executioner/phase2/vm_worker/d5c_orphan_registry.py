"""D5c Gate 3: conservative operator-owned disposable VM run registry.

A trusted managed root, a signed per-run record, and an OS file lock form the
cleanup authorization. Never search/delete general TEMP; never kill a PID;
never follow a symlink or Windows reparse point. This is a recovery pilot for
reviewed VMs, not an untrusted-student security attestation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

ROOT_KEY = '.projectiq-d5c3-root-key'
RECORD = 'run.json'
LEASE = '.lease'
PREFIX = 'piq-d5c3-'
ID_PATTERN = re.compile(r'piq-d5c3-[0-9a-f]{20}\Z')
PHASES = {'PREPARING', 'LAUNCHING', 'LAUNCHED', 'BOOTED', 'STOPPED', 'SWEEPING'}
VALID_ARTIFACTS = frozenset({
    RECORD, RECORD + '.writing', LEASE, 'run.qcow2', 'workspace.raw',
    'seed.iso', 'controller_ed25519', 'controller_ed25519.pub',
    'qemu.log', 'known_hosts', 'known_hosts.old',
    # D5c Gate 4: fixed host staging names used by the real reviewed-fixture runner.
    'snapshot.zip', 'request.json', 'result.json',
    'node_runtime.tar.xz', 'npm_cache.zip',
    'python_pip.whl', 'python_wheelhouse.zip',
})
INSPECTION = 'inspection'
MAX_INSPECTION_ENTRIES = 100_000
MAX_INSPECTION_DEPTH = 48
MAX_RECORD_BYTES = 1400


class RecoveryError(RuntimeError):
    """Uncertain ownership, state, or process identity; leave data untouched."""


def _safe_type(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    if hasattr(info, 'st_file_attributes') and info.st_file_attributes & 0x400:
        raise RecoveryError('WINDOWS_REPARSE_POINT')
    if stat.S_ISLNK(info.st_mode):
        raise RecoveryError('SYMLINK_NOT_ALLOWED')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise RecoveryError('UNEXPECTED_FILE_TYPE')
    if info.st_nlink != 1 and not directory:
        raise RecoveryError('LINKED_FILE_NOT_ALLOWED')


def _root(root: Path) -> Path:
    root = Path(root).absolute()
    _safe_type(root, directory=True)
    _safe_type(root / ROOT_KEY, directory=False)
    if (root / ROOT_KEY).stat().st_size != 32:
        raise RecoveryError('INVALID_ROOT_KEY')
    return root


def initialize_root(root: Path) -> Path:
    """Explicit one-time operator registration, never auto-adopt an unknown root."""
    root = Path(root).absolute()
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(root / ROOT_KEY, flags, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(secrets.token_bytes(32))
        _root(root)
    except BaseException:
        # Do not recursively delete; only a root created by this call exists.
        raise
    return root


def _key(root: Path) -> bytes:
    root = _root(root)
    return (root / ROOT_KEY).read_bytes()


def _run(root: Path, folder: Path) -> Path:
    root = _root(root)
    folder = Path(folder).absolute()
    if folder.parent != root or not ID_PATTERN.fullmatch(folder.name):
        raise RecoveryError('RUN_OUTSIDE_MANAGED_ROOT')
    _safe_type(folder, directory=True)
    return folder


def _sign(key: bytes, record: dict) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _record(root: Path, folder: Path) -> dict:
    folder = _run(root, folder)
    file = folder / RECORD
    _safe_type(file, directory=False)
    if file.stat().st_size > MAX_RECORD_BYTES:
        raise RecoveryError('OVERSIZED_RUN_RECORD')
    try:
        obj = json.loads(file.read_text('ascii'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RecoveryError('INVALID_RUN_RECORD') from exc
    if type(obj) is not dict or set(obj) != {
        'schema', 'id', 'phase', 'pid', 'born', 'image', 'mac'
    }:
        raise RecoveryError('INVALID_RECORD_SHAPE')
    mac = obj.pop('mac')
    if not (type(mac) is str and len(mac) == 64 and
            hmac.compare_digest(mac, _sign(_key(root), obj))):
        raise RecoveryError('RECORD_SIGNATURE_INVALID')
    if (obj['schema'] != 'd5c3/1' or obj['id'] != folder.name or
        type(obj['phase']) is not str or obj['phase'] not in PHASES or
        type(obj['pid']) is not int or not 0 <= obj['pid'] < 2**31 or
        type(obj['born']) is not int or not 0 <= obj['born'] < 2**64 or
        type(obj['image']) is not str or len(obj['image']) > 400):
        raise RecoveryError('INVALID_RECORD_VALUES')
    if obj['phase'] in ('PREPARING', 'LAUNCHING') and (
            obj['pid'] != 0 or obj['born'] != 0 or obj['image'] != ''):
        raise RecoveryError('INVALID_PRELAUNCH_IDENTITY')
    if obj['phase'] in ('LAUNCHED', 'BOOTED', 'STOPPED', 'SWEEPING') and (
            obj['pid'] == 0 or obj['born'] == 0 or not obj['image']):
        raise RecoveryError('MISSING_PROCESS_IDENTITY')
    return obj


def _write(root: Path, folder: Path, record: dict) -> None:
    obj = dict(record)
    obj['mac'] = _sign(_key(root), record)
    payload = (json.dumps(obj, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    if len(payload) > MAX_RECORD_BYTES:
        raise RecoveryError('OVERSIZED_RUN_RECORD')
    target = _run(root, folder) / RECORD
    temp = target.with_name(RECORD + '.writing')
    # A stale temp file is a recoverable interrupted atomic write; never
    # overwrite a symlink/reparse point supplied at the temp name.
    if temp.exists() or temp.is_symlink():
        _safe_type(temp, directory=False)
    temp.write_bytes(payload)
    os.replace(temp, target)


def create_run(root: Path) -> Path:
    root = _root(root)
    folder = root / (PREFIX + secrets.token_hex(10))
    folder.mkdir(mode=0o700, exist_ok=False)
    (folder / LEASE).write_bytes(b'1')
    _write(root, folder, {'schema':'d5c3/1','id':folder.name,'phase':'PREPARING',
                          'pid':0,'born':0,'image':''})
    return folder


def update_run(root: Path, folder: Path, phase: str, *, pid: int = 0,
               born: int = 0, image: str = '') -> None:
    if phase not in PHASES:
        raise RecoveryError('INVALID_PHASE')
    old = _record(root, folder)
    allowed = {
        'PREPARING': {'LAUNCHING'},
        'LAUNCHING': {'LAUNCHED'},
        'LAUNCHED': {'BOOTED', 'STOPPED'},
        'BOOTED': {'STOPPED'},
        'STOPPED': {'SWEEPING'},
        'SWEEPING': {'SWEEPING'},
    }
    if phase not in allowed[old['phase']]:
        raise RecoveryError('INVALID_PHASE_TRANSITION')
    if old['phase'] != 'LAUNCHING' and phase not in ('LAUNCHING', 'SWEEPING'):
        if (pid, born, image) != (old['pid'], old['born'], old['image']):
            raise RecoveryError('PROCESS_IDENTITY_CHANGED')
    if phase == 'SWEEPING':
        pid, born, image = old['pid'], old['born'], old['image']
    _write(root, folder, {**old, 'phase':phase,'pid':pid,'born':born,'image':image})


@contextmanager
def exclusive_lease(folder: Path):
    """Cross-process live-controller lock; fail closed on busy/inaccessible."""
    path = Path(folder) / LEASE
    _safe_type(path, directory=False)
    with path.open('r+b', buffering=0) as handle:
        if os.name == 'nt':
            import msvcrt
            # On Windows, even read(1) on a byte locked by ANOTHER process
            # can raise PermissionError. Lock FIRST, then read the marker.
            # Contention must be reported as SKIPPED_ACTIVE, never as a
            # generic invalid run or permission failure.
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RecoveryError('ACTIVE_OR_UNREADABLE_LEASE') from exc
            try:
                handle.seek(0)
                if handle.read(1) != b'1':
                    raise RecoveryError('INVALID_LEASE')
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RecoveryError('ACTIVE_OR_UNREADABLE_LEASE') from exc
            try:
                handle.seek(0)
                if handle.read(1) != b'1':
                    raise RecoveryError('INVALID_LEASE')
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _children_verified(folder: Path) -> list[Path]:
    contents = list(folder.iterdir())
    if len(contents) > len(VALID_ARTIFACTS) + 1:
        raise RecoveryError('TOO_MANY_ARTIFACTS')
    for item in contents:
        if item.name == INSPECTION:
            _safe_type(item, directory=True)
            _inspection_entries(item)  # Full bounded no-reparse preflight.
        elif item.name in VALID_ARTIFACTS:
            _safe_type(item, directory=False)
        else:
            raise RecoveryError('UNKNOWN_ARTIFACT')
    if not ({RECORD, LEASE} <= {x.name for x in contents}):
        raise RecoveryError('MISSING_REQUIRED_ARTIFACT')
    return contents


def _inspection_entries(directory: Path) -> list[Path]:
    """Preflight all entries before deleting any: only real files/directories.

    The inspected GitHub source has arbitrary filenames, but its sole owned
    root is `inspection`. Unknown top-level names are still refused. Never
    follow symlinks, Windows junctions/reparse points, or hardlinked files.
    """
    _safe_type(directory, directory=True)
    seen: list[Path] = []
    stack = [(directory, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_INSPECTION_DEPTH:
            raise RecoveryError('INSPECTION_TREE_TOO_DEEP')
        _safe_type(current, directory=True)
        seen.append(current)
        if len(seen) > MAX_INSPECTION_ENTRIES:
            raise RecoveryError('INSPECTION_TREE_TOO_LARGE')
        for child in current.iterdir():
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                _safe_type(child, directory=True)
                stack.append((child, depth + 1))
            else:
                _safe_type(child, directory=False)
                seen.append(child)
                if len(seen) > MAX_INSPECTION_ENTRIES:
                    raise RecoveryError('INSPECTION_TREE_TOO_LARGE')
    return seen


def _remove_verified_inspection(directory: Path) -> None:
    # Independently revalidate immediately before removal; if anything changes,
    # refuse. This does not claim resistance to hostile same-user races/ACLs.
    entries = _inspection_entries(directory)
    files = [p for p in entries if not stat.S_ISDIR(p.lstat().st_mode)]
    directories = [p for p in entries if stat.S_ISDIR(p.lstat().st_mode)]
    for path in files:
        _safe_type(path, directory=False)
        path.unlink()
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        _safe_type(path, directory=True)
        path.rmdir()


def windows_process_state(pid: int, born: int, image: str) -> str:
    """Identity-aware process check; never terminate using a recorded PID.

    Returns DEAD only for a nonexistent or already-exited handle. PID reuse,
    unknown image, access denial, or a live matching QEMU blocks deletion.
    """
    if os.name != 'nt':
        return 'UNKNOWN'
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_void_p]
    k.GetProcessTimes.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    handle = k.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        return 'DEAD' if ctypes.get_last_error() in (87, 1168) else 'UNKNOWN'
    try:
        state = k.WaitForSingleObject(handle, 0)
        if state == 0:
            return 'DEAD'
        if state != 258:
            return 'UNKNOWN'
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return 'UNKNOWN'
        class FILETIME(ctypes.Structure):
            _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]
        times = [FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *(ctypes.byref(x) for x in times)):
            return 'UNKNOWN'
        observed_born = times[0].low | (times[0].high << 32)
        if (os.path.normcase(os.path.realpath(buf.value)) !=
            os.path.normcase(os.path.realpath(image)) or observed_born != born):
            return 'UNKNOWN'  # possible PID reuse; never assume absence
        return 'ALIVE'
    finally:
        k.CloseHandle(handle)


def windows_creation_time(proc) -> int:
    """Get creation FILETIME from the exact live Popen handle, not a PID."""
    if os.name != 'nt' or proc is None or not getattr(proc, '_handle', None):
        raise RecoveryError('PROCESS_HANDLE_UNAVAILABLE')
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_void_p]
    k.GetProcessTimes.restype = wintypes.BOOL
    class FILETIME(ctypes.Structure):
        _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]
    times = [FILETIME() for _ in range(4)]
    if not k.GetProcessTimes(proc._handle, *(ctypes.byref(x) for x in times)):
        raise RecoveryError('PROCESS_CREATION_UNREADABLE')
    born = times[0].low | (times[0].high << 32)
    if born == 0:
        raise RecoveryError('INVALID_PROCESS_CREATION')
    return born


def recover_managed_root(root: Path, expected_image: str, *,
                         process_state=None) -> list[dict]:
    """Sweep one explicitly registered root. Unknown objects are untouched."""
    root = _root(root)
    if process_state is None:
        process_state = windows_process_state
    outcomes = []
    for folder in sorted(root.iterdir()):
        if folder.name == ROOT_KEY:
            continue
        if not folder.name.startswith(PREFIX):
            continue
        status = 'MANUAL_REVIEW'
        reason = 'UNVERIFIED'
        try:
            folder = _run(root, folder)
            record = _record(root, folder)
            _children_verified(folder)
            with exclusive_lease(folder):
                if record['phase'] == 'LAUNCHING':
                    raise RecoveryError('QEMU_LAUNCH_IDENTITY_GAP')
                if record['phase'] != 'PREPARING':
                    if os.path.normcase(os.path.realpath(record['image'])) != os.path.normcase(os.path.realpath(expected_image)):
                        raise RecoveryError('WRONG_PINNED_QEMU_IMAGE')
                    state = process_state(record['pid'], record['born'], record['image'])
                    if state != 'DEAD':
                        raise RecoveryError('QEMU_ALIVE_OR_IDENTITY_AMBIGUOUS')
                # No unexpected files. Record SWEEPING before unlocking. If
                # recovery is interrupted, a later sweep retries safely.
                if record['phase'] != 'PREPARING' and record['phase'] != 'SWEEPING':
                    update_run(root, folder, 'SWEEPING') if record['phase'] == 'STOPPED' else _write(root,folder,{**record,'phase':'SWEEPING'})
                status = 'READY_TO_REMOVE'
            # On Windows the lease file must be closed before removing it.
            # This run ID is immutable and cannot be acquired by a new worker.
            _children_verified(folder)
            inspected = folder / INSPECTION
            if inspected.exists() or inspected.is_symlink():
                _remove_verified_inspection(inspected)
            for name in sorted(VALID_ARTIFACTS - {LEASE, RECORD}):
                item = folder / name
                try:
                    _safe_type(item, directory=False)
                except FileNotFoundError:
                    continue
                item.unlink()
            (folder / RECORD).unlink()
            (folder / LEASE).unlink()
            folder.rmdir()
            status, reason = 'RECOVERED', 'VERIFIED_STALE'
        except (RecoveryError, OSError, ValueError, UnicodeError) as exc:
            reason = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
            status = 'SKIPPED_ACTIVE' if reason == 'ACTIVE_OR_UNREADABLE_LEASE' else 'MANUAL_REVIEW'
        outcomes.append({'run': folder.name, 'status': status, 'reason': reason})
    return outcomes
