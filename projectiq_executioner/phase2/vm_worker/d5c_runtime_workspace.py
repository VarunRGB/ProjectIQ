"""D5c Gate 4: enrol the real reviewed-fixture runner in the managed VM registry.

Only Windows/WHPX runs are supported by the verified host process-lifetime
mechanism. Ownership is operator-managed, not student-controlled. This module
is deliberately fail-closed and is NOT arbitrary untrusted-code attestation.
"""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path

from . import d5c_orphan_registry as registry


def _windows_runtime_supported() -> bool:
    return os.name == 'nt'


class RuntimeWorkspaceError(RuntimeError):
    """Managed runtime is not proven safe to create or recover."""


class ManagedRuntimeRun:
    """Tracks VM identity, live ownership and post-stop deletion.

    open() holds an exclusive OS lease until close(). Only after that lease
    is released may the recovery engine remove this run's files. The caller
    must stop QEMU before close(), and must inspect cleanup_verified.
    """

    def __init__(self, config: dict, work_root: Path | None = None):
        value = config.get('managed_run_root')
        if value is not None:
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                raise RuntimeWorkspaceError('INVALID_MANAGED_RUN_ROOT')
            root = Path(value)
        elif work_root is not None:
            root = Path(work_root).absolute() / 'projectiq_d5c_managed_runtime'
        else:
            root = (Path(__file__).resolve().parents[3] / 'outputs' / 'executioner'
                    / 'projectiq_d5c_managed_runtime')
        self.root = root.absolute()
        binary = config.get('qemu_binary') or shutil.which('qemu-system-x86_64')
        if not binary or not isinstance(binary, str):
            raise RuntimeWorkspaceError('PINNED_QEMU_BINARY_UNAVAILABLE')
        resolved = shutil.which(binary) or binary
        self.image = str(Path(resolved).resolve())
        self.folder: Path | None = None
        self._held = None
        self._phase = 'NOT_STARTED'
        self._pid = 0
        self._born = 0
        self.cleanup_verified = False
        self.cleanup_reason = 'NOT_ATTEMPTED'
        self.previous_sweep: list[dict] = []

    def open(self) -> Path:
        if not _windows_runtime_supported():
            raise RuntimeWorkspaceError('WINDOWS_LIFETIME_GUARD_REQUIRED')
        if self.folder is not None:
            raise RuntimeWorkspaceError('WORKSPACE_ALREADY_OPEN')
        try:
            if not self.root.exists():
                # Do not silently create a user-supplied arbitrary parent tree.
                if not self.root.parent.is_dir():
                    raise RuntimeWorkspaceError('MANAGED_PARENT_NOT_INITIALIZED')
                try:
                    registry.initialize_root(self.root)
                except FileExistsError:
                    # A concurrent initializer may have created the root.
                    # Require its complete authenticated key; no auto-adoption.
                    registry._root(self.root)
            registry._root(self.root)
            self.previous_sweep = registry.recover_managed_root(self.root, self.image)
            if any(x['status'] == 'MANUAL_REVIEW' for x in self.previous_sweep):
                raise RuntimeWorkspaceError('UNRESOLVED_MANAGED_RUN_REQUIRES_REVIEW')
            # Registration and acquisition are atomic relative to all sweeps.
            # Never let another controller observe a fresh, unleased VM run.
            folder, lease = registry.create_leased_run(self.root)
            self.folder = folder
            self._held = lease
            self._phase = 'PREPARING'
            return folder
        except (OSError, ValueError, registry.RecoveryError) as exc:
            raise RuntimeWorkspaceError('MANAGED_WORKSPACE_SETUP_FAILED') from exc

    def mark_launching(self) -> None:
        if self.folder is None or self._held is None or self._phase != 'PREPARING':
            raise RuntimeWorkspaceError('INVALID_LAUNCH_STATE')
        registry.update_run(self.root, self.folder, 'LAUNCHING')
        self._phase = 'LAUNCHING'

    def mark_launched(self, proc) -> None:
        if self.folder is None or self._held is None or self._phase != 'LAUNCHING':
            raise RuntimeWorkspaceError('INVALID_PROCESS_STATE')
        if proc is None or proc.poll() is not None or type(proc.pid) is not int:
            raise RuntimeWorkspaceError('QEMU_NOT_LIVE_FOR_RECORDING')
        # The shared QemuWorker.start() already verified exact job membership;
        # verify it again on the same process handle immediately before signing.
        from .d5c_windows_job import verify_child_in_job
        if not verify_child_in_job(proc):
            raise RuntimeWorkspaceError('QEMU_NOT_IN_WINDOWS_JOB')
        born = registry.windows_creation_time(proc)
        registry.update_run(self.root, self.folder, 'LAUNCHED',
                            pid=proc.pid, born=born, image=self.image)
        self._pid, self._born, self._phase = proc.pid, born, 'LAUNCHED'

    def mark_booted(self) -> None:
        if self.folder is None or self._phase != 'LAUNCHED':
            raise RuntimeWorkspaceError('INVALID_BOOT_STATE')
        registry.update_run(self.root, self.folder, 'BOOTED',
                            pid=self._pid, born=self._born, image=self.image)
        self._phase = 'BOOTED'

    def mark_stopped(self) -> None:
        if self.folder is None or self._phase not in ('LAUNCHED', 'BOOTED'):
            raise RuntimeWorkspaceError('INVALID_STOP_STATE')
        registry.update_run(self.root, self.folder, 'STOPPED',
                            pid=self._pid, born=self._born, image=self.image)
        self._phase = 'STOPPED'

    def close(self) -> None:
        """Release live ownership and independently verify removal.

        Never claim cleanup on an exception, active QEMU, unsafe path or
        unsupported phase. Unexpected failures are preserved for manual review.
        """
        if self.folder is None:
            self.cleanup_reason = 'WORKSPACE_NOT_OPEN'
            return
        try:
            # Keep root ownership across per-run lease release AND deletion.
            # Otherwise a peer could delete this run between those operations,
            # giving the original controller a spurious cleanup failure.
            with registry.managed_root_lease(self.root):
                if self._held is not None:
                    lease = self._held
                    self._held = None
                    lease.__exit__(None, None, None)
                outcomes = registry._recover_managed_root_locked(self.root, self.image)
                entry = next((x for x in outcomes if x['run'] == self.folder.name), None)
                if (entry and entry['status'] == 'RECOVERED'
                        and not self.folder.exists()):
                    self.cleanup_verified = True
                    self.cleanup_reason = 'VERIFIED_REMOVED'
                else:
                    self.cleanup_reason = entry['reason'] if entry else 'RUN_NOT_ACCOUNTED_FOR'
        except (OSError, ValueError, registry.RecoveryError) as exc:
            self.cleanup_reason = (str(exc) if isinstance(exc, registry.RecoveryError)
                                   else type(exc).__name__)
        finally:
            if self._held is not None:
                # Root-lock failure cannot leave a live run lease held forever.
                lease, self._held = self._held, None
                try:
                    lease.__exit__(None, None, None)
                except (OSError, ValueError, registry.RecoveryError):
                    self.cleanup_reason = 'LEASE_RELEASE_UNVERIFIED'

    @contextmanager
    def hold(self):
        if self._held is None or self.folder is None:
            raise RuntimeWorkspaceError('WORKSPACE_LEASE_NOT_HELD')
        try:
            yield self.folder
        finally:
            self.close()
