"""4D-C trusted guest-only real-browser DOM probe.

Only launches an already installed, operator-pinned Chrome/Chromium from the
sealed Linux base image. Runs under the existing app UID/cgroup; no shell,
remote URLs, --no-sandbox, webdriver, new packages or student-provided JS.
This pilot asserts visible rendered tag text on the reviewed React application.
"""
from __future__ import annotations

from html.parser import HTMLParser
import http.client
import errno
from pathlib import Path
import os
import re
from typing import Any, Callable

SCHEMA = '0.1'
_BROWSER_PATHS = frozenset(('/usr/bin/google-chrome-stable', '/usr/bin/chromium',
                            '/opt/google/chrome/chrome'))
_TAGS = frozenset(('h1', 'h2', 'p', 'button'))
_SHA40 = re.compile(r'[a-f0-9]{40}\Z', re.ASCII)
_SHA64 = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_REPO = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z', re.ASCII)
_TEXT = re.compile(r'[A-Za-z0-9 _.,:!?()/+-]{1,120}\Z', re.ASCII)
MAX_DOM = 128_000


class BrowserUiError(ValueError):
    """Unsafe browser instruction, inconsistent evidence, or malformed input."""


def _deny(code: str) -> None:
    raise BrowserUiError(code)


def validate_guest_browser_bundle(bundle: Any, *, expected_source: dict | None = None,
                                  expected_adapter: str = 'node_fullstack_offline_v1') -> None:
    if (not isinstance(bundle, dict) or set(bundle) != {
            'schema_version', 'source', 'adapter', 'catalog_sha256',
            'page_path', 'checks', 'browser_binary'}
            or bundle['schema_version'] != SCHEMA
            or bundle['adapter'] != expected_adapter
            or bundle['adapter'] != 'node_fullstack_offline_v1'
            or bundle['page_path'] != '/index.html'
            or bundle['browser_binary'] not in _BROWSER_PATHS
            or not isinstance(bundle['catalog_sha256'], str)
            or not _SHA64.fullmatch(bundle['catalog_sha256'])):
        _deny('INVALID_BROWSER_UI_BUNDLE')
    src = bundle['source']
    if (not isinstance(src, dict) or set(src) != {'kind', 'repository', 'commit_sha'}
            or src.get('kind') != 'github'
            or not isinstance(src.get('repository'), str) or not _REPO.fullmatch(src['repository'])
            or not isinstance(src.get('commit_sha'), str) or not _SHA40.fullmatch(src['commit_sha'])
            or expected_source is not None and src != expected_source):
        _deny('BROWSER_UI_SOURCE_MISMATCH')
    checks = bundle['checks']
    if not isinstance(checks, list) or not 1 <= len(checks) <= 4:
        _deny('BROWSER_UI_CHECK_COUNT')
    seen = set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {'tag', 'exact_text'}
                or check.get('tag') not in _TAGS or check['tag'] in seen
                or type(check.get('exact_text')) is not str
                or not _TEXT.fullmatch(check['exact_text'])):
            _deny('INVALID_BROWSER_UI_CHECK')
        seen.add(check['tag'])


class _RenderedElements(HTMLParser):
    def __init__(self, wanted: set[str]):
        super().__init__(convert_charrefs=True)
        self.wanted = wanted
        self.stack = []
        self.open = []
        self.values: dict[str, list[str]] = {x: [] for x in wanted}

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        if tag in self.wanted:
            self.open.append((tag, len(self.stack), []))

    def handle_data(self, data):
        for _, _, chunks in self.open:
            chunks.append(data)

    def handle_endtag(self, tag):
        # Reviewed React markup has well-formed, non-void h1/p nodes.
        if not self.stack or self.stack[-1] != tag:
            return
        depth = len(self.stack)
        for item in self.open[:]:
            name, start_depth, chunks = item
            if name == tag and start_depth == depth:
                self.values[name].append(' '.join(''.join(chunks).split()))
                self.open.remove(item)
        self.stack.pop()


def dom_checks(html: str, checks: list[dict]) -> list[dict]:
    if not isinstance(html, str) or len(html.encode('utf-8')) > MAX_DOM:
        _deny('INVALID_BROWSER_DOM')
    doc = _RenderedElements({x['tag'] for x in checks})
    doc.feed(html)
    return [{'tag': x['tag'], 'matched': doc.values[x['tag']] == [x['exact_text']]}
            for x in checks]


# Failure-only structural fingerprint. Never persist raw DOM or unexpected text.
_PAGE_P_STATES = frozenset(('ABSENT', 'EXPECTED', 'INITIAL',
                            'BACKEND_UNAVAILABLE', 'SQLITE_UNAVAILABLE', 'OTHER'))
_PAGE_SIZE_BINS = frozenset(('UNDER_2K', 'UNDER_16K', 'UNDER_128K'))


class _DomFingerprintParser(_RenderedElements):
    def __init__(self):
        super().__init__({'title', 'h1', 'p'})
        self.root_present = False
        self.main_present = False
        self.vite_js_asset_present = False

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attrs = dict(attrs)
        if tag == 'div' and attrs.get('id') == 'root':
            self.root_present = True
        if tag == 'main':
            self.main_present = True
        if (tag == 'script' and isinstance(attrs.get('src'), str)
                and re.fullmatch(r'/assets/[A-Za-z0-9_.-]{1,120}\.js', attrs['src'])):
            self.vite_js_asset_present = True


def _dom_fingerprint(html: str) -> dict:
    """Bounded, allowlisted page structure, never literal content or excerpts."""
    parsed = _DomFingerprintParser()
    parsed.feed(html)
    headings = parsed.values['h1']
    paras = parsed.values['p']
    if not paras:
        state = 'ABSENT'
    elif paras == ['SQLite verified']:
        state = 'EXPECTED'
    elif paras == ['Checking backend...']:
        state = 'INITIAL'
    elif paras == ['Backend unavailable']:
        state = 'BACKEND_UNAVAILABLE'
    elif paras == ['SQLite unavailable']:
        state = 'SQLITE_UNAVAILABLE'
    else:
        state = 'OTHER'
    length = len(html.encode('utf-8'))
    size_bin = ('UNDER_2K' if length <= 2048 else
                'UNDER_16K' if length <= 16384 else 'UNDER_128K')
    return {
        'classification': 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
        'title_expected': parsed.values['title'] == ['ProjectIQ D4 Fullstack Pilot'],
        'root_present': parsed.root_present,
        'main_present': parsed.main_present,
        'vite_js_asset_present': parsed.vite_js_asset_present,
        'chrome_error_marker': ('chrome-error://chromewebdata' in html or
                                'ERR_CONNECTION_REFUSED' in html or
                                'ERR_EMPTY_RESPONSE' in html),
        'h1_count': min(len(headings), 16),
        'p_count': min(len(paras), 16),
        'h1_expected': headings == ['ProjectIQ D4 pilot'],
        'p_state': state,
        'html_size_bucket': size_bin,
    }


def _validate_dom_fingerprint(record) -> None:
    fixed = {'classification', 'title_expected', 'root_present', 'main_present',
             'vite_js_asset_present', 'chrome_error_marker', 'h1_count', 'p_count',
             'h1_expected', 'p_state', 'html_size_bucket'}
    if (not isinstance(record, dict) or set(record) != fixed or
            record['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION' or
            any(type(record[name]) is not bool for name in (
                'title_expected', 'root_present', 'main_present',
                'vite_js_asset_present', 'chrome_error_marker', 'h1_expected')) or
            any(type(record[name]) is not int or not 0 <= record[name] <= 16
                for name in ('h1_count', 'p_count')) or
            record['p_state'] not in _PAGE_P_STATES or
            record['html_size_bucket'] not in _PAGE_SIZE_BINS):
        _deny('INVALID_BROWSER_DOM_FINGERPRINT')



# This probe is intentionally fixed to the already approved localhost page.
# Never accept a URL or path from a student bundle. Only fixed flags leave VM.
_NAV_STATES = frozenset(('HTTP_200_EXPECTED_HTML', 'HTTP_200_OTHER_HTML',
                         'HTTP_OTHER_STATUS', 'NOT_HTML', 'RESPONSE_TOO_LARGE',
                         'UNAVAILABLE'))

def _fixed_local_page_probe(chrome_dom: str) -> dict:
    normalized = ''.join(chrome_dom.lower().split())
    blank = normalized in ('<html><head></head><body></body></html>',
                           '<html><head></head><body></body></html>\n')
    result = {'status':'UNAVAILABLE', 'chrome_blank_document':blank,
              'static_title_present':False, 'react_root_present':False,
              'built_asset_reference_present':False}
    conn = None
    try:
        conn = http.client.HTTPConnection('127.0.0.1', 8765, timeout=4)
        conn.request('GET', '/index.html')
        response = conn.getresponse()
        content_type = response.getheader('Content-Type','')
        raw = response.read(8193)
        if len(raw) > 8192:
            result['status'] = 'RESPONSE_TOO_LARGE'
        elif response.status != 200:
            result['status'] = 'HTTP_OTHER_STATUS'
        elif not content_type.lower().startswith('text/html'):
            result['status'] = 'NOT_HTML'
        else:
            result['static_title_present'] = b'<title>ProjectIQ D4 Fullstack Pilot</title>' in raw
            result['react_root_present'] = bool(re.search(rb'<div\s+id=["\']root["\']', raw))
            result['built_asset_reference_present'] = bool(re.search(
                rb'/assets/[A-Za-z0-9_.-]{1,120}\.js', raw))
            result['status'] = (
                'HTTP_200_EXPECTED_HTML' if result['static_title_present'] and
                result['react_root_present'] and result['built_asset_reference_present']
                else 'HTTP_200_OTHER_HTML')
    except (OSError, TimeoutError, UnicodeError, ValueError):
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
    return result

def _validate_navigation_probe(value: Any) -> None:
    expected = {'status', 'chrome_blank_document', 'static_title_present',
                'react_root_present', 'built_asset_reference_present'}
    if (not isinstance(value, dict) or set(value) != expected or
            value['status'] not in _NAV_STATES or
            any(type(value[k]) is not bool for k in expected - {'status'})):
        _deny('INVALID_BROWSER_NAVIGATION_PROBE')

# Failure-only Chrome stdout structure. Never persist HTML, stderr or arbitrary text.
_CAPTURE_KINDS = frozenset(('EMPTY_CAPTURE', 'WRAPPER_STDOUT',
                            'NON_HTML_STDOUT', 'EMPTY_HTML_DOCUMENT',
                            'CHROME_ERROR_DOCUMENT', 'OTHER_HTML_DOCUMENT',
                            'APPLICATION_HTML', 'MULTIPLE_HTML_DOCUMENTS'))
_CAPTURE_PREFIXES = frozenset(('EMPTY', 'DOCTYPE', 'HTML', 'OTHER_MARKUP', 'TEXT'))


class _BodyShape(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.body_seen = False
        self.body_has_elements = False
        self.body_has_text = False

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.body_seen = True
            self.in_body = True
        elif self.in_body:
            self.body_has_elements = True

    def handle_data(self, data):
        if self.in_body and data.strip():
            self.body_has_text = True

    def handle_endtag(self, tag):
        if tag == 'body':
            self.in_body = False


def _capture_shape(raw: bytes) -> dict:
    """Characterize stdout using constant labels only; never publish page content."""
    if not isinstance(raw, bytes) or len(raw) > MAX_DOM:
        _deny('INVALID_BROWSER_CAPTURE_SHAPE')
    lower = raw.lower()
    leading = lower.lstrip()
    if not leading:
        prefix = 'EMPTY'
    elif leading.startswith(b'<!doctype'):
        prefix = 'DOCTYPE'
    elif re.match(rb'<html(?:\s|>|/)', leading):
        prefix = 'HTML'
    elif leading.startswith(b'<'):
        prefix = 'OTHER_MARKUP'
    else:
        prefix = 'TEXT'
    html_count = min(len(re.findall(rb'<html(?=[\s>/])', lower)), 3)
    parser = _BodyShape()
    if html_count:
        try:
            parser.feed(raw.decode('utf-8'))
        except (UnicodeError, ValueError):
            pass
    empty_body = (parser.body_seen and not parser.body_has_elements and
                  not parser.body_has_text)
    chrome_error = any(marker in lower for marker in (
        b'chrome-error://', b'chromewebdata', b'err_connection_',
        b'err_empty_response', b'err_name_not_resolved',
        b'this site can', b'chrome://error'))
    wrapper = (b'running scope as unit:' in lower[:1024] or
               b'running as unit:' in lower[:1024] or
               b'failed to start transient scope unit' in lower[:1024])
    root = bool(re.search(rb'<div\s+[^>]*id\s*=\s*[\"\']root[\"\']', lower))
    if not leading:
        kind = 'EMPTY_CAPTURE'
    elif not html_count:
        kind = 'WRAPPER_STDOUT' if wrapper else 'NON_HTML_STDOUT'
    elif html_count > 1:
        kind = 'MULTIPLE_HTML_DOCUMENTS'
    elif chrome_error:
        kind = 'CHROME_ERROR_DOCUMENT'
    elif empty_body:
        kind = 'EMPTY_HTML_DOCUMENT'
    elif root:
        kind = 'APPLICATION_HTML'
    else:
        kind = 'OTHER_HTML_DOCUMENT'
    return {
        'classification': 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
        'kind': kind,
        'prefix': prefix,
        'byte_count': len(raw),
        'html_tag_count': html_count,
        'doctype_present': b'<!doctype' in lower,
        'body_tag_present': parser.body_seen,
        'body_empty': bool(empty_body),
        'wrapper_marker': bool(wrapper),
        'chrome_error_marker': bool(chrome_error),
    }


def _validate_capture_shape(value: Any) -> None:
    required = {'classification', 'kind', 'prefix', 'byte_count',
                'html_tag_count', 'doctype_present', 'body_tag_present',
                'body_empty', 'wrapper_marker', 'chrome_error_marker'}
    if (not isinstance(value, dict) or set(value) != required or
            value['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION' or
            value['kind'] not in _CAPTURE_KINDS or
            value['prefix'] not in _CAPTURE_PREFIXES or
            type(value['byte_count']) is not int or not 0 <= value['byte_count'] <= MAX_DOM or
            type(value['html_tag_count']) is not int or not 0 <= value['html_tag_count'] <= 3 or
            any(type(value[field]) is not bool for field in (
                'doctype_present', 'body_tag_present', 'body_empty',
                'wrapper_marker', 'chrome_error_marker'))):
        _deny('INVALID_BROWSER_CAPTURE_SHAPE')


# On empty Chrome DOM, prove whether its approved systemd-run/sudo/app-UID
# stdout path can transport fixed bytes. No raw page, argv or stderr escapes.
_STDOUT_MARKER = b'PIQ4DC_STDOUT_BOUNDARY_OK\n'
_STDERR_MARKER = b'PIQ4DC_STDERR_BOUNDARY_OK\n'

def _empty_capture_issue(trusted_process, workspace: Path, browser: Path) -> str:
    command = ['/usr/bin/python3', '-I', '-c',
               'import os; os.write(1, b"PIQ4DC_STDOUT_BOUNDARY_OK\\n"); '
               'os.write(2, b"PIQ4DC_STDERR_BOUNDARY_OK\\n")']
    try:
        sentinel = trusted_process(command, project_code=True, timeout=12,
                                   cwd=Path(workspace))
        if (type(sentinel.returncode) is not int or sentinel.returncode != 0 or
                sentinel.stdout != _STDOUT_MARKER or
                sentinel.stderr != _STDERR_MARKER):
            return 'BROWSER_EMPTY_STDOUT_SCOPE_UNVERIFIED'
        version = trusted_process([str(browser), '--version'], project_code=True,
                                  timeout=12, cwd=Path(workspace))
        output = getattr(version, 'stdout', b'')
        if (type(version.returncode) is not int or version.returncode != 0 or
                not isinstance(output, bytes) or not 0 < len(output) <= 512 or
                not output.startswith((b'Google Chrome ', b'Chromium '))):
            return 'BROWSER_EMPTY_STDOUT_CHROME_VERSION_UNVERIFIED'
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return 'BROWSER_EMPTY_STDOUT_SCOPE_UNVERIFIED'
    return 'BROWSER_EMPTY_STDOUT_DUMP_DOM'


# Fixed-only local/browser render diagnostics. These controls execute only
# after the reviewed application returned zero DOM bytes. They are never used
# as acceptance evidence, do not expose raw HTML and cannot accept user URLs.
_SMOKE_DATA_URL = 'data:text/html,%3C!doctype%20html%3E%3Chtml%3E%3Cbody%3E%3Ch1%3EPIQ4DC_SMOKE_OK%3C/h1%3E%3C/body%3E%3C/html%3E'
_SMOKE_LOCAL_URL = 'http://127.0.0.1:8765/index.html'
_RENDER_RESULT = frozenset(('EXPECTED', 'OTHER_HTML', 'EMPTY_STDOUT',
                            'NONZERO', 'TIMEOUT', 'LAUNCH_REJECTED',
                            'INVALID_RESULT', 'OUTPUT_TOO_LARGE', 'NON_HTML'))

def _render_smoke(trusted_process, cmd: list[str], quota_home: Path, workspace: Path) -> dict:
    """Two bounded, fixed-target Chrome commands through the same app scope."""
    output = {}
    for name, target in (('data', _SMOKE_DATA_URL), ('localhost', _SMOKE_LOCAL_URL)):
        probe_profile = quota_home / ('phase4dc-smoke-' + name)
        argv = [('--user-data-dir=' + str(probe_profile)) if item.startswith('--user-data-dir=')
                else ('--timeout=8000' if (item.startswith('--timeout=') or
                     item.startswith('--virtual-time-budget=')) else item)
                for item in cmd]
        if (argv.count('--dump-dom') != 1 or not any(a.startswith('--user-data-dir=') for a in argv)):
            output[name] = {'result':'INVALID_RESULT', 'stdout_bytes':0,
                            'exit_code':None, 'stderr_flags':[]}
            continue
        argv[-1] = target
        result = 'LAUNCH_REJECTED'
        byte_count = 0
        exit_code = None
        flags = []
        try:
            process = trusted_process(argv, project_code=True, browser_profile=True,
                                      timeout=22, cwd=Path(workspace))
            raw = getattr(process, 'stdout', None)
            rc = getattr(process, 'returncode', None)
            flags = _stderr_flags(getattr(process, 'stderr', None))
            if type(rc) is not int or not -1024 <= rc <= 1024 or not isinstance(raw, bytes):
                result = 'INVALID_RESULT'
            else:
                exit_code = rc
                byte_count = min(len(raw), MAX_DOM + 1)
                if rc != 0:
                    result = 'NONZERO'
                elif len(raw) > MAX_DOM:
                    result = 'OUTPUT_TOO_LARGE'
                elif not raw:
                    result = 'EMPTY_STDOUT'
                elif name == 'data' and b'<h1>PIQ4DC_SMOKE_OK</h1>' in raw:
                    result = 'EXPECTED'
                elif name == 'localhost' and (b'ProjectIQ D4 Fullstack Pilot' in raw and
                                              b'id="root"' in raw):
                    result = 'EXPECTED'
                elif raw.lstrip().lower().startswith((b'<!doctype', b'<html')):
                    result = 'OTHER_HTML'
                else:
                    result = 'NON_HTML'
        except RuntimeError as exc:
            flags = _stderr_flags(getattr(exc, 'browser_stderr', None))
            result = ('TIMEOUT' if _launch_exception_issue(exc) in (
                      'BROWSER_COMMAND_TIMEOUT','BROWSER_COMMAND_TIMEOUT_WITH_OUTPUT')
                      else 'LAUNCH_REJECTED')
        except (OSError, ValueError, TypeError, AttributeError):
            result = 'LAUNCH_REJECTED'
        output[name] = {'result':result, 'stdout_bytes':byte_count,
                        'exit_code':exit_code, 'stderr_flags':flags}
    return {'classification':'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
            'data':output['data'], 'localhost':output['localhost']}


def _validate_render_smoke(value) -> None:
    if (not isinstance(value, dict) or set(value) != {'classification','data','localhost'}
            or value['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'):
        _deny('INVALID_BROWSER_SMOKE_DIAGNOSTIC')
    for item in (value['data'], value['localhost']):
        if (not isinstance(item, dict) or set(item) != {
                'result','stdout_bytes','exit_code','stderr_flags'}
                or item['result'] not in _RENDER_RESULT
                or type(item['stdout_bytes']) is not int
                or not 0 <= item['stdout_bytes'] <= MAX_DOM + 1
                or (item['exit_code'] is not None and
                    (type(item['exit_code']) is not int or not -1024 <= item['exit_code'] <= 1024))
                or not isinstance(item['stderr_flags'], list)
                or len(item['stderr_flags']) > len(_STDERR_FLAGS)
                or any(type(flag) is not str or flag not in _STDERR_FLAGS
                       for flag in item['stderr_flags'])
                or len(set(item['stderr_flags'])) != len(item['stderr_flags'])):
            _deny('INVALID_BROWSER_SMOKE_DIAGNOSTIC')


# The normal Chrome command uses a strict wildcard DNS override. This failure-
# only diagnostic distinguishes that override from actual app-UID loopback
# connectivity. It runs only on the operator-reviewed fixed localhost URL.
# QEMU's separate execution-network policy remains restrict=on throughout.
_LOOPBACK_SENTINEL = b'PIQ4DC_LOCAL_HTTP_OK'
_DNS_RULE = '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'
_LOOPBACK_HTTP_RESULTS = frozenset(('HTTP_200_EXPECTED_HTML', 'HTTP_200_OTHER_HTML',
                                    'HTTP_UNAVAILABLE', 'HTTP_LAUNCH_REJECTED'))
_LOOPBACK_CHROME_RESULTS = _RENDER_RESULT | frozenset(('DOM_MATCHED', 'HTML_SHELL_ONLY'))

def _same_app_uid_http(trusted_process, workspace: Path) -> str:
    # No output except one fixed token; no HTML or HTTP error enters evidence.
    source = chr(10).join((
        'import http.client, sys',
        'connection = None',
        'try:',
        '    connection = http.client.HTTPConnection("127.0.0.1", 8765, timeout=4)',
        '    connection.request("GET", "/index.html")',
        '    response = connection.getresponse()',
        '    data = response.read(8193)',
        '    if response.status == 200 and len(data) <= 8192 and b"ProjectIQ D4 Fullstack Pilot" in data and b"root" in data:',
        '        sys.stdout.write("PIQ4DC_LOCAL_HTTP_OK")',
        'except (OSError, TimeoutError, ValueError):',
        '    pass',
        'finally:',
        '    if connection is not None:',
        '        connection.close()',
    )) + chr(10)
    try:
        p=trusted_process(['/usr/bin/python3','-I','-c',source],
                          project_code=True,timeout=10,cwd=Path(workspace))
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return 'HTTP_LAUNCH_REJECTED'
    if getattr(p,'returncode',None) != 0:
        return 'HTTP_UNAVAILABLE'
    raw = getattr(p,'stdout',None)
    if raw == _LOOPBACK_SENTINEL:
        return 'HTTP_200_EXPECTED_HTML'
    return 'HTTP_200_OTHER_HTML' if isinstance(raw,bytes) and raw else 'HTTP_UNAVAILABLE'


def _chrome_without_dns_override(trusted_process, cmd: list[str], quota_home: Path,
                                 workspace: Path) -> dict:
    result = {'result':'LAUNCH_REJECTED','stdout_bytes':0,'exit_code':None,'stderr_flags':[]}
    if (cmd.count(_DNS_RULE) != 1 or cmd.count('--dump-dom') != 1
            or cmd[-1] != _SMOKE_LOCAL_URL or '--no-sandbox' in cmd
            or not any(part.startswith('--user-data-dir=') for part in cmd)):
        return result
    # Remove ONE DNS override only. Browser, user, cgroup, sandbox, URL, network
    # isolation and other Chrome flags remain identical. Unique temp profile.
    argv=[]
    for part in cmd:
        if part == _DNS_RULE:
            continue
        if part.startswith('--user-data-dir='):
            argv.append('--user-data-dir=' + str(quota_home / 'phase4dc-smoke-no-dns-rule'))
        elif part.startswith(('--timeout=','--virtual-time-budget=')):
            argv.append('--timeout=12000')
        else:
            argv.append(part)
    try:
        p=trusted_process(argv,project_code=True,browser_profile=True,
                          timeout=26,cwd=Path(workspace))
        rc=getattr(p,'returncode',None)
        raw=getattr(p,'stdout',None)
        result['stderr_flags']=_stderr_flags(getattr(p,'stderr',None))
        if type(rc) is not int or not -1024 <= rc <= 1024 or not isinstance(raw,bytes):
            result['result']='INVALID_RESULT'
            return result
        result['exit_code']=rc
        result['stdout_bytes']=min(len(raw),MAX_DOM+1)
        if rc != 0:
            result['result']='NONZERO'
        elif len(raw)>MAX_DOM:
            result['result']='OUTPUT_TOO_LARGE'
        elif not raw:
            result['result']='EMPTY_STDOUT'
        elif raw.lstrip().lower().startswith((b'<!doctype',b'<html')):
            # Static HTML alone isn't enough: we need actual React + API DOM.
            try:
                outcome=dom_checks(raw.decode('utf-8'), [
                    {'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                    {'tag':'p','exact_text':'SQLite verified'}])
                if all(x['matched'] for x in outcome):
                    result['result']='DOM_MATCHED'
                elif b'ProjectIQ D4 Fullstack Pilot' in raw and b'id="root"' in raw:
                    result['result']='HTML_SHELL_ONLY'
                else:
                    result['result']='OTHER_HTML'
            except (ValueError,UnicodeError):
                result['result']='NON_HTML'
        else:
            result['result']='NON_HTML'
    except RuntimeError as exc:
        result['stderr_flags']=_stderr_flags(getattr(exc,'browser_stderr',None))
        result['result']='TIMEOUT' if _launch_exception_issue(exc) in (
            'BROWSER_COMMAND_TIMEOUT','BROWSER_COMMAND_TIMEOUT_WITH_OUTPUT') else 'LAUNCH_REJECTED'
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    return result


def _loopback_differential(trusted_process, cmd: list[str], quota_home: Path,
                           workspace: Path) -> dict:
    return {'classification':'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
            'app_uid_http':_same_app_uid_http(trusted_process,workspace),
            'chrome_without_dns_rule':_chrome_without_dns_override(
                trusted_process,cmd,quota_home,workspace)}


def _validate_loopback_differential(value) -> None:
    if (not isinstance(value,dict) or set(value) != {
            'classification','app_uid_http','chrome_without_dns_rule'}
            or value['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
            or value['app_uid_http'] not in _LOOPBACK_HTTP_RESULTS):
        _deny('INVALID_LOOPBACK_DIFFERENTIAL')
    b=value['chrome_without_dns_rule']
    if (not isinstance(b,dict) or set(b) != {'result','stdout_bytes','exit_code','stderr_flags'}
            or b['result'] not in _LOOPBACK_CHROME_RESULTS
            or type(b['stdout_bytes']) is not int or not 0 <= b['stdout_bytes'] <= MAX_DOM+1
            or (b['exit_code'] is not None and (type(b['exit_code']) is not int
                 or not -1024 <= b['exit_code'] <= 1024))
            or not isinstance(b['stderr_flags'],list)
            or len(b['stderr_flags'])>len(_STDERR_FLAGS)
            or any(type(x) is not str or x not in _STDERR_FLAGS for x in b['stderr_flags'])
            or len(set(b['stderr_flags']))!=len(b['stderr_flags'])):
        _deny('INVALID_LOOPBACK_DIFFERENTIAL')

def _empty(bundle, *, status='INCONCLUSIVE', issue='BROWSER_RUNTIME_UNAVAILABLE'):
    return {'schema_version': SCHEMA, 'stage': 'guest_browser_ui',
            'source': bundle['source'], 'adapter': bundle['adapter'],
            'catalog_sha256': bundle['catalog_sha256'],
            'execution': 'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
            'status': status, 'issue_code': issue,
            'checks': [{'tag': x['tag'], 'matched': None} for x in bundle['checks']]}


def _browser_exit_issue(returncode: int, stderr: bytes | None) -> str:
    """Allowlisted categories only: Chrome stderr and page content stay in guest.

    Do not include raw Chrome output, command lines, paths, or error messages in
    the host report; they could contain source-controlled values or secrets.
    """
    if returncode in (111, 112, 113, 114):
        return 'BROWSER_APP_LAUNCHER_REJECTED'
    if returncode in (-9, 137):
        return 'BROWSER_RESOURCE_LIMIT_OR_KILL'
    if returncode in (-11, -6, 134, 139):
        return 'BROWSER_PROCESS_CRASHED'
    # Chrome diagnostics are not trusted and must never be published verbatim.
    # Treat only tightly defined marker strings as signal for troubleshooting.
    if isinstance(stderr, bytes):
        sample = stderr[:16_384].lower()
        if (b'no usable sandbox' in sample
                or b'failed to move to new namespace' in sample
                or b'failed to create user namespace' in sample
                or b'namespace sandbox' in sample):
            return 'BROWSER_SANDBOX_UNAVAILABLE'
        if (b'error while loading shared libraries' in sample
                or b'cannot open shared object file' in sample):
            return 'BROWSER_SHARED_LIBRARY_UNAVAILABLE'
        if (b'processsingleton' in sample or b'user data directory is already in use' in sample):
            return 'BROWSER_PROFILE_UNAVAILABLE'
        if (b'resource temporarily unavailable' in sample
                or b'pthread_create' in sample):
            return 'BROWSER_PROCESS_LIMIT'
    return 'BROWSER_EXIT_NONZERO'


def _pid_limit_events(*, root: Path = Path('/sys/fs/cgroup'), uid: int | None = None) -> int | None:
    """Read the kernel's PID-ceiling event count; never grant more processes.

    Fixed path corresponds to the trusted, previously verified evaluation slice.
    No raw process IDs or application content cross the guest boundary.
    """
    if uid is None:
        getuid = getattr(os, 'getuid', None)
        uid = getuid() if callable(getuid) else None
    if type(uid) is not int or not 1 <= uid <= 2**31 - 1:
        return None
    path = (root / 'user.slice' / f'user-{uid}.slice' / f'user@{uid}.service'
            / 'projectiq.slice' / 'projectiq-eval.slice' / 'pids.events')
    try:
        if path.is_symlink() or path.parent.is_symlink():
            return None
        value = path.read_text(encoding='ascii')
    except (OSError, UnicodeError, ValueError):
        return None
    if len(value) > 512:
        return None
    hits = [m.group(1) for line in value.splitlines()
            if (m := re.fullmatch(r'max\s+([0-9]{1,15})', line))]
    return int(hits[0]) if len(hits) == 1 else None


# Diagnostic-only, bounded readings from the fixed trusted cgroup hierarchy.
# Never take paths, PIDs, commands or names from Chrome/student output.
_CGROUP_FIELDS = ('max', 'current', 'peak', 'events', 'events_local')
_CGROUP_LAYERS = ('user_slice', 'user_manager', 'projectiq', 'evaluation')
_STDERR_FLAGS = ('PTHREAD_CREATE', 'EAGAIN', 'SANDBOX', 'USER_NAMESPACE',
                 'ZYGOTE', 'FORK', 'MISSING_LIBRARY', 'PROFILE_IN_USE')
_LAUNCH_LINE = re.compile(
    rb'(?m)^PIQ4DC_LIMITS nproc=([0-9]{1,10})/([0-9]{1,10}) '
    rb'scope=(max|[0-9]{1,10}|-) current=([0-9]{1,10}|-)\r?$')


def _safe_count(raw: str, *, max_value: int = 10**10) -> int | None:
    return (int(raw) if raw.isascii() and raw.isdecimal()
            and 0 < len(raw) <= 12 and int(raw) <= max_value else None)


def _read_counter(node: Path, name: str) -> int | str | None:
    try:
        path = node / name
        if path.is_symlink() or node.is_symlink():
            return None
        raw = path.read_text(encoding='ascii')
        if len(raw) > 256:
            return None
        raw = raw.strip()
        if name == 'pids.max' and raw == 'max':
            return 'max'
        if name in ('pids.events', 'pids.events.local'):
            rows = [row.group(1) for line in raw.splitlines()
                    if (row := re.fullmatch(r'max\s+([0-9]{1,12})', line))]
            if len(rows) != 1:
                return None
            return _safe_count(rows[0])
        return _safe_count(raw)
    except (OSError, UnicodeError, ValueError):
        return None


def _pid_snapshot(*, root: Path = Path('/sys/fs/cgroup'),
                  uid: int | None = None) -> dict[str, dict[str, int | str | None]]:
    if uid is None:
        getuid = getattr(os, 'getuid', None)
        uid = getuid() if callable(getuid) else None
    base = {'max': None, 'current': None, 'peak': None,
            'events': None, 'events_local': None}
    if type(uid) is not int or not 1 <= uid <= 2**31 - 1:
        return {name: dict(base) for name in _CGROUP_LAYERS}
    root_path = root / 'user.slice' / f'user-{uid}.slice'
    nodes = (root_path, root_path / f'user@{uid}.service',
             root_path / f'user@{uid}.service' / 'projectiq.slice',
             root_path / f'user@{uid}.service' / 'projectiq.slice' / 'projectiq-eval.slice')
    if root.is_symlink():
        return {name: dict(base) for name in _CGROUP_LAYERS}
    return {name: {
        'max': _read_counter(node, 'pids.max'),
        'current': _read_counter(node, 'pids.current'),
        'peak': _read_counter(node, 'pids.peak'),
        'events': _read_counter(node, 'pids.events'),
        'events_local': _read_counter(node, 'pids.events.local')
    } for name, node in zip(_CGROUP_LAYERS, nodes)}


def _stderr_flags(stderr: bytes | None) -> list[str]:
    if not isinstance(stderr, bytes):
        return []
    sample = stderr[:16_384].lower()
    matches = {
        'PTHREAD_CREATE': b'pthread_create' in sample,
        'EAGAIN': b'resource temporarily unavailable' in sample,
        'SANDBOX': b'no usable sandbox' in sample or b'namespace sandbox' in sample,
        'USER_NAMESPACE': b'failed to move to new namespace' in sample
                          or b'failed to create user namespace' in sample,
        'ZYGOTE': b'zygote' in sample,
        'FORK': b'fork' in sample,
        'MISSING_LIBRARY': b'error while loading shared libraries' in sample,
        'PROFILE_IN_USE': b'processsingleton' in sample,
    }
    return [key for key in _STDERR_FLAGS if matches[key]]


def _launcher_limits(stderr: bytes | None) -> dict[str, int | str | None]:
    found: dict[str, int | str | None] = {
        'nproc_soft': None, 'nproc_hard': None,
        'scope_max': None, 'scope_current': None}
    if not isinstance(stderr, bytes):
        return found
    markers = list(_LAUNCH_LINE.finditer(stderr[:16_384]))
    if len(markers) != 1:
        return found
    m = markers[0]
    found['nproc_soft'] = _safe_count(m.group(1).decode('ascii'))
    found['nproc_hard'] = _safe_count(m.group(2).decode('ascii'))
    found['scope_max'] = (m.group(3).decode('ascii') if m.group(3) == b'max'
                          else _safe_count(m.group(3).decode('ascii')))
    found['scope_current'] = _safe_count(m.group(4).decode('ascii'))
    return found


def _diagnostics(proc: Any, before: dict, after: dict) -> dict:
    returncode = getattr(proc, 'returncode', None)
    stderr = getattr(proc, 'stderr', None)
    return {
        'exit_code': returncode if type(returncode) is int and -1024 <= returncode <= 1024 else None,
        'stderr_flags': _stderr_flags(stderr),
        'launcher': _launcher_limits(stderr),
        'cgroups': {name: {'before': before[name], 'after': after[name]}
                    for name in _CGROUP_LAYERS},
        'interpretation': 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
    }


def _validate_diagnostics(diag: Any) -> None:
    def valid_count(value):
        return value is None or (type(value) is int and 0 <= value <= 10**10)
    def valid_limit(value):
        return valid_count(value) or value == 'max'
    if (not isinstance(diag, dict) or set(diag) != {
            'exit_code', 'stderr_flags', 'launcher', 'cgroups', 'interpretation'}
            or diag['interpretation'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
            or (diag['exit_code'] is not None and
                (type(diag['exit_code']) is not int or not -1024 <= diag['exit_code'] <= 1024))
            or type(diag['stderr_flags']) is not list
            or len(diag['stderr_flags']) > len(_STDERR_FLAGS)
            or any(x not in _STDERR_FLAGS for x in diag['stderr_flags'])
            or len(set(diag['stderr_flags'])) != len(diag['stderr_flags'])):
        _deny('INVALID_BROWSER_DIAGNOSTICS')
    launcher = diag['launcher']
    if (not isinstance(launcher, dict) or set(launcher) != {
            'nproc_soft', 'nproc_hard', 'scope_max', 'scope_current'}
            or not valid_count(launcher['nproc_soft'])
            or not valid_count(launcher['nproc_hard'])
            or not valid_limit(launcher['scope_max'])
            or not valid_count(launcher['scope_current'])):
        _deny('INVALID_BROWSER_DIAGNOSTICS')
    groups = diag['cgroups']
    if not isinstance(groups, dict) or set(groups) != set(_CGROUP_LAYERS):
        _deny('INVALID_BROWSER_DIAGNOSTICS')
    for group in groups.values():
        if not isinstance(group, dict) or set(group) != {'before', 'after'}:
            _deny('INVALID_BROWSER_DIAGNOSTICS')
        for snapshot in group.values():
            if (not isinstance(snapshot, dict) or set(snapshot) != set(_CGROUP_FIELDS)
                    or not valid_limit(snapshot['max'])
                    or any(not valid_count(snapshot[key])
                           for key in ('current', 'peak', 'events', 'events_local'))):
                _deny('INVALID_BROWSER_DIAGNOSTICS')


def _launch_exception_issue(exc: OSError | RuntimeError | ValueError) -> str:
    """Map only known trusted-launch failures to fixed codes; never publish exception text.

    The VM worker raises GuestFailure (a RuntimeError) on a 55s subprocess
    timeout. Previously this was flattened into BROWSER_PROCESS_UNAVAILABLE,
    obscuring the difference between a Chrome crash and an unreturned run.
    """
    if isinstance(exc, OSError):
        if exc.errno == errno.EAGAIN:
            return 'BROWSER_LAUNCH_EAGAIN'
        if exc.errno in (errno.EACCES, errno.EPERM):
            return 'BROWSER_LAUNCH_PERMISSION_DENIED'
        if exc.errno == errno.ENOENT:
            return 'BROWSER_LAUNCH_BINARY_UNAVAILABLE'
    if isinstance(exc, RuntimeError) and len(exc.args) == 1 and type(exc.args[0]) is str:
        reason = exc.args[0]
        if reason == 'APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT':
            return 'BROWSER_COMMAND_TIMEOUT'
        if reason in ('BROWSER_COMMAND_TIMEOUT',
                      'BROWSER_COMMAND_TIMEOUT_WITH_OUTPUT'):
            return reason
        if reason in ('BROWSER_PROFILE_REQUIRES_REVIEWED_CHROME_COMMAND',
                      'BROWSER_PROFILE_REQUIRES_PROJECT_CODE',
                      'APP_RESOURCE_LAUNCH_INVALID_COMMAND'):
            return 'BROWSER_LAUNCH_POLICY_REJECTED'
    return 'BROWSER_PROCESS_UNAVAILABLE'


def run_browser_ui(bundle: dict, *, expected_source: dict, expected_adapter: str,
                   workspace: Path, trusted_process: Callable[..., Any]) -> dict:
    """A real Chrome headless renderer; subprocess stays in guest app cgroup.

    This initial slice deliberately has no browser clicks or JavaScript supplied
    by the operator. The React page must *render* expected dynamic DOM content.
    """
    validate_guest_browser_bundle(bundle, expected_source=expected_source,
                                  expected_adapter=expected_adapter)
    browser = Path(bundle['browser_binary'])
    if not (browser.is_file() and os.access(browser, os.X_OK)):
        return _empty(bundle)
    # The base image is SHA-pinned on the host. Refuse any runtime path that
    # could be provided by student/checkout/temporary directory content.
    resolved = browser.resolve()
    if not (str(resolved).startswith('/opt/google/chrome/') or
            str(resolved).startswith('/usr/lib/chromium/') or
            str(resolved) in _BROWSER_PATHS):
        return _empty(bundle, issue='BROWSER_BINARY_OUTSIDE_TRUSTED_IMAGE')
    quota_home = os.environ.get('PROJECTIQ_QUOTA_HOME')
    if (not quota_home or not Path(quota_home).is_dir()
            or not Path(quota_home).resolve().is_relative_to(Path(workspace).resolve())):
        return _empty(bundle, issue='BROWSER_QUOTA_HOME_UNAVAILABLE')
    profile = Path(quota_home) / 'phase4dc-browser-profile'
    # No --no-sandbox: a browser-sandbox failure is INCONCLUSIVE, never PASS.
    # Constrain discretionary parallelism without disabling Chrome's sandbox.
    cmd = [str(browser), '--headless=new', '--disable-gpu',
           '--renderer-process-limit=1', '--num-raster-threads=1',
           '--disable-dev-shm-usage', '--disable-background-networking',
           '--disable-extensions', '--no-first-run', '--no-default-browser-check',
           '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
           '--timeout=20000', '--user-data-dir=' + str(profile),
           '--dump-dom', 'http://127.0.0.1:8765/index.html']
    pids_before = _pid_limit_events()
    snapshot_before = _pid_snapshot()
    try:
        proc = trusted_process(cmd, project_code=True, browser_profile=True, timeout=55,
                               cwd=Path(workspace))
    except (OSError, RuntimeError, ValueError) as exc:
        report = _empty(bundle, issue=_launch_exception_issue(exc))
        # Retain only numeric and allowlisted stderr markers on timeout.
        # Chrome stdout and raw stderr never leave the guest.
        preview = type('BrowserPreview', (), {
            'returncode': None, 'stderr': getattr(exc, 'browser_stderr', None)
        })()
        report['diagnostics'] = _diagnostics(preview, snapshot_before, _pid_snapshot())
        return report
    pids_after = _pid_limit_events()
    snapshot_after = _pid_snapshot()
    pid_ceiling_hit = (pids_before is not None and pids_after is not None
                       and pids_after > pids_before)
    if type(proc.returncode) is not int:
        return _empty(bundle, issue='BROWSER_PROCESS_RESULT_INVALID')
    if proc.returncode != 0:
        issue = _browser_exit_issue(proc.returncode, getattr(proc, 'stderr', None))
        report = _empty(bundle, issue='BROWSER_CGROUP_PIDS_LIMIT_HIT' if pid_ceiling_hit else issue)
        report['diagnostics'] = _diagnostics(proc, snapshot_before, snapshot_after)
        return report
    if not isinstance(proc.stdout, bytes):
        return _empty(bundle, issue='BROWSER_STDOUT_UNAVAILABLE')
    if len(proc.stdout) > MAX_DOM:
        return _empty(bundle, issue='BROWSER_DOM_TOO_LARGE')
    if not proc.stdout:
        issue = _empty_capture_issue(trusted_process, workspace, browser)
        report = _empty(bundle, issue=issue)
        report['diagnostics'] = _diagnostics(proc, snapshot_before, snapshot_after)
        if issue == 'BROWSER_EMPTY_STDOUT_DUMP_DOM':
            report['render_smoke'] = _render_smoke(trusted_process, cmd, Path(quota_home), Path(workspace))
            report['loopback_differential'] = _loopback_differential(trusted_process, cmd, Path(quota_home), Path(workspace))
        return report
    try:
        doc = proc.stdout.decode('utf-8')
        matches = dom_checks(doc, bundle['checks'])
    except (ValueError, UnicodeError):
        return _empty(bundle, issue='BROWSER_DOM_INVALID')
    passed = all(x['matched'] for x in matches)
    report = _empty(bundle, status='PASS' if passed else 'FAIL',
                    issue=None if passed else 'RENDERED_DOM_DID_NOT_MATCH')
    report['checks'] = matches
    if not passed:
        report['page_fingerprint'] = _dom_fingerprint(doc)
        if (not report['page_fingerprint']['title_expected'] and
                not report['page_fingerprint']['root_present']):
            report['navigation_probe'] = _fixed_local_page_probe(doc)
        report['capture_shape'] = _capture_shape(proc.stdout)
    return report


def validate_browser_ui_report(bundle: dict, report: Any) -> None:
    validate_guest_browser_bundle(bundle, expected_source=bundle['source'],
                                  expected_adapter=bundle['adapter'])
    required = {'schema_version', 'stage', 'source', 'adapter', 'catalog_sha256',
                'execution', 'status', 'issue_code', 'checks'}
    if (not isinstance(report, dict) or not required <= set(report)
            or set(report) - required - {'diagnostics', 'page_fingerprint', 'navigation_probe', 'capture_shape', 'render_smoke', 'loopback_differential'}
            or report['schema_version'] != SCHEMA or report['stage'] != 'guest_browser_ui'
            or report['source'] != bundle['source'] or report['adapter'] != bundle['adapter']
            or report['catalog_sha256'] != bundle['catalog_sha256']
            or report['execution'] != 'HEADLESS_BROWSER_IN_DISPOSABLE_VM'
            or report['status'] not in ('PASS', 'FAIL', 'INCONCLUSIVE')
            or not isinstance(report['checks'], list)
            or len(report['checks']) != len(bundle['checks'])):
        _deny('INVALID_BROWSER_UI_REPORT')
    for got, expected in zip(report['checks'], bundle['checks']):
        if (not isinstance(got, dict) or set(got) != {'tag', 'matched'}
                or got['tag'] != expected['tag']
                or got['matched'] is not None and type(got['matched']) is not bool):
            _deny('BROWSER_UI_REPORT_CHECK_INVALID')
    if 'loopback_differential' in report:
        if (report['status'] != 'INCONCLUSIVE' or
                report['issue_code'] != 'BROWSER_EMPTY_STDOUT_DUMP_DOM'):
            _deny('LOOPBACK_ONLY_ON_EMPTY_DOM')
        _validate_loopback_differential(report['loopback_differential'])
    if 'render_smoke' in report:
        if (report['status'] != 'INCONCLUSIVE' or
                report['issue_code'] != 'BROWSER_EMPTY_STDOUT_DUMP_DOM'):
            _deny('BROWSER_SMOKE_ONLY_ON_EMPTY_CAPTURE')
        _validate_render_smoke(report['render_smoke'])
    if 'diagnostics' in report:
        _validate_diagnostics(report['diagnostics'])
        if report['status'] != 'INCONCLUSIVE':
            _deny('BROWSER_DIAGNOSTICS_ONLY_ON_FAILURE')
    if 'page_fingerprint' in report:
        if report['status'] != 'FAIL':
            _deny('BROWSER_DOM_FINGERPRINT_ONLY_ON_FAIL')
        _validate_dom_fingerprint(report['page_fingerprint'])
    if 'navigation_probe' in report:
        if (report['status'] != 'FAIL' or 'page_fingerprint' not in report or
                report['page_fingerprint']['title_expected'] or
                report['page_fingerprint']['root_present']):
            _deny('BROWSER_NAVIGATION_PROBE_ONLY_ON_EMPTY_TARGET')
        _validate_navigation_probe(report['navigation_probe'])
    if 'capture_shape' in report:
        if report['status'] != 'FAIL' or 'page_fingerprint' not in report:
            _deny('BROWSER_CAPTURE_SHAPE_FAIL_ONLY')
        _validate_capture_shape(report['capture_shape'])
    states = [x['matched'] for x in report['checks']]
    if report['status'] == 'PASS':
        if states != [True] * len(states) or report['issue_code'] is not None:
            _deny('BROWSER_UI_FALSE_PASS')
    elif report['status'] == 'FAIL':
        if (any(x is None for x in states) or all(states)
                or report['issue_code'] != 'RENDERED_DOM_DID_NOT_MATCH'):
            _deny('BROWSER_UI_INVALID_FAIL')
    elif (any(x is not None for x in states) or
          report['issue_code'] not in {
              'BROWSER_RUNTIME_UNAVAILABLE', 'BROWSER_BINARY_OUTSIDE_TRUSTED_IMAGE',
              'BROWSER_QUOTA_HOME_UNAVAILABLE', 'BROWSER_PROCESS_UNAVAILABLE',
              'BROWSER_COMMAND_TIMEOUT', 'BROWSER_COMMAND_TIMEOUT_WITH_OUTPUT',
              'BROWSER_LAUNCH_POLICY_REJECTED',
              'BROWSER_LAUNCH_EAGAIN', 'BROWSER_LAUNCH_PERMISSION_DENIED',
              'BROWSER_LAUNCH_BINARY_UNAVAILABLE',
              'BROWSER_PROCESS_RESULT_INVALID',
              'BROWSER_APP_LAUNCHER_REJECTED', 'BROWSER_RESOURCE_LIMIT_OR_KILL',
              'BROWSER_PROCESS_CRASHED', 'BROWSER_SANDBOX_UNAVAILABLE',
              'BROWSER_SHARED_LIBRARY_UNAVAILABLE', 'BROWSER_PROFILE_UNAVAILABLE',
              'BROWSER_PROCESS_LIMIT', 'BROWSER_CGROUP_PIDS_LIMIT_HIT',
              'BROWSER_EXIT_NONZERO',
              'BROWSER_STDOUT_UNAVAILABLE', 'BROWSER_DOM_TOO_LARGE',
              'BROWSER_EMPTY_STDOUT_SCOPE_UNVERIFIED',
              'BROWSER_EMPTY_STDOUT_CHROME_VERSION_UNVERIFIED',
              'BROWSER_EMPTY_STDOUT_DUMP_DOM',
              'BROWSER_FAILED_OR_OUTPUT_TOO_LARGE', 'BROWSER_DOM_INVALID'}):
        _deny('BROWSER_UI_INVALID_INCONCLUSIVE')
