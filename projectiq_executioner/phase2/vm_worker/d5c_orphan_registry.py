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
import time
from contextlib import contextmanager
from pathlib import Path

ROOT_KEY = '.projectiq-d5c3-root-key'
# A permanent root-level lease serializes cooperative registration and sweeping.
ROOT_LEASE = '.lease'
# Durable, authenticated recovery authority survives deletion of run.json/.lease.
ROOT_SWEEP = '.projectiq-d5c5-sweep.json'
ROOT_SWEEP_TEMP = ROOT_SWEEP + '.writing'
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
        (root / ROOT_LEASE).write_bytes(b'1')
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
    if obj['phase'] in ('LAUNCHED', 'BOOTED', 'STOPPED') and (
            obj['pid'] == 0 or obj['born'] == 0 or not obj['image']):
        raise RecoveryError('MISSING_PROCESS_IDENTITY')
    if obj['phase'] == 'SWEEPING' and not (
            (obj['pid'], obj['born'], obj['image']) == (0, 0, '') or
            (obj['pid'] > 0 and obj['born'] > 0 and bool(obj['image']))):
        raise RecoveryError('INVALID_SWEEP_IDENTITY')
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


def _create_run_unlocked(root: Path) -> Path:
    """Create a signed PREPARING record; caller owns the root-level lease."""
    root = _root(root)
    folder = root / (PREFIX + secrets.token_hex(10))
    folder.mkdir(mode=0o700, exist_ok=False)
    (folder / LEASE).write_bytes(b'1')
    _write(root, folder, {'schema':'d5c3/1','id':folder.name,'phase':'PREPARING',
                          'pid':0,'born':0,'image':''})
    return folder


def create_run(root: Path) -> Path:
    """Create an *unleased* test/recovery fixture; never use for a live VM."""
    with managed_root_lease(root):
        return _create_run_unlocked(root)


def create_leased_run(root: Path):
    """Publish a runtime run and acquire its live lease in one root-locked step.

    A concurrent sweeper cannot observe a newly published, unleased runtime.
    The returned context manager is held by the caller until QEMU stops.
    """
    with managed_root_lease(root):
        folder = _create_run_unlocked(root)
        lease = exclusive_lease(folder)
        lease.__enter__()
        return folder, lease


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


@contextmanager
def managed_root_lease(root: Path, *, timeout_seconds: float = 15.0):
    """Serialize all cooperative run creation and cleanup on this managed root.

    Existing Gate 4 roots have only the root key. Upgrade them by atomically
    creating a permanent lock file; do not replace or delete a live lock inode.
    A busy lock is bounded, and failure is safe (no VM starts or files removed).
    """
    root = _root(root)
    if type(timeout_seconds) not in (float, int) or not 0 <= timeout_seconds <= 60:
        raise RecoveryError('INVALID_ROOT_LOCK_TIMEOUT')
    path = root / ROOT_LEASE
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as file:
            file.write(b'1')
            file.flush()
            os.fsync(file.fileno())
    until = time.monotonic() + timeout_seconds
    while True:
        lock = exclusive_lease(root)
        try:
            lock.__enter__()
            break
        except RecoveryError as exc:
            if str(exc) != 'ACTIVE_OR_UNREADABLE_LEASE' or time.monotonic() >= until:
                raise
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


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


def _journal_payload(record: dict) -> dict:
    return {key: record[key] for key in ('id', 'pid', 'born', 'image')}


def _write_sweep_journal(root: Path, record: dict) -> None:
    """Durable signed cleanup authority, kept until the directory is removed."""
    target = root / ROOT_SWEEP
    temporary = root / ROOT_SWEEP_TEMP
    if target.exists() or target.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise RecoveryError('PENDING_SWEEP_JOURNAL')
    payload = {'schema': 'd5c5/1', **_journal_payload(record)}
    body = {**payload, 'mac': _sign(_key(root), payload)}
    data = (json.dumps(body, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    if len(data) > MAX_RECORD_BYTES:
        raise RecoveryError('OVERSIZED_SWEEP_JOURNAL')
    # No untrusted input is used in this path. A torn temporary is removable
    # after the global lease is reacquired, before any directory is touched.
    with temporary.open('xb') as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)


def _read_sweep_journal(root: Path) -> dict:
    path = root / ROOT_SWEEP
    _safe_type(path, directory=False)
    if not 0 < path.stat().st_size <= MAX_RECORD_BYTES:
        raise RecoveryError('INVALID_SWEEP_JOURNAL_SIZE')
    try:
        obj = json.loads(path.read_text('ascii'))
    except (UnicodeError, ValueError) as exc:
        raise RecoveryError('INVALID_SWEEP_JOURNAL') from exc
    if type(obj) is not dict or set(obj) != {'schema', 'id', 'pid', 'born', 'image', 'mac'}:
        raise RecoveryError('INVALID_SWEEP_JOURNAL_SHAPE')
    mac = obj.pop('mac')
    if type(mac) is not str or not hmac.compare_digest(mac, _sign(_key(root), obj)):
        raise RecoveryError('SWEEP_JOURNAL_SIGNATURE_INVALID')
    if (obj['schema'] != 'd5c5/1' or type(obj['id']) is not str or
            not ID_PATTERN.fullmatch(obj['id']) or type(obj['pid']) is not int or
            type(obj['born']) is not int or type(obj['image']) is not str or
            not (0 <= obj['pid'] < 2**31 and 0 <= obj['born'] < 2**64 and
                 len(obj['image']) <= 400) or
            not ((obj['pid'], obj['born'], obj['image']) == (0, 0, '') or
                 (obj['pid'] > 0 and obj['born'] > 0 and bool(obj['image'])))):
        raise RecoveryError('INVALID_SWEEP_JOURNAL_VALUES')
    return obj


def _partial_children_verified(folder: Path) -> None:
    """Run contents during deletion: still allowlisted, but possibly missing."""
    contents = list(folder.iterdir())
    if len(contents) > len(VALID_ARTIFACTS) + 1:
        raise RecoveryError('TOO_MANY_ARTIFACTS')
    for item in contents:
        if item.name == INSPECTION:
            _inspection_entries(item)
        elif item.name in VALID_ARTIFACTS:
            _safe_type(item, directory=False)
        else:
            raise RecoveryError('UNKNOWN_ARTIFACT')


def _finish_journaled_sweep(root: Path, expected_image: str, process_state) -> dict:
    """Retry cleanup even after the per-run lease or record was removed.

    Caller MUST hold the permanent root lease. The HMAC-rooted journal and
    repeated process check, not a missing PID or empty folder, authorize this.
    """
    record = _read_sweep_journal(root)
    folder = root / record['id']
    if record['pid']:
        if os.path.normcase(os.path.realpath(record['image'])) != os.path.normcase(os.path.realpath(expected_image)):
            raise RecoveryError('WRONG_PINNED_QEMU_IMAGE')
        if process_state(record['pid'], record['born'], record['image']) != 'DEAD':
            raise RecoveryError('QEMU_ALIVE_OR_IDENTITY_AMBIGUOUS')
    if folder.exists() or folder.is_symlink():
        _run(root, folder)
        lease_path = folder / LEASE
        # The per-run lease may already have been removed in a previous sweep.
        # If it exists, it must be unlocked before deleting anything.
        def verify_remaining() -> None:
            _partial_children_verified(folder)
            metadata = folder / RECORD
            if metadata.exists() or metadata.is_symlink():
                signed = _record(root, folder)
                if signed['phase'] != 'SWEEPING' or _journal_payload(signed) != _journal_payload(record):
                    raise RecoveryError('SWEEP_JOURNAL_RECORD_MISMATCH')
        if lease_path.exists() or lease_path.is_symlink():
            with exclusive_lease(folder):
                verify_remaining()
        else:
            verify_remaining()
        _partial_children_verified(folder)
        # Revalidate each object immediately before removal. The permanent
        # root lease ensures another cooperative controller cannot sweep it.
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
        for name in (RECORD, LEASE):
            item = folder / name
            try:
                _safe_type(item, directory=False)
            except FileNotFoundError:
                continue
            item.unlink()
        folder.rmdir()
    (root / ROOT_SWEEP).unlink()
    return {'run': record['id'], 'status': 'RECOVERED', 'reason': 'VERIFIED_STALE'}


def _recover_managed_root_locked(root: Path, expected_image: str, *,
                                 process_state=None) -> list[dict]:
    """Internal only: caller MUST own the global root lease throughout.

    An interrupted journaled sweep is completed first. Ambiguous VM identity,
    tampered metadata, unknown artifacts and active run leases remain untouched.
    """
    root = _root(root)
    if process_state is None:
        process_state = windows_process_state
    outcomes = []
    temp = root / ROOT_SWEEP_TEMP
    if temp.exists() or temp.is_symlink():
        _safe_type(temp, directory=False)
        if temp.stat().st_size > MAX_RECORD_BYTES:
            raise RecoveryError('UNSAFE_SWEEP_JOURNAL_TEMP')
        temp.unlink()  # Stale uncommitted write; no deletion was authorized.
    journal = root / ROOT_SWEEP
    if journal.exists() or journal.is_symlink():
        try:
            outcomes.append(_finish_journaled_sweep(root, expected_image, process_state))
        except (RecoveryError, OSError, ValueError, UnicodeError) as exc:
            reason = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
            status = 'SKIPPED_ACTIVE' if reason == 'ACTIVE_OR_UNREADABLE_LEASE' else 'MANUAL_REVIEW'
            outcomes.append({'run': 'PENDING_SWEEP', 'status': status, 'reason': reason})
            return outcomes  # Refuse other cleanup while authority is unresolved.
    for folder in sorted(root.iterdir()):
        if not folder.name.startswith(PREFIX):
            continue
        status = 'MANUAL_REVIEW'
        reason = 'UNVERIFIED'
        try:
            folder = _run(root, folder)
            # Obtain the per-run lease BEFORE reading the manifest: a live
            # controller may still be publishing lifecycle transitions.
            with exclusive_lease(folder):
                record = _record(root, folder)
                _children_verified(folder)
                if record['phase'] == 'LAUNCHING':
                    raise RecoveryError('QEMU_LAUNCH_IDENTITY_GAP')
                if record['pid']:
                    if (os.path.normcase(os.path.realpath(record['image'])) !=
                            os.path.normcase(os.path.realpath(expected_image))):
                        raise RecoveryError('WRONG_PINNED_QEMU_IMAGE')
                    if process_state(record['pid'], record['born'], record['image']) != 'DEAD':
                        raise RecoveryError('QEMU_ALIVE_OR_IDENTITY_AMBIGUOUS')
                if record['phase'] != 'SWEEPING':
                    if record['phase'] == 'STOPPED':
                        update_run(root, folder, 'SWEEPING')
                    else:
                        _write(root, folder, {**record, 'phase': 'SWEEPING'})
                    record = _record(root, folder)
                _write_sweep_journal(root, record)
            outcomes.append(_finish_journaled_sweep(root, expected_image, process_state))
            continue
        except (RecoveryError, OSError, ValueError, UnicodeError) as exc:
            reason = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
            status = 'SKIPPED_ACTIVE' if reason == 'ACTIVE_OR_UNREADABLE_LEASE' else 'MANUAL_REVIEW'
            outcomes.append({'run': folder.name, 'status': status, 'reason': reason})
            if (root / ROOT_SWEEP).exists():
                break  # Do not open another sweep while the journal remains.
    return outcomes


def recover_managed_root(root: Path, expected_image: str, *,
                         process_state=None) -> list[dict]:
    """Public fail-closed recovery; always acquire root ownership first."""
    with managed_root_lease(root):
        return _recover_managed_root_locked(root, expected_image,
                                            process_state=process_state)
