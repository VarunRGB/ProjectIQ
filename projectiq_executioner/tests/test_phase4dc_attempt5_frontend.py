"""Attempt 5: strict wire contracts and real frontend-transport negative paths.

No VM or Chrome is started by these offline tests. The server-failure regression
runs only a temporary localhost fixture, never the real guest agent entrypoint.
"""
from __future__ import annotations
import copy
import http.client
import json
import os
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase4 import guest_browser_cdp as cdp
from projectiq_executioner.phase4 import guest_browser_ui as ui
from projectiq_executioner.phase2.vm_worker import guest_agent as agent


class Attempt5Helper(unittest.TestCase):
    def _wire(self, record):
        return b'PIQ4DC_A5_HELPER ' + json.dumps(record).encode('ascii') + b'\n'

    def test_01_bounded_helper_emit_and_parse(self):
        seen=[]
        with patch.object(cdp.os,'write',side_effect=lambda fd,b: (seen.append(b),len(b))[1]):
            with patch.object(cdp,'_a5_raw_http_get',return_value={
                'phase':'READ','outcome':'EOF_NO_RESPONSE'}):
                with patch.object(cdp,'_a5_uid_task_count',return_value=150):
                    cdp._a5_emit_helper()
        raw=b''.join(seen)
        self.assertEqual(len(seen),1)
        self.assertLess(len(raw),700)
        parsed=ui._a5_parse_helper(raw)
        self.assertEqual(parsed['collection_state'],'VALID')
        self.assertEqual(parsed['details']['socket']['outcome'],'EOF_NO_RESPONSE')
        self.assertEqual(parsed['details']['uid_tasks'],150)
        ui._a5_validate_helper_report(parsed)

    def test_02_parsing_fails_closed(self):
        good=cdp._a5_helper_details
        sample={'classification':'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
            'version':1,'socket':{'phase':'READ','outcome':'EOF_NO_RESPONSE'},
            'uid_tasks':150,'nproc_soft':512,'nproc_hard':512}
        wire=self._wire(sample)
        self.assertEqual(ui._a5_parse_helper(wire)['collection_state'],'VALID')
        self.assertEqual(ui._a5_parse_helper(wire+wire)['collection_state'],'DUPLICATE')
        self.assertEqual(ui._a5_parse_helper(b'PIQ4DC_A5_HELPER {oops}\n')['collection_state'],'MALFORMED')
        self.assertEqual(ui._a5_parse_helper(b'')['collection_state'],'ABSENT')
        self.assertEqual(ui._a5_parse_helper(None)['collection_state'],'STDERR_UNAVAILABLE')
        self.assertEqual(ui._a5_parse_helper(b'x'*65537)['collection_state'],'STDERR_TRUNCATED')
        self.assertEqual(ui._a5_parse_helper(b'PIQ4DC_A5_HELPER '+b'X'*513)['collection_state'],'OVERSIZE')
        for bad in ({'url':'http://private'}, {'uid_tasks':True},
                    {'socket':{'phase':'SNEAK','outcome':'EOF_NO_RESPONSE'}},
                    {'classification':'SECURITY_ATTESTED'}, {'nproc_hard':-1}):
            r=copy.deepcopy(sample)
            r.update(bad)
            with self.subTest(bad=bad):
                self.assertEqual(ui._a5_parse_helper(self._wire(r))['collection_state'],'MALFORMED')
        self.assertIs(good,cdp._a5_helper_details)

    def test_03_socket_exception_errno_mapping(self):
        import errno
        tests={errno.ECONNRESET:'CONNECTION_RESET',errno.ECONNREFUSED:'CONNECTION_REFUSED',
            errno.ECONNABORTED:'CONNECTION_ABORTED',errno.EPIPE:'BROKEN_PIPE',
            errno.EAGAIN:'TASK_TEMPORARILY_UNAVAILABLE',
            errno.EMFILE:'FD_LIMIT',errno.EACCES:'PERMISSION_DENIED'}
        for num,expected in tests.items():
            with self.subTest(num=num):
                self.assertEqual(cdp._a5_socket_error(OSError(num,'secret')),
                                 expected)
        self.assertEqual(cdp._a5_socket_error(TimeoutError()),'CONNECTION_TIMEOUT')

    def test_04_real_200_and_empty_response(self):
        for mode,expected in [('ok','HTTP_200'),('empty','EOF_NO_RESPONSE')]:
            ready=threading.Event()
            server=socket.socket();server.bind(('127.0.0.1',0))
            server.listen(1);server.settimeout(1.8)
            port=server.getsockname()[1]
            def serve():
                ready.set()
                try:
                    conn,_=server.accept()
                    with conn:
                        _=conn.recv(256)
                        if mode=='ok':
                            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')
                finally:
                    server.close()
            thread=threading.Thread(target=serve,daemon=True)
            thread.start();ready.wait(1)
            try:
                got=cdp._a5_raw_http_get(port=port)
                self.assertEqual(got['outcome'],expected)
                self.assertEqual(got['phase'],'DONE' if mode=='ok' else 'READ')
            finally:
                thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_05_refused_connection_is_exact(self):
        # A freshly closed OS port can be filtered/raced and return TIMEOUT.
        # Assert the exact mapping by deterministically injecting the errno.
        import errno
        synthetic = ConnectionRefusedError(errno.ECONNREFUSED, 'synthetic')
        with patch.object(cdp.socket, 'create_connection', side_effect=synthetic):
            actual = cdp._a5_raw_http_get()
        self.assertEqual(actual, {'phase':'CONNECT','outcome':'CONNECTION_REFUSED'})

    def test_05b_windows_winsock_mapping(self):
        for win_error, expected in ((10061,'CONNECTION_REFUSED'),
                                    (10054,'CONNECTION_RESET'),
                                    (10053,'CONNECTION_ABORTED'),
                                    (10060,'CONNECTION_TIMEOUT'),
                                    (10013,'PERMISSION_DENIED'),
                                    (10024,'FD_LIMIT')):
            with self.subTest(win_error=win_error):
                self.assertEqual(cdp._a5_socket_error(OSError(win_error, 'synthetic')),expected)

    def test_05c_missing_resource_and_uid_failures_preserve_socket(self):
        import sys
        # Simulates Windows where `import resource` is unsupported and a
        # resource observation fails independently from the socket probe.
        record = {'phase':'READ','outcome':'EOF_NO_RESPONSE'}
        with patch.dict(sys.modules, {'resource':None}):
            with patch.object(cdp,'_a5_raw_http_get',return_value=record):
                with patch.object(cdp,'_a5_uid_task_count',side_effect=AttributeError('synthetic')):
                    result = cdp._a5_helper_details()
        self.assertEqual(result['socket'],record)
        self.assertIsNone(result['uid_tasks'])
        self.assertIsNone(result['nproc_soft'])
        self.assertIsNone(result['nproc_hard'])
        ui._a5_validate_helper_details(result)

    def test_05d_non_posix_uid_measurement_is_unknown(self):
        # Test the Windows branch even when the suite runs on Linux.
        with patch.object(cdp,'os',SimpleNamespace(name='nt')):
            self.assertIsNone(cdp._a5_uid_task_count())

    def test_06_helper_socket_does_not_return_page_bytes(self):
        s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1)
        port=s.getsockname()[1]
        def serve():
            try:
                conn,_=s.accept()
                with conn:
                    conn.recv(256)
                    conn.sendall(b'HTTP/1.1 500 SECRET PRIVATE COOKIE\r\n\r\n')
            finally:
                s.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        result=cdp._a5_raw_http_get(port=port)
        thread.join(1)
        self.assertEqual(result,{'phase':'DONE','outcome':'HTTP_OTHER'})
        self.assertNotIn('SECRET',str(result))


class Attempt5Frontend(unittest.TestCase):
    events = agent._A5_EVENTS

    def _log(self,dir,lines):
        (Path(dir)/'d4_frontend.log').write_bytes(b'PIQ4DC_HTTP READY\n'+
                                                 b'\n'.join(lines)+b'\n')

    def test_07_read_limits_uid_and_events(self):
        with tempfile.TemporaryDirectory() as d:
            self._log(d,[b'PIQ4DC_A5 LIMIT 96 96 512 512',
                         b'PIQ4DC_A5 UIDTASK 74',
                         b'PIQ4DC_A5 EVT ACCEPT',
                         b'PIQ4DC_A5 FDCOUNT 21',
                         b'PIQ4DC_A5 THREADCOUNT 2',
                         b'PIQ4DC_A5 UIDTASK 169',
                         b'PIQ4DC_A5 EVT DISPATCH_ATTEMPT',
                         b'PIQ4DC_A5 EVT DISPATCH_FAIL_RUNTIME'])
            s=agent._a5_server_snapshot(Path(d))
        self.assertEqual(s['state'],'VALID')
        self.assertEqual(s['limits']['nproc_hard'],96)
        self.assertEqual(s['samples'],[74,169])
        self.assertEqual(s['fds'],[21])
        self.assertEqual(s['threads'],[2])
        self.assertEqual(s['counts']['DISPATCH_FAIL_RUNTIME'],1)
        self.assertEqual(s['counts']['GET_ENTER'],0)

    def test_08_compare_before_during_after_and_recovery(self):
        count=lambda **xs:dict({key:0 for key in self.events},**xs)
        # (Don't pass duplicate default keys via dict() ** syntax.)
        def mk(**v):
            c={key:0 for key in self.events};c.update(v)
            return c
        l={'nproc_soft':96,'nproc_hard':96,'nofile_soft':512,'nofile_hard':512}
        b={'state':'VALID','counts':mk(ACCEPT=2,GET_ENTER=2),
           'samples':[70,72], 'fds':[11,12], 'threads':[1,2], 'limits':l}
        d={'state':'VALID','counts':mk(ACCEPT=5,GET_ENTER=2,
              DISPATCH_ATTEMPT=3,DISPATCH_FAIL_RUNTIME=3),
           'samples':[70,72,160,169,171], 'fds':[11,12,20,22,25],
           'threads':[1,2,2,2,2], 'limits':l}
        a={'state':'VALID','counts':mk(ACCEPT=6,GET_ENTER=3,
              DISPATCH_ATTEMPT=4,DISPATCH_FAIL_RUNTIME=3,DISPATCH_OK=1),
           'samples':[70,72,160,169,171,75], 'fds':[11,12,20,22,25,14],
           'threads':[1,2,2,2,2,2], 'limits':l}
        x=agent._a5_report(b,d,a,'HTTP_200')
        ui._a5_validate_frontend_report(x)
        self.assertEqual(x['during_delta']['DISPATCH_FAIL_RUNTIME'],3)
        self.assertEqual(x['during_delta']['GET_ENTER'],0)
        self.assertEqual(x['post_delta']['GET_ENTER'],1)
        self.assertEqual(x['uid_during'],{'min':160,'max':171,'count':3})
        self.assertEqual(x['uid_after']['min'],75)
        self.assertEqual(x['fds_during']['max'],25)
        self.assertEqual(x['threads_after']['count'],1)
        self.assertEqual(x['post_http'],'HTTP_200')

    def test_09_missing_and_oversize_are_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            miss=agent._a5_server_snapshot(Path(d))
            self.assertEqual(miss['state'],'LOG_MISSING')
            (Path(d)/'d4_frontend.log').write_bytes(b'x'*100001)
            large=agent._a5_server_snapshot(Path(d))
            self.assertEqual(large['state'],'LOG_TOO_LARGE')
        x=agent._a5_report(miss,miss,large,'OTHER_OS_ERROR')
        self.assertEqual(x['trace_state'],'LOG_MISSING')
        self.assertIsNone(x['during_delta'])
        ui._a5_validate_frontend_report(x)

    def test_10_unknown_marker_and_duplicate_limit_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for extra in ([b'PIQ4DC_A5 EVT UNKNOWN'],
                          [b'PIQ4DC_A5 LIMIT 96 96 512 512',b'PIQ4DC_A5 LIMIT 96 96 512 512'],
                          [b'PIQ4DC_A5 UIDTASK http://secret']):
                with self.subTest(extra=extra):
                    self._log(d,extra)
                    self.assertEqual(agent._a5_server_snapshot(Path(d))['state'],'INVALID_LOG')

    def test_11_partial_or_invalid_snapshot_cannot_forge_counts(self):
        a={'state':'VALID','counts':{k:0 for k in self.events},
           'samples':[50],'fds':[10],'threads':[1],
           'limits':{'nproc_soft':96,'nproc_hard':96,'nofile_soft':512,'nofile_hard':512}}
        b=copy.deepcopy(a);b['counts']['ACCEPT']=3;b['samples'].append(160)
        c=copy.deepcopy(a) # counter decrease from b
        r=agent._a5_report(a,b,c,'REMOTE_DISCONNECTED')
        self.assertEqual(r['trace_state'],'SNAPSHOT_INVALID')
        self.assertIsNone(r['during_delta'])
        ui._a5_validate_frontend_report(r)

    def test_12_ui_strict_report_contract(self):
        empty=agent._a5_report({'state':'LOG_MISSING'},
             {'state':'LOG_MISSING'},{'state':'LOG_MISSING'},'OTHER_FAILURE')
        ui._a5_validate_frontend_report(empty)
        for change in (lambda x:x.update({'secret':'url'}),
                       lambda x:x.update({'trace_state':'VALID'}),
                       lambda x:x['limits'].update({'nproc_hard':True}),
                       lambda x:x.update({'post_http':'secret'}),
                       lambda x:x['uid_before'].update({'count':True}),
                       lambda x:x['uid_before'].update({'count':2}),
                       lambda x:x['fds_before'].update({'max':True}),
                       lambda x:x['threads_during'].update({'count':3}),
                       lambda x:x.update({'during_delta':{'GET_ENTER':1}})):
            data=copy.deepcopy(empty);change(data)
            with self.assertRaises(ui.BrowserUiError):
                ui._a5_validate_frontend_report(data)

    def test_13_exact_post_failure_classification(self):
        class FakeConnection:
            def request(self,*args,**kwargs): pass
            def getresponse(self): raise http.client.RemoteDisconnected('private body')
            def close(self): pass
        with patch('http.client.HTTPConnection',return_value=FakeConnection()):
            self.assertEqual(agent._a5_http_get_after(),'REMOTE_DISCONNECTED')

    def test_14_post_http_200_without_reading_body(self):
        class FakeConnection:
            def request(self,*args,**kwargs): pass
            def getresponse(self): return SimpleNamespace(status=200)
            def close(self): pass
        with patch('http.client.HTTPConnection',return_value=FakeConnection()):
            self.assertEqual(agent._a5_http_get_after(),'HTTP_200')

    def test_15_frontend_preserves_no_data_if_instrumentation_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self._log(d,[b'PIQ4DC_HTTP TCP ACCEPT'])
            s=agent._a5_server_snapshot(Path(d))
            self.assertEqual(s['state'],'NOT_INSTRUMENTED')
            result=agent._a5_report(s,s,s,'CONNECTION_REFUSED')
            self.assertIsNone(result['pre_counts'])

    def test_16_only_known_header_fields_leave_guest(self):
        with tempfile.TemporaryDirectory() as d:
            self._log(d,[b'PIQ4DC_A5 LIMIT 96 96 512 512',
                         b'SECRET=abcd',b'GET /private_token HTTP/1.1',
                         b'PIQ4DC_A5 FDCOUNT 23',
                         b'PIQ4DC_A5 THREADCOUNT 2',
                         b'PIQ4DC_A5 EVT GET_ENTER'])
            s=agent._a5_server_snapshot(Path(d))
        self.assertEqual(s['state'],'VALID')
        self.assertNotIn('SECRET',str(s))
        self.assertNotIn('private_token',str(s))

    def test_17_server_fixed_pool_and_instrumentation(self):
        import inspect
        src=inspect.getsource(agent._d4_frontend_serve)
        self.assertIn("a5_write('EVT', 'DISPATCH_ATTEMPT')",src)
        self.assertIn("a5_write('EVT', 'ACCEPT')",src)
        self.assertIn('class ObservedServer(HTTPServer):',src)
        self.assertIn('self._pending.put_nowait((request, client_address))',src)
        self.assertIn('target=self._worker_loop',src)
        self.assertNotIn('super().process_request_thread',src)
        self.assertNotIn('--no-sandbox',src)

    def test_18_proving_server_thread_launch_failure_can_precede_do_get(self):
        # Deliberate injection into stdlib dispatch, *not* the user's app.
        observed={'accept':0,'get':0,'thread_fail':0}
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address=True
            daemon_threads=True
            def get_request(self):
                sock,addr=super().get_request()
                observed['accept']+=1
                return sock,addr
            def process_request(self,request,addr):
                try:
                    return super().process_request(request,addr)
                except RuntimeError:
                    observed['thread_fail']+=1
                    raise
            def handle_error(self,*args): pass
        class Handler(socketserver.BaseRequestHandler):
            def handle(self): observed['get']+=1
        with Server(('127.0.0.1',0),Handler) as server:
            with patch.object(threading.Thread,'start',side_effect=RuntimeError("can't start new thread")):
                # Directly dispatch from foreground after sending connection
                # because the patched Thread.start must not affect test setup.
                client=socket.create_connection(server.server_address,timeout=1)
                try:
                    server._handle_request_noblock()
                finally:
                    client.close()
        self.assertEqual(observed['accept'],1)
        self.assertEqual(observed['thread_fail'],1)
        self.assertEqual(observed['get'],0)

    def test_19_nproc_policy_guard_is_not_modified_by_patch(self):
        source=Path(agent.__file__).read_text('utf-8')
        self.assertIn('192 if _browser_requested else 96',source)
        self.assertIn('PIQ4DC_A5 ',source)

    def test_20_helper_report_does_not_cause_browser_pass(self):
        from projectiq_executioner.tests.test_phase4dc_network_forensics import bundle
        b=bundle();r=ui._empty(b,issue='BROWSER_CDP_NET_ERR_EMPTY_RESPONSE')
        r['attempt5_client']=ui._a5_parse_helper(b'')
        r['attempt5_frontend']=agent._a5_report({'state':'LOG_MISSING'},
             {'state':'LOG_MISSING'},{'state':'LOG_MISSING'},'OTHER_FAILURE')
        ui.validate_browser_ui_report(b,r)
        r['status']='PASS';r['issue_code']=None
        r['checks']=[{'tag':c['tag'],'matched':True} for c in b['checks']]
        with self.assertRaises(ui.BrowserUiError):
            ui.validate_browser_ui_report(b,r)

if __name__=='__main__':
    unittest.main()
