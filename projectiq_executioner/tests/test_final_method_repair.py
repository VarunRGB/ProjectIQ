"""Regression checks for method inference, approved IDs and safe review errors."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from projectiq_executioner.phase3.method_reconciliation import reconcile_file_methods
from projectiq_executioner.orchestration.review_errors import review_error_codes
from projectiq_executioner.orchestration.contracts import OrchestrationError


def fixture():
    # Shape from the prior source-audited fixture; links reflect actual Phase 1
    # _match_api_routes output for the two local GET fetches, not catalog data.
    raw = json.loads((Path(__file__).parent/'fixtures'/'showcase_repair_evidence.json').read_text(encoding='utf8'))
    manifest = deepcopy(raw['manifest'])
    manifest.update(schema_version='1.0', phase=1, source=raw['plan']['source'])
    manifest['inventory'] = {'api_links': []}
    for path, client in [('logic/get_tasks.php','public/js/admin_dashboard.js'),
                         ('logic/get_user_tasks.php','public/js/user_dashboard.js')]:
        manifest['inventory']['api_links'].append(dict(server_path=path, server_source=path,
            server_method='UNKNOWN', server_kind='file_endpoint', client_path='../'+path,
            client_source=client, client_method='GET', status='method_unverified', confidence='medium'))
    return manifest, raw['plan']


def id_for(method,path):
    return 'TC-'+sha256(json.dumps(['HTTP_ROUTE',method,path],separators=(',',':')).encode()).hexdigest()[:16].upper()


class TestReconciliation(unittest.TestCase):
    def test_recover_both_get_targets_and_approved_id(self):
        m,_=fixture(); original=deepcopy(m)
        rows=reconcile_file_methods(m,m['routes'])
        servers={x['path']:x for x in rows if x['kind']=='file_endpoint'}
        for path,want in [('logic/get_tasks.php','TC-DBBD1D1C5869CFA1'),('logic/get_user_tasks.php','TC-F43FD3D196B888CE')]:
            self.assertEqual(servers[path]['method'],'GET')
            self.assertTrue(servers[path]['_client_method_hint'])
            self.assertEqual(id_for(servers[path]['method'],'/'+path),want)
            self.assertTrue(any(x['kind']=='client_http_call' and x['path']=='/'+path for x in rows))
        self.assertEqual(servers['auth/logout.php']['method'],'UNKNOWN')
        self.assertEqual(m,original)

    def test_absent_link_no_guess(self):
        m,_=fixture();m['inventory']['api_links']=[]
        self.assertEqual(reconcile_file_methods(m,m['routes']),m['routes'])

    def test_external_or_wrong_source_link_rejected(self):
        for key,value in [('client_path','https://example.org/logic/get_tasks.php'),('client_source','elsewhere.js'),('server_source','other/get_tasks.php'),('server_path','../logic/get_tasks.php')]:
            m,_=fixture();m['inventory']['api_links']=[dict(m['inventory']['api_links'][0],**{key:value})]
            self.assertEqual(reconcile_file_methods(m,m['routes']),m['routes'])

    def test_conflicting_method_not_guessed(self):
        m,_=fixture();m['inventory']['api_links']=m['inventory']['api_links'][:1]
        m['routes'].append(dict(next(x for x in m['routes'] if x['path']=='../logic/get_tasks.php'),method='POST'))
        self.assertEqual(reconcile_file_methods(m,m['routes']),m['routes'])

    def test_ambiguous_server_and_explicit_method_preserved(self):
        m,_=fixture();m['inventory']['api_links']=m['inventory']['api_links'][:1]
        m['routes'].append(dict(next(x for x in m['routes'] if x['path']=='logic/get_tasks.php'),kind='declared_http_route',method='POST'))
        self.assertEqual(reconcile_file_methods(m,m['routes']),m['routes'])

    def test_capped_inventory_not_used(self):
        m,_=fixture();m['inventory']['static_source_scan_capped']=True
        self.assertEqual(reconcile_file_methods(m,m['routes']),m['routes'])

    def test_order_independent(self):
        m,_=fixture();first=reconcile_file_methods(m,m['routes'])
        m['routes'].reverse();m['inventory']['api_links'].reverse()
        self.assertEqual(first,list(reversed(reconcile_file_methods(m,m['routes']))))

    def test_review_code_closed_vocabulary(self):
        self.assertEqual(review_error_codes(OrchestrationError('UNSAFE_CATALOG_PATH')),['UNSAFE_CATALOG_PATH'])
        self.assertEqual(review_error_codes(ValueError('CRUD_ENDPOINT_NOT_ESTABLISHED_IN_PHASE3')),[])
        self.assertEqual(review_error_codes(OrchestrationError('PRIVATE_VALUE_MUST_NOT_BE_PRINTED')),[])


class TestActualPlanner(unittest.TestCase):
    def test_full_generator_preserves_post_ids_and_restores_get_ids(self):
        from projectiq_executioner.phase3.generator import generate_test_plan
        from projectiq_executioner.phase3.contracts import validate_test_plan
        m,old=fixture()
        candidate={'source':m['source'],'adapter':'php_mysql_v1','status':'CANDIDATE_NEEDS_VM_VERIFICATION','probe_path':'/public/login.html'}
        plan=generate_test_plan(m,candidate)
        validate_test_plan(plan)
        by_id={c['id']:c for c in plan['test_cases']}
        for c in old['test_cases']:
            if c['target']['method'] is not None:
                self.assertIn(c['id'],by_id)
        for path,tid in [('/logic/get_tasks.php','TC-DBBD1D1C5869CFA1'),('/logic/get_user_tasks.php','TC-F43FD3D196B888CE')]:
            case=by_id[tid]
            self.assertEqual(case['target']['path'],path)
            self.assertEqual(case['target']['method'],'GET')
            self.assertEqual(case['status'],'NEEDS_ORACLE')
            self.assertEqual(case['assertions'],[])
            self.assertIn('client_server_method_hint',case['evidence_kinds'])
            self.assertNotIn(id_for(None,path),by_id)
        self.assertEqual(len(plan['test_cases']),11)
        self.assertEqual(plan['summary']['ready_for_phase4'],1)
        self.assertEqual(plan['execution_context']['operator_approval'],'NOT_EVALUATED')

if __name__=='__main__':unittest.main()
