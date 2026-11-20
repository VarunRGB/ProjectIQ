"""Gate 2B.1 post-OOM systemd-scope cleanup regression tests (no VM)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_memory_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import d5_memory_acceptance as host


class FakeProcess:
    def __init__(self):
        self._exit = None
        self.calls = []

    def poll(self):
        return self._exit

    def kill(self):
        self.calls.append('kill')
        self._exit = -9

    def wait(self, timeout=None):
        self.calls.append('wait')
        return self._exit


class CleanupTests(unittest.TestCase):
    def test_missing_scope_is_absent_and_symlink_is_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(guest._scope_cleanup_state(root / 'missing'), ('ABSENT', 0))
            if os.name != 'nt':
                node = root / 'link'
                node.symlink_to(root, target_is_directory=True)
                self.assertEqual(guest._scope_cleanup_state(node), ('UNKNOWN', 0))

    def test_no_descendant_tasks_required_for_drained(self):
        with tempfile.TemporaryDirectory() as td:
            node = Path(td)
            (node / 'cgroup.procs').write_text('')
            (node / 'cgroup.events').write_text('populated 0\nfrozen 0\n')
            self.assertEqual(guest._scope_cleanup_state(node), ('DRAINED', 0))
            (node / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
            self.assertEqual(guest._scope_cleanup_state(node), ('POPULATED', 0))
            (node / 'cgroup.procs').write_text('7\n')
            self.assertEqual(guest._scope_cleanup_state(node), ('POPULATED', 1))
            (node / 'cgroup.events').write_text('frozen 0\n')
            self.assertEqual(guest._scope_cleanup_state(node), ('UNKNOWN', 1))

    def _workers(self, root):
        return [(FakeProcess(), 'piq-mem-1111111111111111',
                 root / 'piq-mem-1111111111111111.scope', root / '.release-a'),
                (FakeProcess(), 'piq-mem-2222222222222222',
                 root / 'piq-mem-2222222222222222.scope', root / '.release-b')]

    def test_cooperative_stop_cleans_both_without_systemd_kill(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workers = self._workers(root)
            # Models exact trusted controller paths: home/marker and slice.
            unit1, unit2 = workers[0][1], workers[1][1]
            releases = [guest.CONTROLLER_HOME / ('.projectiq-mem-release-' +
                         u.removeprefix('piq-mem-')) for u in (unit1, unit2)]
            workers = [(p,u,guest.CGROUP/'projectiq-eval.slice'/(u+'.scope'),r)
                       for (p,u,_path,_release),r in zip(workers,releases)]
            created=[]
            def publish(path, uid): created.append(path)
            diag={}
            with patch.object(guest,'_publish_release',side_effect=publish), \
                 patch.object(guest,'_scope_cleanup_state',return_value=('ABSENT',0)), \
                 patch.object(guest,'_scope_kill_command') as kill, \
                 patch.object(guest,'_scope_command') as stop:
                self.assertTrue(guest._cleanup_scoped_workers(workers,{},diag,1000))
            self.assertEqual(len(created),2)
            kill.assert_not_called(); stop.assert_not_called()
            self.assertEqual(diag['cooperative_stop_markers_published'],2)
            self.assertEqual([r['scope_state'] for r in diag['scope_cleanup_observations']],
                             ['ABSENT','ABSENT'])

    def test_unresponsive_worker_fallback_requires_kernel_drain(self):
        with tempfile.TemporaryDirectory() as td:
            workers=self._workers(Path(td))
            workers=[(p,u,guest.CGROUP/'projectiq-eval.slice'/(u+'.scope'),
                      guest.CONTROLLER_HOME / ('.projectiq-mem-release-'+u[8:]))
                     for p,u,_path,_release in workers]
            with patch.object(guest,'_publish_release'), \
                 patch.object(guest,'_scope_cleanup_state',return_value=('POPULATED',1)), \
                 patch.object(guest,'_scope_kill_command',return_value=(1,'OTHER_ERROR')) as kill, \
                 patch.object(guest,'_scope_command',return_value=0) as stop, \
                 patch.object(guest.time,'monotonic',side_effect=[0,7,8,21]), \
                 patch.object(guest.time,'sleep'):
                diag={}
                self.assertFalse(guest._cleanup_scoped_workers(workers,{},diag,1000))
            self.assertEqual(kill.call_count,2)
            self.assertEqual(stop.call_count,2)
            self.assertEqual([r['remaining_tasks'] for r in diag['scope_cleanup_observations']], [1,1])

    def test_worker_checks_trusted_stop_before_and_during_stress(self):
        text=guest.WORKER
        self.assertIn('stop_marker=marker.with_name(marker.name',text)
        self.assertEqual(text.count('if stop_requested(): sys.exit(0)'),3)
        self.assertIn('st.st_uid!=controller',text)
        self.assertIn('st.st_mode & 0o022',text)

    def test_kill_reason_is_bounded_enum(self):
        from types import SimpleNamespace
        for message, expected in [(b'unrecognized option --kill-whom','UNSUPPORTED_OPTION'),
                                  (b'Unit is not loaded','UNIT_NOT_FOUND'),
                                  (b'access denied','PERMISSION_DENIED'),
                                  (b'misc diagnostic 12345','OTHER_ERROR')]:
            with patch.object(guest.subprocess,'run',return_value=SimpleNamespace(
                    returncode=1,stderr=message)):
                rc,reason=guest._scope_kill_command(['/usr/bin/systemctl'],{})
            self.assertEqual((rc,reason),(1,expected))

    def test_unexpected_unit_identity_denied_before_kill(self):
        with tempfile.TemporaryDirectory() as td:
            workers = self._workers(Path(td))
            workers[0] = (workers[0][0], 'other.service', workers[0][2], workers[0][3])
            with patch.object(guest, '_publish_release') as publish, self.assertRaises(ValueError):
                guest._cleanup_scoped_workers(workers, {}, {}, 1000)
            publish.assert_not_called()

    def test_report_parser_accepts_verified_cleanup_observations(self):
        checks = {c: 'NOT_VERIFIED' for c in guest.CHECKS}
        checks['worker_scopes_cleaned'] = 'PASS'
        checks['two_unprivileged_scoped_workers'] = 'PASS'
        row = {'worker': 1, 'kill_returncode': 0, 'stop_returncode': 0,
               'scope_state': 'ABSENT', 'remaining_tasks': 0,
               'wrapper_exit_code_after': -9,
               'stop_marker_published': True, 'kill_failure_reason':'NONE'}
        obs = [row, dict(row, worker=2, scope_state='DRAINED')]
        worker = {'worker': 1, 'scope_seen': True, 'max_cgroup_procs': 2,
                  'app_uid_seen': True, 'cgroup_seen': True, 'argv_seen': True,
                  'wrapper_exit_code': -1, 'verified': True}
        diagnostic = {'scope_cleanup_observations': obs, 'scope_cleanup_observed': True,
                      'cooperative_stop_markers_published': 2,
                      'verified_worker_count': 2,
                      'worker_launch_observations': [worker, dict(worker, worker=2)]}
        record = {'schema_version': '0.1', 'checks': checks, 'diagnostics': diagnostic}
        self.assertIsNotNone(host._parse_guest(json.dumps(record).encode()))
        for bad in (
            [dict(row, scope_state='POPULATED'), obs[1]],
            [dict(row, remaining_tasks=True), obs[1]],
            [dict(row, kill_returncode='0'), obs[1]],
            [dict(row, wrapper_exit_code_after=-1), obs[1]],
            [dict(row, scope_state='UNKNOWN'), obs[1]],
            [obs[0]],
            [dict(row, stop_marker_published='yes'), obs[1]],
            [dict(row, kill_failure_reason='fake'), obs[1]],
        ):
            with self.subTest(bad=bad):
                record['diagnostics'] = dict(diagnostic, scope_cleanup_observations=bad)
                self.assertIsNone(host._parse_guest(json.dumps(record).encode()))

    def test_report_parser_rejects_cleanup_claim_without_observations(self):
        checks = {c: 'NOT_VERIFIED' for c in guest.CHECKS}
        checks['worker_scopes_cleaned'] = 'PASS'
        checks['two_unprivileged_scoped_workers'] = 'PASS'
        row = {'worker': 1, 'scope_seen': True, 'max_cgroup_procs': 2,
               'app_uid_seen': True, 'cgroup_seen': True, 'argv_seen': True,
               'wrapper_exit_code': -1, 'verified': True}
        r = {'schema_version': '0.1', 'checks': checks, 'diagnostics': {
             'scope_cleanup_observed': True,
                      'cooperative_stop_markers_published': 2, 'verified_worker_count': 2,
             'worker_launch_observations': [row, dict(row, worker=2)]}}
        self.assertIsNone(host._parse_guest(json.dumps(r).encode()))


if __name__ == '__main__':
    unittest.main()
