"""D5b B4: no live VMs; reject forged acceptance and wrong checkout inputs."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_b4_acceptance as b4


def good_gate(name):
    _, _, stage, status, execution, checks, _ = b4.GATES[name]
    obj = {'schema_version':'0.1','phase':2,'stage':stage,'status':status,
           'execution':execution,'student_code_executed':False,
           'security_attestation':b4.ATTESTATION,'cleanup':b4.CLEAN,
           'checks':{c:'PASS' for c in checks}}
    launches = [{'worker':i,'verified':True,'app_uid_seen':True,
                 'cgroup_seen':True,'argv_seen':True,'scope_seen':True}
                for i in (1,2)]
    cleanup = [{'worker':i,'scope_state':'ABSENT','remaining_tasks':0,
                'stop_marker_published':True} for i in (1,2)]
    d={'worker_launch_observations':launches,'scope_cleanup_observations':cleanup,
       'verified_worker_count':2,'scope_cleanup_observed':True}
    if name == 'memory':
        d.update(memory_peak_bytes=2147483648,memory_oom_kill_delta=1)
    elif name == 'pid':
        d.update(concurrent_scopes_at_limit=True,scope_limits_above_parent=True,
                 max_parent_pids_current=80,parent_pids_after=0,
                 first_scope_tasks_held=32,second_scope_tasks_held=48,
                 pids_events_max_delta=0,second_scope_events_max_delta=1)
    elif name == 'io':
        d.update(concurrent_scope_io_observed=True,workspace_files_removed=True,
                 baseline_scope_cleaned=True,baseline_bps=328965019,
                 eval_parent_wbytes_delta=134217728,eval_elapsed_ms=8074,
                 eval_aggregate_bps=134217728*1000//8074,
                 baseline_wbytes_delta=67108864,first_scope_wbytes=67108864,
                 second_scope_wbytes=67108864)
    elif name == 'recovery':
        d.update(arm_crash_exit_code=42,arm_cancel_exit_code=0,
                 first_vm_evaluation_tasks=2,fresh_vm_evaluation_tasks=0,
                 first_vm_worker_armed=True,forced_vm_termination_observed=True,
                 first_vm_storage_removed=True,fresh_vm_storage_removed=True,
                 fresh_vm_sentinel_absent=True)
    obj['observations' if name=='recovery' else 'kernel_observations'] = d
    return obj


def good_fixtures():
    rows=[];docs={}
    for name,(adapter,servers,build) in b4.FIXTURES.items():
        rows.append({'Fixture':name,'Startup':'PASS','HTTP':'200',
                     'Servers':servers,'Cleanup':'PASS'})
        evidence={'status':'PREEXEC_KERNEL_HANDOFF_VERIFIED' if build else 'NOT_APPLICABLE',
                  'npm_ci':2 if build else 0, 'vite_build':1 if build else 0,
                  'pip_install':0}
        g={'status':'STARTUP_VERIFIED','http_status':200,
           'cgroup_placement':{'status':'SERVER_PIDS_VERIFIED','server_count':servers,
                               'builds_verified':False,'database_service_verified':False},
           'build_launch_evidence':evidence}
        if build:
            g['service_results']={'frontend_http':200,'backend_http':200,
                'built_asset_http':200,'frontend_proxy_api_http':200,'sqlite_verified':True}
        docs[name]={'status':'STARTUP_VERIFIED','execution':'PERFORMED_IN_DISPOSABLE_VM',
                    'cleanup':b4.CLEAN,'adapter':adapter,
                    'security_attestation':b4.ATTESTATION,
                    'source':{'repository':'Test/Fixture','commit_sha':'a'*40},
                    'guest_result':g}
    return {'Passed':True,'Failure':None,'Results':rows,
            'SecurityAttestation':b4.ATTESTATION},docs


class GateEvidenceTests(unittest.TestCase):
    def test_all_four_full_evidence_records_accepted(self):
        self.assertEqual(sum(len(x[5]) for x in b4.GATES.values()),36)
        for name in b4.GATES:
            with self.subTest(name=name):
                self.assertEqual(b4.validate_gate(name,good_gate(name)),[])

    def test_no_status_only_pass(self):
        for name in b4.GATES:
            with self.subTest(name=name):
                forged=good_gate(name)
                forged.pop('observations' if name=='recovery' else 'kernel_observations')
                self.assertIn('KERNEL_OR_CLEANUP_EVIDENCE_INCONSISTENT',
                              b4.validate_gate(name,forged))

    def test_fail_closed_on_missing_or_added_checks(self):
        for name in b4.GATES:
            with self.subTest(name=name):
                for change in ('missing','extra','not_verified'):
                    x=good_gate(name)
                    k=next(iter(x['checks']))
                    if change=='missing':x['checks'].pop(k)
                    elif change=='extra':x['checks']['bogus']='PASS'
                    else:x['checks'][k]='NOT_VERIFIED'
                    self.assertIn('CHECKS_NOT_ALL_VERIFIED',b4.validate_gate(name,x))

    def test_attestation_student_and_cleanup_cannot_be_forged(self):
        for field,bad in [('security_attestation','ATTESTED'),('student_code_executed',True),
                          ('cleanup','NOT_VERIFIED'),('phase',True),('execution','NOT_PERFORMED')]:
            r=good_gate('memory');r[field]=bad
            self.assertTrue(b4.validate_gate('memory',r),field)

    def test_memory_without_oom_or_cleanup_rejected(self):
        for key,bad in [('memory_oom_kill_delta',0),('memory_peak_bytes',1),
                        ('scope_cleanup_observed',False)]:
            r=good_gate('memory');r['kernel_observations'][key]=bad
            self.assertTrue(b4.validate_gate('memory',r),key)
        r=good_gate('memory')
        r['kernel_observations']['scope_cleanup_observations'][1]['remaining_tasks']=1
        self.assertTrue(b4.validate_gate('memory',r))

    def test_pid_denial_counter_and_concurrent_limit_required(self):
        r=good_gate('pid')
        r['kernel_observations']['pids_events_max_delta']=0
        r['kernel_observations']['second_scope_events_max_delta']=0
        self.assertTrue(b4.validate_gate('pid',r))
        r=good_gate('pid')
        r['kernel_observations']['concurrent_scopes_at_limit']=False
        self.assertTrue(b4.validate_gate('pid',r))

    def test_io_measurement_recomputed(self):
        for key,bad in [('baseline_bps',1),('eval_aggregate_bps',25000000),
                        ('eval_parent_wbytes_delta',0),('eval_elapsed_ms',1)]:
            r=good_gate('io');r['kernel_observations'][key]=bad
            self.assertTrue(b4.validate_gate('io',r),key)

    def test_crash_cancel_and_new_vm_are_separate(self):
        for key,bad in [('arm_crash_exit_code',0),('arm_cancel_exit_code',42),
                        ('fresh_vm_evaluation_tasks',1),('fresh_vm_sentinel_absent',False),
                        ('forced_vm_termination_observed',False)]:
            r=good_gate('recovery');r['observations'][key]=bad
            self.assertTrue(b4.validate_gate('recovery',r),key)


class FixtureEvidenceTests(unittest.TestCase):
    def test_all_three_approved_fixtures(self):
        a,b=good_fixtures()
        self.assertEqual(b4.validate_fixture_run(a,b),[])

    def test_missing_duplicate_and_negative_fixture(self):
        for bad in ('not_passed','missing','duplicate','claim_builds_constrained'):
            with self.subTest(bad=bad):
                a,b=good_fixtures()
                if bad=='not_passed':a['Passed']=False
                if bad=='missing':a['Results'].pop()
                if bad=='duplicate':a['Results'][1]=a['Results'][0].copy()
                if bad=='claim_builds_constrained':
                    b['FullStack']['guest_result']['cgroup_placement']['builds_verified']=True
                self.assertTrue(b4.validate_fixture_run(a,b))

    def test_must_preserve_unverified_db_scope_and_test_oracles(self):
        a,b=good_fixtures()
        b['MiniCollabApp']['guest_result']['cgroup_placement']['database_service_verified']=True
        self.assertTrue(b4.validate_fixture_run(a,b))
        a,b=good_fixtures()
        b['FullStack']['guest_result']['service_results']['sqlite_verified']=False
        self.assertTrue(b4.validate_fixture_run(a,b))

    def test_source_sha_required(self):
        a,b=good_fixtures()
        b['FSDL']['source']['commit_sha']='main'
        self.assertTrue(b4.validate_fixture_run(a,b))


class HostEvidenceTests(unittest.TestCase):
    def test_inspection_never_runs_vm(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(b4,'check_checkout',return_value={'git_head':b4.PINNED_HEAD}):
                with patch.object(b4,'_run_logged',side_effect=AssertionError('unexpected process')):
                    r=b4.run(Path(td),Path('config'),Path('harness'),Path(td)/'out.json',
                             execute=False,ack_resources=False,ack_vm_kill=False)
                    self.assertEqual(r['status'],'INSPECTION_ONLY_NOT_ATTESTED')

    def test_missing_ack_never_runs_vm(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(b4,'check_checkout',return_value={}):
                with patch.object(b4,'_run_logged',side_effect=AssertionError('unexpected process')):
                    r=b4.run(Path(td),Path('config'),Path('harness'),Path(td)/'out.json',
                             execute=True,ack_resources=True,ack_vm_kill=False)
                    self.assertEqual(r['status'],'EXPLICIT_DESTRUCTIVE_TEST_ACKNOWLEDGEMENT_REQUIRED')

    def test_json_evidence_rejects_missing_oversize_and_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'report.json'
            with self.assertRaises(ValueError):b4._json(path)
            path.write_text('{"a":1}',encoding='utf-8')
            self.assertEqual(b4._json(path),{'a':1})
            with patch.object(Path,'is_symlink',return_value=True):
                with self.assertRaises(ValueError):b4._json(path)
            path.write_bytes(b'x'*100)
            with self.assertRaises(ValueError):b4._json(path,limit=10)

    def test_vendored_script_digest_matches(self):
        script=Path(__file__).resolve().parents[1]/'tools'/'d5_b4_regression.ps1'
        data=script.read_text(encoding='utf-8-sig').replace('\r\n','\n').encode('utf-8')
        self.assertEqual(hashlib.sha256(data).hexdigest(),b4.HARNESS_SHA256)
        self.assertIn('MiniCollabApp',data.decode())

    def test_run_saves_atomic_report(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'subdir'/'report.json'
            b4._record(path,{'status':'TEST'})
            self.assertEqual(json.loads(path.read_text())['status'],'TEST')
            self.assertFalse(path.with_name(path.name+'.tmp').exists())

    def test_clean_execution_does_not_imply_untrusted_attestation(self):
        r=b4._base()
        self.assertFalse(r['student_code_executed'])
        self.assertFalse(r['all_supported_adapters_verified'])
        self.assertEqual(r['security_attestation'],b4.ATTESTATION)
        self.assertIn('MYSQL_SYSTEM_SERVICE_NOT_CONFIRMED_IN_EVALUATION_SLICE',r['remaining_security_gates'])


if __name__=='__main__':
    unittest.main()
