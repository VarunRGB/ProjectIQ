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
            or bundle['adapter'] not in ('node_fullstack_offline_v1', 'php_mysql_v1')
            or (bundle['adapter'] == 'node_fullstack_offline_v1' and
                bundle['page_path'] != '/index.html')
            or (bundle['adapter'] == 'php_mysql_v1' and
                bundle['page_path'] != '/public/login.html')
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
    if bundle['adapter'] == 'php_mysql_v1' and checks != [
            {'tag': 'h2', 'exact_text': 'Login'},
            {'tag': 'button', 'exact_text': 'Login'}]:
        _deny('BROWSER_PHP_LOGIN_CATALOG_REQUIRED')


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



# Only constant-domain Chrome/network evidence. No raw stderr, URLs, HTML or IDs.
_FORENSIC_COUNTERS = ('document_request','document_response','document_response_extra',
                      'document_finished','document_failed','asset_request','api_request',
                      'other_request','target_crash','runtime_exception','target_detached','redirect_count')
_FORENSIC_EVENTS = ('pids_max','memory_high','memory_max','memory_oom',
                    'memory_oom_kill','memory_oom_group_kill')
_FORENSIC_PROCESS = ('browser_children','network_services','renderers',
                     'zygotes','gpu_processes','crashpad_processes')
_FORENSIC_TCP = ('listen','syn_sent','established','close_wait','time_wait','other')


def _validate_forensic_trace(value):
    def count(v,limit=9999):
        return type(v) is int and 0 <= v <= limit
    def optional_count(v,limit=2**40):
        return v is None or count(v,limit)
    def optional_bool(v):
        return v is None or type(v) is bool
    if (not isinstance(value,dict) or set(value) != {
           'classification','protocol','process','kernel','tcp','app_uid_connect'}
          or value['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
          or value['app_uid_connect'] not in {'CONNECTED','REFUSED','TIMEOUT','OTHER_ERROR',
              'PERMISSION_DENIED','NETWORK_UNREACHABLE','HOST_UNREACHABLE',
              'CONNECTION_RESET','CONNECTION_ABORTED','ADDRESS_UNAVAILABLE',
              'PROTOCOL_UNSUPPORTED','NO_SOCKET_BUFFERS','PROCESS_FD_LIMIT',
              'SYSTEM_FD_LIMIT','TEMPORARILY_UNAVAILABLE'}):
        _deny('INVALID_4DC_FORENSICS')
    p=value['protocol']
    if (not isinstance(p,dict) or set(p) != {'stage','counters','frame',
            'document_http_status','failure_canceled','blocked_reason',
            'request_started','response_seen'}
          or p['stage'] not in {'NOT_STARTED','INITIAL','CREATING_TARGET',
               'ATTACHING_SESSION','ENABLING_DOMAINS','NAVIGATING','READING_DOM'}
          or p['frame'] not in {'NONE','TARGET','ERROR_PAGE','OTHER'}
          or not optional_bool(p['failure_canceled'])
          or type(p['request_started']) is not bool or type(p['response_seen']) is not bool
          or (p['document_http_status'] is not None and not count(p['document_http_status'],599))
          or (p['document_http_status'] is not None and p['document_http_status'] < 100)
          or p['blocked_reason'] not in {'NOT_REPORTED','csp','mixed-content','origin',
              'inspector','subresource-filter','content-type','other',
              'coep-frame-resource-needs-coep-header',
              'corp-not-same-origin','corp-not-same-origin-after-defaulted-to-same-origin-by-coep',
              'corp-not-same-site'}
          or not isinstance(p['counters'],dict)
          or set(p['counters']) != set(_FORENSIC_COUNTERS)
          or not all(count(v,99) for v in p['counters'].values())):
        _deny('INVALID_4DC_FORENSICS_PROTOCOL')
    proc=value['process']
    if (not isinstance(proc,dict) or set(proc) != set(_FORENSIC_PROCESS) | {
            'browser_alive','browser_netns_equal','network_service_netns_equal','proc_tree_readable'}
          or any(not count(proc[k],128) for k in _FORENSIC_PROCESS)
          or not all(optional_bool(proc[k]) for k in ('browser_alive',
              'browser_netns_equal','network_service_netns_equal'))
          or type(proc['proc_tree_readable']) is not bool):
        _deny('INVALID_4DC_FORENSICS_PROCESS')
    kernel=value['kernel']
    if (not isinstance(kernel,dict) or set(kernel) != set(_FORENSIC_EVENTS) | {
              'pids_current','pids_max_limit','memory_current','memory_max_limit'}
          or any(not optional_count(kernel[k]) for k in kernel)):
        _deny('INVALID_4DC_FORENSICS_KERNEL')
    tcp=value['tcp']
    if (not isinstance(tcp,dict) or set(tcp) != set(_FORENSIC_TCP) | {'readable'}
          or type(tcp['readable']) is not bool
          or any(not count(tcp[k],200) for k in _FORENSIC_TCP)):
        _deny('INVALID_4DC_FORENSICS_TCP')


def _parse_forensic_trace(stderr):
    if not isinstance(stderr,bytes) or len(stderr) > 65536:
        return None
    lines=[line[17:].rstrip(b'\r') for line in stderr.split(b'\n')
           if line.startswith(b'PIQ4DC_FORENSICS ')]
    if len(lines) != 1 or not 2 <= len(lines[0]) <= 3800:
        return None
    try:
        import json
        obj=json.loads(lines[0].decode('ascii'))
        _validate_forensic_trace(obj)
        return obj
    except (ValueError, UnicodeError, TypeError, BrowserUiError):
        return None


_SERVER_TRACE_FIELDS = ('index_seen','index_http_200','index_http_3xx','index_http_4xx',
                        'index_http_5xx','index_http_other','index_complete','index_socket_failure',
                        'asset_seen','asset_http_200','api_seen','api_http_200','other_seen')

def _validate_server_trace(value):
    if (not isinstance(value,dict) or set(value) != {
            'classification','instrumentation_active',*_SERVER_TRACE_FIELDS} and set(value) != {'classification','instrumentation_active',*_SERVER_TRACE_FIELDS,'tcp_accepted'}
          or value['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
          or type(value['instrumentation_active']) is not bool
          or any(type(value[k]) is not int or not 0 <= value[k] <= 9999
                 for k in _SERVER_TRACE_FIELDS) or
          ('tcp_accepted' in value and (type(value['tcp_accepted']) is not int or
                 not 0 <= value['tcp_accepted'] <= 9999))):
        _deny('INVALID_4DC_SERVER_TRACE')



# Attempt 2 independent controller evidence: never depend on forensics JSON.
_CDP_TRIAGE_STAGES = frozenset(('ENTRY','SOCKET_CONTROL','SPAWNING','SPAWNED',
    'TARGET_CREATE','TARGET_CREATED','SESSION_ATTACH','SESSION_ATTACHED',
    'DOMAINS_ENABLE','DOMAINS_ENABLED','NAVIGATE_DISPATCH','NAVIGATE_REPLY',
    'DOM_EVALUATE','DOM_READY','ERROR_CAUGHT','FORENSIC_BEGIN','FORENSIC_DONE',
    'FORENSIC_FAILED','FORENSIC_SKIPPED','CLEANUP'))
_CDP_TRIAGE_FAULTS = frozenset(('NONE','OSError','ValueError','TypeError',
    'RuntimeError','IndexError','KeyError','AttributeError','MemoryError',
    'BrokenPipeError','FileNotFoundError','PermissionError','TimeoutError',
    'AssertionError','OTHER'))
_CDP_TRIAGE_RETURNS = frozenset(('NOT_RETURNED','ZERO_NO_DOM','ZERO_INVALID_DOM',
    'HELPER_119','HELPER_REJECTED','NONZERO_OTHER','SIGNALLED', 'RETURN_INVALID'))
# Controller-owned post-mortem survives if the CDP helper/Chrome is OOM-killed.
_ATTEMPT2_KERNEL_FIELDS = ('pids_max','memory_high','memory_max','memory_oom','memory_oom_kill')


def _attempt2_kernel_events(*, root=Path('/sys/fs/cgroup'), uid=None):
    result = dict.fromkeys(_ATTEMPT2_KERNEL_FIELDS)
    if uid is None:
        getuid = getattr(os,'getuid',None)
        uid = getuid() if callable(getuid) else None
    if type(uid) is not int or not 1 <= uid <= 2**31-1:
        return result
    group = (root / 'user.slice' / f'user-{uid}.slice' / f'user@{uid}.service'
             / 'projectiq.slice' / 'projectiq-eval.slice')
    try:
        if group.is_symlink() or not group.is_dir():
            return result
        for filename, prefix, allowed in (
             ('pids.events','pids_',('max',)),
             ('memory.events','memory_',('high','max','oom','oom_kill'))):
            raw = (group / filename).read_bytes()[:4096]
            for row in raw.splitlines():
                parts = row.split()
                if (len(parts)==2 and parts[0].decode('ascii','ignore') in allowed
                        and 0 < len(parts[1]) <= 14 and parts[1].isascii()
                        and parts[1].isdigit()):
                    key = prefix + parts[0].decode('ascii')
                    result[key] = min(int(parts[1]),2**40)
    except (OSError,ValueError,UnicodeError):
        pass
    return result


def _attempt2_kernel_delta(before,after):
    out={}
    for k in _ATTEMPT2_KERNEL_FIELDS:
        b=before.get(k) if isinstance(before,dict) else None
        a=after.get(k) if isinstance(after,dict) else None
        out[k] = min(a-b,9999) if type(a) is int and type(b) is int and a>=b else None
    return out

_CDP_TRIAGE_TRACES = frozenset(('VALID','ABSENT','MALFORMED','DUPLICATE',
    'OVERSIZE','STDERR_UNAVAILABLE','STDERR_TRUNCATED'))
_CDP_TRIAGE_LAUNCH = frozenset(('NONE','TIMEOUT','POLICY_REJECTED','OS_ERROR','OTHER_EXCEPTION'))
_CDP_TRIAGE_CODES = frozenset(('CDP_INVALID_JSON','CDP_INVALID_MESSAGE',
    'CDP_FRAME_TOO_LARGE','CDP_PENDING_TOO_LARGE','CDP_REPLY_TIMEOUT',
    'CDP_PIPE_CLOSED','CDP_PIPE_WRITE_FAILED','CDP_PROTOCOL_ERROR',
    'CDP_MISSING_RESULT','CDP_TOO_MANY_PENDING','CDP_COMMAND_TOO_LARGE',
    'CDP_NO_PAGE_TARGET','CDP_NO_SESSION','CDP_NAVIGATION_ERROR',
    'CDP_ADMIN_POLICY_BLOCK','CDP_CLIENT_BLOCK','CDP_CONNECTION_REFUSED',
    'CDP_CONNECTION_RESET','CDP_CONNECTION_TIMEOUT','CDP_ADDRESS_UNREACHABLE',
    'CDP_NETWORK_ERROR','CDP_JAVASCRIPT_CONTEXT_ERROR','CDP_DOM_TOO_LARGE',
    'CDP_EMPTY_DOM','CDP_UNKNOWN_FAILURE','CDP_RUNTIME_ERROR'))


def _attempt2_helper_code(stderr):
    if not isinstance(stderr,bytes):
        return 'NOT_REPORTED'
    for line in stderr[:65536].split(b'\n'):
        try:
            value = line.rstrip(b'\r').decode('ascii', 'strict')
        except UnicodeError:
            continue
        if value in _CDP_TRIAGE_CODES or re.fullmatch(r'CDP_NET_ERR_[A-Z][A-Z0-9_]{0,47}',value,re.ASCII):
            return value
    return 'NOT_REPORTED'


def _attempt2_trace_state(stderr):
    if not isinstance(stderr,bytes):
        return 'STDERR_UNAVAILABLE'
    head = stderr[:65536]
    marker = b'PIQ4DC_FORENSICS '
    candidates = [row[len(marker):].rstrip(b'\r') for row in head.split(b'\n')
                  if row.startswith(marker)]
    if len(candidates) > 1:
        return 'DUPLICATE'
    if len(candidates) == 0:
        return 'STDERR_TRUNCATED' if len(stderr) > 65536 else 'ABSENT'
    if len(candidates[0]) > 3800:
        return 'OVERSIZE'
    return 'VALID' if _parse_forensic_trace(stderr) is not None else 'MALFORMED'


def _attempt2_triage(proc=None, *, launch='NONE', kernel_before=None, kernel_after=None):
    stderr = getattr(proc,'stderr',None) if proc is not None else None
    rc = getattr(proc,'returncode',None) if proc is not None else None
    if proc is None:
        state = 'NOT_RETURNED'
    elif type(rc) is not int:
        state = 'RETURN_INVALID'
    elif rc == 0:
        raw = getattr(proc,'stdout',None)
        state = 'ZERO_NO_DOM' if raw == b'' else 'ZERO_INVALID_DOM'
    elif rc == 119:
        state = 'HELPER_119'
    elif rc in (111,112,113,114,118):
        state = 'HELPER_REJECTED'
    elif rc < 0 or rc > 128:
        state = 'SIGNALLED'
    else:
        state = 'NONZERO_OTHER'
    stage = 'NOT_REPORTED'
    fault = 'NONE'
    stage_count = 0
    if isinstance(stderr,bytes):
        for line in stderr[:65536].split(b'\n'):
            if line.startswith(b'PIQ4DC_STAGE '):
                value = line[13:].rstrip(b'\r').decode('ascii','ignore')
                if value in _CDP_TRIAGE_STAGES:
                    if value not in {'ERROR_CAUGHT','FORENSIC_BEGIN',
                                     'FORENSIC_DONE','FORENSIC_FAILED','FORENSIC_SKIPPED','CLEANUP'}:
                        stage = value
                    stage_count = min(stage_count+1,99)
            elif line.startswith(b'PIQ4DC_FAULT '):
                value = line[13:].rstrip(b'\r').decode('ascii','ignore')
                if value in _CDP_TRIAGE_FAULTS:
                    fault = value
    record = {'classification':'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
              'helper_return':state,'last_stage':stage,'stage_markers':stage_count,
              'kernel_events':_attempt2_kernel_delta(kernel_before,kernel_after),
              'fault_class':fault,'helper_code':_attempt2_helper_code(stderr),
              'forensics_state':_attempt2_trace_state(stderr),
              'launch_failure':launch,
              'stderr_size_bin':('UNAVAILABLE' if not isinstance(stderr,bytes) else
                'UNDER_2K' if len(stderr)<=2048 else
                'UNDER_16K' if len(stderr)<=16384 else
                'UNDER_64K' if len(stderr)<=65536 else 'OVER_64K')}
    return record


def _validate_attempt2_triage(item):
    if (not isinstance(item,dict) or set(item) != {'classification',
           'helper_return','last_stage','stage_markers','fault_class','helper_code','kernel_events',
           'forensics_state','launch_failure','stderr_size_bin'}
          or item['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
          or not isinstance(item['kernel_events'],dict)
          or set(item['kernel_events']) != set(_ATTEMPT2_KERNEL_FIELDS)
          or any(v is not None and (type(v) is not int or not 0<=v<=9999)
                 for v in item['kernel_events'].values())
          or item['helper_return'] not in _CDP_TRIAGE_RETURNS
          or item['last_stage'] not in (_CDP_TRIAGE_STAGES | {'NOT_REPORTED'})
          or type(item['stage_markers']) is not int or not 0 <= item['stage_markers'] <= 99
          or item['fault_class'] not in _CDP_TRIAGE_FAULTS
          or (item['helper_code'] not in (_CDP_TRIAGE_CODES | {'NOT_REPORTED'})
              and not (isinstance(item['helper_code'],str) and re.fullmatch(
                   r'CDP_NET_ERR_[A-Z][A-Z0-9_]{0,47}',item['helper_code'],re.ASCII)))
          or item['forensics_state'] not in _CDP_TRIAGE_TRACES
          or item['launch_failure'] not in _CDP_TRIAGE_LAUNCH
          or item['stderr_size_bin'] not in {
              'UNAVAILABLE','UNDER_2K','UNDER_16K','UNDER_64K','OVER_64K'}):
        _deny('INVALID_ATTEMPT2_CDP_TRIAGE')

# Attempt 3: small, independently validated diagnostic evidence.
_A3_STATES = frozenset(('VALID','ABSENT','MALFORMED','DUPLICATE','OVERSIZE',
                        'STDERR_UNAVAILABLE','STDERR_TRUNCATED'))
_A3_FORENSIC = frozenset(('DONE','FAILED','SKIPPED','BEGIN_ONLY',
                          'MULTIPLE_TERMINALS','NOT_REPORTED'))
_A3_FORENSIC_REASONS = frozenset(('NOT_REPORTED','LENGTH_OR_ENCODING',
    'OSError','ValueError','TypeError','KeyError','AttributeError','IndexError',
    'RuntimeError','MemoryError','PermissionError','FileNotFoundError','OTHER'))
_A3_PROBE_STATUS = frozenset(('NOT_RUN','SKIPPED_NO_SESSION',
    'SKIPPED_BROWSER_EXITED','PARTIAL','SERVER_SETUP_FAILED','COMPLETED',
    'PROBE_EXCEPTION','ENCODING_FAILED'))
_A3_GET = frozenset(('NOT_RUN','HTTP_200','HTTP_OTHER','TIMEOUT','IO_ERROR'))
_A3_DOM = frozenset(('NOT_RUN','MATCH','NO_MATCH','CDP_ERROR','CDP_TIMEOUT'))
_A3_NAV = frozenset(('NOT_RUN','OK','NAV_ERROR','CDP_ERROR','CDP_TIMEOUT'))
_A3_DETAIL_KEYS = frozenset(('classification','probe_status',
    'original_document_request','original_document_response','original_document_failed',
    'original_network_error','browser_alive','network_service_count',
    'proc_tree_readable','browser_netns_equal','network_service_netns_equal',
    'frontend_python_get','minimal_python_get','data_nav','data_dom',
    'minimal_nav','minimal_dom','minimal_tcp_accepts','minimal_http_gets'))


def _attempt3_validate_details(obj):
    if not isinstance(obj,dict) or set(obj) != _A3_DETAIL_KEYS:
        _deny('INVALID_ATTEMPT3_DETAIL_SHAPE')
    enums=('classification','probe_status','frontend_python_get','minimal_python_get',
           'data_nav','data_dom','minimal_nav','minimal_dom','original_network_error')
    if any(type(obj[key]) is not str for key in enums):
        _deny('INVALID_ATTEMPT3_STRING_TYPES')
    if (obj['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
            or obj['probe_status'] not in _A3_PROBE_STATUS
            or obj['frontend_python_get'] not in _A3_GET
            or obj['minimal_python_get'] not in _A3_GET
            or obj['data_dom'] not in _A3_DOM
            or obj['minimal_dom'] not in _A3_DOM):
        _deny('INVALID_ATTEMPT3_DETAIL_ENUM')
    if not (obj['original_network_error'] in ('NOT_OBSERVED','OTHER') or
        re.fullmatch(r'NET_[A-Z][A-Z0-9_]{0,47}',
                     obj['original_network_error'],re.ASCII)):
        _deny('INVALID_ATTEMPT3_ORIGINAL_ERROR')
    for k in ('data_nav','minimal_nav'):
        value=obj[k]
        if not (type(value) is str and
                (value in _A3_NAV or re.fullmatch(r'NET_[A-Z][A-Z0-9_]{0,47}',
                                                 value,re.ASCII))):
            _deny('INVALID_ATTEMPT3_NAV')
    for k in ('browser_alive','proc_tree_readable',
              'browser_netns_equal','network_service_netns_equal'):
        if obj[k] is not None and type(obj[k]) is not bool:
            _deny('INVALID_ATTEMPT3_BOOLEAN')
    for k in ('original_document_request','original_document_response',
              'original_document_failed','network_service_count',
              'minimal_tcp_accepts','minimal_http_gets'):
        if obj[k] is not None and (type(obj[k]) is not int or
                                  not 0 <= obj[k] <= 99):
            _deny('INVALID_ATTEMPT3_COUNT')
    if (obj['probe_status'] == 'NOT_RUN' and any(
           obj[k] != 'NOT_RUN' for k in
           ('frontend_python_get','minimal_python_get','data_nav','minimal_nav'))):
        _deny('INVALID_ATTEMPT3_SKIPPED_FIELDS')


def _attempt3_forensic_terminal(stderr):
    if not isinstance(stderr,bytes):
        return 'NOT_REPORTED'
    lines = stderr[:65536].split(b'\n')
    sentinels = [x for x in lines if x in (
        b'PIQ4DC_STAGE FORENSIC_DONE', b'PIQ4DC_STAGE FORENSIC_FAILED',
        b'PIQ4DC_STAGE FORENSIC_SKIPPED')]
    if len(sentinels) > 1:
        return 'MULTIPLE_TERMINALS'
    if len(sentinels) == 1:
        return sentinels[0].split(b'_')[-1].decode('ascii')
    if b'PIQ4DC_STAGE FORENSIC_BEGIN' in lines:
        return 'BEGIN_ONLY'
    return 'NOT_REPORTED'


def _attempt3_forensic_reason(stderr):
    if not isinstance(stderr,bytes):
        return 'NOT_REPORTED'
    choices=[]
    for row in stderr[:65536].split(b'\n'):
        if row.startswith(b'PIQ4DC_A3_FORENSICS_FAIL '):
            choices.append(row[25:].rstrip(b'\r'))
        elif row == b'PIQ4DC_A3_FORENSICS_SKIP LENGTH_OR_ENCODING':
            choices.append(b'LENGTH_OR_ENCODING')
    if len(choices)!=1:
        return 'NOT_REPORTED'
    try:
        value=choices[0].decode('ascii','strict')
    except UnicodeError:
        return 'NOT_REPORTED'
    return value if value in _A3_FORENSIC_REASONS else 'NOT_REPORTED'


def _attempt3_parse(stderr):
    result = {'collection_state':'ABSENT','forensic_terminal':'NOT_REPORTED',
              'forensic_reason':'NOT_REPORTED','details':None}
    if not isinstance(stderr,bytes):
        result['collection_state']='STDERR_UNAVAILABLE'
        return result
    result['forensic_terminal'] = _attempt3_forensic_terminal(stderr)
    result['forensic_reason'] = _attempt3_forensic_reason(stderr)
    if len(stderr)>65536:
        result['collection_state']='STDERR_TRUNCATED'
        return result
    marker = b'PIQ4DC_A3 '
    rows=[row[len(marker):].rstrip(b'\r') for row in stderr.split(b'\n')
          if row.startswith(marker)]
    if len(rows)>1:
        result['collection_state']='DUPLICATE'
        return result
    if not rows:
        return result
    if not 2 <= len(rows[0]) <= 1600:
        result['collection_state']='OVERSIZE'
        return result
    try:
        import json
        obj=json.loads(rows[0].decode('ascii','strict'))
        _attempt3_validate_details(obj)
    except (ValueError,TypeError,UnicodeError,BrowserUiError):
        result['collection_state']='MALFORMED'
        return result
    result['collection_state']='VALID'
    result['details']=obj
    return result


def _attempt3_validate_report(value):
    if (not isinstance(value,dict) or
            set(value) != {'collection_state','forensic_terminal','forensic_reason','details'}
            or any(type(value[k]) is not str for k in
                   ('collection_state','forensic_terminal','forensic_reason'))
            or value['collection_state'] not in _A3_STATES
            or value['forensic_terminal'] not in _A3_FORENSIC
            or value['forensic_reason'] not in _A3_FORENSIC_REASONS):
        _deny('INVALID_ATTEMPT3_RECORD')
    if value['collection_state']=='VALID':
        _attempt3_validate_details(value['details'])
    elif value['details'] is not None:
        _deny('INVALID_ATTEMPT3_MISSING_DETAILS')


# ProjectIQ Attempt 4: validate only fixed, bounded observational diagnostic values.
_A4_STATES = frozenset(('VALID', 'ABSENT', 'MALFORMED', 'OVERSIZE',
                        'DUPLICATE', 'STDERR_UNAVAILABLE', 'STDERR_TRUNCATED'))
_A4_FAULTS = frozenset(('NONE', 'OSError', 'ValueError', 'TypeError',
    'AttributeError', 'RuntimeError', 'IndexError', 'KeyError', 'MemoryError',
    'PermissionError', 'FileNotFoundError', 'TimeoutError', 'AssertionError',
    'BrokenPipeError', 'OTHER'))
_A4_PROBES = ('process','frontend','data','minimal')
_A4_HTTP = frozenset(('NOT_RUN','HTTP_200','HTTP_OTHER','TIMEOUT','IO_ERROR'))
_A4_DOM = frozenset(('NOT_RUN','MATCH','NO_MATCH','CDP_ERROR','CDP_TIMEOUT'))
_A4_NAV_BASE = frozenset(('NOT_RUN','OK','NAV_ERROR','CDP_ERROR','CDP_TIMEOUT'))


def _a4_is_nav(x):
    return (type(x) is str and
            (x in _A4_NAV_BASE or
             re.fullmatch(r'NET_[A-Z][A-Z0-9_]{0,47}',x,re.ASCII) is not None))


def _a4_is_count(x):
    return x is None or (type(x) is int and 0 <= x <= 99)


def _a4_is_bool(x):
    return x is None or type(x) is bool


def _a4_validate_details(d):
    if not isinstance(d,dict) or set(d) != {
            'classification','version','collector','original_error',
            'original_before','original_after','process','frontend','data','minimal'}:
        _deny('A4_SHAPE')
    if (d['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
        or type(d['version']) is not int or d['version'] != 1
        or d['collector'] not in ('PARTIAL','COMPLETED','FAILED')
        or type(d['original_error']) is not str
        or not (d['original_error'] in ('NOT_REPORTED','RUNTIME_ERROR','OTHER')
                or re.fullmatch(r'NET_[A-Z][A-Z0-9_]{0,47}',d['original_error'],re.ASCII))):
        _deny('A4_HEADER')
    for key in ('original_before','original_after'):
        x=d[key]
        if (not isinstance(x,dict) or set(x)!= {'request','response','failed'}
                or any(not _a4_is_count(v) for v in x.values())):
            _deny('A4_COUNTS')
    for name, keys in (
        ('process', {'state','error','alive','tree','browser_netns','network_netns',
                      'network_services'}),
        ('frontend',{'state','error','python_get'}),
        ('data', {'state','error','navigate','dom'}),
        ('minimal',{'state','error','python_get','navigate','dom','tcp_accepted',
                    'first_bytes','request_lines','get_requests','http_200'})):
        x=d[name]
        if (not isinstance(x,dict) or set(x)!=keys
            or type(x['state']) is not str or x['state'] not in ('NOT_RUN','OK','FAILED','SKIPPED')
            or type(x['error']) is not str or x['error'] not in _A4_FAULTS):
            _deny('A4_STAGE')
        if (x['state']=='FAILED') != (x['error']!='NONE'):
            _deny('A4_STAGE_FAULT_MISMATCH')
    p=d['process']
    if (not all(_a4_is_bool(p[k]) for k in ('alive','tree','browser_netns','network_netns'))
        or not _a4_is_count(p['network_services'])):
        _deny('A4_PROCESS')
    if (type(d['frontend']['python_get']) is not str or
            d['frontend']['python_get'] not in _A4_HTTP):
        _deny('A4_FRONTEND')
    if (not _a4_is_nav(d['data']['navigate'])
        or type(d['data']['dom']) is not str
        or d['data']['dom'] not in _A4_DOM):
        _deny('A4_DATA')
    m=d['minimal']
    if (type(m['python_get']) is not str or m['python_get'] not in _A4_HTTP
        or not _a4_is_nav(m['navigate'])
        or type(m['dom']) is not str or m['dom'] not in _A4_DOM
        or any(not _a4_is_count(m[k]) for k in
               ('tcp_accepted','first_bytes','request_lines','get_requests','http_200'))):
        _deny('A4_MINIMAL')
    if (m['navigate']!='OK' and m['dom']!='NOT_RUN') or (
            d['data']['navigate']!='OK' and d['data']['dom']!='NOT_RUN'):
        _deny('A4_DOM_WITHOUT_NAV')
    if d['collector']=='COMPLETED' and any(d[k]['state'] not in ('OK','SKIPPED') for k in _A4_PROBES):
        _deny('A4_FALSE_COMPLETION')
    if d['collector']=='FAILED' and any(d[k]['state']=='OK' for k in _A4_PROBES):
        _deny('A4_INCONSISTENT_FAILURE')


def _a4_parse(stderr):
    result={'collection_state':'ABSENT','details':None,'steps':[],
            'entered':False,'unexpected':'NONE','emit_failed':'NONE'}
    if not isinstance(stderr,bytes):
        result['collection_state']='STDERR_UNAVAILABLE'
        return result
    if len(stderr)>65536:
        result['collection_state']='STDERR_TRUNCATED'
        return result
    rows=stderr.split(b'\n')
    prefix=b'PIQ4DC_A4 '
    candidates=[x[len(prefix):].rstrip(b'\r') for x in rows if x.startswith(prefix)]
    result['entered']=b'PIQ4DC_A4_ENTER' in rows
    for name, field in ((b'PIQ4DC_A4_UNEXPECTED ','unexpected'),
                        (b'PIQ4DC_A4_EMIT_FAILED ','emit_failed')):
        found=[x[len(name):].rstrip(b'\r') for x in rows if x.startswith(name)]
        if len(found)==1:
            try:
                value=found[0].decode('ascii','strict')
                if value in _A4_FAULTS:
                    result[field]=value
            except UnicodeError:
                pass
    for x in rows:
        if not x.startswith(b'PIQ4DC_A4_STEP '):
            continue
        parts=x.rstrip(b'\r').split(b' ')
        if len(parts)!=4:
            continue
        try:
            name,status,fault=(v.decode('ascii','strict') for v in parts[1:])
        except UnicodeError:
            continue
        if (name in _A4_PROBES and status in ('OK','FAILED','SKIPPED')
             and fault in _A4_FAULTS and len(result['steps']) < 8):
            result['steps'].append(name+':'+status+':'+fault)
    if len(candidates)>1:
        result['collection_state']='DUPLICATE'
        return result
    if len(candidates)==0:
        return result
    if not 2<=len(candidates[0])<=4096:
        result['collection_state']='OVERSIZE'
        return result
    try:
        import json
        obj=json.loads(candidates[0].decode('ascii','strict'))
        _a4_validate_details(obj)
    except (UnicodeError,ValueError,TypeError,BrowserUiError):
        result['collection_state']='MALFORMED'
        return result
    result['collection_state']='VALID'
    result['details']=obj
    return result


def _a4_validate_report(r):
    if (not isinstance(r,dict)
        or set(r) != {'collection_state','details','steps','entered','unexpected','emit_failed'}
        or type(r['collection_state']) is not str or r['collection_state'] not in _A4_STATES
        or type(r['entered']) is not bool
        or type(r['unexpected']) is not str or r['unexpected'] not in _A4_FAULTS
        or type(r['emit_failed']) is not str or r['emit_failed'] not in _A4_FAULTS
        or not isinstance(r['steps'],list) or len(r['steps'])>8
        or any(type(v) is not str or not re.fullmatch(
            r'(process|frontend|data|minimal):(OK|FAILED|SKIPPED):[A-Za-z]+',v,re.ASCII)
            for v in r['steps'])):
        _deny('A4_INVALID_REPORT')
    if len(set(x.split(':')[0] for x in r['steps'])) != len(r['steps']):
        _deny('A4_DUPLICATE_STEP')
    for v in r['steps']:
        _name,_status,fault=v.split(':')
        if fault not in _A4_FAULTS or ((_status=='FAILED') != (fault!='NONE')):
            _deny('A4_INVALID_STEP_FAULT')
    if r['collection_state']=='VALID':
        _a4_validate_details(r['details'])
    elif r['details'] is not None:
        _deny('A4_FALSE_DETAILS')


# ProjectIQ Phase 4D-C Attempt 5: strict bounded validation of helper-side
# connection-stage evidence. Never a security attestation or PASS oracle.
_A5_HELPER_STATES = frozenset(('VALID','ABSENT','DUPLICATE','MALFORMED','OVERSIZE',
                               'STDERR_UNAVAILABLE','STDERR_TRUNCATED'))
_A5_HELPER_PHASES = frozenset(('CONNECT','WRITE','READ','DONE'))
_A5_HELPER_OUTCOMES = frozenset((
    'NOT_RUN','HTTP_200','HTTP_OTHER','EOF_NO_RESPONSE',
    'CONNECTION_REFUSED','CONNECTION_RESET','CONNECTION_ABORTED',
    'CONNECTION_TIMEOUT','BROKEN_PIPE','PERMISSION_DENIED','FD_LIMIT',
    'TASK_TEMPORARILY_UNAVAILABLE','OTHER_OS_ERROR','OTHER_FAILURE'))


def _a5_validate_helper_details(data):
    if (type(data) is not dict or set(data) != {
            'classification','version','socket','uid_tasks','nproc_soft','nproc_hard'}
          or data['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
          or type(data['version']) is not int or data['version'] != 1
          or type(data['socket']) is not dict
          or set(data['socket']) != {'phase','outcome'}
          or data['socket']['phase'] not in _A5_HELPER_PHASES
          or data['socket']['outcome'] not in _A5_HELPER_OUTCOMES):
        _deny('A5_INVALID_HELPER_RECORD')
    for key in ('uid_tasks','nproc_soft','nproc_hard'):
        value = data[key]
        if value is not None and (type(value) is not int or not 0 <= value <= 65535):
            _deny('A5_INVALID_HELPER_RESOURCE')


def _a5_parse_helper(stderr):
    report = {'collection_state':'ABSENT', 'details':None}
    if not isinstance(stderr, bytes):
        report['collection_state'] = 'STDERR_UNAVAILABLE'
        return report
    if len(stderr) > 65536:
        report['collection_state'] = 'STDERR_TRUNCATED'
        return report
    prefix = b'PIQ4DC_A5_HELPER '
    rows = [row[len(prefix):].rstrip(b'\r') for row in stderr.split(b'\n')
            if row.startswith(prefix)]
    if len(rows) > 1:
        report['collection_state'] = 'DUPLICATE'
    elif len(rows) == 1:
        if not 2 <= len(rows[0]) <= 512:
            report['collection_state'] = 'OVERSIZE'
        else:
            try:
                import json
                obj = json.loads(rows[0].decode('ascii', 'strict'))
                _a5_validate_helper_details(obj)
            except (UnicodeError, ValueError, TypeError, BrowserUiError):
                report['collection_state'] = 'MALFORMED'
            else:
                report['collection_state'] = 'VALID'
                report['details'] = obj
    return report


def _a5_validate_helper_report(record):
    if (type(record) is not dict or set(record) != {'collection_state','details'}
            or record['collection_state'] not in _A5_HELPER_STATES):
        _deny('A5_INVALID_HELPER_SHAPE')
    if record['collection_state'] == 'VALID':
        _a5_validate_helper_details(record['details'])
    elif record['details'] is not None:
        _deny('A5_UNSUPPORTED_HELPER_DETAILS')


_A5_EVENTS = ('ACCEPT','DISPATCH_ATTEMPT','DISPATCH_OK','DISPATCH_FAIL_RUNTIME',
    'DISPATCH_FAIL_OS','THREAD_ENTER','THREAD_EXIT','HANDLER_CONSTRUCT',
    'PARSE_ENTER','PARSE_OK','PARSE_REJECT','GET_ENTER','RESPONSE_200',
    'RESPONSE_OTHER','HEADERS_END','HANDLER_ERROR')
_A5_HTTP_OUTCOMES = frozenset((
    'HTTP_200','HTTP_OTHER','REMOTE_DISCONNECTED','CONNECTION_REFUSED',
    'CONNECTION_RESET','CONNECTION_ABORTED','TIMEOUT','BAD_STATUS',
    'BROKEN_PIPE','PERMISSION_DENIED','FD_LIMIT','OTHER_OS_ERROR',
    'OTHER_FAILURE'))
_A5_FRONTEND_STATES = frozenset(('VALID','LOG_MISSING','LOG_UNAVAILABLE',
    'LOG_TOO_LARGE','NOT_INSTRUMENTED','INVALID_LOG','SNAPSHOT_INVALID'))


def _a5_check_counter_map(value):
    return (type(value) is dict and set(value) == set(_A5_EVENTS)
            and all(type(x) is int and 0 <= x <= 9999 for x in value.values()))


def _a5_check_samples(value):
    return (type(value) is dict and set(value) == {'min','max','count'}
            and type(value['count']) is int and 0 <= value['count'] <= 128
            and all(v is None or (type(v) is int and 0 <= v <= 65535)
                    for v in (value['min'],value['max']))
            and ((value['count'] == 0 and value['min'] is None and value['max'] is None)
                 or (value['count'] > 0 and value['min'] is not None
                     and value['max'] is not None
                     and value['min'] <= value['max'])))


def _a5_validate_frontend_report(r):
    if (type(r) is not dict or set(r) != {
            'classification','version','trace_state','limits',
            'pre_counts','during_delta','post_delta',
            'uid_before','uid_during','uid_after',
            'fds_before','fds_during','fds_after',
            'threads_before','threads_during','threads_after','post_http'}
          or r['classification'] != 'OBSERVATIONAL_NOT_SECURITY_ATTESTATION'
          or type(r['version']) is not int or r['version'] != 1
          or r['trace_state'] not in _A5_FRONTEND_STATES
          or r['post_http'] not in _A5_HTTP_OUTCOMES
          or type(r['limits']) is not dict
          or set(r['limits']) != {'nproc_soft','nproc_hard','nofile_soft','nofile_hard'}):
        _deny('A5_INVALID_FRONTEND_SHAPE')
    for v in r['limits'].values():
        if v is not None and (type(v) is not int or not 0 <= v <= 65535):
            _deny('A5_INVALID_FRONTEND_LIMIT')
    for key in ('pre_counts','during_delta','post_delta'):
        if r[key] is not None and not _a5_check_counter_map(r[key]):
            _deny('A5_INVALID_FRONTEND_COUNT')
    for key in ('uid_before','uid_during','uid_after',
                'fds_before','fds_during','fds_after',
                'threads_before','threads_during','threads_after'):
        if not _a5_check_samples(r[key]):
            _deny('A5_INVALID_FRONTEND_UID')
    if r['trace_state'] != 'VALID':
        if any(r[x] is not None for x in ('pre_counts','during_delta','post_delta')):
            _deny('A5_UNTRUSTED_COUNTERS')
    else:
        if any(r[x] is None for x in ('pre_counts','during_delta','post_delta')):
            _deny('A5_MISSING_COUNTERS')


def _cdp_failure_issue(returncode: object, stderr: object) -> str:
    """Map only bounded, fixed CDP codes or Chromium net error identifiers."""
    if isinstance(stderr, bytes):
        # Attempt-2 stage markers can exceed the old 512-byte scan window.
        # This bounded scan must still find the exact helper error line.
        sample = stderr[:8192]
        # The helper emits one tightly bounded token. Do not forward raw stderr.
        net_match = (re.search(
            rb'(?:\A|\n)CDP_NET_ERR_([A-Z][A-Z0-9_]{0,47})\r?(?:\n|\Z)', sample)
            if returncode == 119 else None)
        if net_match is not None:
            return 'BROWSER_CDP_NET_ERR_' + net_match.group(1).decode('ascii')
        mapping = (
            (b'CDP_ADMIN_POLICY_BLOCK', 'BROWSER_CDP_ADMIN_POLICY_BLOCK'),
            (b'CDP_CONNECTION_REFUSED', 'BROWSER_CDP_CONNECTION_REFUSED'),
            (b'CDP_NETWORK_ERROR', 'BROWSER_CDP_NAVIGATION_FAILED'),
            (b'CDP_NAVIGATION_ERROR', 'BROWSER_CDP_NAVIGATION_FAILED'),
            (b'CDP_REPLY_TIMEOUT', 'BROWSER_CDP_TIMEOUT'),
            (b'CDP_PIPE_CLOSED', 'BROWSER_CDP_PIPE_CLOSED'),
            (b'CDP_PIPE_WRITE_FAILED', 'BROWSER_CDP_PIPE_CLOSED'),
            (b'CDP_PROTOCOL_ERROR', 'BROWSER_CDP_PROTOCOL_ERROR'),
            (b'CDP_RUNTIME_ERROR', 'BROWSER_CDP_RUNTIME_ERROR'),
        )
        for marker, issue in mapping:
            if marker in sample:
                return issue
    if returncode in (111, 112, 113, 114, 118):
        return 'BROWSER_CDP_HELPER_REJECTED'
    return 'BROWSER_CDP_UNAVAILABLE'


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


def _p4dc_pending_backend_dom(raw: bytes, checks: list[dict]) -> bool:
    """Only the approved pilot's initial React state warrants a CDP retry.

    `--dump-dom` captures one snapshot, not an app-specific asynchronous ready
    state.  Reuse the existing bounded CDP polling path rather than accepting
    an initial paragraph as a definitive failure. Never inspect unbounded HTML
    or treat script text / malformed HTML as proof the app is ready.
    """
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DOM or
            [(x.get('tag'), x.get('exact_text')) for x in checks] != [
                ('h1', 'ProjectIQ D4 pilot'), ('p', 'SQLite verified')]):
        return False
    try:
        fp = _dom_fingerprint(raw.decode('utf-8', 'strict'))
    except (UnicodeError, ValueError, TypeError):
        return False
    return (fp['title_expected'] and fp['root_present'] and
            fp['main_present'] and fp['vite_js_asset_present'] and
            fp['h1_expected'] and fp['h1_count'] == 1 and
            fp['p_count'] == 1 and fp['p_state'] == 'INITIAL' and
            not fp['chrome_error_marker'])


def run_browser_ui(bundle: dict, *, expected_source: dict, expected_adapter: str,
                   workspace: Path, trusted_process: Callable[..., Any],
                   cdp_mode: bool = False) -> dict:
    """A real Chrome headless renderer; subprocess stays in guest app cgroup.

    This initial slice deliberately has no browser clicks or JavaScript supplied
    by the operator. The React page must *render* expected dynamic DOM content.
    """
    validate_guest_browser_bundle(bundle, expected_source=expected_source,
                                  expected_adapter=expected_adapter)
    # The existing CDP helper is pinned to React /index.html.
    # PHP uses one bounded direct Chrome capture until reviewed CDP support exists.
    if bundle['adapter'] == 'php_mysql_v1' and cdp_mode:
        _deny('BROWSER_PHP_CDP_MODE_UNSUPPORTED')
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
           '--dump-dom', 'http://127.0.0.1:8765' + bundle['page_path']]
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
    if not proc.stdout or (cdp_mode and _p4dc_pending_backend_dom(proc.stdout, bundle['checks'])):
        if bundle['adapter'] == 'php_mysql_v1':
            # No accidental React /index.html fallback on a PHP page.
            return _empty(bundle, issue='BROWSER_EMPTY_STDOUT_DUMP_DOM')
        if cdp_mode:
            # `--dump-dom` occasionally returns exit=0 and zero HTML after an
            # HTTP navigation. CDP uses a *private* pipe, not an exposed port,
            # and checks real DOM state instead of inferring from exit status.
            kernel_before = _attempt2_kernel_events()
            try:
                cdp = trusted_process(
                    ['/usr/bin/python3', '-I', '/home/projectiq/guest_browser_cdp.py'],
                    project_code=True, browser_profile=True, timeout=43,
                    cwd=Path(workspace))
            except (OSError, RuntimeError, ValueError) as exc:
                reason = str(exc.args[0]) if len(exc.args) == 1 and type(exc.args[0]) is str else ''
                launch = ('TIMEOUT' if 'TIMEOUT' in reason else
                          'POLICY_REJECTED' if 'POLICY' in reason or 'PROFILE' in reason else
                          'OS_ERROR' if isinstance(exc,OSError) else 'OTHER_EXCEPTION')
                report = _empty(bundle, issue='BROWSER_CDP_UNAVAILABLE')
                report['cdp_triage'] = _attempt2_triage(launch=launch,
                    kernel_before=kernel_before,kernel_after=_attempt2_kernel_events())
                return report
            if (getattr(cdp, 'returncode', None) != 0 or
                    not isinstance(getattr(cdp, 'stdout', None), bytes) or
                    not cdp.stdout or len(cdp.stdout) > MAX_DOM):
                issue = _cdp_failure_issue(getattr(cdp, 'returncode', None),
                                           getattr(cdp, 'stderr', None))
                report = _empty(bundle, issue=issue)
                report['cdp_triage'] = _attempt2_triage(cdp,
                    kernel_before=kernel_before,kernel_after=_attempt2_kernel_events())
                report['attempt4_probe'] = _a4_parse(getattr(cdp, 'stderr', None))
                report['attempt5_client'] = _a5_parse_helper(getattr(cdp, 'stderr', None))
                trace = _parse_forensic_trace(getattr(cdp, 'stderr', None))
                if trace is not None:
                    report['cdp_forensics'] = trace
                return report
            try:
                dom = cdp.stdout.decode('utf-8', 'strict')
                matches = dom_checks(dom, bundle['checks'])
            except (UnicodeError, ValueError):
                return _empty(bundle, issue='BROWSER_DOM_INVALID')
            passed = all(x['matched'] for x in matches)
            report = _empty(bundle, status='PASS' if passed else 'FAIL',
                            issue=None if passed else 'RENDERED_DOM_DID_NOT_MATCH')
            report['checks'] = matches
            report['render_engine'] = 'CDP_PIPE'
            if not passed:
                report['page_fingerprint'] = _dom_fingerprint(dom)
                report['capture_shape'] = _capture_shape(cdp.stdout)
            return report
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
    if not passed and bundle['adapter'] == 'node_fullstack_offline_v1':
        # These existing fingerprint labels are specific to the React pilot.
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
            or set(report) - required - {'diagnostics', 'page_fingerprint', 'navigation_probe', 'capture_shape', 'render_smoke', 'loopback_differential', 'render_engine', 'cdp_forensics', 'server_trace', 'cdp_triage', 'attempt3_probe', 'attempt4_probe', 'attempt5_client', 'attempt5_frontend'}
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
    if 'attempt5_client' in report:
        if report['status'] != 'INCONCLUSIVE':
            _deny('ATTEMPT5_HELPER_FAIL_ONLY')
        _a5_validate_helper_report(report['attempt5_client'])
    if 'attempt5_frontend' in report:
        if report['status'] != 'INCONCLUSIVE':
            _deny('ATTEMPT5_FRONTEND_FAIL_ONLY')
        _a5_validate_frontend_report(report['attempt5_frontend'])
    if 'attempt4_probe' in report:
        if (report['status'] != 'INCONCLUSIVE' or
                not isinstance(report['issue_code'], str) or
                not report['issue_code'].startswith('BROWSER_CDP_')):
            _deny('ATTEMPT4_FAIL_ONLY')
        _a4_validate_report(report['attempt4_probe'])
    if 'attempt3_probe' in report:
        if (report['status'] != 'INCONCLUSIVE' or
                not isinstance(report['issue_code'], str) or
                not report['issue_code'].startswith('BROWSER_CDP_')):
            _deny('ATTEMPT3_FAIL_ONLY')
        _attempt3_validate_report(report['attempt3_probe'])
    if 'cdp_triage' in report:
        if (report['status'] != 'INCONCLUSIVE' or
                not isinstance(report['issue_code'],str) or
                not report['issue_code'].startswith('BROWSER_CDP_')):
            _deny('ATTEMPT2_TRIAGE_FAIL_ONLY')
        _validate_attempt2_triage(report['cdp_triage'])
    if 'cdp_forensics' in report:
        if report['status'] != 'INCONCLUSIVE' or not str(report['issue_code']).startswith('BROWSER_CDP_'):
            _deny('4DC_FORENSICS_FAIL_ONLY')
        _validate_forensic_trace(report['cdp_forensics'])
    if 'server_trace' in report:
        if report['status'] != 'INCONCLUSIVE':
            _deny('4DC_SERVER_TRACE_FAIL_ONLY')
        _validate_server_trace(report['server_trace'])
    if 'render_engine' in report and (report['render_engine'] != 'CDP_PIPE' or
            report['status'] not in ('PASS', 'FAIL')):
        _deny('INVALID_BROWSER_RENDER_ENGINE')
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
          (not (isinstance(report['issue_code'], str) and re.fullmatch(
              r'BROWSER_CDP_NET_ERR_[A-Z][A-Z0-9_]{0,47}', report['issue_code'], re.ASCII))
           and report['issue_code'] not in {
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
              'BROWSER_CDP_UNAVAILABLE', 'BROWSER_CDP_NAVIGATION_FAILED',
              'BROWSER_CDP_ADMIN_POLICY_BLOCK', 'BROWSER_CDP_CONNECTION_REFUSED',
              'BROWSER_CDP_TIMEOUT',
              'BROWSER_CDP_PIPE_CLOSED', 'BROWSER_CDP_PROTOCOL_ERROR',
              'BROWSER_CDP_RUNTIME_ERROR', 'BROWSER_CDP_HELPER_REJECTED',
              'BROWSER_STDOUT_UNAVAILABLE', 'BROWSER_DOM_TOO_LARGE',
              'BROWSER_EMPTY_STDOUT_SCOPE_UNVERIFIED',
              'BROWSER_EMPTY_STDOUT_CHROME_VERSION_UNVERIFIED',
              'BROWSER_EMPTY_STDOUT_DUMP_DOM',
              'BROWSER_FAILED_OR_OUTPUT_TOO_LARGE', 'BROWSER_DOM_INVALID'})):
        _deny('BROWSER_UI_INVALID_INCONCLUSIVE')
