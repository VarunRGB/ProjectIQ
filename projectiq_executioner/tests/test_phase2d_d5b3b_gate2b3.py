"""Gate 2B.3 actual write-throttling gate: offline negative evidence tests."""
import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_io_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import d5_io_acceptance as accept


def success_record():
    checks={name:'PASS' for name in guest.CHECKS}
    launches=[{'worker':i,'scope_seen':True,'max_cgroup_procs':2,
               'app_uid_seen':True,'cgroup_seen':True,'argv_seen':True,
               'wrapper_exit_code':-1,'verified':True} for i in (1,2)]
    cleaned=[{'worker':i,'kill_returncode':-2,'stop_returncode':-2,
              'scope_state':'ABSENT','remaining_tasks':0,
              'wrapper_exit_code_after':0,'stop_marker_published':True,
              'kill_failure_reason':'NOT_ATTEMPTED'} for i in (1,2)]
    baseline=64*1024*1024
    parent=128*1024*1024
    d={'baseline_launch_verified':True,'baseline_scope_cleaned':True,
       'baseline_cleanup_scope_state':'ABSENT','baseline_cleanup_remaining_tasks':0,
       'baseline_cleanup_wrapper_exit_code':0,
       'baseline_cleanup_stop_marker_published':True,
       'baseline_wbytes_delta':baseline,'baseline_scope_wbytes':baseline,
       'baseline_elapsed_ms':2000,'baseline_bps':baseline*1000//2000,
       'baseline_outcome':'FASTER_THAN_EVAL_LIMIT',
       'verified_worker_count':2,'first_scope_wbytes':baseline,
       'second_scope_wbytes':baseline,'eval_parent_wbytes_delta':parent,
       'eval_elapsed_ms':8000,'eval_aggregate_bps':parent*1000//8000,
       'concurrent_scope_io_observed':True,'release_markers_visible_to_app':True,
       'stress_monitor_outcome':'BLOCK_IO_WRITES_ACCOUNTED',
       'controller_home_mode_before':448,'controller_home_mode_after':0o711,
       'scope_cleanup_observed':True,'cooperative_stop_markers_published':2,
       'workspace_free_before':220*1024*1024,'workspace_free_after':219*1024*1024,
       'workspace_files_removed':True,'probe_stage':'KERNEL_COUNTERS_COLLECTED',
       'worker_launch_observations':launches,
       'scope_cleanup_observations':cleaned}
    return {'schema_version':'0.1','checks':checks,'diagnostics':d}


class IOEvidenceTests(unittest.TestCase):
    def test_fixed_policy_and_bounded_direct_io(self):
        self.assertEqual(guest.WRITE_BPS,16*1024*1024)
        self.assertEqual(guest.WORKER_BYTES,64*1024*1024)
        self.assertEqual(guest.BASELINE_MIN_BPS,24*1024*1024)
        self.assertEqual(guest.THROTTLED_MAX_BPS,20*1024*1024)
        self.assertEqual(accept.WORKSPACE_MIB,256)
        self.assertEqual(accept.VM_RAM_MIB,1536)
        self.assertIn('os.O_DIRECT',guest.WORKER)
        self.assertIn('mmap.mmap',guest.WORKER)
        self.assertIn('os.fdatasync',guest.WORKER)
        self.assertIn('os.O_EXCL',guest.WORKER)
        self.assertNotIn('subprocess.Popen',guest.WORKER)
        compile(guest.WORKER,'trusted fixed worker','exec')

    def test_evidence_gate_requires_both_scopes_and_faster_baseline(self):
        d=success_record()['diagnostics']
        self.assertTrue(guest._valid_io_evidence(d))
        failures=[('baseline_wbytes_delta',0),('baseline_bps',16*1024*1024),
                  ('eval_parent_wbytes_delta',64*1024*1024),
                  ('eval_elapsed_ms',1000),('eval_aggregate_bps',27*1024*1024),
                  ('first_scope_wbytes',4*1024*1024),
                  ('second_scope_wbytes',4*1024*1024),
                  ('concurrent_scope_io_observed',False),
                  ('release_markers_visible_to_app',False)]
        for k,v in failures:
            with self.subTest(field=k):
                self.assertFalse(guest._valid_io_evidence({**d,k:v}))
        self.assertFalse(guest._valid_io_evidence({**d,'baseline_bps':True}))

    def test_io_stat_requires_device_specific_wbytes_wios(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);f=folder/'io.stat'
            f.write_text('8:0 rbytes=12 wbytes=67108864 wios=64\n8:16 wbytes=9 wios=1\n')
            self.assertEqual(guest._io_stat(folder,'8:0'),(67108864,64))
            self.assertEqual(guest._io_stat(folder,'8:99'),(0,0))
            for bad in ('8:0 wbytes=5\n','8:0 wbytes=-8 wios=1\n',
                        '8:0 wbytes=5 wbytes=8 wios=1\n',
                        '8:0 wbytes=5 wios=1\n8:0 wbytes=6 wios=1\n'):
                with self.subTest(text=bad):
                    f.write_text(bad)
                    with self.assertRaises(ValueError):guest._io_stat(folder,'8:0')

    def test_unthrottled_baseline_must_not_have_nested_wbps_cap(self):
        with tempfile.TemporaryDirectory() as td:
            parent=Path(td);scope=parent/'piq-io-test.scope';scope.mkdir()
            for node in (scope,parent):
                (node/'io.max').write_text('8:0 wbps=max\n',encoding='ascii')
            self.assertTrue(guest._no_tighter_io_cap(scope,'8:0'))
            (scope/'io.max').write_text('8:0 wbps=16777216\n')
            self.assertFalse(guest._no_tighter_io_cap(scope,'8:0'))
            (scope/'io.max').write_text('8:0 wbps=max\n')
            (parent/'io.max').write_text('8:0 wbps=16777216\n')
            self.assertFalse(guest._no_tighter_io_cap(scope,'8:0'))

    def test_fixed_bounded_ext4_workspace_is_required(self):
        code=inspect.getsource(guest._workspace_mount_ok)
        self.assertIn("'ext4'",code)
        self.assertIn("'nosuid'",code)
        self.assertIn("'nodev'",code)
        self.assertIn('WORKSPACE_MIB',code)
        self.assertIn("'projectiq_exec'",code)
        self.assertIn('WORKSPACE.is_symlink()',code)

    def test_trusted_cleanup_uses_scope_not_wrapper(self):
        code=inspect.getsource(guest._cleanup_scoped_workers)
        self.assertIn("r'piq-io-[0-9a-f]{16}'",code)
        self.assertIn('stop_marker_published',code)
        self.assertIn('_scope_cleanup_state',code)
        self.assertIn('cgroup.events',inspect.getsource(guest._scope_cleanup_state))
        self.assertIn("'/usr/bin/systemctl'",code)

    def test_timekeeping_starts_before_release_and_preflight_is_separate(self):
        source=inspect.getsource(guest._test_io)
        self.assertIn('_verify_app_marker_visibility(uid,env)',source)
        self.assertEqual(source.count('_app_can_read_release('),0)
        baseline=source[source.index('release=worker[3]'):source.index('elapsed=max(1,round(1000*',source.index('release=worker[3]'))]
        self.assertLess(baseline.index('started=time.monotonic()'),
                        baseline.index('_publish_release(release,uid)'))
        evaluate=source[source.index("diag['probe_stage']='EVAL_RELEASE_VISIBILITY'"):]
        self.assertLess(evaluate.index('started=time.monotonic()'),
                        evaluate.index('_publish_release(release,uid)'))

    def test_worker_launch_scope_is_checked_independently(self):
        code=inspect.getsource(guest._launch_worker)
        self.assertIn('_scope_snapshot',code)
        self.assertIn('argv_seen',code)
        self.assertIn('projectiq_run',code)
        self.assertIn('systemd-run',code)
        self.assertIn('IOAccounting=yes',code)


class BaselinePreflightTests(unittest.TestCase):
    def test_iostat_allows_well_formed_optional_controller_fields(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            (folder/'io.stat').write_text(
                '8:0 rbytes=12 wbytes=67108864 wios=64 '
                'avg_lat=12.5 cost.usage=42.7 qos_hint=ready\n',
                encoding='ascii')
            self.assertEqual(guest._io_stat(folder,'8:0'),(67108864,64))

    def test_prewrite_device_row_without_write_keys_starts_at_zero(self):
        # The guest's BASELINE_IOSTAT_INVALID failure occurred before any
        # baseline worker launched. Other io.stat device fields are not write
        # evidence, and their presence must not block a genuine zero baseline.
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            f=folder/'io.stat'
            for raw in ('8:0 rbytes=4096 rios=1\n',
                        '8:0 dbytes=1024 dios=1 cost.usage=0.0\n',
                        '8:0 rbytes=0 rios=0 qos_hint=ready\n'):
                with self.subTest(raw=raw):
                    f.write_text(raw,encoding='ascii')
                    self.assertEqual(guest._io_stat(folder,'8:0'),(0,0))

    def test_zero_prewrite_counter_can_be_followed_by_real_write_stats(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            f=folder/'io.stat'
            f.write_text('8:0 rbytes=4096 rios=1\n',encoding='ascii')
            before=guest._io_stat(folder,'8:0')[0]
            f.write_text('8:0 rbytes=4096 rios=1 wbytes=67108864 wios=64\n',encoding='ascii')
            after=guest._io_stat(folder,'8:0')[0]
            self.assertEqual(after-before,guest.BASELINE_BYTES)

    def test_read_only_preflight_reaches_baseline_worker_launch(self):
        with tempfile.TemporaryDirectory() as td:
            parent=Path(td)/'projectiq.slice'
            parent.mkdir()
            (parent/'io.stat').write_text('8:0 rbytes=512 rios=1\n',encoding='ascii')
            with patch.object(guest,'_device_id',return_value='8:0'), \
                 patch.object(guest,'_no_tighter_io_cap',return_value=True), \
                 patch.object(guest,'_workspace_free',return_value=210*1024*1024), \
                 patch.object(guest,'_verify_app_marker_visibility',return_value=True), \
                 patch.object(guest,'_launch_worker',side_effect=ValueError('synthetic stop')) as launch:
                diag={}
                checks={key:'NOT_VERIFIED' for key in guest.CHECKS}
                with self.assertRaisesRegex(ValueError,'synthetic stop'):
                    guest._test_io(parent/'projectiq-eval.slice',1000,1001,{},diag,checks)
                launch.assert_called_once()
                self.assertEqual(diag['probe_stage'],'BASELINE_WORKER_LAUNCHING')
                self.assertEqual(checks['baseline_scope_cleaned'],'NOT_VERIFIED')

    def test_partial_write_counters_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            f=folder/'io.stat'
            for raw in ('8:0 rbytes=1 wbytes=67108864\n',
                        '8:0 rios=1 wios=2\n',
                        '8:0 rbytes=3 wbytes=??? wios=10\n'):
                with self.subTest(raw=raw):
                    f.write_text(raw,encoding='ascii')
                    with self.assertRaises(guest.IOStatFormatError):
                        guest._io_stat(folder,'8:0')

    def test_empty_device_record_is_zero_before_any_disk_write(self):
        # The kernel can expose the device's row before counters appear.
        # A zero baseline alone must not count as proof of throttling.
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            (folder/'io.stat').write_text('8:0\n', encoding='ascii')
            self.assertEqual(guest._io_stat(folder,'8:0'),(0,0))
            (folder/'io.stat').write_text('8:0 wbytes=67108864 wios=64\n',encoding='ascii')
            self.assertEqual(guest._io_stat(folder,'8:0'),(67108864,64))

    def test_iostat_malformed_token_classes_are_distinct_and_bounded(self):
        cases={
          'UNEXPECTED_BARE_TOKEN':'8:0 unexpected\n',
          'DUPLICATE_FIELD':'8:0 rbytes=1 rbytes=2\n',
          'INVALID_OPTIONAL_KEY':'8:0 UpperKey=1\n',
          'INVALID_OPTIONAL_VALUE':'8:0 rios=\n',
        }
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)
            for reason, row in cases.items():
                with self.subTest(reason=reason):
                    (path/'io.stat').write_text(row,encoding='ascii')
                    with self.assertRaises(guest.IOStatFormatError) as cm:
                        guest._io_stat(path,'8:0')
                    self.assertEqual(str(cm.exception),reason)

    def test_host_accepts_only_fixed_classified_format_diagnostics(self):
        for reason in ('UNEXPECTED_BARE_TOKEN','DUPLICATE_FIELD',
                       'INVALID_OPTIONAL_KEY','INVALID_OPTIONAL_VALUE'):
            with self.subTest(reason=reason):
                report={'schema_version':'0.1',
                        'checks':{key:'NOT_VERIFIED' for key in guest.CHECKS},
                        'diagnostics':{'probe_stage':'BASELINE_IOSTAT_INVALID',
                                       'baseline_io_stat_problem':reason}}
                self.assertIsNotNone(accept._parse_guest(json.dumps(report).encode('utf-8')))
        report['diagnostics']['baseline_io_stat_problem']='UNTRUSTED_UNKNOWN'
        self.assertIsNone(accept._parse_guest(json.dumps(report).encode('utf-8')))

    def test_iostat_required_counters_remain_strict(self):
        cases={
          'INVALID_REQUIRED_COUNTER':'8:0 wbytes=7.5 wios=1\n',
          'MISSING_REQUIRED_COUNTER':'8:0 wbytes=7\n',
          'DUPLICATE_FIELD':'8:0 wbytes=7 wios=1 wbytes=8\n',
          'DUPLICATE_DEVICE':'8:0 wbytes=1 wios=1\n8:0 wbytes=2 wios=2\n',
        }
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            for reason, value in cases.items():
                with self.subTest(reason=reason):
                    (folder/'io.stat').write_text(value,encoding='ascii')
                    with self.assertRaises(guest.IOStatFormatError) as cm:
                        guest._io_stat(folder,'8:0')
                    self.assertEqual(str(cm.exception),reason)

    def test_baseline_preflight_failure_does_not_claim_cleanup_failure(self):
        with patch.object(guest,'_device_id',return_value='8:0'), \
             patch.object(guest,'_no_tighter_io_cap',return_value=True), \
             patch.object(guest,'_workspace_free',return_value=210*1024*1024), \
             patch.object(guest,'_verify_app_marker_visibility',return_value=True), \
             patch.object(guest,'_io_stat',side_effect=guest.IOStatFormatError('MALFORMED_FIELD')):
            diag={};checks={key:'NOT_VERIFIED' for key in guest.CHECKS}
            guest._test_io(Path('/fake/projectiq.slice/projectiq-eval.slice'),
                           1000,1001,{},diag,checks)
            self.assertEqual(diag['probe_stage'],'BASELINE_IOSTAT_INVALID')
            self.assertEqual(diag['baseline_io_stat_problem'],'MALFORMED_FIELD')
            self.assertEqual(checks['baseline_scope_cleaned'],'NOT_VERIFIED')
            self.assertNotIn('baseline_scope_cleaned',diag)

    def test_launch_failure_stage_distinguished_from_counter_preflight(self):
        with patch.object(guest,'_device_id',return_value='8:0'), \
             patch.object(guest,'_no_tighter_io_cap',return_value=True), \
             patch.object(guest,'_workspace_free',return_value=210*1024*1024), \
             patch.object(guest,'_verify_app_marker_visibility',return_value=True), \
             patch.object(guest,'_io_stat',return_value=(0,0)), \
             patch.object(guest,'_launch_worker',side_effect=ValueError('synthetic')):
            diag={};checks={key:'NOT_VERIFIED' for key in guest.CHECKS}
            with self.assertRaises(ValueError):
                guest._test_io(Path('/fake/projectiq.slice/projectiq-eval.slice'),
                               1000,1001,{},diag,checks)
            self.assertEqual(diag['probe_stage'],'BASELINE_WORKER_LAUNCHING')
            self.assertEqual(checks['baseline_scope_cleaned'],'NOT_VERIFIED')

    def test_host_parser_accepts_only_bounded_baseline_failure_reason(self):
        checks={key:'NOT_VERIFIED' for key in guest.CHECKS}
        good={'schema_version':'0.1','checks':checks,
              'diagnostics':{'probe_stage':'BASELINE_IOSTAT_INVALID',
                             'baseline_io_stat_problem':'MALFORMED_FIELD'}}
        self.assertIsNotNone(accept._parse_guest(json.dumps(good).encode()))
        bad=json.loads(json.dumps(good))
        bad['diagnostics']['baseline_io_stat_problem']='../../untrusted'
        self.assertIsNone(accept._parse_guest(json.dumps(bad).encode()))


class HostValidatorTests(unittest.TestCase):
    @staticmethod
    def parse(data):
        return accept._parse_guest(json.dumps(data).encode('utf-8'))

    def test_complete_evidence_accepted(self):
        self.assertIsNotNone(self.parse(success_record()))

    def test_forged_measurements_are_rejected(self):
        mutations=[('baseline_wbytes_delta',0),('baseline_bps',16*1024*1024),
                   ('baseline_elapsed_ms',999999),('baseline_scope_wbytes',0),
                   ('baseline_launch_verified',False),('baseline_scope_cleaned',False),
                   ('baseline_cleanup_scope_state','POPULATED'),
                   ('baseline_cleanup_remaining_tasks',1),
                   ('baseline_cleanup_wrapper_exit_code',-1),
                   ('baseline_cleanup_stop_marker_published',False),
                   ('eval_parent_wbytes_delta',0),('eval_elapsed_ms',100),
                   ('eval_aggregate_bps',128*1024*1024),
                   ('first_scope_wbytes',0),('second_scope_wbytes',0),
                   ('concurrent_scope_io_observed',False),
                   ('stress_monitor_outcome','MONITOR_TIMEOUT'),
                   ('release_markers_visible_to_app',False)]
        for key,value in mutations:
            with self.subTest(field=key):
                report=success_record();report['diagnostics'][key]=value
                self.assertIsNone(self.parse(report))

    def test_cleanup_and_workspace_recovery_cannot_be_forged(self):
        mutations=(
            lambda r:r['diagnostics']['scope_cleanup_observations'][1].update(scope_state='POPULATED'),
            lambda r:r['diagnostics']['scope_cleanup_observations'][1].update(remaining_tasks=1),
            lambda r:r['diagnostics']['scope_cleanup_observations'][1].update(stop_marker_published=False),
            lambda r:r['diagnostics']['worker_launch_observations'][0].update(argv_seen=False),
            lambda r:r['diagnostics']['worker_launch_observations'][1].update(verified=False),
            lambda r:r['diagnostics'].update(scope_cleanup_observed=False),
            lambda r:r['diagnostics'].update(cooperative_stop_markers_published=1),
            lambda r:r['diagnostics'].update(workspace_files_removed=False),
            lambda r:r['diagnostics'].update(workspace_free_after=0),
        )
        for change in mutations:
            with self.subTest(mutate=change):
                report=success_record();change(report)
                self.assertIsNone(self.parse(report))

    def test_unverified_baseline_is_allowed_but_not_a_pass(self):
        report={'schema_version':'0.1','checks':{c:'NOT_VERIFIED' for c in guest.CHECKS},'diagnostics':{}}
        self.assertIsNotNone(self.parse(report))
        report['checks']['unthrottled_baseline_exceeds_limit']='PASS'
        self.assertIsNone(self.parse(report))

    def test_reject_bad_schema_extra_fields_untrusted_strings(self):
        record=success_record();record['diagnostics']['student_log']='ignored'
        self.assertIsNone(self.parse(record))
        record=success_record();record['checks']['aggregate_write_rate_throttled']=True
        self.assertIsNone(self.parse(record))
        record=success_record();record['diagnostics']['probe_error']='<script>'
        self.assertIsNone(self.parse(record))
        self.assertIsNone(accept._parse_guest(b'{invalid'))
        self.assertIsNone(accept._parse_guest(b'x'*7501))

    def test_explicit_host_optin_required(self):
        with patch.object(accept,'verify_host_toolchain',return_value={
            'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(accept,'QemuWorker') as vm:
            self.assertEqual(accept.run({},run_clean_vm=False)['status'],
                             'INSPECTION_ONLY_NOT_ATTESTED')
            self.assertEqual(accept.run({},run_clean_vm=True)['status'],
                             'IO_STRESS_CONFIRMATION_REQUIRED')
            vm.assert_not_called()

    def test_fail_closed_attestation_and_cleanup(self):
        report=accept._base_report()
        self.assertEqual(report['security_attestation'],'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertEqual(report['workspace_disk_mib'],256)
        self.assertIn('ROOT_QCOW2', ' '.join(report['remaining_security_gates']))
        self.assertEqual(len(guest.CHECKS),10)

if __name__=='__main__': unittest.main()
