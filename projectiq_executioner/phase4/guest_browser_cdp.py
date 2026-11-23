"""Trusted, pipe-only Chrome DevTools Protocol DOM collector for ProjectIQ 4D-C.

Executed as projectiq_run inside the pre-verified application cgroup. No WebSocket
port, external URL, operator/student JavaScript or browser-sandbox bypass.
Only bounded HTML goes to the *guest controller*, which performs reviewed checks.
"""
from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

BROWSER = '/usr/bin/google-chrome-stable'
PAGE = 'http://127.0.0.1:8765/index.html'
MAX_DOM = 128_000
MAX_FRAME = 256_000
MAX_PENDING = 400_000
DEADLINE_SECONDS = 29


class CdpFailure(RuntimeError):
    pass


# Chromium network error *identifiers* only; never pass URLs, messages, or HTML.
_NET_ERROR_PATTERN = re.compile(r'net::ERR_([A-Z][A-Z0-9_]{0,47})\Z', re.ASCII)
_NET_FAILURE_PATTERN = re.compile(r'CDP_NET_ERR_[A-Z][A-Z0-9_]{0,47}\Z', re.ASCII)


def _navigation_error_code(value: object) -> str:
    if isinstance(value, str):
        match = _NET_ERROR_PATTERN.fullmatch(value)
        if match is not None:
            return 'CDP_NET_ERR_' + match.group(1)
    return 'CDP_NAVIGATION_ERROR'


# This source is embedded into the reviewed CDP helper by the guarded installer.
# It runs only in a disposable Linux VM under projectiq_run and exports no raw data.
TRACE_MARKER = 'PIQ4DC_FORENSICS '
TRACE_CLASS = 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
_COUNTER_KEYS = ('document_request', 'document_response', 'document_response_extra',
                 'document_finished', 'document_failed', 'asset_request',
                 'api_request', 'other_request', 'target_crash',
                 'runtime_exception', 'target_detached', 'redirect_count')
_EVENT_KEYS = ('pids_max', 'memory_high', 'memory_max', 'memory_oom',
               'memory_oom_kill', 'memory_oom_group_kill')
_TCP_KEYS = ('listen', 'syn_sent', 'established', 'close_wait', 'time_wait', 'other')
_PROCESS_KEYS = ('browser_children', 'network_services', 'renderers',
                 'zygotes', 'gpu_processes', 'crashpad_processes')


def _bound_count(value, maximum=9999):
    return min(value, maximum) if type(value) is int and value >= 0 else 0


def _cgroup_snapshot():
    """Exact known evaluation-slice kernel counters. Missing = None, never zero."""
    result = dict.fromkeys(_EVENT_KEYS)
    result.update({'pids_current': None, 'pids_max_limit': None,
                   'memory_current': None, 'memory_max_limit': None})
    try:
        lines = Path('/proc/self/cgroup').read_text('ascii').splitlines()
        rows = [x.split(':', 2)[2].strip('/') for x in lines if x.startswith('0::')]
        if len(rows) != 1:
            return result
        parts = rows[0].split('/')
        if (len(parts) != 6 or parts[0] != 'user.slice'
                or parts[3:5] != ['projectiq.slice', 'projectiq-eval.slice']
                or not parts[5].startswith('piq-app-')
                or not parts[5].endswith('.scope')):
            return result
        node = Path('/sys/fs/cgroup').joinpath(*parts[:5])
        if node.is_symlink() or not node.is_dir():
            return result
        files = (('pids.events', ('max',)),
                 ('memory.events', ('high', 'max', 'oom', 'oom_kill', 'oom_group_kill')))
        for name, keys in files:
            for line in (node / name).read_text('ascii')[:2048].splitlines():
                pair = line.split()
                if len(pair) == 2 and pair[0] in keys and pair[1].isascii() and pair[1].isdecimal():
                    key = 'pids_max' if name == 'pids.events' else 'memory_' + pair[0]
                    result[key] = min(int(pair[1]), 2**31-1)
        for name, key in (('pids.current', 'pids_current'),
                          ('pids.max', 'pids_max_limit'),
                          ('memory.current', 'memory_current'),
                          ('memory.max', 'memory_max_limit')):
            raw = (node / name).read_text('ascii')[:40].strip()
            if raw.isascii() and raw.isdecimal():
                result[key] = min(int(raw), 2**40)
    except (OSError, UnicodeError, ValueError):
        pass
    return result


def _cgroup_delta(before, after):
    out = {}
    for key in _EVENT_KEYS:
        b, a = before.get(key), after.get(key)
        out[key] = min(a-b, 2**31-1) if type(a) is int and type(b) is int and a >= b else None
    for key in ('pids_current', 'pids_max_limit', 'memory_current', 'memory_max_limit'):
        x = after.get(key)
        out[key] = x if type(x) is int and 0 <= x <= 2**40 else None
    return out


def _preflight_connect():
    """Same app UID/network namespace as CDP helper, no external destinations."""
    try:
        with socket.create_connection(('127.0.0.1', 8765), timeout=1.2):
            return 'CONNECTED'
    except ConnectionRefusedError:
        return 'REFUSED'
    except TimeoutError:
        return 'TIMEOUT'
    except OSError as exc:
        import errno
        known = {
            errno.EACCES: 'PERMISSION_DENIED', errno.EPERM: 'PERMISSION_DENIED',
            errno.ENETUNREACH: 'NETWORK_UNREACHABLE',
            errno.EHOSTUNREACH: 'HOST_UNREACHABLE',
            errno.ECONNRESET: 'CONNECTION_RESET',
            errno.ECONNABORTED: 'CONNECTION_ABORTED',
            errno.EADDRNOTAVAIL: 'ADDRESS_UNAVAILABLE',
            errno.EPROTONOSUPPORT: 'PROTOCOL_UNSUPPORTED',
            errno.ENOBUFS: 'NO_SOCKET_BUFFERS',
            errno.EMFILE: 'PROCESS_FD_LIMIT', errno.ENFILE: 'SYSTEM_FD_LIMIT',
            errno.EAGAIN: 'TEMPORARILY_UNAVAILABLE',
        }
        return known.get(exc.errno, 'OTHER_ERROR')
    except ValueError:
        return 'OTHER_ERROR'


def _process_sample(proc):
    result = {key: 0 for key in _PROCESS_KEYS}
    result.update({'browser_alive': None, 'browser_netns_equal': None,
                   'network_service_netns_equal': None, 'proc_tree_readable': False})
    if proc is None:
        return result
    result['browser_alive'] = proc.poll() is None
    root_pid = proc.pid
    try:
        me_netns = os.readlink('/proc/self/ns/net')
        result['browser_netns_equal'] = os.readlink(f'/proc/{root_pid}/ns/net') == me_netns
    except OSError:
        me_netns = None
    try:
        queue = [root_pid]
        seen = set()
        for _ in range(128):
            if not queue:
                break
            pid = queue.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            children = Path(f'/proc/{pid}/task/{pid}/children').read_bytes()[:4096].split()
            for child in children:
                if child.isdigit():
                    next_pid = int(child)
                    if 1 < next_pid < 2**22 and next_pid not in seen and len(queue) < 128:
                        queue.append(next_pid)
            if pid == root_pid:
                continue
            result['browser_children'] = _bound_count(result['browser_children'] + 1, 128)
            raw = Path(f'/proc/{pid}/cmdline').read_bytes()[:4096]
            if b'--type=renderer\x00' in raw:
                result['renderers'] += 1
            if b'--type=zygote\x00' in raw:
                result['zygotes'] += 1
            if b'--type=gpu-process\x00' in raw:
                result['gpu_processes'] += 1
            if (b'--type=utility\x00' in raw and
                    b'network.mojom.NetworkService' in raw):
                result['network_services'] += 1
                if me_netns is not None:
                    try:
                        same = os.readlink(f'/proc/{pid}/ns/net') == me_netns
                        result['network_service_netns_equal'] = same
                    except OSError:
                        pass
            if b'chrome_crashpad_handler' in raw:
                result['crashpad_processes'] += 1
        result['proc_tree_readable'] = True
    except (OSError, UnicodeError, ValueError):
        # A partial snapshot is not proof of zero processes.
        result['proc_tree_readable'] = False
    return result


def _tcp_snapshot():
    """Host-namespace aggregate for fixed TCP port 8765, NOT Chrome attribution."""
    out = dict.fromkeys(_TCP_KEYS, 0)
    out['readable'] = False
    states = {'0A': 'listen', '02': 'syn_sent', '01': 'established',
              '08': 'close_wait', '06': 'time_wait'}
    try:
        for path in ('/proc/net/tcp', '/proc/net/tcp6'):
            lines = Path(path).read_text('ascii')[:65536].splitlines()
            out['readable'] = True
            for line in lines[1:]:
                parts = line.split()
                if len(parts) < 4 or ':' not in parts[1] or ':' not in parts[2]:
                    continue
                ports = (parts[1].rsplit(':', 1)[-1], parts[2].rsplit(':', 1)[-1])
                if '223D' not in ports:  # hex(8765) = 0x223D
                    continue
                key = states.get(parts[3], 'other')
                out[key] = _bound_count(out[key] + 1, 200)
    except (OSError, UnicodeError, ValueError):
        out['readable'] = False
    return out


def _trace_from_cdp(cdp):
    if cdp is None:
        return {'stage': 'NOT_STARTED', 'counters': {k: 0 for k in _COUNTER_KEYS},
                'frame': 'NONE', 'document_http_status': None,
                'failure_canceled': None, 'blocked_reason': 'NOT_REPORTED',
                'request_started': False, 'response_seen': False}
    return {'stage': cdp.stage, 'counters': dict(cdp.counts),
            'frame': cdp.frame_state, 'document_http_status': cdp.document_http_status,
            'failure_canceled': cdp.failure_canceled, 'blocked_reason': cdp.blocked_reason,
            'request_started': bool(cdp.page_requests),
            'response_seen': bool(cdp.counts['document_response'])}


def _emit_failure_forensics(cdp, proc, before, app_connect):
    record = {'classification': TRACE_CLASS, 'protocol': _trace_from_cdp(cdp),
              'process': _process_sample(proc), 'kernel': _cgroup_delta(before, _cgroup_snapshot()),
              'tcp': _tcp_snapshot(), 'app_uid_connect': app_connect}
    # All fields above are fixed enums, bounded numbers, or booleans. No URLs,
    # command lines, raw HTML, headers, cookies, or process IDs are exported.
    data = json.dumps(record, separators=(',', ':'), allow_nan=False, sort_keys=True)
    if len(data) <= 3800 and data.isascii():
        os.write(2, (TRACE_MARKER + data + '\n').encode('ascii'))
        return True
    return False



# Independent, constant-only CDP lifecycle evidence. These markers are readable
# by the controller even if the large forensic JSON cannot be collected.
_ATTEMPT2_STAGES = frozenset(('ENTRY', 'SOCKET_CONTROL', 'SPAWNING', 'SPAWNED',
    'TARGET_CREATE', 'TARGET_CREATED', 'SESSION_ATTACH', 'SESSION_ATTACHED',
    'DOMAINS_ENABLE', 'DOMAINS_ENABLED', 'NAVIGATE_DISPATCH', 'NAVIGATE_REPLY',
    'DOM_EVALUATE', 'DOM_READY', 'ERROR_CAUGHT', 'FORENSIC_BEGIN',
    'FORENSIC_DONE', 'FORENSIC_FAILED', 'FORENSIC_SKIPPED', 'CLEANUP'))


def _attempt2_stage(stage: str) -> None:
    if stage not in _ATTEMPT2_STAGES:
        raise ValueError('INVALID_CDP_STAGE')
    os.write(2, ('PIQ4DC_STAGE ' + stage + '\n').encode('ascii'))


def _attempt2_emit_forensics(cdp, proc, before, app_connect) -> None:
    _attempt2_stage('FORENSIC_BEGIN')
    try:
        emitted = _emit_failure_forensics(cdp, proc, before, app_connect)
    except Exception as exc:
        # Fixed allowlist only; never publish raw exception strings or paths.
        kind = type(exc).__name__
        if kind not in ('OSError','ValueError','TypeError','KeyError',
                        'AttributeError','IndexError','RuntimeError',
                        'MemoryError','PermissionError','FileNotFoundError'):
            kind = 'OTHER'
        try:
            os.write(2, ('PIQ4DC_A3_FORENSICS_FAIL ' + kind + '\n').encode('ascii'))
        except OSError:
            pass
        _attempt2_stage('FORENSIC_FAILED')
    else:
        if not emitted:
            try:
                os.write(2, b'PIQ4DC_A3_FORENSICS_SKIP LENGTH_OR_ENCODING\n')
            except OSError:
                pass
        _attempt2_stage('FORENSIC_DONE' if emitted else 'FORENSIC_SKIPPED')


# ProjectIQ 4D-C diagnostic attempt 3. Trusted, bounded, failure-only code.
# This is inserted in guest_browser_cdp.py and runs inside the existing VM.
_A3_MARKER = 'PIQ4DC_A3 '
_A3_CLASS = 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
_A3_DATA_URL = 'data:text/html,%3Chtml%3E%3Cbody%3EPIQ4DC_A3_DATA%3C/body%3E%3C/html%3E'
_A3_MIN_BODY = b'<html><body>PIQ4DC_A3_MINIMAL</body></html>'
_A3_TARGET_PATH = b'/__piq_a3'
_A3_HEADER = (b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
              b'Content-Length: ' + str(len(_A3_MIN_BODY)).encode('ascii') +
              b'\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n')


def _a3_empty():
    return {
        'classification': _A3_CLASS, 'probe_status': 'NOT_RUN',
        'original_document_request': None, 'original_document_response': None,
        'original_document_failed': None, 'original_network_error': 'NOT_OBSERVED',
        'browser_alive': None, 'network_service_count': None, 'proc_tree_readable': None,
        'browser_netns_equal': None, 'network_service_netns_equal': None,
        'frontend_python_get': 'NOT_RUN', 'minimal_python_get': 'NOT_RUN',
        'data_nav': 'NOT_RUN', 'data_dom': 'NOT_RUN',
        'minimal_nav': 'NOT_RUN', 'minimal_dom': 'NOT_RUN',
        'minimal_tcp_accepts': None, 'minimal_http_gets': None,
    }


def _a3_http_get(port, path, timeout=1.0):
    # Fixed loopback destination, bounded response read; return no response bytes.
    import http.client
    conn = None
    try:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
        conn.request('GET', path, headers={'Connection':'close'})
        reply = conn.getresponse()
        reply.read(128)
        return 'HTTP_200' if reply.status == 200 else 'HTTP_OTHER'
    except (TimeoutError, socket.timeout):
        return 'TIMEOUT'
    except (OSError, ValueError, http.client.HTTPException):
        return 'IO_ERROR'
    finally:
        if conn is not None:
            try: conn.close()
            except OSError: pass


def _a3_start_minimal():
    # A new *ephemeral loopback* listener, no arbitrary URL or outside traffic.
    # A minimal independent HTTP baseline; never exposed beyond the VM.
    import threading
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(('127.0.0.1', 0))
        server.listen(4)
        server.settimeout(0.15)
        port = server.getsockname()[1]
        counters = {'accepted': 0, 'gets': 0}
        stop = threading.Event()

        def serve():
            end = time.monotonic() + 6.2
            while not stop.is_set() and time.monotonic() < end and counters['accepted'] < 6:
                try:
                    conn, _addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                counters['accepted'] += 1
                try:
                    conn.settimeout(0.6)
                    # Bound request-line observation to 1024 bytes; tolerate
                    # segmented TCP delivery without storing request headers.
                    line = bytearray()
                    for _ in range(8):
                        chunk = conn.recv(min(128, 1024 - len(line)))
                        if not chunk:
                            break
                        line.extend(chunk)
                        if b'\r\n' in line or len(line) >= 1024:
                            break
                    first = bytes(line).split(b'\r\n', 1)[0]
                    # No headers, peer addresses, source, or request lines logged.
                    if first == b'GET ' + _A3_TARGET_PATH + b' HTTP/1.1' or first == b'GET ' + _A3_TARGET_PATH + b' HTTP/1.0':
                        counters['gets'] += 1
                        conn.sendall(_A3_HEADER + _A3_MIN_BODY)
                    else:
                        conn.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                except (OSError, ValueError):
                    pass
                finally:
                    try: conn.close()
                    except OSError: pass
        thread = threading.Thread(target=serve, name='piq-a3-loopback', daemon=True)
        thread.start()
        return server, port, counters, stop, thread
    except BaseException:
        server.close()
        raise


def _a3_navigate(cdp, url, *, seconds):
    deadline = time.monotonic() + seconds
    try:
        nav = cdp.call('Page.navigate', {'url': url},
                       session=cdp.session_id, deadline=deadline)
    except CdpFailure as exc:
        return ('CDP_TIMEOUT' if exc.args == ('CDP_REPLY_TIMEOUT',) else 'CDP_ERROR')
    except (OSError, ValueError, TypeError):
        return 'CDP_ERROR'
    value = nav.get('errorText')
    if value is None:
        return 'OK'
    if isinstance(value, str):
        m = _NET_ERROR_PATTERN.fullmatch(value)
        if m is not None:
            return 'NET_' + m.group(1)
    return 'NAV_ERROR'


def _a3_dom(cdp, expected, *, seconds=1.15):
    end = time.monotonic() + seconds
    expression = ('Boolean(document.body && document.body.textContent && '
                  'document.body.textContent.includes(' + json.dumps(expected) + '))')
    while time.monotonic() < end:
        try:
            result = cdp.call('Runtime.evaluate',
                              {'expression': expression, 'returnByValue': True,
                               'awaitPromise': False}, session=cdp.session_id,
                              deadline=end)
        except CdpFailure as exc:
            return 'CDP_TIMEOUT' if exc.args == ('CDP_REPLY_TIMEOUT',) else 'CDP_ERROR'
        except (OSError, TypeError, ValueError):
            return 'CDP_ERROR'
        if result.get('result', {}).get('value') is True:
            return 'MATCH'
        time.sleep(min(0.12, max(0, end-time.monotonic())))
    return 'NO_MATCH'


def _a3_collect(cdp, proc):
    result = _a3_empty()
    if cdp is not None:
        result['original_document_request'] = cdp.counts.get('document_request')
        result['original_document_response'] = cdp.counts.get('document_response')
        result['original_document_failed'] = cdp.counts.get('document_failed')
        error = cdp.navigation_error
        if isinstance(error,str):
            if re.fullmatch(r'CDP_NET_ERR_[A-Z][A-Z0-9_]{0,47}',error,re.ASCII):
                result['original_network_error'] = 'NET_'+error[len('CDP_NET_ERR_'):]
            else:
                result['original_network_error'] = 'OTHER'
    if proc is not None:
        sample = _process_sample(proc)
        if sample['proc_tree_readable']:
            result['network_service_count'] = min(99,sample['network_services'])
        for name in ('browser_alive', 'proc_tree_readable', 'browser_netns_equal',
                     'network_service_netns_equal'):
            result[name] = sample[name]
    if cdp is None or not isinstance(cdp.session_id, str) or not cdp.session_id:
        result['probe_status'] = 'SKIPPED_NO_SESSION'
        return result
    if proc is None or proc.poll() is not None:
        result['probe_status'] = 'SKIPPED_BROWSER_EXITED'
        return result
    result['probe_status'] = 'PARTIAL'
    # First verify the real frontend with a Python HTTP GET, not just a TCP connect.
    # This control intentionally contributes to the existing server's GET counters.
    result['frontend_python_get'] = _a3_http_get(8765, '/index.html?__piq_a3_python_control=1', timeout=1.0)
    result['data_nav'] = _a3_navigate(cdp, _A3_DATA_URL, seconds=1.5)
    if result['data_nav'] == 'OK':
        result['data_dom'] = _a3_dom(cdp, 'PIQ4DC_A3_DATA', seconds=1.05)
    server = None
    stop = None
    thread = None
    try:
        server, port, counters, stop, thread = _a3_start_minimal()
        result['minimal_python_get'] = _a3_http_get(port, '/__piq_a3', timeout=0.8)
        before_a, before_g = counters['accepted'], counters['gets']
        result['minimal_nav'] = _a3_navigate(
            cdp, 'http://127.0.0.1:' + str(port) + '/__piq_a3', seconds=2.2)
        if result['minimal_nav'] == 'OK':
            result['minimal_dom'] = _a3_dom(cdp, 'PIQ4DC_A3_MINIMAL', seconds=1.05)
        # Allow pending socket operations to reach the diagnostic server.
        time.sleep(0.12)
        result['minimal_tcp_accepts'] = min(99, max(0, counters['accepted']-before_a))
        result['minimal_http_gets'] = min(99, max(0, counters['gets']-before_g))
    except (OSError, ValueError, RuntimeError):
        result['probe_status'] = 'SERVER_SETUP_FAILED'
    finally:
        if stop is not None:
            stop.set()
        if server is not None:
            try: server.close()
            except OSError: pass
        if thread is not None:
            thread.join(timeout=0.4)
    if result['probe_status'] == 'PARTIAL':
        result['probe_status'] = 'COMPLETED'
    return result


def _a3_emit(cdp, proc):
    # Keep this record independent of the larger, sometimes missing forensics.
    try:
        result = _a3_collect(cdp, proc)
    except Exception:
        result = _a3_empty()
        result['probe_status'] = 'PROBE_EXCEPTION'
    try:
        raw = json.dumps(result, separators=(',', ':'), sort_keys=True, allow_nan=False)
        if len(raw) <= 1600 and raw.isascii():
            os.write(2, (_A3_MARKER + raw + '\n').encode('ascii'))
        else:
            fallback = _a3_empty()
            fallback['probe_status'] = 'ENCODING_FAILED'
            os.write(2, (_A3_MARKER + json.dumps(fallback, separators=(',', ':')) + '\n').encode('ascii'))
    except (OSError, ValueError, TypeError):
        # A missing marker is explicitly reported and NEVER interpreted as zero.
        pass


# ProjectIQ 4D-C Attempt 4: trusted, fail-only browser transport differential.
# All exported values are fixed strings, bounded counters, or booleans.
_A4_MARKER = 'PIQ4DC_A4 '
_A4_CLASS = 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
_A4_PROBES = ('process', 'frontend', 'data', 'minimal')
_A4_FAULTS = frozenset(('NONE', 'OSError', 'ValueError', 'TypeError',
    'AttributeError', 'RuntimeError', 'IndexError', 'KeyError', 'MemoryError',
    'PermissionError', 'FileNotFoundError', 'TimeoutError', 'AssertionError',
    'BrokenPipeError', 'OTHER'))
_A4_BODY = b'<html><body>PIQ4DC_A4_MINIMAL</body></html>'
_A4_PATH = b'/__piq_a4'
_A4_HEADER = (b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
    b'Content-Length: ' + str(len(_A4_BODY)).encode('ascii') +
    b'\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n')
_A4_DATA = ('data:text/html,%3Chtml%3E%3Cbody%3E'
            'PIQ4DC_A4_DATA%3C/body%3E%3C/html%3E')


def _a4_fault(exc):
    kind = type(exc).__name__
    return kind if kind in _A4_FAULTS else 'OTHER'


def _a4_count(x):
    return min(99, x) if type(x) is int and x >= 0 else None


def _a4_empty():
    stage = lambda: {'state': 'NOT_RUN', 'error': 'NONE'}
    return {
        'classification': _A4_CLASS, 'version': 1, 'collector': 'PARTIAL',
        'original_error': 'NOT_REPORTED',
        'original_before': {'request': None, 'response': None, 'failed': None},
        'original_after': {'request': None, 'response': None, 'failed': None},
        'process': dict(stage(), alive=None, tree=None, browser_netns=None,
                        network_netns=None, network_services=None),
        'frontend': dict(stage(), python_get='NOT_RUN'),
        'data': dict(stage(), navigate='NOT_RUN', dom='NOT_RUN'),
        'minimal': dict(stage(), python_get='NOT_RUN', navigate='NOT_RUN',
                        dom='NOT_RUN', tcp_accepted=None, first_bytes=None,
                        request_lines=None, get_requests=None, http_200=None),
    }


def _a4_step(name, status, fault='NONE'):
    # Small independent evidence markers survive a failed final JSON encoder.
    if name not in _A4_PROBES or status not in ('OK', 'FAILED', 'SKIPPED') or fault not in _A4_FAULTS:
        return
    try:
        os.write(2, ('PIQ4DC_A4_STEP ' + name + ' ' + status + ' ' + fault + '\n').encode('ascii'))
    except OSError:
        pass


def _a4_mark_error(result, name, exc):
    result[name]['state'] = 'FAILED'
    result[name]['error'] = _a4_fault(exc)
    _a4_step(name, 'FAILED', result[name]['error'])


def _a4_original_counts(cdp):
    if cdp is None:
        return {'request': None, 'response': None, 'failed': None}
    counts = getattr(cdp, 'counts', None)
    if not isinstance(counts, dict):
        return {'request': None, 'response': None, 'failed': None}
    return {
        'request': _a4_count(counts.get('document_request')),
        'response': _a4_count(counts.get('document_response')),
        'failed': _a4_count(counts.get('document_failed')),
    }


def _a4_start_minimal():
    # Independent minimal loopback HTTP service. Fixed response, ephemeral port.
    import threading
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(('127.0.0.1', 0))
        server.listen(8)
        server.settimeout(0.1)
        stats = {'tcp_accepted': 0, 'first_bytes': 0, 'request_lines': 0,
                 'get_requests': 0, 'http_200': 0}
        lock = threading.Lock()
        stop = threading.Event()

        def add(key):
            with lock:
                stats[key] = min(99, stats[key] + 1)

        def serve():
            end = time.monotonic() + 8.0
            while not stop.is_set() and time.monotonic() < end:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                add('tcp_accepted')
                try:
                    conn.settimeout(0.5)
                    received = bytearray()
                    for _ in range(8):
                        chunk = conn.recv(min(128, 1024 - len(received)))
                        if not chunk:
                            break
                        if not received:
                            add('first_bytes')
                        received.extend(chunk)
                        if b'\r\n' in received or len(received) >= 1024:
                            break
                    if b'\r\n' not in received:
                        continue
                    add('request_lines')
                    first = bytes(received).split(b'\r\n', 1)[0]
                    if first in (b'GET ' + _A4_PATH + b' HTTP/1.1',
                                 b'GET ' + _A4_PATH + b' HTTP/1.0'):
                        add('get_requests')
                        conn.sendall(_A4_HEADER + _A4_BODY)
                        add('http_200')
                    else:
                        conn.sendall(b'HTTP/1.1 404 Not Found\r\n'
                                     b'Content-Length: 0\r\nConnection: close\r\n\r\n')
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
        thread = threading.Thread(target=serve, daemon=True, name='piq-a4-minimal')
        thread.start()
        return server, server.getsockname()[1], stats, lock, stop, thread
    except BaseException:
        server.close()
        raise


def _a4_collect(cdp, proc, original_code):
    result = _a4_empty()
    if (isinstance(original_code, str) and
            re.fullmatch(r'CDP_NET_ERR_[A-Z][A-Z0-9_]{0,47}', original_code, re.ASCII)):
        result['original_error'] = 'NET_' + original_code[len('CDP_NET_ERR_'):]
    elif original_code == 'CDP_RUNTIME_ERROR':
        result['original_error'] = 'RUNTIME_ERROR'
    elif isinstance(original_code, str):
        result['original_error'] = 'OTHER'
    result['original_before'] = _a4_original_counts(cdp)

    try:
        if proc is None:
            result['process']['state'] = 'SKIPPED'
            _a4_step('process', 'SKIPPED')
        else:
            sample = _process_sample(proc)
            result['process'].update({
                'alive': sample['browser_alive'],
                'tree': sample['proc_tree_readable'],
                'browser_netns': sample['browser_netns_equal'],
                'network_netns': sample['network_service_netns_equal'],
                'network_services': (_a4_count(sample['network_services'])
                                     if sample['proc_tree_readable'] else None),
                'state': 'OK'})
            _a4_step('process', 'OK')
    except Exception as exc:
        _a4_mark_error(result, 'process', exc)

    # Python frontend control runs even if Chrome is gone.
    try:
        result['frontend']['python_get'] = _a3_http_get(
            8765, '/index.html?__piq_a4_control=1', timeout=0.85)
        result['frontend']['state'] = 'OK'
        _a4_step('frontend', 'OK')
    except Exception as exc:
        _a4_mark_error(result, 'frontend', exc)

    session = (cdp is not None and isinstance(getattr(cdp, 'session_id', None), str)
               and bool(cdp.session_id))
    alive = proc is not None and proc.poll() is None
    if not session or not alive:
        result['data']['state'] = 'SKIPPED'
        _a4_step('data', 'SKIPPED')
    else:
        try:
            result['data']['navigate'] = _a3_navigate(cdp, _A4_DATA, seconds=1.1)
            if result['data']['navigate'] == 'OK':
                result['data']['dom'] = _a3_dom(cdp, 'PIQ4DC_A4_DATA', seconds=0.7)
            result['data']['state'] = 'OK'
            _a4_step('data', 'OK')
        except Exception as exc:
            _a4_mark_error(result, 'data', exc)

    server = stop = thread = None
    try:
        server, port, stats, lock, stop, thread = _a4_start_minimal()
        result['minimal']['python_get'] = _a3_http_get(port, '/__piq_a4', timeout=0.85)
        # Only count the post-Python-control TCP/HTTP activity.
        with lock:
            before = dict(stats)
        if session and proc is not None and proc.poll() is None:
            result['minimal']['navigate'] = _a3_navigate(
                cdp, f'http://127.0.0.1:{port}/__piq_a4', seconds=1.9)
            if result['minimal']['navigate'] == 'OK':
                result['minimal']['dom'] = _a3_dom(
                    cdp, 'PIQ4DC_A4_MINIMAL', seconds=0.7)
        time.sleep(0.12)
        with lock:
            after = dict(stats)
        for field in ('tcp_accepted', 'first_bytes', 'request_lines',
                      'get_requests', 'http_200'):
            result['minimal'][field] = min(99, max(0, after[field] - before[field]))
        result['minimal']['state'] = 'OK' if session and alive else 'SKIPPED'
        _a4_step('minimal', result['minimal']['state'])
    except Exception as exc:
        _a4_mark_error(result, 'minimal', exc)
    finally:
        if stop is not None:
            stop.set()
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        if thread is not None:
            thread.join(timeout=0.65)

    result['original_after'] = _a4_original_counts(cdp)
    result['collector'] = ('COMPLETED' if all(result[x]['state'] in ('OK', 'SKIPPED')
                                         for x in _A4_PROBES) else 'PARTIAL')
    return result


def _a4_emit(cdp, proc, original_code):
    try:
        os.write(2, b'PIQ4DC_A4_ENTER\n')
    except OSError:
        pass
    try:
        result = _a4_collect(cdp, proc, original_code)
    except Exception as exc:
        result = _a4_empty()
        result['collector'] = 'FAILED'
        try:
            os.write(2, ('PIQ4DC_A4_UNEXPECTED '+_a4_fault(exc)+'\n').encode('ascii'))
        except OSError:
            pass
    try:
        payload = json.dumps(result, separators=(',', ':'), sort_keys=True,
                             ensure_ascii=True, allow_nan=False)
        if len(payload) > 4096 or not payload.isascii():
            raise ValueError('A4_RECORD_TOO_LONG')
        os.write(2, (_A4_MARKER + payload + '\n').encode('ascii'))
    except Exception as exc:
        try:
            os.write(2, ('PIQ4DC_A4_EMIT_FAILED '+_a4_fault(exc)+'\n').encode('ascii'))
        except OSError:
            pass


# ProjectIQ Phase 4D-C Attempt 5: independent, fixed-protocol socket control.
# This is observational only; do not use it to approve untrusted projects.
_A5_HELPER_MARKER = 'PIQ4DC_A5_HELPER '
_A5_HELPER_CLASS = 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
_A5_SOCKET_PHASES = frozenset(('CONNECT', 'WRITE', 'READ', 'DONE'))
_A5_SOCKET_OUTCOMES = frozenset((
    'NOT_RUN', 'HTTP_200', 'HTTP_OTHER', 'EOF_NO_RESPONSE',
    'CONNECTION_REFUSED', 'CONNECTION_RESET', 'CONNECTION_ABORTED',
    'CONNECTION_TIMEOUT', 'BROKEN_PIPE', 'PERMISSION_DENIED',
    'FD_LIMIT', 'TASK_TEMPORARILY_UNAVAILABLE', 'OTHER_OS_ERROR',
    'OTHER_FAILURE'))


def _a5_bound_resource(value):
    return value if type(value) is int and 0 <= value <= 65535 else None


def _a5_socket_error(exc):
    import errno
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return 'CONNECTION_TIMEOUT'
    numbers = {
        errno.ECONNREFUSED: 'CONNECTION_REFUSED',
        errno.ECONNRESET: 'CONNECTION_RESET',
        errno.ECONNABORTED: 'CONNECTION_ABORTED',
        errno.EPIPE: 'BROKEN_PIPE',
        errno.EACCES: 'PERMISSION_DENIED',
        errno.EPERM: 'PERMISSION_DENIED',
        errno.EMFILE: 'FD_LIMIT',
        errno.ENFILE: 'FD_LIMIT',
        errno.EAGAIN: 'TASK_TEMPORARILY_UNAVAILABLE',
    }
    # Windows WinSock codes may appear as winerror or errno. This is only
    # classification: no raw OS error strings ever leave the guest.
    winsock = {
        10061: 'CONNECTION_REFUSED',  # WSAECONNREFUSED
        10054: 'CONNECTION_RESET',    # WSAECONNRESET
        10053: 'CONNECTION_ABORTED',  # WSAECONNABORTED
        10060: 'CONNECTION_TIMEOUT',  # WSAETIMEDOUT
        10013: 'PERMISSION_DENIED',   # WSAEACCES
        10024: 'FD_LIMIT',            # WSAEMFILE
    }
    code = getattr(exc, 'winerror', None)
    if type(code) is int and code in winsock:
        return winsock[code]
    code = getattr(exc, 'errno', None)
    return numbers.get(code, winsock.get(code, 'OTHER_OS_ERROR'))


def _a5_raw_http_get(port=8765, path='/index.html?__piq_a5_helper=1'):
    # Discrete connect/write/read milestones, fixed loopback URL and request.
    # Read at most 32 bytes; NEVER return response contents.
    assert type(port) is int and 1 <= port <= 65535
    assert path == '/index.html?__piq_a5_helper=1'
    phase = 'CONNECT'
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1.0) as conn:
            phase = 'WRITE'
            conn.settimeout(1.0)
            conn.sendall(b'GET /index.html?__piq_a5_helper=1 HTTP/1.1\r\n'
                         b'Host: 127.0.0.1\r\nConnection: close\r\n\r\n')
            phase = 'READ'
            first = bytearray()
            for _ in range(6):
                block = conn.recv(min(32, 96-len(first)))
                if not block:
                    break
                first.extend(block)
                if b'\r\n' in first or len(first) >= 96:
                    break
            if not first:
                return {'phase': phase, 'outcome': 'EOF_NO_RESPONSE'}
            if bytes(first).startswith((b'HTTP/1.0 200', b'HTTP/1.1 200')):
                return {'phase': 'DONE', 'outcome': 'HTTP_200'}
            return {'phase': 'DONE', 'outcome': 'HTTP_OTHER'}
    except OSError as exc:
        return {'phase': phase, 'outcome': _a5_socket_error(exc)}
    except Exception:
        return {'phase': phase, 'outcome': 'OTHER_FAILURE'}


def _a5_uid_task_count():
    # Aggregate for the current real UID only. No PIDs, names, paths, or
    # command lines are emitted. This is a best-effort, racy observation.
    if os.name != 'posix' or not hasattr(os, 'getuid'):
        return None  # Windows has no Linux-style real UID task measurement.
    uid = os.getuid()
    total = 0
    seen = 0
    try:
        with os.scandir('/proc') as entries:
            for item in entries:
                if not item.name.isascii() or not item.name.isdecimal():
                    continue
                seen += 1
                if seen > 8192:
                    return None
                try:
                    if os.stat(item.path).st_uid != uid:
                        continue
                    with os.scandir(item.path + '/task') as threads:
                        for task in threads:
                            if task.name.isascii() and task.name.isdecimal():
                                total += 1
                                if total > 65535:
                                    return None
                except FileNotFoundError:
                    continue  # proc races with exits; sample remains best-effort
                except (OSError, PermissionError):
                    return None
        return total
    except (OSError, ValueError):
        return None


def _a5_helper_details():
    # Keep transport evidence independent of platform-specific resource APIs.
    # Linux VM still records real RLIMIT_NPROC / UID task counts; unsupported
    # platforms and measurement failures use None, NEVER an invented zero.
    transport = _a5_raw_http_get()
    try:
        uid_tasks = _a5_uid_task_count()
    except (AttributeError, OSError, ValueError, NotImplementedError):
        uid_tasks = None
    nproc_soft = nproc_hard = None
    try:
        import resource
        nproc_soft, nproc_hard = resource.getrlimit(resource.RLIMIT_NPROC)
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    return {
        'classification': _A5_HELPER_CLASS,
        'version': 1,
        'socket': transport,
        'uid_tasks': _a5_bound_resource(uid_tasks),
        'nproc_soft': _a5_bound_resource(nproc_soft),
        'nproc_hard': _a5_bound_resource(nproc_hard),
    }


def _a5_emit_helper():
    try:
        value = _a5_helper_details()
    except Exception:
        value = {
            'classification': _A5_HELPER_CLASS, 'version': 1,
            'socket': {'phase': 'CONNECT', 'outcome': 'OTHER_FAILURE'},
            'uid_tasks': None, 'nproc_soft': None, 'nproc_hard': None,
        }
    try:
        payload = json.dumps(value, separators=(',', ':'), sort_keys=True,
                             allow_nan=False, ensure_ascii=True)
        if len(payload) <= 512 and payload.isascii():
            os.write(2, (_A5_HELPER_MARKER + payload + '\n').encode('ascii'))
    except (OSError, ValueError, TypeError):
        pass


class PipeCdp:
    def __init__(self, write_fd: int, read_fd: int):
        self.write_fd = write_fd
        self.read_fd = read_fd
        self.counter = 0
        self.buffer = bytearray()
        self.pending = {}
        self.navigation_error = None
        self.loading_failed = False
        self.main_frame = None
        self.session_id = None
        self.stage = 'INITIAL'
        self.page_requests = set()
        self.counts = {key: 0 for key in _COUNTER_KEYS}
        self.frame_state = 'NONE'
        self.document_http_status = None
        self.failure_canceled = None
        self.blocked_reason = 'NOT_REPORTED'

    def _observe(self, message: dict) -> None:
        method = message.get('method')
        params = message.get('params')
        if not isinstance(params, dict):
            params = {}
        if (self.session_id is not None and isinstance(method, str)
                and method.startswith(('Network.', 'Page.', 'Runtime.', 'Inspector.'))
                and message.get('sessionId') != self.session_id):
            return
        if method == 'Network.requestWillBeSent':
            rid = params.get('requestId')
            req = params.get('request')
            url = req.get('url') if isinstance(req, dict) else None
            kind = params.get('type')
            if url == PAGE and kind == 'Document' and isinstance(rid, str) and len(rid) < 128:
                if len(self.page_requests) < 64:
                    self.page_requests.add(rid)
                self._inc('document_request')
                if params.get('redirectResponse') is not None:
                    self._inc('redirect_count')
            elif url == 'http://127.0.0.1:8765/api/sqlite-check':
                self._inc('api_request')
            elif (isinstance(url, str) and len(url) < 280 and
                  re.fullmatch(r'http://127\.0\.0\.1:8765/assets/[A-Za-z0-9_.-]{1,120}', url)):
                self._inc('asset_request')
            else:
                self._inc('other_request')
        elif method == 'Network.responseReceived':
            if (isinstance(params.get('requestId'), str) and
                    params['requestId'] in self.page_requests):
                self._inc('document_response')
                response = params.get('response')
                status = response.get('status') if isinstance(response,dict) else None
                if type(status) in (int,float) and 100 <= status <= 599 and int(status) == status:
                    self.document_http_status = int(status)
        elif method == 'Network.responseReceivedExtraInfo':
            if (isinstance(params.get('requestId'), str) and
                    params['requestId'] in self.page_requests):
                self._inc('document_response_extra')
                status = params.get('statusCode')
                if type(status) is int and 100 <= status <= 599 and self.document_http_status is None:
                    self.document_http_status = status
        elif method == 'Network.loadingFinished':
            if (isinstance(params.get('requestId'), str) and
                    params['requestId'] in self.page_requests):
                self._inc('document_finished')
        elif method == 'Network.loadingFailed':
            if (params.get('type') == 'Document' or
                    (isinstance(params.get('requestId'), str) and
                     params['requestId'] in self.page_requests)):
                self._inc('document_failed')
                self.loading_failed = True
                self.navigation_error = _navigation_error_code(params.get('errorText'))
                if type(params.get('canceled')) is bool:
                    self.failure_canceled = params['canceled']
                reason = params.get('blockedReason')
                self.blocked_reason = reason if reason in {
                    'csp','mixed-content','origin','inspector','subresource-filter',
                    'content-type','coep-frame-resource-needs-coep-header',
                    'other','corp-not-same-origin','corp-not-same-origin-after-defaulted-to-same-origin-by-coep',
                    'corp-not-same-site'} else 'NOT_REPORTED'
        elif method == 'Page.frameNavigated':
            frame = params.get('frame')
            if isinstance(frame, dict) and not frame.get('parentId'):
                self.main_frame = frame.get('url')
                self.frame_state = ('TARGET' if self.main_frame == PAGE else
                                    'ERROR_PAGE' if self.main_frame == 'chrome-error://chromewebdata/' else 'OTHER')
        elif method in ('Inspector.targetCrashed', 'Target.targetCrashed'):
            self._inc('target_crash')
        elif method == 'Runtime.exceptionThrown':
            self._inc('runtime_exception')
        elif method == 'Target.detachedFromTarget':
            self._inc('target_detached')

    def _inc(self, key):
        self.counts[key] = min(99, self.counts[key] + 1)


    def _receive(self, deadline: float) -> dict:
        while True:
            pos = self.buffer.find(0)
            if pos >= 0:
                if pos > MAX_FRAME:
                    raise CdpFailure('CDP_FRAME_TOO_LARGE')
                chunk = bytes(self.buffer[:pos])
                del self.buffer[:pos+1]
                try:
                    message = json.loads(chunk)
                except (UnicodeError, ValueError) as exc:
                    raise CdpFailure('CDP_INVALID_JSON') from exc
                if not isinstance(message, dict):
                    raise CdpFailure('CDP_INVALID_MESSAGE')
                self._observe(message)
                return message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpFailure('CDP_REPLY_TIMEOUT')
            ready, _, _ = select.select([self.read_fd], [], [], min(remaining, 0.5))
            if ready:
                data = os.read(self.read_fd, 65536)
                if not data:
                    raise CdpFailure('CDP_PIPE_CLOSED')
                self.buffer.extend(data)
                if len(self.buffer) > MAX_PENDING:
                    raise CdpFailure('CDP_PENDING_TOO_LARGE')

    def call(self, method: str, params: dict | None = None, *, session: str | None = None,
             deadline: float) -> dict:
        self.counter += 1
        key = self.counter
        msg = {'id': key, 'method': method, 'params': params or {}}
        if session is not None:
            msg['sessionId'] = session
        raw = json.dumps(msg, separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\0'
        if len(raw) > 8192:
            raise CdpFailure('CDP_COMMAND_TOO_LARGE')
        try:
            os.write(self.write_fd, raw)
        except OSError as exc:
            raise CdpFailure('CDP_PIPE_WRITE_FAILED') from exc
        if key in self.pending:
            return self.pending.pop(key)
        while True:
            event = self._receive(deadline)
            if event.get('id') == key:
                if 'error' in event:
                    raise CdpFailure('CDP_PROTOCOL_ERROR')
                result = event.get('result')
                if not isinstance(result, dict):
                    raise CdpFailure('CDP_MISSING_RESULT')
                return result
            if type(event.get('id')) is int and 0 < event['id'] <= 100000:
                # Unexpected replies are bounded, kept only for matching calls.
                if len(self.pending) >= 64:
                    raise CdpFailure('CDP_TOO_MANY_PENDING')
                self.pending[event['id']] = event


def _start_chrome(profile: Path) -> tuple[subprocess.Popen, int, int]:
    to_child_r, to_child_w = os.pipe()
    from_child_r, from_child_w = os.pipe()
    def prepare():
        os.dup2(to_child_r, 3, inheritable=True)
        os.dup2(from_child_w, 4, inheritable=True)
        # Python pipes are CLOEXEC. If to_child_r is already fd 3, dup2(3, 3)
        # leaves it CLOEXEC, even with inheritable=True. Force both Chrome CDP
        # descriptors inheritable after dup2, before execve.
        os.set_inheritable(3, True)
        os.set_inheritable(4, True)
    args = [BROWSER, '--headless=new', '--disable-gpu',
            '--renderer-process-limit=1', '--num-raster-threads=1',
            '--disable-dev-shm-usage', '--disable-background-networking',
            '--disable-extensions', '--no-first-run', '--no-default-browser-check',
            '--no-proxy-server',
            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
            '--remote-debugging-pipe', '--user-data-dir=' + str(profile),
            'about:blank']
    try:
        proc = subprocess.Popen(args, close_fds=False,
                                preexec_fn=prepare, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        os.close(to_child_w)
        os.close(from_child_r)
        raise
    finally:
        os.close(to_child_r)
        os.close(from_child_w)
    return proc, to_child_w, from_child_r


def _collect(deadline: float, cdp: PipeCdp) -> bytes:
    # Create exactly one fresh target; never select an existing Chrome tab.
    setattr(cdp, 'stage', 'CREATING_TARGET')
    _attempt2_stage('TARGET_CREATE')
    created = cdp.call('Target.createTarget', {'url': 'about:blank'},
                       deadline=deadline)
    _attempt2_stage('TARGET_CREATED')
    page_id = created.get('targetId')
    if not isinstance(page_id, str) or not page_id:
        raise CdpFailure('CDP_NO_PAGE_TARGET')
    setattr(cdp, 'stage', 'ATTACHING_SESSION')
    _attempt2_stage('SESSION_ATTACH')
    attach = cdp.call('Target.attachToTarget',
                      {'targetId': page_id, 'flatten': True}, deadline=deadline)
    _attempt2_stage('SESSION_ATTACHED')
    session = attach.get('sessionId')
    if not isinstance(session, str) or not session:
        raise CdpFailure('CDP_NO_SESSION')
    setattr(cdp, 'session_id', session)
    setattr(cdp, 'stage', 'ENABLING_DOMAINS')
    _attempt2_stage('DOMAINS_ENABLE')
    for method in ('Page.enable', 'Network.enable', 'Runtime.enable'):
        cdp.call(method, session=session, deadline=deadline)
    _attempt2_stage('DOMAINS_ENABLED')
    setattr(cdp, 'stage', 'NAVIGATING')
    _attempt2_stage('NAVIGATE_DISPATCH')
    nav = cdp.call('Page.navigate', {'url': PAGE}, session=session, deadline=deadline)
    _attempt2_stage('NAVIGATE_REPLY')
    if 'errorText' in nav:
        code = nav.get('errorText')
        raise CdpFailure(_navigation_error_code(code))
    setattr(cdp, 'stage', 'READING_DOM')
    _attempt2_stage('DOM_EVALUATE')
    expression = 'document.documentElement ? document.documentElement.outerHTML : ""'
    last = b''
    while time.monotonic() < deadline:
        if cdp.navigation_error:
            raise CdpFailure(cdp.navigation_error)
        if cdp.main_frame == 'chrome-error://chromewebdata/':
            raise CdpFailure('CDP_NETWORK_ERROR')
        state = cdp.call('Runtime.evaluate',
                         {'expression': expression, 'returnByValue': True,
                          'awaitPromise': False},
                         session=session, deadline=deadline)
        if state.get('exceptionDetails') is not None:
            raise CdpFailure('CDP_JAVASCRIPT_CONTEXT_ERROR')
        value = state.get('result', {}).get('value', '')
        if isinstance(value, str):
            last = value.encode('utf-8')
            if len(last) > MAX_DOM:
                raise CdpFailure('CDP_DOM_TOO_LARGE')
            if '<h1>ProjectIQ D4 pilot</h1>' in value and '<p>SQLite verified</p>' in value:
                _attempt2_stage('DOM_READY')
                return last
        time.sleep(0.25)
    if not last:
        raise CdpFailure('CDP_EMPTY_DOM')
    return last


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


def main() -> int:
    # Only a fixed, read-only renderer attached to the already-reviewed app.
    if sys.platform != 'linux' or os.geteuid() == 0 or len(sys.argv) != 1:
        return 118
    home = os.environ.get('PROJECTIQ_QUOTA_HOME') or os.environ.get('HOME', '')
    quota = Path(home)
    if not quota.is_dir() or not str(quota).startswith('/srv/projectiq-workspace/'):
        return 118
    profile = quota / 'phase4dc-cdp-profile'
    proc = None
    cdp = None
    descriptors = ()
    kernel_before = {}
    app_connect = 'OTHER_ERROR'
    _attempt2_stage('ENTRY')
    try:
        kernel_before = _cgroup_snapshot()
        _attempt2_stage('SOCKET_CONTROL')
        app_connect = _preflight_connect()
        _attempt2_stage('SPAWNING')
        proc, write_fd, read_fd = _start_chrome(profile)
        _attempt2_stage('SPAWNED')
        descriptors = (write_fd, read_fd)
        cdp = PipeCdp(write_fd, read_fd)
        html = _collect(time.monotonic() + DEADLINE_SECONDS, cdp)
        if not html or len(html) > MAX_DOM:
            return 119
        view = memoryview(html)
        while view:
            written = os.write(1, view)
            if written <= 0:
                raise CdpFailure('CDP_PIPE_WRITE_FAILED')
            view = view[written:]
        return 0
    except CdpFailure as exc:
        _attempt2_stage('ERROR_CAUGHT')
        # Never log Chrome/page text. The caller maps fixed diagnostic codes.
        code = exc.args[0]
        if not _NET_FAILURE_PATTERN.fullmatch(code) and code not in {
                        'CDP_FRAME_TOO_LARGE', 'CDP_INVALID_JSON', 'CDP_INVALID_MESSAGE',
                        'CDP_REPLY_TIMEOUT', 'CDP_PIPE_CLOSED', 'CDP_PENDING_TOO_LARGE',
                        'CDP_COMMAND_TOO_LARGE', 'CDP_PIPE_WRITE_FAILED',
                        'CDP_PROTOCOL_ERROR', 'CDP_MISSING_RESULT', 'CDP_TOO_MANY_PENDING',
                        'CDP_NO_PAGE_TARGET', 'CDP_NO_SESSION', 'CDP_NAVIGATION_ERROR',
                        'CDP_ADMIN_POLICY_BLOCK', 'CDP_CLIENT_BLOCK',
                        'CDP_CONNECTION_REFUSED', 'CDP_CONNECTION_RESET',
                        'CDP_CONNECTION_TIMEOUT', 'CDP_ADDRESS_UNREACHABLE',
                        'CDP_NETWORK_ERROR', 'CDP_JAVASCRIPT_CONTEXT_ERROR',
                        'CDP_DOM_TOO_LARGE', 'CDP_EMPTY_DOM'}:
            code = 'CDP_UNKNOWN_FAILURE'
        os.write(2, (code + '\n').encode('ascii'))
        _a4_emit(cdp, proc, code)
        _a5_emit_helper()
        _attempt2_emit_forensics(cdp, proc, kernel_before, app_connect)
        return 119
    except Exception as exc:
        _attempt2_stage('ERROR_CAUGHT')
        # Catch diagnostics/helper bugs too, while never exposing exception text.
        kind = type(exc).__name__
        if kind not in ('OSError', 'ValueError', 'TypeError', 'RuntimeError',
                        'IndexError', 'KeyError', 'AttributeError', 'MemoryError',
                        'BrokenPipeError', 'FileNotFoundError', 'PermissionError',
                        'TimeoutError', 'AssertionError'):
            kind = 'OTHER'
        os.write(2, ('PIQ4DC_FAULT ' + kind + '\n').encode('ascii'))
        os.write(2, b'CDP_RUNTIME_ERROR\n')
        _a4_emit(cdp, proc, 'CDP_RUNTIME_ERROR')
        _a5_emit_helper()
        _attempt2_emit_forensics(cdp, proc, kernel_before, app_connect)
        return 119
    finally:
        try:
            _attempt2_stage('CLEANUP')
        except OSError:
            pass
        for fd in descriptors:
            try:
                os.close(fd)
            except OSError:
                pass
        if proc is not None:
            _stop(proc)


if __name__ == '__main__':
    raise SystemExit(main())
