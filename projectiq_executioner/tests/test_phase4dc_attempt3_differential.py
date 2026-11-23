"""Attempt-3: no QEMU/Chrome required; deterministic local tests.

The real Chrome/network behavior must be tested separately in the disposable VM.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4 import guest_browser_cdp as helper
from projectiq_executioner.phase4 import guest_browser_ui as ui


class FakeProc:
    pid = os.getpid()
    def poll(self):
        return None


class FakeCdp:
    session_id = 'TEST_SESSION'
    counts = {'document_request': 1, 'document_response': 0, 'document_failed': 1}
    navigation_error = 'CDP_NET_ERR_SOCKET_NOT_CONNECTED'

    def __init__(self, fail_minimal=False, fail_data=False):
        self.fail_minimal = fail_minimal
        self.fail_data = fail_data
        self.calls=[]

    def call(self, method, params=None, *, session=None, deadline=None):
        self.calls.append((method,params))
        if method == 'Page.navigate':
            url = params['url']
            if url.startswith('data:') and self.fail_data:
                return {'errorText':'net::ERR_SOCKET_NOT_CONNECTED'}
            if url.startswith('http://127.0.0.1:'):
                if self.fail_minimal:
                    return {'errorText':'net::ERR_SOCKET_NOT_CONNECTED'}
                # Fake Chrome's HTTP request in the test only.
                from urllib.parse import urlsplit
                split = urlsplit(url)
                self.last_response = helper._a3_http_get(split.port, split.path, timeout=0.8)
            return {}
        if method == 'Runtime.evaluate':
            return {'result': {'value': True}}
        raise AssertionError('unexpected CDP call')


class Attempt3DifferentialTests(unittest.TestCase):
    def test_minimal_http_control_and_close(self):
        server, port, counts, stop, thread = helper._a3_start_minimal()
        try:
            self.assertEqual(helper._a3_http_get(port, '/__piq_a3'), 'HTTP_200')
            self.assertEqual(counts['accepted'],1)
            self.assertEqual(counts['gets'],1)
            self.assertEqual(helper._a3_http_get(port, '/wrong'), 'HTTP_OTHER')
            self.assertEqual(counts['gets'],1)
        finally:
            stop.set()
            server.close()
            thread.join(timeout=0.5)
        self.assertFalse(thread.is_alive())

    def test_differential_success_and_chrome_attributable_observation(self):
        fake=FakeCdp()
        result=helper._a3_collect(fake, FakeProc())
        self.assertEqual(result['probe_status'],'COMPLETED')
        self.assertEqual(result['original_document_request'],1)
        self.assertEqual(result['original_document_failed'],1)
        self.assertEqual(result['data_nav'],'OK')
        self.assertEqual(result['data_dom'],'MATCH')
        self.assertEqual(result['minimal_python_get'],'HTTP_200')
        self.assertEqual(result['minimal_nav'],'OK')
        self.assertEqual(result['minimal_dom'],'MATCH')
        self.assertGreaterEqual(result['minimal_tcp_accepts'],1)
        self.assertGreaterEqual(result['minimal_http_gets'],1)
        ui._attempt3_validate_details(result)

    def test_chrome_minimal_failure_but_python_succeeds(self):
        result=helper._a3_collect(FakeCdp(fail_minimal=True), FakeProc())
        self.assertEqual(result['minimal_python_get'],'HTTP_200')
        self.assertEqual(result['minimal_nav'],'NET_SOCKET_NOT_CONNECTED')
        self.assertEqual(result['minimal_dom'],'NOT_RUN')
        self.assertEqual(result['minimal_tcp_accepts'],0)
        ui._attempt3_validate_details(result)

    def test_data_failure_is_not_reinterpreted_as_success(self):
        result=helper._a3_collect(FakeCdp(fail_data=True), FakeProc())
        self.assertEqual(result['data_nav'],'NET_SOCKET_NOT_CONNECTED')
        self.assertEqual(result['data_dom'],'NOT_RUN')
        ui._attempt3_validate_details(result)

    def test_no_session_does_not_invent_zero_network_failures(self):
        result=helper._a3_collect(None, FakeProc())
        self.assertEqual(result['probe_status'],'SKIPPED_NO_SESSION')
        self.assertIsNone(result['original_document_request'])
        self.assertIsNone(result['minimal_tcp_accepts'])
        ui._attempt3_validate_details(result)

    def test_wire_roundtrip_and_forensics_marker(self):
        value=helper._a3_empty()
        raw=(b'PIQ4DC_A3 ' + json.dumps(value,separators=(',',':')).encode() + b'\n'
             + b'PIQ4DC_STAGE FORENSIC_BEGIN\n'
             + b'PIQ4DC_STAGE FORENSIC_SKIPPED\n')
        record=ui._attempt3_parse(raw)
        self.assertEqual(record['collection_state'],'VALID')
        self.assertEqual(record['forensic_terminal'],'SKIPPED')
        self.assertEqual(record['forensic_reason'],'NOT_REPORTED')
        ui._attempt3_validate_report(record)

    def test_malformed_duplicate_and_missing_trace_are_visible(self):
        val=helper._a3_empty()
        message=b'PIQ4DC_A3 '+json.dumps(val).encode()+b'\n'
        self.assertEqual(ui._attempt3_parse(message+message)['collection_state'],'DUPLICATE')
        self.assertEqual(ui._attempt3_parse(b'PIQ4DC_A3 <script>')['collection_state'],'MALFORMED')
        self.assertEqual(ui._attempt3_parse(b'')['collection_state'],'ABSENT')
        self.assertEqual(ui._attempt3_parse(None)['collection_state'],'STDERR_UNAVAILABLE')
        self.assertEqual(ui._attempt3_parse(b'PIQ4DC_A3 '+b'A'*1601)['collection_state'],'OVERSIZE')
        self.assertEqual(ui._attempt3_parse(b'PIQ4DC_STAGE FORENSIC_FAILED\n')['forensic_terminal'],'FAILED')
        self.assertEqual(ui._attempt3_parse(b'PIQ4DC_A3_FORENSICS_FAIL ValueError\n')['forensic_reason'],'ValueError')
        self.assertEqual(ui._attempt3_parse(b'PIQ4DC_A3_FORENSICS_SKIP LENGTH_OR_ENCODING\n')['forensic_reason'],'LENGTH_OR_ENCODING')

    def test_reject_malicious_or_unbounded_fields(self):
        v=helper._a3_empty()
        with self.assertRaises(ui.BrowserUiError):
            ui._attempt3_validate_details({**v,'minimal_nav':'http://private-host'})
        with self.assertRaises(ui.BrowserUiError):
            ui._attempt3_validate_details({**v,'original_document_request':False})
        with self.assertRaises(ui.BrowserUiError):
            ui._attempt3_validate_details({**v,'extra':'raw-cookie'})
        with self.assertRaises(ui.BrowserUiError):
            ui._attempt3_validate_details({**v,'browser_alive':'true'})
        with self.assertRaises(ui.BrowserUiError):
            ui._attempt3_validate_details({**v,'original_network_error': {'bad':'type'}})
        with self.assertRaises(ui.BrowserUiError):
            ui._attempt3_validate_report({'collection_state': [],
                'forensic_terminal':'DONE','forensic_reason':'NOT_REPORTED','details':None})

    def test_probe_emit_is_bounded_and_contains_no_source(self):
        chunks=[]
        def capture(fd,data):
            self.assertEqual(fd,2)
            chunks.append(data)
            return len(data)
        with patch.object(helper,'_a3_collect',return_value=helper._a3_empty()):
            with patch.object(helper.os,'write',side_effect=capture):
                helper._a3_emit(None,None)
        raw=b''.join(chunks)
        self.assertLessEqual(len(raw),1650)
        self.assertIn(b'PIQ4DC_A3 ',raw)
        self.assertNotIn(b'cookie',raw.lower())
        self.assertEqual(ui._attempt3_parse(raw)['collection_state'],'VALID')

    def test_exact_foreign_event_tag_not_collected(self):
        a=helper._a3_empty()
        with self.assertRaises(ui.BrowserUiError):
            ui._attempt3_validate_report({'collection_state':'ABSENT',
                'forensic_terminal':'NOT_REPORTED','forensic_reason':'NOT_REPORTED','details':a})
        # Data from a different successful protocol is not an automatic PASS.
        a['probe_status']='COMPLETED'
        a['data_nav']='OK'
        a['data_dom']='MATCH'
        ui._attempt3_validate_details(a)


if __name__=='__main__':
    unittest.main()
