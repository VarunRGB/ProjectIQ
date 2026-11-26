"""Real CLI file IO, bounded parsing, refusal to overwrite, and privacy tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from projectiq_executioner.tests.test_phase6_core import SRC,quality,vm,run
from projectiq_executioner.phase6.__main__ import main

class CliChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def save(self,name,value):
        p=self.root/name
        p.write_text(json.dumps(value),encoding='utf-8')
        return p
    def call(self,*args):
        return main(list(args))
    def test_quality_only(self):
        q=self.save('quality.json',quality())
        result=self.call('--phase5',str(q),'--out',str(self.root/'out'))
        self.assertEqual(result,0)
        data=json.loads((self.root/'out'/'phase6_diagnosis.json').read_text())
        self.assertEqual(data['execution'],'NOT_PERFORMED')
        self.assertTrue(any(d['code']=='PHASE5_SECRET_INDICATOR' for d in data['diagnoses']))
    def test_report_and_vm(self):
        r=self.save('run.json',run())
        v=self.save('vm.json',vm())
        self.assertEqual(self.call('--run',str(r),'--vm',str(v),'--out',str(self.root/'out')),0)
        result=json.loads((self.root/'out'/'phase6_diagnosis.json').read_text())
        self.assertEqual(result['status'],'DIAGNOSED')
        self.assertEqual(result['summary']['vm_verified_runs'],1)
        self.assertEqual(len(result['evidence_receipts']),2)
    def test_output_two_formats(self):
        q=self.save('quality.json',quality())
        self.call('--phase5',str(q),'--out',str(self.root/'out'))
        self.assertIn('Root-cause hypotheses',(self.root/'out'/'phase6_diagnosis.md').read_text())
    def test_second_run_refused(self):
        q=self.save('quality.json',quality())
        self.assertEqual(self.call('--phase5',str(q),'--out',str(self.root/'out')),0)
        self.assertEqual(self.call('--phase5',str(q),'--out',str(self.root/'out')),2)
    def test_wrong_vm_count(self):
        with self.assertRaises(SystemExit):self.call('--run','a','--vm','a','--vm','b','--out','x')
    def test_no_inputs(self):
        with self.assertRaises(SystemExit):self.call('--out','x')
    def test_invalid_json(self):
        p=self.root/'bad.json';p.write_text('{unclosed')
        self.assertEqual(self.call('--phase5',str(p),'--out',str(self.root/'out')),2)
        self.assertFalse((self.root/'out'/'phase6_diagnosis.json').exists())
    def test_duplicate_json(self):
        p=self.root/'bad.json';p.write_text('{"x":1,"x":2}')
        self.assertEqual(self.call('--phase5',str(p),'--out',str(self.root/'out')),2)
    def test_invalid_source(self):
        q=quality();q['source']['commit_sha']='0'*40
        r=self.save('run.json',run());v=self.save('vm.json',vm());qpath=self.save('quality.json',q)
        self.assertEqual(self.call('--run',str(r),'--vm',str(v),'--phase5',str(qpath),
                                   '--out',str(self.root/'out')),2)
    def test_extra_raw_fields_are_not_copied(self):
        r=run();r['raw_request_password']='SUPER_SECRET_RAW_CREDENTIAL'
        p=self.save('run.json',r)
        self.assertEqual(self.call('--run',str(p),'--out',str(self.root/'out')),0)
        self.assertNotIn('SUPER_SECRET_RAW_CREDENTIAL',(self.root/'out'/'phase6_diagnosis.json').read_text())
        self.assertNotIn('SUPER_SECRET_RAW_CREDENTIAL',(self.root/'out'/'phase6_diagnosis.md').read_text())
    def test_file_digest_receipt(self):
        q=self.save('quality.json',quality())
        self.call('--phase5',str(q),'--out',str(self.root/'out'))
        data=json.loads((self.root/'out'/'phase6_diagnosis.json').read_text())
        self.assertEqual(len(data['evidence_receipts'][0]['sha256']),64)
    def test_symlink_input_refused(self):
        if not hasattr(os,'symlink'):self.skipTest('No symlink support')
        q=self.save('quality.json',quality());ln=self.root/'alias.json'
        try:ln.symlink_to(q)
        except OSError:self.skipTest('Symlink creation forbidden')
        self.assertEqual(self.call('--phase5',str(ln),'--out',str(self.root/'out')),2)
    def test_bad_dependencies_ignored_no(self):
        d={'schema_version':'0.1','source':SRC,'edges':[{'test_id':'UNKNOWN','requires':'UNKNOWN'}]}
        q=self.save('quality.json',quality());dep=self.save('deps.json',d)
        self.assertEqual(self.call('--phase5',str(q),'--dependencies',str(dep),
                                   '--out',str(self.root/'out')),2)
    def test_output_json_is_stable(self):
        q=self.save('quality.json',quality())
        a=self.root/'one';b=self.root/'two'
        self.assertEqual(self.call('--phase5',str(q),'--out',str(a)),0)
        self.assertEqual(self.call('--phase5',str(q),'--out',str(b)),0)
        self.assertEqual((a/'phase6_diagnosis.json').read_bytes(),(b/'phase6_diagnosis.json').read_bytes())
    def test_no_source_code_execution(self):
        marker=self.root/'EXECUTED'
        q=quality();q['evil']={'__import__':f'__import__("pathlib").Path("{marker}").touch()'}
        p=self.save('quality.json',q)
        self.assertEqual(self.call('--phase5',str(p),'--out',str(self.root/'out')),0)
        self.assertFalse(marker.exists())

if __name__=='__main__':unittest.main()
