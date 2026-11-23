"""Phase 4D-C permanent frontend pool regression tests.

Offline-only: temporary localhost processes, no Chrome, QEMU, network access,
or changes to resource policy. Live tests run against the actual guest frontend
function, not a simplified HTTP fixture.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import inspect
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from projectiq_executioner.phase2.vm_worker import guest_agent as agent

ROOT=Path(__file__).resolve().parents[2]
WORKER_CODE=r'''
import os,sys,types,threading
from pathlib import Path
if sys.platform == 'win32':
    # Resource is Unix-only; this test does not modify or query resource limits.
    sys.modules['resource'] = types.ModuleType('resource')
from projectiq_executioner.phase2.vm_worker.guest_agent import _d4_frontend_serve
mode=sys.argv[3]
if mode in ('deny_extra', 'deny_after_two'):
    original=threading.Thread.start
    starts=[0]
    permitted=4 if mode=='deny_extra' else 2
    def guarded_start(self,*args,**kwargs):
        starts[0]+=1
        if starts[0]>permitted:
            raise RuntimeError('test-only: further task starts forbidden')
        return original(self,*args,**kwargs)
    threading.Thread.start=guarded_start
_d4_frontend_serve(Path(sys.argv[1]),int(sys.argv[2]),8766)
'''


def _port():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def _get(port, timeout=1.5):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        conn.request('GET','/index.html',headers={'Connection':'close'})
        response=conn.getresponse()
        return response.status,response.read(4096)
    finally:
        conn.close()


class FrontendPoolFix(unittest.TestCase):
    def test_01_source_has_fixed_pool_without_request_threads(self):
        src=inspect.getsource(agent._d4_frontend_serve)
        self.assertIn('class ObservedServer(HTTPServer)',src)
        self.assertIn('self._pending = queue.Queue(maxsize=self._pending_limit)',src)
        self.assertIn('target=self._worker_loop',src)
        self.assertIn('self._pending.put_nowait(',src)
        self.assertIn('self.shutdown_request(request)',src)
        self.assertIn('sock.settimeout(self._socket_timeout_seconds)',src)
        self.assertIn('server.server_close()',src)
        self.assertNotIn('class ObservedServer(ThreadingHTTPServer)',src)
        self.assertNotIn('super().process_request_thread(',src)
        self.assertLess(src.index('worker.start()'),src.index('server.serve_forever('))
        self.assertNotIn('--no-sandbox',src)

    def _start(self, mode, directory, port):
        env=os.environ.copy()
        env.pop('PROJECTIQ_4DC_HTTP_DIAG',None)
        env['PYTHONPATH']=str(ROOT)+os.pathsep+env.get('PYTHONPATH','')
        proc=subprocess.Popen(
            [sys.executable,'-u','-c',WORKER_CODE,str(directory),str(port),mode],
            cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        return proc

    def _stop(self, proc):
        try:
            if proc.poll() is None:
                proc.terminate()
            _stdout, stderr=proc.communicate(timeout=3)
            self.assertLess(len(stderr),40000)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=3)
            self.fail('frontend subprocess failed to stop within 3 seconds')

    def _ready(self, proc, port):
        until=time.monotonic()+4.0
        while time.monotonic()<until:
            if proc.poll() is not None:
                self.fail('frontend process exited during startup')
            try:
                status,body=_get(port,timeout=.5)
                self.assertEqual(status,200)
                self.assertIn(b'POOL_TEST_OK',body)
                return
            except (OSError,http.client.HTTPException):
                time.sleep(.05)
        self.fail('frontend startup HTTP probe timed out')

    def test_02_live_server_remains_usable_when_new_threads_forbidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'index.html').write_text('<html>POOL_TEST_OK</html>',encoding='utf8')
            port=_port();proc=self._start('deny_extra',folder,port)
            try:
                self._ready(proc,port)
                for _ in range(8):
                    status,body=_get(port)
                    self.assertEqual(status,200)
                    self.assertIn(b'POOL_TEST_OK',body)
                self.assertIsNone(proc.poll())
            finally:
                self._stop(proc)

    def test_03_partial_request_does_not_block_other_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'index.html').write_text('<html>POOL_TEST_OK</html>',encoding='utf8')
            port=_port();proc=self._start('normal',folder,port)
            slow=None
            try:
                self._ready(proc,port)
                slow=socket.create_connection(('127.0.0.1',port),timeout=1)
                slow.sendall(b'GET /index.html HTTP/1.0\r\nHost: local')
                time.sleep(.15)
                start=time.monotonic()
                status,_=_get(port,timeout=3.0)
                self.assertEqual(status,200)
                self.assertLess(time.monotonic()-start,2.9)
            finally:
                if slow is not None:slow.close()
                self._stop(proc)

    def test_04_worker_start_failure_fails_closed_before_server_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'index.html').write_text('<html>POOL_TEST_OK</html>',encoding='utf8')
            port=_port();proc=self._start('deny_after_two',folder,port)
            try:
                _out,stderr=proc.communicate(timeout=6)
                self.assertNotEqual(proc.returncode,0)
                self.assertIn(b'NODE_FULLSTACK_FRONTEND_WORKER_POOL_UNAVAILABLE',stderr)
                self.assertLess(len(stderr),40000)
                with self.assertRaises((OSError,http.client.HTTPException)):
                    _get(port,timeout=.5)
            finally:
                if proc.poll() is None:self._stop(proc)

    def test_05_parallel_browser_like_asset_load_without_thread_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'index.html').write_text('<html>POOL_TEST_OK</html>',encoding='utf8')
            port=_port();proc=self._start('deny_extra',folder,port)
            try:
                self._ready(proc,port)
                with ThreadPoolExecutor(max_workers=8) as workers:
                    futures=[workers.submit(_get,port,3) for _ in range(12)]
                    values=[f.result(timeout=5) for f in futures]
                self.assertEqual(len(values),12)
                self.assertTrue(all(status==200 and b'POOL_TEST_OK' in body
                                    for status,body in values))
            finally:
                self._stop(proc)


if __name__=='__main__':
    unittest.main()
