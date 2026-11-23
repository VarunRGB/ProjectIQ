"""Phase 4D-C Attempt 4 regression: *failure-path* reliability and strict evidence validation.

No VM, real Chrome, Internet or external fixtures needed. Browser controls use a
fixed loopback listener and a deterministic fake CDP connection.
"""
import json
import os
import re
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase4 import guest_browser_cdp as h
from projectiq_executioner.phase4 import guest_browser_ui as ui


class Proc:
    pid=os.getpid()
    def poll(self): return None


class CDP:
    session_id='STABLE_SESSION'
    navigation_error='CDP_NET_ERR_SOCKET_NOT_CONNECTED'

    def __init__(self, minimal_fail=False, data_fail=False):
        self.counts={'document_request':1,'document_response':0,'document_failed':1}
        self.minimal_fail=minimal_fail
        self.data_fail=data_fail
        self.requests=[]

    def call(self, method, params=None, *, session=None, deadline=None):
        if session!='STABLE_SESSION':
            raise AssertionError('unexpected CDP session')
        if method=='Page.navigate':
            url=params['url']
            self.requests.append(url)
            if url.startswith('data:') and self.data_fail:
                return {'errorText':'net::ERR_SOCKET_NOT_CONNECTED'}
            if url.startswith('http://127.0.0.1:'):
                if self.minimal_fail:
                    return {'errorText':'net::ERR_SOCKET_NOT_CONNECTED'}
                from urllib.parse import urlsplit
                part=urlsplit(url)
                status=h._a3_http_get(part.port,part.path,timeout=0.7)
                if status!='HTTP_200':
                    return {'errorText':'net::ERR_CONNECTION_REFUSED'}
            return {}
        if method=='Runtime.evaluate':
            return {'result': {'value':True}}
        raise AssertionError('unexpected CDP method')


class Attempt4Tests(unittest.TestCase):
    def test_01_pid_children_bytes_regression(self):
        # Reproduce the exact bug triggered in the guest: bytes have no isdecimal.
        self.assertFalse(hasattr(b'1234', 'isdecimal'))
        self.assertTrue(b'1234'.isdigit())
        original=Path.read_bytes
        def fake_read(path):
            val=path.as_posix()
            if val.endswith('/children'):
                return b'998001\n' if '/998001/' not in val else b''
            if val.endswith('/cmdline'):
                return b'chrome\x00--type=utility\x00network.mojom.NetworkService\x00'
            return original(path)
        class FakeProc:
            pid=998000
            def poll(self): return None
        with patch.object(Path,'read_bytes',fake_read):
            with patch.object(h.os,'readlink',return_value='net:[123]'):
                sample=h._process_sample(FakeProc())
        self.assertTrue(sample['proc_tree_readable'])
        self.assertEqual(sample['network_services'],1)
        self.assertTrue(sample['network_service_netns_equal'])

    def test_02_fixed_minimal_server(self):
        server, port, stats, lock, stop, thread=h._a4_start_minimal()
        try:
            self.assertEqual(h._a3_http_get(port,'/__piq_a4'),'HTTP_200')
            self.assertEqual(h._a3_http_get(port,'/wrong'),'HTTP_OTHER')
            with lock:
                self.assertEqual(stats['tcp_accepted'],2)
                self.assertEqual(stats['get_requests'],1)
                self.assertEqual(stats['http_200'],1)
                self.assertEqual(stats['request_lines'],2)
                self.assertEqual(stats['first_bytes'],2)
        finally:
            stop.set(); server.close(); thread.join(timeout=1.5)
        self.assertFalse(thread.is_alive())

    def test_03_full_probe_even_if_frontend_python_io_fails(self):
        x=CDP()
        with patch.object(h,'_a3_http_get',side_effect=self._http_succeeds_except_frontend):
            record=h._a4_collect(x,Proc(),'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(record['frontend']['state'],'FAILED')
        self.assertEqual(record['frontend']['error'],'AttributeError')
        self.assertEqual(record['data']['dom'],'MATCH')
        self.assertEqual(record['minimal']['python_get'],'HTTP_200')
        self.assertEqual(record['minimal']['state'],'OK')
        self.assertEqual(record['collector'],'PARTIAL')
        ui._a4_validate_details(record)

    @staticmethod
    def _http_succeeds_except_frontend(port,path,timeout=1.0):
        if port==8765:
            raise AttributeError('private exception text must never leave the VM')
        return 'HTTP_200'

    def test_04_successful_differential_preserves_connections(self):
        record=h._a4_collect(CDP(),Proc(),'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(record['original_error'],'NET_SOCKET_NOT_CONNECTED')
        self.assertEqual(record['data']['navigate'],'OK')
        self.assertEqual(record['data']['dom'],'MATCH')
        self.assertEqual(record['minimal']['python_get'],'HTTP_200')
        self.assertEqual(record['minimal']['navigate'],'OK')
        self.assertEqual(record['minimal']['dom'],'MATCH')
        self.assertGreaterEqual(record['minimal']['tcp_accepted'],1)
        self.assertGreaterEqual(record['minimal']['get_requests'],1)
        ui._a4_validate_details(record)

    def test_05_minimal_chrome_failure_but_python_succeeds(self):
        d=h._a4_collect(CDP(minimal_fail=True),Proc(),'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(d['minimal']['python_get'],'HTTP_200')
        self.assertEqual(d['minimal']['navigate'],'NET_SOCKET_NOT_CONNECTED')
        self.assertEqual(d['minimal']['get_requests'],0)
        self.assertEqual(d['minimal']['dom'],'NOT_RUN')
        ui._a4_validate_details(d)

    def test_06_data_control_failure_still_runs_minimal(self):
        d=h._a4_collect(CDP(data_fail=True),Proc(),'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(d['data']['navigate'],'NET_SOCKET_NOT_CONNECTED')
        self.assertEqual(d['minimal']['python_get'],'HTTP_200')
        self.assertEqual(d['minimal']['navigate'],'OK')
        ui._a4_validate_details(d)

    def test_07_process_sampling_exception_isolated(self):
        with patch.object(h,'_process_sample',side_effect=AttributeError('SECRET_PATH')):
            d=h._a4_collect(CDP(),Proc(),'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(d['process']['state'],'FAILED')
        self.assertEqual(d['process']['error'],'AttributeError')
        self.assertEqual(d['data']['navigate'],'OK')
        self.assertEqual(d['minimal']['python_get'],'HTTP_200')
        ui._a4_validate_details(d)

    def test_08_minimal_server_failure_preserves_prior_results(self):
        with patch.object(h,'_a4_start_minimal',side_effect=ValueError('PRIVATE')):
            d=h._a4_collect(CDP(),Proc(),'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(d['process']['state'],'OK')
        self.assertEqual(d['data']['navigate'],'OK')
        self.assertEqual(d['minimal']['state'],'FAILED')
        self.assertEqual(d['minimal']['error'],'ValueError')
        ui._a4_validate_details(d)

    def test_09_no_session_still_tests_python_and_minimal_server(self):
        with patch.object(h,'_a3_http_get',return_value='HTTP_200'):
            d=h._a4_collect(None,None,'CDP_RUNTIME_ERROR')
        self.assertEqual(d['process']['state'],'SKIPPED')
        self.assertEqual(d['data']['state'],'SKIPPED')
        self.assertEqual(d['minimal']['state'],'SKIPPED')
        self.assertEqual(d['minimal']['python_get'],'HTTP_200')
        ui._a4_validate_details(d)

    def test_10_wire_parse_roundtrip_and_forensic_status(self):
        d=h._a4_empty()
        sample=b'PIQ4DC_A4_ENTER\nPIQ4DC_A4_STEP process OK NONE\n'
        sample += b'PIQ4DC_A4 '+json.dumps(d,separators=(',',':')).encode()+b'\n'
        parsed=ui._a4_parse(sample)
        self.assertEqual(parsed['collection_state'],'VALID')
        self.assertTrue(parsed['entered'])
        self.assertEqual(parsed['steps'],['process:OK:NONE'])
        ui._a4_validate_report(parsed)

    def test_11_capture_emitted_record_no_exception_text(self):
        seen=[]
        def sink(fd,data):
            self.assertEqual(fd,2)
            seen.append(data)
            return len(data)
        with patch.object(h.os,'write',side_effect=sink):
            with patch.object(h,'_a4_collect',return_value=h._a4_empty()):
                h._a4_emit(None,None,'CDP_RUNTIME_ERROR')
        data=b''.join(seen)
        self.assertIn(b'PIQ4DC_A4_ENTER',data)
        parsed=ui._a4_parse(data)
        self.assertEqual(parsed['collection_state'],'VALID')
        self.assertLess(len(data),4096)

    def test_12_top_level_probe_failure_emits_bounded_fault(self):
        chunks=[]
        with patch.object(h.os,'write',side_effect=lambda fd,b: (chunks.append(b),len(b))[1]):
            with patch.object(h,'_a4_collect',side_effect=AttributeError('COOKIE=SECRET')):
                h._a4_emit(None,None,'CDP_RUNTIME_ERROR')
        raw=b''.join(chunks)
        self.assertNotIn(b'COOKIE',raw)
        self.assertNotIn(b'SECRET',raw)
        p=ui._a4_parse(raw)
        self.assertEqual(p['collection_state'],'VALID')
        self.assertEqual(p['unexpected'],'AttributeError')
        self.assertEqual(p['details']['collector'],'FAILED')

    def test_13_report_validation_blocks_unknown_fields(self):
        d=h._a4_empty()
        for mutate in (
            lambda x:x.update({'leak':'http://secret'}),
            lambda x:x['process'].update({'alive':'true'}),
            lambda x:x['minimal'].update({'tcp_accepted':False}),
            lambda x:x['frontend'].update({'python_get':'http://private'}),
            lambda x:x['frontend'].update({'python_get':['HTTP_200']}),
            lambda x:x['data'].update({'dom':['MATCH']}),
            lambda x:x['minimal'].update({'dom':['MATCH']}),
            lambda x:x['data'].update({'dom':'MATCH'}),
            lambda x:x['process'].update({'state':'OK','error':'AttributeError'}),
        ):
            y=json.loads(json.dumps(d))
            mutate(y)
            with self.assertRaises(ui.BrowserUiError):
                ui._a4_validate_details(y)

    def test_14_duplicate_malformed_missing_and_oversize(self):
        d=h._a4_empty()
        row=b'PIQ4DC_A4 '+json.dumps(d).encode()+b'\n'
        self.assertEqual(ui._a4_parse(row+row)['collection_state'],'DUPLICATE')
        self.assertEqual(ui._a4_parse(b'PIQ4DC_A4 {bad}\n')['collection_state'],'MALFORMED')
        self.assertEqual(ui._a4_parse(b'')['collection_state'],'ABSENT')
        self.assertEqual(ui._a4_parse(None)['collection_state'],'STDERR_UNAVAILABLE')
        self.assertEqual(ui._a4_parse(b'PIQ4DC_A4 '+b'X'*4097)['collection_state'],'OVERSIZE')
        self.assertEqual(ui._a4_parse(b'X'*65537)['collection_state'],'STDERR_TRUNCATED')

    def test_15_original_forensic_record_survives_child_processes(self):
        # Smoke for the previously failing combined process sample & CDP path.
        class FakeCDP:
            stage='NAVIGATING'; frame_state='NONE'; document_http_status=None
            failure_canceled=None; blocked_reason='NOT_REPORTED'; page_requests=set()
            counts={k:0 for k in h._COUNTER_KEYS}
        buf=[]
        with patch.object(h.os,'write',side_effect=lambda fd,b: (buf.append(b),len(b))[1]):
            with patch.object(h,'_cgroup_snapshot',return_value={}):
                with patch.object(h,'_tcp_snapshot',return_value={'readable':False}):
                    self.assertTrue(h._emit_failure_forensics(FakeCDP(),None,{},'CONNECTED'))
        self.assertTrue(b''.join(buf).startswith(b'PIQ4DC_FORENSICS '))

    def test_16_data_probe_exception_does_not_erase_minimal_control(self):
        original=h._a3_navigate
        def broken_data(cdp,url,*,seconds):
            if url.startswith('data:'):
                raise AttributeError('private Chrome details should not leave the VM')
            return original(cdp,url,seconds=seconds)
        with patch.object(h,'_a3_navigate',side_effect=broken_data):
            record=h._a4_collect(CDP(),Proc(),'CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(record['data']['state'],'FAILED')
        self.assertEqual(record['data']['error'],'AttributeError')
        self.assertEqual(record['minimal']['state'],'OK')
        self.assertEqual(record['minimal']['python_get'],'HTTP_200')
        ui._a4_validate_details(record)

    def test_17_failed_final_serialization_preserves_stage_marker(self):
        chunks=[]
        with patch.object(h,'_a4_collect',return_value=h._a4_empty()):
            with patch.object(h.os,'write',side_effect=lambda fd,b: (chunks.append(b),len(b))[1]):
                with patch.object(h.json,'dumps',side_effect=ValueError('private value')):
                    h._a4_emit(None,None,'CDP_RUNTIME_ERROR')
        raw=b''.join(chunks)
        parsed=ui._a4_parse(raw)
        self.assertTrue(parsed['entered'])
        self.assertEqual(parsed['emit_failed'],'ValueError')
        self.assertEqual(parsed['collection_state'],'ABSENT')
        self.assertNotIn(b'private value',raw)

    def test_18_fail_only_controller_contract(self):
        a=h._a4_empty()
        r={'collection_state':'VALID','details':a,'steps':[],
           'entered':True,'unexpected':'NONE','emit_failed':'NONE'}
        ui._a4_validate_report(r)
        with self.assertRaises(ui.BrowserUiError):
            ui._a4_validate_report({**r,'steps':['process:OK:NONE','process:OK:NONE']})
        with self.assertRaises(ui.BrowserUiError):
            ui._a4_validate_report({**r,'steps':['frontend:FAILED:NONE']})


if __name__=='__main__':
    unittest.main()
