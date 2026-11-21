"""Trusted root-only guest bootstrap: block app UID writes to root-disk scratch.

Installed as data in the disposable VM's cloud-init seed, *before* the
app-evaluation ready marker is published. Never run on the Windows host.

Linux POSIX ACLs deny one named UID without changing other users' customary
1777 /tmp and /var/tmp permissions. Existing ACL entries are preserved.
Only a short, fixed allowlist of trusted VM paths is modified.
"""
from __future__ import annotations

import errno
import os
import stat
import struct
import sys
from pathlib import Path

try:
    import pwd
except ImportError:  # Windows offline import only
    pwd = None

ACL_XATTR = 'system.posix_acl_access'
ACL_VERSION = 2
USER_OBJ, USER, GROUP_OBJ, GROUP, MASK, OTHER = 1, 2, 4, 8, 16, 32
NO_ID = 0xFFFFFFFF
ACL_FORMAT = '<HHI'
ACL_SIZE = struct.calcsize(ACL_FORMAT)


class StorageGuardError(RuntimeError):
    pass


def _entries(raw: bytes) -> list[tuple[int, int, int]]:
    if len(raw) < 4 or (len(raw) - 4) % ACL_SIZE or struct.unpack('<I', raw[:4])[0] != ACL_VERSION:
        raise StorageGuardError('INVALID_ROOT_TMP_ACL')
    entries = [struct.unpack_from(ACL_FORMAT, raw, n) for n in range(4, len(raw), ACL_SIZE)]
    if not 3 <= len(entries) <= 64:
        raise StorageGuardError('INVALID_ROOT_TMP_ACL')
    seen = set()
    for tag, perm, identity in entries:
        if tag not in (USER_OBJ, USER, GROUP_OBJ, GROUP, MASK, OTHER) or perm > 7:
            raise StorageGuardError('INVALID_ROOT_TMP_ACL')
        if tag in (USER, GROUP):
            if identity == NO_ID or (tag, identity) in seen:
                raise StorageGuardError('INVALID_ROOT_TMP_ACL')
            seen.add((tag, identity))
        else:
            if identity != NO_ID or (tag, None) in seen:
                raise StorageGuardError('INVALID_ROOT_TMP_ACL')
            seen.add((tag, None))
    if not all((t, None) in seen for t in (USER_OBJ, GROUP_OBJ, OTHER)):
        raise StorageGuardError('INVALID_ROOT_TMP_ACL')
    if (USER in (entry[0] for entry in entries) or GROUP in (entry[0] for entry in entries)) and (MASK, None) not in seen:
        raise StorageGuardError('INVALID_ROOT_TMP_ACL')
    return entries


def _acl_with_denied_write(raw: bytes | None, mode: int, uid: int) -> bytes:
    """Return a valid ACL; preserve all unrelated entries and their mask."""
    if type(uid) is not int or uid <= 0 or uid >= NO_ID:
        raise StorageGuardError('INVALID_APP_UID')
    if raw is None:
        entries = [(USER_OBJ, (mode >> 6) & 7, NO_ID),
                   (GROUP_OBJ, (mode >> 3) & 7, NO_ID),
                   (OTHER, mode & 7, NO_ID)]
    else:
        entries = _entries(raw)
    modified = []
    seen_uid = False
    has_mask = False
    for tag, perm, identity in entries:
        if tag == USER and identity == uid:
            modified.append((USER, perm & ~2, identity))
            seen_uid = True
        else:
            modified.append((tag, perm, identity))
        has_mask |= tag == MASK
    if not seen_uid:
        modified.append((USER, 5, uid))  # read + traverse only
    if not has_mask:
        modified.append((MASK, (mode >> 3) & 7, NO_ID))
    # Linux ACL canonical order: owner, named users, group owner, named groups,
    # mask, other. Preserving group permissions avoids changing trusted users.
    order = {USER_OBJ: 0, USER: 1, GROUP_OBJ: 2, GROUP: 3, MASK: 4, OTHER: 5}
    modified.sort(key=lambda entry: (order[entry[0]], entry[2] if entry[0] in (USER, GROUP) else 0))
    result = struct.pack('<I', ACL_VERSION) + b''.join(struct.pack(ACL_FORMAT, *entry) for entry in modified)
    parsed = _entries(result)
    if len([x for x in parsed if x[0] == USER and x[2] == uid and not x[1] & 2]) != 1:
        raise StorageGuardError('ROOT_TMP_ACL_VERIFICATION_FAILED')
    return result


def _deny_app_tmp_write(path: Path, uid: int) -> None:
    try:
        before = path.lstat()
        if (not stat.S_ISDIR(before.st_mode) or before.st_uid != 0 or
                before.st_mode & 0o1777 != 0o1777):
            raise StorageGuardError('UNEXPECTED_ROOT_TMP_DIRECTORY')
        try:
            current = os.getxattr(path, ACL_XATTR, follow_symlinks=False)
        except OSError as exc:
            if exc.errno != getattr(errno, 'ENODATA', 61):
                raise
            current = None
        desired = _acl_with_denied_write(current, before.st_mode, uid)
        os.setxattr(path, ACL_XATTR, desired, follow_symlinks=False)
        after = path.lstat()
        if (after.st_ino != before.st_ino or after.st_dev != before.st_dev or
                after.st_mode & 0o1777 != 0o1777):
            raise StorageGuardError('ROOT_TMP_ACL_VERIFICATION_FAILED')
        observed = _entries(os.getxattr(path, ACL_XATTR, follow_symlinks=False))
        if len([e for e in observed if e[0] == USER and e[2] == uid and not e[1] & 2]) != 1:
            raise StorageGuardError('ROOT_TMP_ACL_VERIFICATION_FAILED')
    except OSError as exc:
        raise StorageGuardError('ROOT_TMP_ACL_UNAVAILABLE') from exc


def _lock_app_home(path: Path, uid: int) -> None:
    """App UID cannot use its initial home to bypass bounded scratch writes."""
    if path != Path('/home/projectiq_run'):
        raise StorageGuardError('UNEXPECTED_APP_HOME')
    try:
        root_st = path.lstat()
        if not stat.S_ISDIR(root_st.st_mode) or root_st.st_uid not in (uid, 0):
            raise StorageGuardError('UNEXPECTED_APP_HOME')
        nodes = [path]
        for parent in nodes:
            if not parent.is_dir():
                continue
            for entry in parent.iterdir():
                if len(nodes) >= 128:
                    raise StorageGuardError('APP_HOME_TOO_LARGE')
                meta = entry.lstat()
                if (not (stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode)) or
                        meta.st_uid not in (uid, 0)):
                    raise StorageGuardError('UNEXPECTED_APP_HOME_CONTENT')
                nodes.append(entry)
        for node in reversed(nodes):
            os.chown(node, 0, 0, follow_symlinks=False)
            node.chmod(0o555 if node.is_dir() else 0o444)
        # Check the directory inode is still the same (no symlink replacement).
        stat_after = path.lstat()
        if (stat_after.st_ino != root_st.st_ino or stat_after.st_dev != root_st.st_dev or
                stat_after.st_uid != 0 or stat_after.st_mode & 0o222):
            raise StorageGuardError('APP_HOME_LOCK_FAILED')
    except OSError as exc:
        raise StorageGuardError('APP_HOME_LOCK_FAILED') from exc


def main() -> int:
    if sys.platform != 'linux' or pwd is None or os.geteuid() != 0:
        return 2
    try:
        app = pwd.getpwnam('projectiq_run')
        if app.pw_uid <= 0 or app.pw_dir != '/home/projectiq_run':
            raise StorageGuardError('INVALID_APP_UID')
        # Workspace bootstrap must have completed. We never run this on a
        # template-baking worker or before the bounded work disk is mounted.
        if not os.path.ismount('/srv/projectiq-workspace'):
            raise StorageGuardError('WORKSPACE_NOT_MOUNTED')
        _deny_app_tmp_write(Path('/tmp'), app.pw_uid)
        _deny_app_tmp_write(Path('/var/tmp'), app.pw_uid)
        _lock_app_home(Path(app.pw_dir), app.pw_uid)
        return 0
    except (OSError, KeyError, StorageGuardError):
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
