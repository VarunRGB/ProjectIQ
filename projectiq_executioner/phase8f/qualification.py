"""Observe current host posture; never create a worker or grant permission."""
import os
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file
from ..phase2.vm_worker.orchestrator import load_config
from ..phase2.vm_worker.qemu import verify_host_toolchain
from ..phase2.vm_worker.d5c_runtime_workspace import ManagedRuntimeRun
from ..phase2.vm_worker import d5d_host_acl_audit as acl
from ..phase8c_final.storage import inspect_volume, GIB

# These are NOT operator-set booleans. They remain unresolved until an actual
# qualification backend supplies independent, current enforcement evidence.
UNRESOLVED = (
    'DEDICATED_WORKER_IDENTITY_AND_HOST_ISOLATION_UNQUALIFIED',
    'HARD_TOTAL_STORAGE_BOUND_UNQUALIFIED',
    'HYPERVISOR_AND_TOOLCHAIN_PROVENANCE_UNQUALIFIED',
    'INDEPENDENT_NETWORK_AND_CREDENTIAL_BOUNDARY_UNQUALIFIED',
    'UNTRUSTED_BUILD_AND_DESCENDANT_LIFECYCLE_UNQUALIFIED',
    'WORKER_CRASH_RECOVERY_UNQUALIFIED',
    'AUTHENTICATED_LAUNCH_AUTHORITY_NOT_IMPLEMENTED',
)


def qualify(config_path):
    config_path = Path(config_path)
    checks = dict.fromkeys(('config_stable', 'windows_whpx', 'toolchain_image',
                            'registered_root_acl', 'separate_bounded_volume',
                            'disk_headroom', 'non_elevated_token'), 'NOT_VERIFIED')
    report = {'schema_version': '8F-A.worker.1', 'checks': checks,
              'status': 'AUTOMATIC_EXECUTION_NOT_QUALIFIED',
              'runtime_permission': False, 'automatic_untrusted_execution': False,
              'vm_start_attempts': 0, 'config_modified': False,
              'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
              'qualification_blockers': list(UNRESOLVED), 'diagnostics': {}}
    try:
        before = checked_file(config_path, 100_000)
        config = load_config(config_path)
        report['config_sha256'] = before['sha256']
        checks['config_stable'] = 'OBSERVED' if checked_file(config_path, 100_000) == before else 'FAILED'
        if checks['config_stable'] != 'OBSERVED':
            raise ValueError('CONFIG_CHANGED')
    except Exception:
        report['diagnostics']['config'] = 'CONFIG_UNAVAILABLE_INVALID_OR_CHANGED'
    else:
        checks['windows_whpx'] = 'OBSERVED' if os.name == 'nt' and config.get('accelerator', 'whpx') == 'whpx' else 'FAILED'
        try:
            info = verify_host_toolchain(config)
            checks['toolchain_image'] = 'OBSERVED' if info.get('status') == 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' else 'FAILED'
            report['diagnostics']['template_sha256_matches'] = info.get('template_sha256_matches') is True
        except Exception:
            report['diagnostics']['toolchain'] = 'TOOLCHAIN_CHECK_FAILED'
        try:
            r = acl.audit(config_path)
            valid = r.get('status') == acl.SUCCESS and len(r.get('checks', {})) == 5 and all(v == 'PASS' for v in r['checks'].values())
            checks['registered_root_acl'] = 'OBSERVED' if valid else 'FAILED'
            report['diagnostics']['acl_checks'] = {k: r.get('checks', {}).get(k, 'NOT_VERIFIED') for k in ('parent_acl','root_acl','key_acl','lease_acl','registered_root_valid')}
        except Exception:
            report['diagnostics']['acl'] = 'ACL_CHECK_FAILED'
        try:
            volume = inspect_volume(ManagedRuntimeRun(config).root)
            checks['separate_bounded_volume'] = 'OBSERVED' if volume['volume_capacity_bounded'] is True and volume['separate_volume_observed'] is True else 'FAILED'
            checks['disk_headroom'] = 'OBSERVED' if volume['free_bytes'] >= 24 * GIB else 'FAILED'
            report['diagnostics']['volume'] = {k: volume[k] for k in ('total_bytes','free_bytes','separate_volume_observed','volume_capacity_bounded')}
        except Exception:
            report['diagnostics']['volume'] = 'VOLUME_CHECK_FAILED'
        if os.name == 'nt':
            try:
                import ctypes
                from ctypes import wintypes
                # Query actual token elevation; administrator group membership
                # alone is not a dedicated-account or isolation proof.
                token = wintypes.HANDLE()
                adv = ctypes.WinDLL('advapi32', use_last_error=True)
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.GetCurrentProcess.restype = wintypes.HANDLE
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                adv.OpenProcessToken.argtypes = [wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
                adv.GetTokenInformation.argtypes = [wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
                if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
                    raise OSError('TOKEN_QUERY_FAILED')
                try:
                    elevated = wintypes.DWORD(); size = wintypes.DWORD()
                    if not adv.GetTokenInformation(token,20,ctypes.byref(elevated),ctypes.sizeof(elevated),ctypes.byref(size)) or elevated.value not in (0,1):
                        raise OSError('TOKEN_QUERY_FAILED')
                    checks['non_elevated_token'] = 'OBSERVED' if elevated.value == 0 else 'FAILED'
                finally:
                    kernel.CloseHandle(token)
            except Exception:
                report['diagnostics']['token'] = 'TOKEN_QUERY_FAILED'
        try:
            if checked_file(config_path,100_000) != before:
                checks['config_stable'] = 'FAILED'
        except Exception:
            checks['config_stable'] = 'FAILED'
    report['result_sha256'] = digest(report)
    return report
