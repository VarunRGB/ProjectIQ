"""Legacy D4 trusted frontend must work when the Unix resource module is absent.

Runs only the trusted frontend from this checkout with synthetic HTML and a
local test backend. It never executes student project code or launches a VM.
"""
from __future__ import annotations

import http.server
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from projectiq_executioner.phase2.vm_worker import guest_agent


class TestD4WindowsResourcePortability(unittest.TestCase):
    def test_frontend_proxy_runs_without_unix_resource_module(self):
        class Backend(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/api/sqlite-check':
                    self.send_error(404)
                    return
                body = b'{"sqlite":true}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as temp:
            dist = Path(temp) / 'dist'
            dist.mkdir()
            (dist / 'index.html').write_text('<!doctype html><h1>D4 readiness</h1>', encoding='utf-8')
            backend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Backend)
            thread = threading.Thread(target=backend.serve_forever, daemon=True)
            thread.start()
            # Reserve a free loopback port; release before frontend binds it.
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            # This deliberately simulates native Windows' lack of `resource` on
            # every platform, by prohibiting an import in the child process.
            code = (
                'import sys\n'
                'from pathlib import Path\n'
                'from projectiq_executioner.phase2.vm_worker import guest_agent\n'
                'sys.modules["resource"] = None\n'
                'guest_agent._d4_frontend_serve(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))\n'
            )
            child = None
            try:
                clean_env = os.environ.copy()
                clean_env.pop('PROJECTIQ_4DC_HTTP_DIAG', None)
                child = subprocess.Popen(
                    [sys.executable, '-c', code, str(dist), str(port), str(backend.server_port)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, env=clean_env,
                )
                url = f'http://127.0.0.1:{port}/index.html'
                for _ in range(90):
                    if child.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(url, timeout=0.8) as response:
                            self.assertEqual(response.status, 200)
                            self.assertIn(b'D4 readiness', response.read())
                        break
                    except (urllib.error.URLError, OSError):
                        time.sleep(0.04)
                else:
                    self.fail('D4 frontend did not respond without the resource module')
                self.assertIsNone(child.poll(), 'D4 frontend exited early (resource import?)')
                with urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/api/sqlite-check', timeout=2
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'{"sqlite":true}')
            finally:
                if child is not None:
                    child.terminate()
                    try:
                        _out, _err = child.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.communicate(timeout=3)
                backend.shutdown()
                backend.server_close()
                thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
