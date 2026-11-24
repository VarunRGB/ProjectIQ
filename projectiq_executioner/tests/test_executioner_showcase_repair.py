"""Offline showcase regression: real saved report shapes, no GitHub/VM execution."""
from __future__ import annotations
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from importlib import import_module
from projectiq_executioner.orchestration import runners
from projectiq_executioner.orchestration.coverage import (
    route_inventory,plan_review_hints,scenario_links,bounded_observations,workflow_metrics)
from projectiq_executioner.orchestration.matrix import build_test_case_matrix
from projectiq_executioner.orchestration.report import render_markdown

FIX=Path(__file__).parent/'fixtures'/'showcase_repair_evidence.json'

def fixture():return json.loads(FIX.read_text(encoding='utf8'))

class OutputAndCoverage(unittest.TestCase):
    def test_historical_routes_are_not_double_counted(self):
        f=fixture();i=route_inventory(f['manifest'])
        self.assertEqual((i['route_records'],i['server_endpoint_count'],i['client_route_records']),(18,10,8))
        r=next(x for x in i['server_endpoints'] if x['path']=='/logic/get_tasks.php')
        self.assertEqual(r['method'],'UNKNOWN')
        self.assertEqual(r['intended_method'],'GET')
        self.assertEqual(r['method_basis'],'CLIENT_REFERENCE_HINT_ONLY')
        old=copy.deepcopy(f['plan'])
        hints=plan_review_hints(f['plan'],i,f['manifest'])
        self.assertTrue(any(x['path']=='/public/register.html' for x in hints))
        self.assertTrue(all(x['status']=='PROPOSED_NOT_APPROVED' for x in hints))
        self.assertEqual(f['plan'],old)

    def test_duplicate_client_reference_not_second_endpoint(self):
        m={'routes':[{'kind':'file_endpoint','path':'api/test.php','method':'UNKNOWN'},
                     {'kind':'client_http_call','path':'../api/test.php','method':'GET'},
                     {'kind':'client_http_call','path':'../api/test.php','method':'POST'}]}
        i=route_inventory(m);self.assertEqual(i['server_endpoint_count'],1)
        self.assertEqual(i['server_endpoints'][0]['intended_method'],'UNKNOWN')

    def test_reviewed_scenario_bound_without_full_route_pass(self):
        f=fixture();parent='TC-35582FF439994A40';ident='OC-3CB38E63233A37C1'
        compiled={'tests':[{'id':ident,'phase3_test_id':parent,'method':'POST',
                            'path':'/logic/delete_task.php','body':{'private':'DO_NOT_EMIT'},
                            'assertions':[{'kind':'HTTP_STATUS_EQUALS','expected':200},
                                          {'kind':'JSON_BODY_SUBSET_EQUALS','expected':{'private':'DO_NOT_EMIT'}}]}]}
        links=scenario_links('api',compiled)
        self.assertEqual(links[0]['phase3_test_id'],parent)
        self.assertNotIn('DO_NOT_EMIT',json.dumps(links))
        obs=bounded_observations('api',f['api']['run'],links)
        suite={'kind':'api','state':'PASS','vm_verified':True,'approved_scenario_links':links,'observations':obs}
        plan={'cases':f['plan']['test_cases']}
        m=build_test_case_matrix(plan,[suite],effective_mode='REVIEWED')
        row=next(x for x in m['discovered_cases'] if x['test_id']==parent)
        self.assertEqual(row['outcome'],'NOT_TESTED')
        self.assertEqual(row['reason'],'REVIEWED_SCENARIO_ONLY_NOT_FULL_ROUTE')
        self.assertEqual(m['reviewed_check_counts']['PASSED'],1)
        self.assertEqual(m['reviewed_only_checks'][0]['phase3_test_id'],parent)
        suite['approved_scenario_links']=[]
        m=build_test_case_matrix(plan,[suite],effective_mode='REVIEWED')
        self.assertIsNone(m['reviewed_only_checks'][0]['phase3_test_id'])
        suite['vm_verified']=False
        m=build_test_case_matrix(plan,[suite],effective_mode='REVIEWED')
        self.assertEqual(m['reviewed_check_counts']['PASSED'],0)

    def test_browser_boolean_and_workflow_metrics(self):
        obs=bounded_observations('browser_login',{'checks':[{'id':'A','matched':True},{'id':'B','matched':False},{'id':'C','matched':None}]},[])
        self.assertEqual([x['result'] for x in obs],['PASS','FAIL','INCONCLUSIVE'])
        report={'status':'WORKFLOW_PASSED','observations':{'discovery_http':200,'remaining_matches':0,'private':'no'}}
        self.assertEqual(workflow_metrics('dependent_workflow',report),{'discovery_http':200,'remaining_matches':0})
        self.assertEqual(len(bounded_observations('dependent_workflow',report,[])),1)

    def test_approved_unexecuted_route_is_not_a_pass(self):
        parent='TC-35582FF439994A40'
        plan={'cases':fixture()['plan']['test_cases']}
        m=build_test_case_matrix(plan,[{'kind':'api','state':'BLOCKED','vm_verified':False,
                   'approved_scenario_links':[{'phase3_test_id':parent,'check_id':'OC-3CB38E63233A37C1'}]}],effective_mode='REVIEWED')
        row=next(x for x in m['discovered_cases'] if x['test_id']==parent)
        self.assertEqual(row['reason'],'APPROVED_SCENARIO_NO_VERIFIED_OBSERVATION')
        self.assertEqual(row['outcome'],'NOT_TESTED')

class RealRunnerDirectoryContract(unittest.TestCase):
    def test_six_fresh_only_runners_reach_compiler_without_vm(self):
        # Mock the approval compiler, NOT the actual runner's output-directory guard.
        # A sentinel is raised before any VM call. Existing empty dirs must reject.
        class ReachedCompiler(RuntimeError):pass
        for kind in ('db_postconditions','scenario_isolation','dependent_workflow','crud','browser_ui','browser_login'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                _,compiler,module,_=runners._REGISTRY[kind]
                m=import_module('projectiq_executioner.'+module)
                catalog_keys=__import__('projectiq_executioner.orchestration.contracts',fromlist=['SUITE_CATALOGS']).SUITE_CATALOGS[kind]
                catalogs={k:{} for k in catalog_keys}
                path=Path(d)/'new'
                with patch.object(m,compiler,side_effect=ReachedCompiler):
                    with self.assertRaises(ReachedCompiler):
                        runners.invoke(kind,plan={},candidate={},config={},catalogs=catalogs,output_dir=path)
                    self.assertFalse(path.exists())
                    path.mkdir()
                    with self.assertRaises((ValueError,RuntimeError)) as caught:
                        runners.invoke(kind,plan={},candidate={},config={},catalogs=catalogs,output_dir=path)
                    self.assertNotIsInstance(caught.exception,ReachedCompiler)

class ActualHistoricalEvidence(unittest.TestCase):
    def test_saved_api_and_auth_can_be_diagnosed_together(self):
        from projectiq_executioner.phase6.pipeline import build
        f=fixture()
        got=build(plans=[(f['plan'],'PLAN')],run_inputs=[(f[k]['run'],'RUN00'+str(i),f[k]['vm']) for i,k in enumerate(('api','auth'),1)])
        self.assertEqual(got['summary']['vm_verified_runs'],2)
        self.assertEqual(got['summary']['passes'],4)
        self.assertEqual(got['summary']['fails'],1)
        self.assertTrue(all(x['student_defect']=='NOT_ESTABLISHED' for x in got['events']))
        self.assertEqual(sum(x['code']=='ORACLE_NOT_ESTABLISHED' for x in got['diagnoses']),1)
        self.assertFalse(any(x['code']=='UNSUPPORTED_STAGE' for x in got['diagnoses']))

    def test_independent_api_id_requires_matching_guest_observations(self):
        from projectiq_executioner.phase6.pipeline import build
        from projectiq_executioner.phase6.contracts import EvidenceError
        f=fixture();f['api']['run']['results'][0]['http_status']=201
        with self.assertRaises(EvidenceError):build(plans=[(f['plan'],'PLAN')],run_inputs=[(f['api']['run'],'RUN001',f['api']['vm'])])

    def test_rejected_input_does_not_erase_valid_diagnosis(self):
        from projectiq_executioner.orchestration.adapters import diagnose
        f=fixture();f['auth']['run']['results'][0]['http_status']=201
        with tempfile.TemporaryDirectory() as d:
            got=diagnose(plan=f['plan'],runs=[(f[k]['run'],'RUN00'+str(i),f[k]['vm']) for i,k in enumerate(('api','auth'),1)],quality=None,receipts=[],dest=Path(d)/'phase6')
            self.assertEqual(got['status'],'PARTIAL')
            self.assertEqual(got['summary']['vm_verified_runs'],1)
            self.assertTrue(any(x['code']=='INVALID_TEST_EVIDENCE' for x in got['diagnoses']))

class ApplicationReview(unittest.TestCase):
    def test_php_html_js_source_review_and_redaction(self):
        from projectiq_executioner.phase5.application_review import review_application,merge_credential_indicators
        files=[SimpleNamespace(path='logic/read.php',language='PHP',text='<?php\n$x=$pdo->query("SELECT id FROM items");\n'),
               SimpleNamespace(path='auth/register.php',language='PHP',text='<?php\n$role=$_POST["role"];\n$stmt=$pdo->prepare("INSERT INTO users (role) VALUES (?)");\n'),
               SimpleNamespace(path='db/db.php',language='PHP',text='<?php\n$pass="DEMO_SECRET_PLACEHOLDER";\n$pdo=new PDO("mysql:host=localhost",$user,$pass);\n'),
               SimpleNamespace(path='public/register.html',language='HTML',text='<form action="register.php" method="POST"></form>'),
               SimpleNamespace(path='public/js/main.js',language='JavaScript',text='row.innerHTML = `<td>${item.title}</td>`;'),
               SimpleNamespace(path='logic/guarded.php',language='PHP',text='<?php\nsession_start();\nif (!isset($_SESSION["user_id"])) exit;\n$x=$pdo->query("SELECT id FROM items");'),
               SimpleNamespace(path='logic/comment.php',language='PHP',text='<?php\n// $pdo->query("SELECT id FROM items");')]
        got=review_application(files,SimpleNamespace(files={f.path:len(f.text) for f in files}))
        codes={x['code'] for x in got};self.assertTrue({'SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN','SEC_PHP_CLIENT_ROLE_INPUT','SEC_DB_LITERAL_CONFIG','HTML_FORM_TARGET_NOT_FOUND','SEC_DOM_TEMPLATE_HTML'}<=codes)
        self.assertFalse(any(x['path'] in ('logic/guarded.php','logic/comment.php') for x in got))
        self.assertNotIn('DEMO_SECRET_PLACEHOLDER',json.dumps(got))
        security=merge_credential_indicators({'findings':[],'status':'NO_INDICATORS_FOUND','truncated':False,'tracked_sensitive_files':0},got)
        self.assertEqual(security['status'],'INDICATORS_FOUND')
        self.assertEqual(security['findings'][0]['code'],'LITERAL_DB_CONFIGURATION')

    def test_generated_dump_line_noise_only(self):
        from projectiq_executioner.phase5.application_review import reduce_generated_dump_noise
        f=SimpleNamespace(language='SQL',text='-- MySQL dump\nINSERT INTO t VALUES (1);\nSELECT 1;')
        findings=[{'code':'LONG_SOURCE_LINE','line':2},{'code':'LONG_SOURCE_LINE','line':3}]
        self.assertEqual(reduce_generated_dump_noise(f,findings),[findings[1]])

if __name__=='__main__':unittest.main()
