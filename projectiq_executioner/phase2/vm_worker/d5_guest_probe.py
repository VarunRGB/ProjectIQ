"""Trusted D5 acceptance probe, sent via SSH stdin to a CLEAN offline VM.

No student code, fixture, dependency archive, or credential is sent to this VM.
This is an observational probe and explicitly NOT a sandbox attestation.
Only Python's standard library is used in the guest.
"""
from __future__ import annotations

import json
import errno
import os
import socket
import subprocess
import sys


def _tcp(family: int, address: tuple) -> str:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(address)
        return 'FAIL'  # An external connection succeeded despite denial policy.
    except OSError as exc:
        if exc.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
            return 'NOT_VERIFIED'  # No such protocol available; not a denial proof.
        return 'PASS'  # Failed *observed attempt*, not proof of universal isolation.


def _dns() -> str:
    try:
        result = socket.getaddrinfo('example.com', 443, type=socket.SOCK_STREAM)
        return 'FAIL' if result else 'NOT_VERIFIED'
    except socket.gaierror:
        return 'PASS'
    except OSError:
        return 'NOT_VERIFIED'


def _root_sudo() -> str:
    try:
        p = subprocess.run(['sudo', '-n', '/usr/bin/id', '-u'],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
        if p.returncode == 0 and p.stdout.strip() == b'0':
            return 'FAIL'
        return 'PASS'
    except (OSError, subprocess.TimeoutExpired):
        return 'NOT_VERIFIED'


def _mysql_sudo() -> str:
    try:
        p = subprocess.run(['sudo', '-n', '-l'], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=5, check=False)
        if p.returncode != 0:
            return 'NOT_VERIFIED'
        text = p.stdout.decode('utf-8', 'replace').lower()
        return 'PRESENT' if 'nopasswd:' in text and '/usr/bin/mysql' in text else 'NOT_OBSERVED'
    except (OSError, subprocess.TimeoutExpired):
        return 'NOT_VERIFIED'


def _app_uid_boundary() -> dict:
    """Observe separate app UID, permitted controller runas, and denied app sudo.

    No student repo is supplied. These checks are still only observations.
    """
    keys = ('app_uid_distinct_from_controller', 'controller_runas_app',
            'app_root_sudo_denied')
    try:
        import pwd
        app = pwd.getpwnam('projectiq_run')
        controller = pwd.getpwnam('projectiq')
        if app.pw_uid == 0 or app.pw_uid == controller.pw_uid:
            return {k: 'FAIL' for k in keys}
        as_app = subprocess.run(['sudo', '-n', '-u', 'projectiq_run',
                                  '/usr/bin/id', '-u'], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=7, check=False)
        as_root = subprocess.run(['sudo', '-n', '-u', 'projectiq_run',
                                   '/usr/bin/sudo', '-n', '-u', 'root',
                                   '/usr/bin/id', '-u'], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=7, check=False)
        return {
            'app_uid_distinct_from_controller': 'PASS',
            'controller_runas_app': 'PASS' if as_app.returncode == 0 and
                as_app.stdout.strip() == str(app.pw_uid).encode() else 'FAIL',
            'app_root_sudo_denied': 'FAIL' if as_root.returncode == 0 and
                as_root.stdout.strip() == b'0' else 'PASS',
        }
    except (ImportError, KeyError, OSError, subprocess.TimeoutExpired):
        return {k: 'NOT_VERIFIED' for k in keys}


def _shared_mounts() -> str:
    try:
        with open('/proc/mounts', encoding='utf-8') as mounts:
            rows = mounts.read().splitlines()
    except OSError:
        return 'NOT_VERIFIED'
    forbidden = {'9p', 'virtiofs', 'vboxsf', 'cifs', 'nfs', 'nfs4', 'fuse.sshfs', 'fuse.vmhgfs-fuse'}
    for row in rows:
        parts = row.split(' ')
        if len(parts) >= 3 and parts[2] in forbidden:
            return 'FAIL'
    return 'PASS'


def main() -> int:
    if not hasattr(os, 'geteuid'):
        print(json.dumps({'schema_version': '0.1', 'checks': {}}))
        return 2
    checks = {
        'non_root_user': 'PASS' if os.geteuid() != 0 else 'FAIL',
        'root_sudo_denied': _root_sudo(),
        'no_host_shared_mounts_observed': _shared_mounts(),
        'external_ipv4_tcp_attempt': _tcp(socket.AF_INET, ('1.1.1.1', 443)),
        'external_dns_attempt': _dns(),
        'mysql_sudo_exception': _mysql_sudo(),
        **_app_uid_boundary(),
    }
    try:
        checks['external_ipv6_tcp_attempt'] = _tcp(socket.AF_INET6,
            ('2606:4700:4700::1111', 443, 0, 0))
    except (AttributeError, OSError):
        checks['external_ipv6_tcp_attempt'] = 'NOT_VERIFIED'
    print(json.dumps({'schema_version': '0.1', 'checks': checks}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
