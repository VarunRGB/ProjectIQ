"""D5d: independent read-only Windows ACL audit of the managed VM root.

This audits the *host-account* boundary; it cannot establish isolation against
malware already executing as the same Windows operator, nor against VM escapes.
No VM is launched; no ACL is changed or automatically repaired.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import subprocess
from pathlib import Path

from . import d5c_orphan_registry as registry
from .d5c_runtime_workspace import ManagedRuntimeRun, RuntimeWorkspaceError

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
SUCCESS = 'D5D_HOST_ACL_PRECHECK_PASSED_NOT_ATTESTED'
DENIED = 'D5D_HOST_ACL_REQUIRES_REVIEW'
# WriteData/CreateFiles, AppendData/CreateDirs, WriteEA, DeleteChild,
# WriteAttributes, DELETE, WRITE_DAC, WRITE_OWNER. Excludes READ_CONTROL,
# SYNCHRONIZE, read/list and execute rights.
WRITE_MASK = 0x000D0156 | 0x10000000 | 0x40000000
BROAD_SIDS = frozenset({
    'S-1-1-0',       # Everyone
    'S-1-5-11',      # Authenticated Users
    'S-1-5-32-545',  # Builtin Users
    'S-1-5-32-546',  # Builtin Guests
    'S-1-5-32-547',  # Power Users
    'S-1-5-7',       # Anonymous Logon
})
SYSTEM_SIDS = frozenset({'S-1-5-18', 'S-1-5-32-544'})  # SYSTEM, Administrators
NAMES = ('parent', 'root', 'key', 'lease')
MAX_ACL_OUTPUT = 200_000

# Fixed, trusted PowerShell, fed only JSON paths via the environment (never
# interpolated into the script). Return raw SID values to Python for checks;
# do not persist user names/SIDs in the acceptance JSON.
_ACL_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$paths = ConvertFrom-Json -InputObject $env:PROJECTIQ_D5D_AUDIT_PATHS
$operator = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$result = @()
foreach ($name in @('parent', 'root', 'key', 'lease')) {
    $acl = Get-Acl -LiteralPath ([string]$paths.$name) -ErrorAction Stop
    $descriptor = [System.Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
    $daclPresent = [bool]($descriptor.ControlFlags -band [System.Security.AccessControl.ControlFlags]::DiscretionaryAclPresent)
    $daclNull = ($null -eq $descriptor.DiscretionaryAcl)
    $aces = @()
    foreach ($ace in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        $aces += [pscustomobject]@{
            sid = $ace.IdentityReference.Value
            rights = [int64]$ace.FileSystemRights
            type = [string]$ace.AccessControlType
        }
    }
    $result += [pscustomobject]@{
        name = $name
        owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        dacl_present = $daclPresent
        dacl_null = $daclNull
        aces = @($aces)
    }
}
@{ operator = $operator; files = @($result) } | ConvertTo-Json -Depth 6 -Compress
'''


def _windows_supported() -> bool:
    return os.name == 'nt'


def _read_acl_snapshot(paths: dict[str, Path]) -> dict:
    """Read OS DACLs using built-in Windows PowerShell, never modify them."""
    if not _windows_supported():
        raise OSError('WINDOWS_ACL_AUDIT_REQUIRED')
    if set(paths) != set(NAMES):
        raise ValueError('INVALID_AUDIT_PATHS')
    env = os.environ.copy()
    env['PROJECTIQ_D5D_AUDIT_PATHS'] = json.dumps(
        {name: str(paths[name]) for name in NAMES}, sort_keys=True)
    encoded = base64.b64encode(_ACL_SCRIPT.encode('utf-16-le')).decode('ascii')
    # Do not search PATH or the repository for a same-named executable.
    system_root = Path(os.environ.get('SystemRoot') or r'C:\Windows')
    powershell = system_root / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    if not powershell.is_file():
        raise OSError('TRUSTED_POWERSHELL_UNAVAILABLE')
    try:
        proc = subprocess.run(
            [str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=25,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError('ACL_INSPECTION_UNAVAILABLE') from exc
    if proc.returncode or not (0 < len(proc.stdout) < MAX_ACL_OUTPUT):
        raise OSError('ACL_INSPECTION_FAILED')
    try:
        snapshot = json.loads(proc.stdout.decode('utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        # Windows PowerShell 5.1 may write OEM-encoded ASCII for these SID-only
        # objects; all data fields here are ASCII by construction.
        try:
            snapshot = json.loads(proc.stdout.decode('ascii'))
        except (ValueError, UnicodeError) as nested:
            raise OSError('ACL_REPORT_INVALID') from nested
    if type(snapshot) is not dict:
        raise ValueError('ACL_REPORT_INVALID')
    return snapshot


def _assess_snapshot(snapshot: dict, *, findings: dict | None = None) -> dict[str, str]:
    """Conservatively classify ACLs, including unknown writable trustees.

    A DENY ACE cannot make an untrusted ALLOW write appear safe here; accurate
    effective-access evaluation would need token-based OS access checks. We
    therefore treat unknown/complex grants as manual review, not as PASS.
    """
    if type(snapshot) is not dict or type(snapshot.get('operator')) is not str:
        raise ValueError('INVALID_ACL_SNAPSHOT')
    operator = snapshot['operator']
    if not operator.startswith('S-1-'):
        raise ValueError('INVALID_OPERATOR_SID')
    records = snapshot.get('files')
    if type(records) is not list or len(records) != len(NAMES):
        raise ValueError('INVALID_ACL_FILES')
    seen = set()
    results = {}
    if findings is not None:
        findings.clear()
    for record in records:
        if type(record) is not dict or record.get('name') not in NAMES or record['name'] in seen:
            raise ValueError('INVALID_ACL_FILE_NAME')
        name = record['name']
        seen.add(name)
        owner, aces = record.get('owner'), record.get('aces')
        if (type(record.get('dacl_present')) is not bool or
                type(record.get('dacl_null')) is not bool):
            raise ValueError('INVALID_DACL_METADATA')
        if type(owner) is not str or type(aces) is not list or len(aces) > 2048:
            raise ValueError('INVALID_ACL_FILE_RECORD')
        status = 'PASS' if owner in (SYSTEM_SIDS | {operator}) else 'MANUAL_REVIEW'
        reasons = set()
        if status != 'PASS':
            reasons.add('UNRECOGNIZED_OBJECT_OWNER')
        if not record['dacl_present'] or record['dacl_null']:
            status = 'FAIL'  # Null DACL may grant Everyone full access.
            reasons.add('MISSING_OR_NULL_DACL')
        for ace in aces:
            if type(ace) is not dict or set(ace) != {'sid', 'rights', 'type'}:
                raise ValueError('INVALID_ACL_ACE')
            sid, rights, kind = ace['sid'], ace['rights'], ace['type']
            if (type(sid) is not str or not sid.startswith('S-1-') or
                    type(rights) is not int or type(kind) is not str):
                raise ValueError('INVALID_ACL_ACE')
            if rights & WRITE_MASK and kind == 'Allow':
                if sid in BROAD_SIDS:
                    status = 'FAIL'
                    reasons.add('BROAD_PRINCIPAL_HAS_WRITE_ACCESS')
                elif sid == 'S-1-3-4' and owner in (SYSTEM_SIDS | {operator}):
                    # OWNER RIGHTS refers to the actual owner, not a separate
                    # account. Accept it only after the owner SID was checked
                    # against our existing trusted-owner set. An unexpected
                    # owner still requires manual review below.
                    pass
                elif sid not in SYSTEM_SIDS and sid != operator:
                    if status != 'FAIL':
                        status = 'MANUAL_REVIEW'
                    # SID categories only: never persist account identifiers.
                    category = ('CREATOR_OWNER' if sid == 'S-1-3-0' else
                                'OWNER_RIGHTS' if sid == 'S-1-3-4' else
                                'OTHER_PRINCIPAL')
                    reasons.add('UNRECOGNIZED_WRITABLE_' + category)
            elif kind not in ('Allow', 'Deny'):
                if status != 'FAIL':
                    status = 'MANUAL_REVIEW'
                reasons.add('UNRECOGNIZED_ACCESS_TYPE')
        results[name] = status
        if findings is not None:
            findings[name] = sorted(reasons)
    if seen != set(NAMES):
        raise ValueError('INCOMPLETE_ACL_FILES')
    return results


def audit(config_path: Path) -> dict:
    """Audit exactly the configured registered root; never create/delete data."""
    checks = {name + '_acl': 'NOT_VERIFIED' for name in NAMES}
    checks['registered_root_valid'] = 'NOT_VERIFIED'
    result = {
        'schema_version': '0.1', 'stage': 'd5d_host_acl_precheck',
        'status': DENIED, 'security_attestation': ATTESTATION,
        'vm_launched': False, 'student_code_executed': False,
        'acl_modified': False, 'checks': checks,
        # Public-safe reason codes, no account names or raw security identifiers.
        'acl_findings': {name: [] for name in NAMES},
        'still_unverified': [
            'same_windows_account_attacker', 'guest_build_descendant_containment',
            'mysql_daemon_cgroup_accounting', 'guest_root_disk_write_ceiling',
            'independent_network_and_hypervisor_assurance',
        ],
    }
    if not _windows_supported():
        result['failure_code'] = 'WINDOWS_REQUIRED'
        return result
    try:
        with Path(config_path).open('rb') as stream:
            if stream.seek(0, 2) > 100_000:
                raise ValueError('VM_CONFIG_TOO_LARGE')
            stream.seek(0)
            config = json.load(stream)
        if type(config) is not dict:
            raise ValueError('VM_CONFIG_INVALID')
        root = ManagedRuntimeRun(config).root
        registry._safe_type(root.parent, directory=True)
        registry._root(root)
        lease = root / registry.ROOT_LEASE
        registry._safe_type(lease, directory=False)
        if lease.stat().st_size != 1 or lease.read_bytes() != b'1':
            raise ValueError('INVALID_ROOT_LEASE')
        # No deletion or sweep. Unknown or pending journal entries stay intact.
        children = {p.name for p in root.iterdir()}
        if children != {registry.ROOT_KEY, registry.ROOT_LEASE}:
            raise ValueError('ROOT_HAS_UNRESOLVED_ENTRIES')
        checks['registered_root_valid'] = 'PASS'
        evidence = _read_acl_snapshot({
            'parent': root.parent, 'root': root,
            'key': root / registry.ROOT_KEY, 'lease': lease,
        })
        assessed = _assess_snapshot(evidence, findings=result['acl_findings'])
        for name, state in assessed.items():
            checks[name + '_acl'] = state
    except (OSError, ValueError, TypeError, KeyError, registry.RecoveryError,
            RuntimeWorkspaceError) as exc:
        safe_errors = {
            'ROOT_HAS_UNRESOLVED_ENTRIES', 'INVALID_ROOT_LEASE',
            'TRUSTED_POWERSHELL_UNAVAILABLE', 'ACL_INSPECTION_UNAVAILABLE',
            'ACL_INSPECTION_FAILED', 'ACL_REPORT_INVALID',
        }
        result['failure_code'] = (str(exc) if str(exc) in safe_errors
                                  else type(exc).__name__)
    if all(value == 'PASS' for value in checks.values()):
        result['status'] = SUCCESS
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='D5d: read-only Windows host ACL precheck')
    p.add_argument('--vm-config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    record = audit(args.vm_config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ProjectIQ D5d host ACL precheck - ' + record['status'])
    print('  Report: ' + str(args.out))
    return 0 if record['status'] == SUCCESS else 1


if __name__ == '__main__':
    raise SystemExit(main())
