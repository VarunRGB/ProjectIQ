"""Gate 2B.2 bounded PID denial acceptance tests; offline only, no VM needed."""
import copy
import json
import unittest
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_pid_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import d5_pid_acceptance as accept


class EvidenceTests(unittest.TestCase):
    def test_parent_limit_is_eighty_and_probe_is_bounded(self):
        self.assertEqual(guest.TASK_LIMIT, 80)
        self.assertEqual(guest.FIRST_THREAD_TARGET, 30)
        self.assertEqual(guest.SECOND_THREAD_TARGET, 110)
        self.assertEqual(accept.VM_RAM_MIB, 1536)
        self.assertEqual(accept.WORKSPACE_MIB, 96)
        self.assertIn('projectiq-eval.slice', guest.WORKER)
        self.assertIn('threading.Thread', guest.WORKER)
        self.assertNotIn('subprocess.Popen', guest.WORKER)
        self.assertIn('piq-pid-', guest.WORKER)
        self.assertIn('stop_marker', guest.WORKER)

    def test_gate_requirements_only_true_for_valid_kernel_deltas(self):
        valid = {'pids_events_max_delta': 2, 'max_parent_pids_current': 80,
                 'first_scope_tasks_held': 31, 'second_scope_tasks_held': 46,
                 'scope_limits_above_parent': True,
                 'release_markers_visible_to_app': True,
                 'second_scope_events_max_delta': 0,
                 'concurrent_scopes_at_limit':True}
        self.assertTrue(guest._valid_kernel_pid_evidence(valid))
        for change in ({'pids_events_max_delta':0},
                       {'pids_events_max_delta':True},
                       {'max_parent_pids_current':78},
                       {'max_parent_pids_current':81},
                       {'first_scope_tasks_held':24},
                       {'second_scope_tasks_held':29},
                       {'scope_limits_above_parent':False},
                       {'release_markers_visible_to_app':False},
                       {'concurrent_scopes_at_limit':False}):
            with self.subTest(change=change):
                self.assertFalse(guest._valid_kernel_pid_evidence({**valid,**change}))

    def test_local_only_pids_events_supported_when_scope_is_unbounded(self):
        diag = {'pids_events_max_delta':0,'second_scope_events_max_delta':2,
                'max_parent_pids_current':80,'first_scope_tasks_held':31,
                'second_scope_tasks_held':46,'scope_limits_above_parent':True,
                'release_markers_visible_to_app':True,
                'concurrent_scopes_at_limit':True}
        self.assertTrue(guest._valid_kernel_pid_evidence(diag))
        diag['second_scope_events_max_delta']=0
        self.assertFalse(guest._valid_kernel_pid_evidence(diag))

    def test_no_lower_child_scope_task_limit(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            p=Path(td)
            for value, wanted in [('max',True),('81',True),('80',False),
                                  ('32',False),('garbage',False),('79',False)]:
                with self.subTest(value=value):
                    (p/'pids.max').write_text(value, encoding='ascii')
                    self.assertIs(guest._scope_limit_above_parent(p),wanted)

    def test_read_kernel_pids_events_requires_unique_max(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            p=Path(td)/'events'
            p.write_text('max 4\n',encoding='ascii')
            self.assertEqual(guest._read_events(p)['max'],4)
            for bad in ('oom 2\n','max -1\n','max 2\nmax 3\n','max NaN\n'):
                with self.subTest(bad=bad):
                    p.write_text(bad, encoding='ascii')
                    with self.assertRaises(ValueError):guest._read_events(p)

    def test_scope_identity_exact_and_cooperative_cleanup_required(self):
        import inspect
        implementation=inspect.getsource(guest._cleanup_scoped_workers)
        self.assertIn("re.fullmatch(r'piq-pid-[0-9a-f]{16}'",implementation)
        self.assertIn('cgroup.events',inspect.getsource(guest._scope_cleanup_state))
        self.assertIn('stop_marker_published',implementation)


def success_record():
    checks={k:'PASS' for k in guest.CHECKS}
    launch=[{'worker':i,'scope_seen':True,'max_cgroup_procs':2,
             'app_uid_seen':True,'cgroup_seen':True,'argv_seen':True,
             'wrapper_exit_code':-1,'verified':True} for i in (1,2)]
    cleanup=[{'worker':i,'kill_returncode':-2,'stop_returncode':-2,
              'scope_state':'ABSENT','remaining_tasks':0,'wrapper_exit_code_after':0,
              'stop_marker_published':True,'kill_failure_reason':'NOT_ATTEMPTED'}
             for i in (1,2)]
    d={'parent_pids_before':0,'parent_pids_after':0,
       'first_scope_tasks_held':31,'second_scope_tasks_held':47,
       'max_parent_pids_current':80,'pids_events_max_delta':3,
       'second_scope_events_max_delta':0,
       'controller_home_mode_before':448,'controller_home_mode_after':0o711,
       'verified_worker_count':2,'cooperative_stop_markers_published':2,
       'release_markers_visible_to_app':True,'scope_limits_above_parent':True,
       'scope_cleanup_observed':True,'concurrent_scopes_at_limit':True,
       'probe_stage':'KERNEL_COUNTERS_COLLECTED',
       'stress_monitor_outcome':'KERNEL_PID_DENIAL',
       'worker_launch_observations':launch,
       'scope_cleanup_observations':cleanup,
       'wrapper_exit_codes_before_cleanup':[-1,-1]}
    return {'schema_version':'0.1','checks':checks,'diagnostics':d}


class ValidatorTests(unittest.TestCase):
    def parse(self,data):
        return accept._parse_guest(json.dumps(data).encode('utf-8'))

    def test_complete_evidence_accepted(self):
        self.assertIsNotNone(self.parse(success_record()))

    def test_forged_success_without_kernel_denial_rejected(self):
        for field,value in [('pids_events_max_delta',0),('max_parent_pids_current',78),
                            ('first_scope_tasks_held',24),('second_scope_tasks_held',29),
                            ('scope_limits_above_parent',False),
                            ('release_markers_visible_to_app',False),
                            ('stress_monitor_outcome','MONITOR_TIMEOUT'),
                            ('concurrent_scopes_at_limit',False)]:
            with self.subTest(field=field):
                data=success_record();data['diagnostics'][field]=value
                self.assertIsNone(self.parse(data))

    def test_forged_worker_or_cleanup_pass_rejected(self):
        for mutate in (
            lambda r:r['diagnostics']['worker_launch_observations'][1].update(verified=False),
            lambda r:r['diagnostics']['worker_launch_observations'][0].update(argv_seen=False),
            lambda r:r['diagnostics']['scope_cleanup_observations'][1].update(scope_state='POPULATED'),
            lambda r:r['diagnostics']['scope_cleanup_observations'][1].update(remaining_tasks=1),
            lambda r:r['diagnostics']['scope_cleanup_observations'][1].update(stop_marker_published=False),
            lambda r:r['diagnostics'].update(cooperative_stop_markers_published=1),
            lambda r:r['diagnostics'].update(scope_cleanup_observed=False),
            lambda r:r['diagnostics'].update(parent_pids_after=4),
            lambda r:r['diagnostics'].update(verified_worker_count=1),
        ):
            with self.subTest(mutate=mutate):
                data=success_record();mutate(data)
                self.assertIsNone(self.parse(data))

    def test_extra_fields_or_pids_data_injection_rejected(self):
        data=success_record();data['diagnostics']['cmdline']='student data'
        self.assertIsNone(self.parse(data))
        data=success_record();data['diagnostics']['probe_error']='<script>bad</script>'
        self.assertIsNone(self.parse(data))
        data=success_record();data['checks']['kernel_pids_limit_denied']='COMPLETED'
        self.assertIsNone(self.parse(data))
        data=success_record();data['checks']['kernel_pids_limit_denied']=True
        self.assertIsNone(self.parse(data))

    def test_empty_or_oversized_output_rejected(self):
        self.assertIsNone(accept._parse_guest(b''))
        self.assertIsNone(accept._parse_guest(b'x'*6501))
        self.assertIsNone(accept._parse_guest(b'{garbled'))

    def test_unverified_template_accepted_only_as_unverified(self):
        report={'schema_version':'0.1','checks':{x:'NOT_VERIFIED' for x in guest.CHECKS},
                'diagnostics':{}}
        self.assertIsNotNone(self.parse(report))

    def test_explicit_optin_is_required_and_no_vm_launched(self):
        config={}
        with patch.object(accept,'verify_host_toolchain',return_value={
                'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(accept,'QemuWorker') as vm:
            first=accept.run(config,run_clean_vm=False)
            second=accept.run(config,run_clean_vm=True,acknowledge_pid_stress=False)
            self.assertEqual(first['status'],'INSPECTION_ONLY_NOT_ATTESTED')
            self.assertEqual(second['status'],'PID_STRESS_CONFIRMATION_REQUIRED')
            vm.assert_not_called()
            self.assertEqual(second['security_attestation'],
                             'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')

    def test_worker_recovery_without_cleanup_proof_rejected(self):
        data=success_record()
        data['checks']['worker_scopes_cleaned']='FAIL'
        self.assertIsNone(self.parse(data))

if __name__=='__main__':unittest.main()
