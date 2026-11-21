"""D5c Gate 4: real-runtime registration and conservative partial-cleanup tests."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5c_orphan_registry as r
from projectiq_executioner.phase2.vm_worker import d5c_runtime_workspace as runtime
from projectiq_executioner.phase2.vm_worker import d5c_gate4_acceptance as acceptance


class ManagedStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = r.initialize_root(self.home / 'managed')
        self.image = str(self.home / 'qemu-system-x86_64.exe')

    def _new(self):
        return r.create_run(self.root)

    def test_source_inspection_tree_is_verified_and_recovered(self):
        folder = self._new()
        nested = folder / 'inspection' / 'source' / 'assets'
        nested.mkdir(parents=True)
        (nested/'index.html').write_text('<html></html>')
        (folder/'snapshot.zip').write_bytes(b'ZIP')
        (folder/'request.json').write_bytes(b'{}')
        (folder/'node_runtime.tar.xz').write_bytes(b'ARCHIVE')
        (folder/'python_wheelhouse.zip').write_bytes(b'ARCHIVE')
        results = r.recover_managed_root(self.root, self.image)
        self.assertEqual(results[0]['status'], 'RECOVERED')
        self.assertFalse(folder.exists())

    def test_nested_symlink_never_deleted_or_followed(self):
        folder = self._new()
        outside = self.home / 'private'
        outside.write_bytes(b'SENSITIVE')
        inside = folder / 'inspection'
        inside.mkdir()
        try:
            (inside/'link').symlink_to(outside)
        except OSError:
            self.skipTest('Symlink privilege unavailable on Windows')
        results = r.recover_managed_root(self.root,self.image)
        self.assertEqual(results[0]['status'],'MANUAL_REVIEW')
        self.assertTrue(folder.exists())
        self.assertEqual(outside.read_bytes(),b'SENSITIVE')
        (inside/'link').unlink()  # test owner, not the recovery sweep
        self.assertEqual(r.recover_managed_root(self.root,self.image)[0]['status'],'RECOVERED')

    def test_unknown_root_artifact_retained(self):
        folder = self._new()
        (folder/'unowned.log').write_bytes(b'RETAIN')
        self.assertEqual(r.recover_managed_root(self.root,self.image)[0]['status'], 'MANUAL_REVIEW')
        self.assertEqual((folder/'unowned.log').read_bytes(),b'RETAIN')
        (folder/'unowned.log').unlink()
        self.assertEqual(r.recover_managed_root(self.root,self.image)[0]['status'],'RECOVERED')

    def test_partial_cleanup_failure_reports_and_recovers_on_retry(self):
        folder = self._new()
        (folder/'snapshot.zip').write_bytes(b'SNAP')
        (folder/'request.json').write_bytes(b'{}')
        original = Path.unlink
        armed = {'fire':True}
        def inject(path, *args, **kwargs):
            if path.name == 'snapshot.zip' and armed['fire']:
                armed['fire'] = False
                raise PermissionError('injected locked staging file')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',inject):
            first = r.recover_managed_root(self.root,self.image)
        self.assertEqual(first[0]['status'],'MANUAL_REVIEW')
        self.assertTrue(folder.exists())
        second = r.recover_managed_root(self.root,self.image)
        self.assertEqual(second[0]['status'],'RECOVERED')
        self.assertEqual(r.recover_managed_root(self.root,self.image), [])

    def test_mid_cleanup_signed_sweeping_record_retried(self):
        folder = self._new()
        r.update_run(self.root,folder,'LAUNCHING')
        r.update_run(self.root,folder,'LAUNCHED',pid=1094,born=112344,image=self.image)
        r.update_run(self.root,folder,'STOPPED',pid=1094,born=112344,image=self.image)
        (folder/'snapshot.zip').write_bytes(b'S')
        original = Path.unlink
        def injection(path,*a,**k):
            if path.name == 'snapshot.zip':
                raise PermissionError('simulated file lock')
            return original(path,*a,**k)
        with patch.object(r,'windows_process_state',return_value='DEAD'):
            with patch.object(Path,'unlink',injection):
                self.assertEqual(r.recover_managed_root(self.root,self.image)[0]['status'],'MANUAL_REVIEW')
            self.assertEqual(r._record(self.root,folder)['phase'],'SWEEPING')
            self.assertEqual(r.recover_managed_root(self.root,self.image)[0]['status'],'RECOVERED')

    def test_launching_gap_keeps_storage_for_review(self):
        folder = self._new()
        r.update_run(self.root,folder,'LAUNCHING')
        (folder/'run.qcow2').write_bytes(b'QEMU')
        self.assertEqual(r.recover_managed_root(self.root,self.image)[0]['status'],'MANUAL_REVIEW')
        self.assertTrue(folder.exists())


class RuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.config={'managed_run_root':str(self.base/'managed'),
                     'qemu_binary':str(self.base/'qemu-system-x86_64.exe')}

    def _open(self):
        run = runtime.ManagedRuntimeRun(self.config)
        with patch.object(runtime,'_windows_runtime_supported',return_value=True):
            work = run.open()
        return run, work

    def test_real_runtime_is_registered_and_removed(self):
        run, work=self._open()
        with run.hold() as folder:
            self.assertEqual(work,folder)
            self.assertEqual(r._record(run.root,folder)['phase'],'PREPARING')
            (folder/'snapshot.zip').write_bytes(b'snapshot')
        self.assertTrue(run.cleanup_verified)
        self.assertEqual(run.cleanup_reason,'VERIFIED_REMOVED')
        self.assertFalse(work.exists())

    def test_workspace_retains_partial_cleanup_and_can_retry(self):
        run, work=self._open()
        original=Path.unlink
        def inject(path,*a,**kw):
            if path.name=='snapshot.zip':raise PermissionError('locked')
            return original(path,*a,**kw)
        with patch.object(Path,'unlink',inject):
            with run.hold() as folder:
                (folder/'snapshot.zip').write_bytes(b'123')
        self.assertFalse(run.cleanup_verified)
        self.assertTrue(work.exists())
        self.assertEqual(r.recover_managed_root(run.root,run.image)[0]['status'],'RECOVERED')
        self.assertFalse(work.exists())

    def test_preparing_can_be_swept_on_next_start(self):
        run, folder=self._open()
        run._held.__exit__(None,None,None)
        run._held=None  # simulate controller death without normal close
        (folder/'snapshot.zip').write_bytes(b'old')
        next_run=runtime.ManagedRuntimeRun(self.config)
        with patch.object(runtime,'_windows_runtime_supported',return_value=True):
            fresh=next_run.open()
        self.assertFalse(folder.exists())
        self.assertTrue(any(x['status']=='RECOVERED' for x in next_run.previous_sweep))
        with next_run.hold():pass
        self.assertTrue(next_run.cleanup_verified)
        self.assertFalse(fresh.exists())

    def test_unverified_prior_run_blocks_new_vm(self):
        run,folder=self._open()
        r.update_run(run.root,folder,'LAUNCHING')
        run._held.__exit__(None,None,None)
        run._held=None
        another=runtime.ManagedRuntimeRun(self.config)
        with patch.object(runtime,'_windows_runtime_supported',return_value=True):
            with self.assertRaisesRegex(runtime.RuntimeWorkspaceError,'UNRESOLVED_MANAGED_RUN'):
                another.open()
        self.assertTrue(folder.exists())

    def test_invalid_managed_root_rejected(self):
        for name in ('relative','',None):
            if name is None:
                continue
            with self.subTest(name=name),self.assertRaises(runtime.RuntimeWorkspaceError):
                runtime.ManagedRuntimeRun({**self.config,'managed_run_root':name})

    def test_no_linux_claim_without_lifetime_guard(self):
        if os.name!='nt':
            run=runtime.ManagedRuntimeRun(self.config)
            with self.assertRaisesRegex(runtime.RuntimeWorkspaceError,'WINDOWS_LIFETIME_GUARD'):
                run.open()

    def test_live_launched_booted_stopped_is_signed_and_recovered(self):
        from types import SimpleNamespace
        run, folder=self._open()
        proc=SimpleNamespace(pid=4137, poll=lambda:None)
        from projectiq_executioner.phase2.vm_worker import d5c_windows_job as jobs
        with patch.object(jobs,'verify_child_in_job',return_value=True), \
             patch.object(r,'windows_creation_time',return_value=12345001234), \
             patch.object(r,'windows_process_state',return_value='DEAD'):
            with run.hold():
                run.mark_launching()
                self.assertEqual(r._record(run.root,folder)['phase'],'LAUNCHING')
                run.mark_launched(proc)
                launched=r._record(run.root,folder)
                self.assertEqual((launched['phase'],launched['pid'],launched['born']),
                                 ('LAUNCHED',4137,12345001234))
                run.mark_booted()
                self.assertEqual(r._record(run.root,folder)['phase'],'BOOTED')
                run.mark_stopped()
                self.assertEqual(r._record(run.root,folder)['phase'],'STOPPED')
        self.assertTrue(run.cleanup_verified)
        self.assertFalse(folder.exists())

    def test_live_qemu_cannot_be_swept_even_if_controller_unlocks(self):
        from types import SimpleNamespace
        run, folder=self._open()
        proc=SimpleNamespace(pid=4138,poll=lambda:None)
        from projectiq_executioner.phase2.vm_worker import d5c_windows_job as jobs
        with patch.object(jobs,'verify_child_in_job',return_value=True), \
             patch.object(r,'windows_creation_time',return_value=12345001235), \
             patch.object(r,'windows_process_state',return_value='ALIVE'):
            with run.hold():
                run.mark_launching()
                run.mark_launched(proc)
                run.mark_stopped()  # recorded stop alone is insufficient
        self.assertFalse(run.cleanup_verified)
        self.assertTrue(folder.exists())
        with patch.object(r,'windows_process_state',return_value='DEAD'):
            self.assertEqual(r.recover_managed_root(run.root,run.image)[0]['status'],'RECOVERED')
        self.assertFalse(folder.exists())

    def test_unverified_kernel_job_membership_prevents_identity_record(self):
        from types import SimpleNamespace
        run, folder=self._open()
        proc=SimpleNamespace(pid=4139,poll=lambda:None)
        from projectiq_executioner.phase2.vm_worker import d5c_windows_job as jobs
        with run.hold():
            run.mark_launching()
            with patch.object(jobs,'verify_child_in_job',return_value=False):
                with self.assertRaisesRegex(runtime.RuntimeWorkspaceError,'QEMU_NOT_IN_WINDOWS_JOB'):
                    run.mark_launched(proc)
        self.assertFalse(run.cleanup_verified)
        self.assertTrue(folder.exists())
        self.assertEqual(r._record(run.root,folder)['phase'],'LAUNCHING')

    def test_runtime_source_tracks_real_qemu_lifecycle(self):
        import inspect
        from projectiq_executioner.phase2.vm_worker import orchestrator
        code=inspect.getsource(orchestrator.execute_vm_candidate)
        for element in ('runtime.open()','runtime.hold()','runtime.mark_launching()',
                        'runtime.mark_launched(worker.proc)','runtime.mark_booted()',
                        'runtime.mark_stopped()','runtime.cleanup_verified'):
            self.assertIn(element,code)
        self.assertNotIn('TemporaryDirectory(prefix=',code)
        self.assertIn('NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',code)



class AcceptanceValidatorTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.base=Path(self.t.name)
        self.root=r.initialize_root(self.base/'managed')
        self.runbase=self.base/'gate2a3_regression_test'
        self.runbase.mkdir()
        self.config=self.base/'vm_config.json'
        self.config.write_text(json.dumps({'base_image':'template.qcow2',
            'base_sha256':'0'*64,'approved_snapshots':[],
            'managed_run_root':str(self.root), 'qemu_binary':'qemu.exe'}))
        self.rows=[]
        approved=[]
        for name,(servers,build,adapter) in acceptance.EXPECTED.items():
            d=self.runbase/name.lower();d.mkdir()
            out=d/'vm_execution.json'
            repo='Fixture/'+name
            sha={'FSDL':'a','MiniCollabApp':'b','FullStack':'c'}[name]*40
            approved.append(repo+'@'+sha)
            record={'source':{'repository':repo,'commit_sha':sha},'adapter':adapter,
                'backend':'QEMU_DISPOSABLE_QCOW2_VM',
                'status':'STARTUP_VERIFIED','execution':'PERFORMED_IN_DISPOSABLE_VM',
                'cleanup':acceptance.CLEAN,'managed_runtime':'SIGNED_LEASED_WORKSPACE_REMOVED',
                'managed_run_id':r.PREFIX + {'FSDL':'a','MiniCollabApp':'b','FullStack':'c'}[name]*20,
                'security_attestation':acceptance.ATTESTATION,
                'guest_result':{'http_status':200,'cgroup_placement':{
                    'status':'SERVER_PIDS_VERIFIED',
                    'server_count':servers,'builds_verified':False,
                    'database_service_verified':False},
                    'build_launch_evidence':{'status':build}}}
            out.write_text(json.dumps(record))
            self.rows.append({'Fixture':name,'Report':str(out),
                              'Startup':'PASS','HTTP':'200','Cleanup':'PASS'})
        self.config.write_text(json.dumps({**json.loads(self.config.read_text()),
                                           'approved_snapshots':approved}))
        self.summary=self.runbase/'regression_summary.json'
        self.summary.write_text(json.dumps({'Passed':True,'SecurityAttestation':acceptance.ATTESTATION,
                                            'Results':self.rows}))

    def test_real_fixture_validator_accepts_complete_reports(self):
        from types import SimpleNamespace
        with patch.object(acceptance,'load_config',return_value=json.loads(self.config.read_text())):
            evidence=acceptance.validate(self.summary,self.config)
        self.assertEqual(evidence['status'],acceptance.SUCCESS)
        self.assertEqual(len(evidence['checks']),8)

    def test_validator_rejects_untracked_cleanup_report(self):
        p=self.runbase/'fullstack'/'vm_execution.json'
        val=json.loads(p.read_text());val['managed_runtime']='NOT_VERIFIED'
        p.write_text(json.dumps(val))
        with patch.object(acceptance,'load_config',return_value=json.loads(self.config.read_text())):
            evidence=acceptance.validate(self.summary,self.config)
        self.assertNotEqual(evidence['status'],acceptance.SUCCESS)

    def test_validator_refuses_leftover_registered_run(self):
        r.create_run(self.root)
        with patch.object(acceptance,'load_config',return_value=json.loads(self.config.read_text())):
            evidence=acceptance.validate(self.summary,self.config)
        self.assertEqual(evidence['checks']['no_leftover_managed_runs'],'NOT_VERIFIED')
        self.assertNotEqual(evidence['status'],acceptance.SUCCESS)

    def test_validator_rejects_duplicate_or_tampered_report_path(self):
        self.rows[0]['Report']=self.rows[1]['Report']
        self.summary.write_text(json.dumps({'Passed':True,'SecurityAttestation':acceptance.ATTESTATION,
                                            'Results':self.rows}))
        with patch.object(acceptance,'load_config',return_value=json.loads(self.config.read_text())):
            evidence=acceptance.validate(self.summary,self.config)
        self.assertNotEqual(evidence['status'],acceptance.SUCCESS)


if __name__=='__main__': unittest.main()
