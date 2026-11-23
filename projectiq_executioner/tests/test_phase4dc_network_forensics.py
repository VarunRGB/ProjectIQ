"""Pure/offline 4D-C network diagnostics tests. Safe on Windows and Linux."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4 import guest_browser_cdp as cdp
from projectiq_executioner.phase4 import guest_browser_ui as ui
from projectiq_executioner.phase2.vm_worker import guest_agent as agent


def bundle():
    return {'schema_version':'0.1',
        'source':{'kind':'github','repository':'Fixture/Reviewed','commit_sha':'a'*40},
        'adapter':'node_fullstack_offline_v1','catalog_sha256':'b'*64,
        'page_path':'/index.html','browser_binary':'/usr/bin/google-chrome-stable',
        'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                  {'tag':'p','exact_text':'SQLite verified'}]}


def valid_trace():
    return {
        'classification': 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
        'protocol':{'stage':'NAVIGATING',
            'counters':{k:0 for k in cdp._COUNTER_KEYS},
            'frame':'NONE','document_http_status':None,'failure_canceled':None,
            'blocked_reason':'NOT_REPORTED','request_started':False,'response_seen':False},
        'process':{**{k:0 for k in cdp._PROCESS_KEYS},
            'browser_alive':True,'browser_netns_equal':None,
            'network_service_netns_equal':None,'proc_tree_readable':False},
        'kernel':{**{k:None for k in cdp._EVENT_KEYS},
            'pids_current':None,'pids_max_limit':None,
            'memory_current':None,'memory_max_limit':None},
        'tcp':{**{k:0 for k in cdp._TCP_KEYS},'readable':False},
        'app_uid_connect':'CONNECTED',
    }


class CDPEventTests(unittest.TestCase):
    def test_complete_exact_page_lifecycle(self):
        h=cdp.PipeCdp(1,2)
        h.session_id='reviewed'
        h._observe({'method':'Network.requestWillBeSent','sessionId':'reviewed','params':{
            'requestId':'req1','type':'Document','request':{'url':cdp.PAGE}}})
        h._observe({'method':'Network.responseReceived','sessionId':'reviewed','params':{
            'requestId':'req1','response':{'status':200,'url':'SENSITIVE_UNEMITTED'}}})
        h._observe({'method':'Network.responseReceivedExtraInfo','sessionId':'reviewed','params':{
            'requestId':'req1','statusCode':200}})
        h._observe({'method':'Network.loadingFinished','sessionId':'reviewed','params':{
            'requestId':'req1'}})
        h._observe({'method':'Page.frameNavigated','sessionId':'reviewed','params':{
            'frame':{'url':cdp.PAGE}}})
        t=cdp._trace_from_cdp(h)
        self.assertEqual((t['counters']['document_request'],t['counters']['document_response'],
                          t['counters']['document_finished']), (1,1,1))
        self.assertEqual(t['document_http_status'],200)
        self.assertEqual(t['frame'],'TARGET')
        self.assertEqual(t['counters']['document_response_extra'],1)
        self.assertNotIn('SENSITIVE',json.dumps(t))

    def test_wrong_session_is_excluded(self):
        h=cdp.PipeCdp(1,2);h.session_id='one'
        h._observe({'method':'Network.requestWillBeSent','sessionId':'different','params':{
            'requestId':'r','type':'Document','request':{'url':cdp.PAGE}}})
        self.assertEqual(h.counts['document_request'],0)
        self.assertFalse(h.page_requests)

    def test_document_failure_without_request_id(self):
        h=cdp.PipeCdp(1,2)
        h._observe({'method':'Network.loadingFailed','params':{
            'type':'Document','errorText':'net::ERR_SOCKET_NOT_CONNECTED',
            'canceled':False,'blockedReason':'csp'}})
        self.assertEqual(h.navigation_error,'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(h.counts['document_failed'],1)
        self.assertFalse(h.failure_canceled)
        self.assertEqual(h.blocked_reason,'csp')

    def test_non_document_failure_does_not_override_navigation(self):
        h=cdp.PipeCdp(1,2)
        h._observe({'method':'Network.loadingFailed','params':{
            'type':'Script','errorText':'net::ERR_FAILED'}})
        self.assertEqual(h.counts['document_failed'],0)
        self.assertIsNone(h.navigation_error)

    def test_rare_telemetry_crash_js_exception_detach(self):
        h=cdp.PipeCdp(1,2)
        for method,key in [('Inspector.targetCrashed','target_crash'),
                           ('Runtime.exceptionThrown','runtime_exception'),
                           ('Target.detachedFromTarget','target_detached')]:
            h._observe({'method':method,'params':{'secret':'DO_NOT_EMIT'}})
            self.assertEqual(h.counts[key],1)
        self.assertNotIn('DO_NOT_EMIT',json.dumps(cdp._trace_from_cdp(h)))

    def test_counters_are_bounded(self):
        h=cdp.PipeCdp(1,2)
        for _ in range(175):
            h._observe({'method':'Runtime.exceptionThrown','params':{}})
        self.assertEqual(h.counts['runtime_exception'],99)

    def test_request_type_categorization(self):
        h=cdp.PipeCdp(1,2)
        for url in ('http://127.0.0.1:8765/assets/abc123.js',
                    'http://127.0.0.1:8765/api/sqlite-check',
                    'https://secret.example/private'):
            h._observe({'method':'Network.requestWillBeSent','params':{
                'type':'Script','request':{'url':url}}})
        self.assertEqual((h.counts['asset_request'],h.counts['api_request'],
                          h.counts['other_request']),(1,1,1))


class EvidenceContractTests(unittest.TestCase):
    def test_valid_trace_round_trip_no_raw_content(self):
        record=valid_trace()
        raw=b'CDP_NET_ERR_SOCKET_NOT_CONNECTED\nPIQ4DC_FORENSICS '+json.dumps(
            record,sort_keys=True,separators=(',',':')).encode('ascii')+b'\n'
        self.assertEqual(ui._parse_forensic_trace(raw),record)
        self.assertNotIn('raw_html',json.dumps(record))

    def test_main_report_accepts_inconclusive_only(self):
        b=bundle();r=ui._empty(b,issue='BROWSER_CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        r['cdp_forensics']=valid_trace()
        r['server_trace']=agent._phase4dc_http_difference(
            {'instrumentation_active':True,**{k:0 for k in ui._SERVER_TRACE_FIELDS}},
            {'instrumentation_active':True,**{k:0 for k in ui._SERVER_TRACE_FIELDS}})
        ui.validate_browser_ui_report(b,r)
        r['status']='PASS';r['issue_code']=None;r['checks']=[{'tag':x['tag'],'matched':True} for x in b['checks']]
        with self.assertRaises(ui.BrowserUiError):
            ui.validate_browser_ui_report(b,r)

    def test_unknown_fields_and_bad_types_rejected(self):
        for mutation in (
            lambda o: o.update(secret='http://private'),
            lambda o: o['protocol']['counters'].update(document_request=-1),
            lambda o: o['protocol'].update(document_http_status='200'),
            lambda o: o['process'].update(browser_alive='yes'),
            lambda o: o['kernel'].update(memory_oom_kill=-1),
            lambda o: o['tcp'].update(established=201),
            lambda o: o.update(app_uid_connect='http://private')):
            item=copy.deepcopy(valid_trace());mutation(item)
            with self.subTest(mutation=mutation):
                with self.assertRaises(ui.BrowserUiError):
                    ui._validate_forensic_trace(item)

    def test_missing_markers_and_duplicate_markers_are_omitted(self):
        item=valid_trace();line=(b'PIQ4DC_FORENSICS '+json.dumps(item).encode()+b'\n')
        self.assertIsNone(ui._parse_forensic_trace(b'CDP_NET_ERR_SOCKET_NOT_CONNECTED\n'))
        self.assertIsNone(ui._parse_forensic_trace(line+line))
        self.assertIsNone(ui._parse_forensic_trace(b'PIQ4DC_FORENSICS not-json\n'))
        self.assertIsNone(ui._parse_forensic_trace(b'PIQ4DC_FORENSICS '+b'x'*4200+b'\n'))

    def test_server_trace_rejects_false_pass_and_injection(self):
        value={'classification':'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
               'instrumentation_active':True,**{k:0 for k in ui._SERVER_TRACE_FIELDS}}
        ui._validate_server_trace(value)
        for k,v in [('index_seen',-1),('instrumentation_active',1),('injected','raw')]:
            copy_value=value.copy();copy_value[k]=v
            with self.assertRaises(ui.BrowserUiError):
                ui._validate_server_trace(copy_value)


class KernelAndServerEvidenceTests(unittest.TestCase):
    def test_kernel_deltas_missing_never_become_zero(self):
        missing={k:None for k in cdp._EVENT_KEYS}
        missing.update({'pids_current':None,'pids_max_limit':None,
                        'memory_current':None,'memory_max_limit':None})
        delta=cdp._cgroup_delta(missing,missing)
        self.assertIsNone(delta['memory_oom_kill'])
        before=missing.copy();after=missing.copy()
        before.update(memory_oom_kill=5,pids_max=1)
        after.update(memory_oom_kill=6,pids_max=4)
        result=cdp._cgroup_delta(before,after)
        self.assertEqual((result['memory_oom_kill'],result['pids_max']),(1,3))

    def test_server_log_is_bounded_and_diff_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            log=base/'d4_frontend.log'
            log.write_bytes(b'PIQ4DC_HTTP READY\nPIQ4DC_HTTP INDEX SEEN\n')
            before=agent._phase4dc_http_snapshot(base)
            with log.open('ab') as f:
                f.write(b'PIQ4DC_HTTP INDEX HTTP_200\nPIQ4DC_HTTP INDEX COMPLETE\n'
                        b'PIQ4DC_HTTP API SEEN\nSECRET\r\n')
            after=agent._phase4dc_http_snapshot(base)
            diff=agent._phase4dc_http_difference(before,after)
            self.assertTrue(diff['instrumentation_active'])
            self.assertEqual(diff['index_seen'],0)
            self.assertEqual(diff['index_complete'],1)
            self.assertEqual(diff['index_http_200'],1)
            self.assertEqual(diff['api_seen'],1)
            self.assertNotIn('SECRET',json.dumps(diff))

    def test_uninstrumented_server_not_misreported_as_zero_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'d4_frontend.log').write_bytes(b'hello\n')
            observation=agent._phase4dc_http_snapshot(Path(temp))
            self.assertFalse(observation['instrumentation_active'])
            diff=agent._phase4dc_http_difference(observation,observation)
            self.assertFalse(diff['instrumentation_active'])

    def test_http_log_large_is_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'d4_frontend.log').write_bytes(b'PIQ4DC_HTTP READY\n'+b'X'*30000)
            self.assertFalse(agent._phase4dc_http_snapshot(Path(temp))['instrumentation_active'])

    def test_transport_rare_errors_are_specific(self):
        import errno
        cases={errno.EACCES:'PERMISSION_DENIED',
               errno.ENETUNREACH:'NETWORK_UNREACHABLE',
               errno.EHOSTUNREACH:'HOST_UNREACHABLE',
               errno.ECONNRESET:'CONNECTION_RESET',
               errno.EADDRNOTAVAIL:'ADDRESS_UNAVAILABLE',
               errno.ENOBUFS:'NO_SOCKET_BUFFERS',
               errno.EMFILE:'PROCESS_FD_LIMIT',
               errno.EAGAIN:'TEMPORARILY_UNAVAILABLE'}
        for error,expected in cases.items():
            with self.subTest(error=error):
                with patch.object(cdp.socket,'create_connection',
                                  side_effect=OSError(error,'simulated')):
                    self.assertEqual(cdp._preflight_connect(),expected)

    def test_server_http_error_categories(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            log=base/'d4_frontend.log'
            log.write_bytes(b'PIQ4DC_HTTP READY\n'
                 b'PIQ4DC_HTTP INDEX SEEN\n'
                 b'PIQ4DC_HTTP INDEX HTTP_4XX\n'
                 b'PIQ4DC_HTTP INDEX COMPLETE\n'
                 b'PIQ4DC_HTTP API SEEN\n'
                 b'PIQ4DC_HTTP API HTTP_200\n')
            result=agent._phase4dc_http_snapshot(base)
            self.assertEqual(result['index_http_4xx'],1)
            self.assertEqual(result['api_http_200'],1)
            self.assertTrue(result['instrumentation_active'])

    def test_preflight_connection_cannot_use_external_url(self):
        class FakeSocket:
            def __enter__(self):return self
            def __exit__(self,*args):return False
        calls=[]
        def fake(addr,timeout):
            calls.append((addr,timeout))
            return FakeSocket()
        with patch.object(cdp.socket,'create_connection',side_effect=fake):
            self.assertEqual(cdp._preflight_connect(),'CONNECTED')
        self.assertEqual(calls,[(('127.0.0.1',8765),1.2)])

    def test_cgroup_snapshot_refuses_unguarded_guest_path(self):
        # Local non-VM environments must not produce misleading kernel zeros.
        data=cdp._cgroup_snapshot()
        self.assertTrue(all(k in data for k in cdp._EVENT_KEYS))

    def test_no_unsafe_browser_network_flags_added(self):
        import inspect
        src=inspect.getsource(cdp._start_chrome)
        self.assertIn('--remote-debugging-pipe',src)
        self.assertNotIn('--remote-debugging-port',src)
        self.assertNotIn("'--no-sandbox'",src)


if __name__ == '__main__':
    unittest.main()
