"""Actual bridge integration tests; no VM or model calls. Run on the combined checkout."""
import base64
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as bridge
from local_policy import BridgeBusy


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.vm=self.root/'vm.json'; self.vm.write_text('{}')
        self.config=patch.multiple(bridge.Config,root=self.root,vm_config=self.vm,demo_enabled=True,token='t'*48,origin='http://127.0.0.1:5173')
        self.config.start(); self.addCleanup(self.config.stop)
        self.addCleanup(bridge.JOBS.clear)

    def test_http_auth_and_admission(self):
        srv=bridge.ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
        port=srv.server_address[1]; self.addCleanup(srv.server_close)
        t=threading.Thread(target=srv.serve_forever,daemon=True);t.start();self.addCleanup(srv.shutdown)
        with patch.object(bridge.Config,'port',port):
            def call(method,url,headers=None,body=None):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                try:
                    conn.request(method,url,body=body,headers=headers or {});r=conn.getresponse();return r.status,r.read()
                finally:conn.close()
            h={'X-ProjectIQ-Token':'t'*48,'Origin':bridge.Config.origin}
            self.assertEqual(call('GET','/api/capabilities')[0],403)
            self.assertEqual(call('GET','/api/capabilities',{**h,'Origin':'http://example.com'})[0],403)
            self.assertEqual(call('GET','/api/capabilities',{**h,'Host':'example.com'})[0],403)
            status,body=call('GET','/api/capabilities',h);self.assertEqual(status,200);self.assertIn('TRUSTED_DEMO',json.loads(body)['liveExecutionerModes'])
            self.assertEqual(call('OPTIONS','/api/evaluations',{'Origin':bridge.Config.origin})[0],204)
            with bridge.ADMISSION:
                self.assertEqual(call('POST','/api/evaluations',{**h,'Content-Type':'application/json'},'{}')[0],409)
            self.assertEqual(call('POST','/api/evaluations',{**h,'Content-Type':'text/plain'},'{}')[0],422)
            j=bridge.Job('one','test','test');j.proc=Mock();bridge.JOBS['one']=j
            self.assertEqual(call('POST','/api/runs/one/cancel',{**h,'Content-Type':'application/json'},'{}')[0],200)
            self.assertTrue(j.cancelled);j.proc.terminate.assert_not_called()

    def test_upload_collision_and_reserved_name(self):
        d=self.root/'uploads'; data=base64.b64encode(b'abc').decode()
        bridge.write_upload(d,{'name':'demo.txt','b64':data})
        with self.assertRaisesRegex(ValueError,'DUPLICATE_UPLOAD'):bridge.write_upload(d,{'name':'demo.txt','b64':data})
        with self.assertRaisesRegex(ValueError,'INVALID_UPLOAD_FILENAME'):bridge.write_upload(d,{'name':'CON.txt','b64':data})
        self.assertEqual((d/'demo.txt').read_bytes(),b'abc')

    def test_cancel_at_boundary(self):
        j=bridge.Job('one','test','test'); proc=Mock()
        def output():
            j.cancelled=True
            yield 'finished command\n'
        proc.stdout=output();proc.wait.return_value=0
        with patch.object(bridge.subprocess,'Popen',return_value=proc) as spawn:
            code,_=bridge.run_cmd(j,['-m','projectiq_core','evaluate']);self.assertEqual(code,0)
            with self.assertRaisesRegex(RuntimeError,'CANCELLED'):bridge.run_cmd(j,['-m','next'])
            spawn.assert_called_once();proc.terminate.assert_not_called();self.assertNotIn('PROJECTIQ_BRIDGE_TOKEN',spawn.call_args.kwargs['env'])

    def test_failed_admission_releases_slot(self):
        with self.assertRaises(ValueError):bridge.start({})
        with bridge.ADMISSION:pass

    def fake_run(self, mode):
        fx=json.loads((Path(__file__).resolve().parents[2]/'src/mock/fixtures/campusconnect.json').read_text('utf-8'))
        result=copy.deepcopy(fx['result']); job=bridge.Job('one','test','evaluation')
        jobdir=self.root/'job';jobdir.mkdir();(jobdir/'input').mkdir();(jobdir/'input/report.txt').write_text('demo');bridge.dump(jobdir/'rubric.json',result['rubric'])
        req={'title':'test','submission_id':'test','repository_url':'https://github.com/example/app','ref':'a'*40,'executioner':{'mode':mode,'run_readiness':'NO','max_vm_launches':1,'suites':[]},'acknowledge_imported':True,'rubric_mode':'OBJECTIVE_ONLY'}
        if mode=='TRUSTED_DEMO':req['executioner'].update(trust_demo_source=True,confirm_disposable_state=True)
        files={'report':'report.txt','references':[]};calls=[]
        def fake(job,args,**kw):
            args=list(map(str,args));calls.append(args);out=Path(args[args.index('--out')+1]);out.mkdir(parents=True,exist_ok=True)
            if args[1]=='projectiq_executioner.orchestration':bridge.dump(out/'executioner_result.json',{'status':'COMPLETED'})
            elif args[2] in ('collect','bundle'):bridge.dump(out/'evaluation.json',{})
            else:bridge.dump(out/'result.json',result)
            return 0,[]
        bridge.ADMISSION.acquire()
        with patch.object(bridge,'run_cmd',side_effect=fake):bridge.evaluate_job(job,req,files,jobdir)
        self.assertEqual(job.state,'COMPLETED',job.events)
        self.assertEqual(job.agents['rubric']['state'],'failed' if result['rubric_exit_code'] else 'partial' if result['workflow_status']=='INCOMPLETE_ASSESSMENT' else 'done')
        with bridge.ADMISSION:pass
        return calls

    def test_demo_reuses_unified_command(self):
        calls=self.fake_run('TRUSTED_DEMO');self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][:3],['-m','projectiq_core','evaluate']);self.assertIn('--trust-demo-source',calls[0]);self.assertIn('--confirm-disposable-state',calls[0]);self.assertIn(str(self.vm),calls[0])

    def test_reviewed_keeps_existing_composition(self):
        calls=self.fake_run('REVIEWED');self.assertEqual(len(calls),4)
        self.assertEqual(calls[0][1],'projectiq_executioner.orchestration');self.assertEqual([c[2] for c in calls[1:]],['collect','bundle','evaluate']);self.assertNotIn('--trust-demo-source',calls[-1])

if __name__=='__main__':unittest.main()
