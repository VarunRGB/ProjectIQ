"""Linux/Windows-compatible fail-only boundary diagnostics tests for attempt 2."""
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from projectiq_executioner.phase4 import guest_browser_ui as ui
from projectiq_executioner.phase4 import guest_browser_cdp as cdp
from projectiq_executioner.phase2.vm_worker import guest_agent as agent


class Attempt2Diagnostics(unittest.TestCase):
    def _proc(self, rc=119, stderr=b'', stdout=b''):
        return SimpleNamespace(returncode=rc, stderr=stderr, stdout=stdout)

    def test_crash_stage_survives_without_json(self):
        proc = self._proc(stderr=(b'PIQ4DC_STAGE ENTRY\n'
            b'PIQ4DC_STAGE TARGET_CREATE\nPIQ4DC_STAGE ERROR_CAUGHT\n'
            b'PIQ4DC_FAULT AttributeError\nPIQ4DC_STAGE CLEANUP\n'))
        result = ui._attempt2_triage(proc)
        self.assertEqual(result['last_stage'], 'TARGET_CREATE')
        self.assertEqual(result['helper_return'], 'HELPER_119')
        self.assertEqual(result['fault_class'], 'AttributeError')
        self.assertEqual(result['helper_code'], 'NOT_REPORTED')
        self.assertEqual(result['forensics_state'], 'ABSENT')
        ui._validate_attempt2_triage(result)

    def test_navigate_response_stage_is_preserved_not_cleanup(self):
        p = self._proc(stderr=b'PIQ4DC_STAGE NAVIGATE_REPLY\nPIQ4DC_STAGE FORENSIC_DONE\nPIQ4DC_STAGE CLEANUP\n')
        self.assertEqual(ui._attempt2_triage(p)['last_stage'],'NAVIGATE_REPLY')

    def test_linux_exit_status_without_output(self):
        p = self._proc(rc=0,stderr=b'',stdout=b'')
        self.assertEqual(ui._attempt2_triage(p)['helper_return'],'ZERO_NO_DOM')
        self.assertEqual(ui._attempt2_triage(p)['forensics_state'],'ABSENT')

    def test_external_launch_failure_is_distinct(self):
        self.assertEqual(ui._attempt2_triage(launch='TIMEOUT')['helper_return'],'NOT_RETURNED')
        self.assertEqual(ui._attempt2_triage(launch='TIMEOUT')['launch_failure'],'TIMEOUT')

    def test_exit_rejected_and_signal_are_distinct(self):
        self.assertEqual(ui._attempt2_triage(self._proc(118))['helper_return'],'HELPER_REJECTED')
        self.assertEqual(ui._attempt2_triage(self._proc(-9))['helper_return'],'SIGNALLED')
        self.assertEqual(ui._attempt2_triage(self._proc(0,stdout=b'a'))['helper_return'],'ZERO_INVALID_DOM')

    def test_false_or_decorative_tags_cannot_enter_reporting(self):
        p = self._proc(stderr=b'PIQ4DC_STAGE VISIT_SOME_URL\nPIQ4DC_FAULT UserText\n')
        d = ui._attempt2_triage(p)
        self.assertEqual(d['last_stage'],'NOT_REPORTED')
        self.assertEqual(d['fault_class'],'NONE')

    def test_malformed_markers_rejected(self):
        d = ui._attempt2_triage(self._proc())
        for field, invalid in [('last_stage','random'),('fault_class','ExternalData'),
                               ('stage_markers',True),('helper_code','CDP_INJECTED_TEXT'),('forensics_state','BAD'),
                               ('launch_failure','NETWORK_DISABLED'),
                               ('stderr_size_bin','BAD'),('classification','ATTESTED')]:
            x = dict(d);x[field]=invalid
            with self.subTest(field=field), self.assertRaises(ui.BrowserUiError):
                ui._validate_attempt2_triage(x)

    def test_forensics_duplicate_detected(self):
        self.assertEqual(ui._attempt2_trace_state(b'PIQ4DC_FORENSICS {}\nPIQ4DC_FORENSICS {}\n'), 'DUPLICATE')

    def test_forensics_malformed_detected(self):
        self.assertEqual(ui._attempt2_trace_state(b'PIQ4DC_FORENSICS bad-json\n'), 'MALFORMED')

    def test_forensics_oversize_detected(self):
        self.assertEqual(ui._attempt2_trace_state(b'PIQ4DC_FORENSICS '+b'0'*3801+b'\n'), 'OVERSIZE')

    def test_forensics_missing_detected(self):
        self.assertEqual(ui._attempt2_trace_state(b'CDP_NET_ERR_SOCKET_NOT_CONNECTED\n'), 'ABSENT')

    def test_forensics_large_stderr_not_silently_absent(self):
        self.assertEqual(ui._attempt2_trace_state(b'0'*65537), 'STDERR_TRUNCATED')

    def test_forensics_stderr_unavailable(self):
        self.assertEqual(ui._attempt2_trace_state(None), 'STDERR_UNAVAILABLE')

    def test_stderr_buckets(self):
        self.assertEqual(ui._attempt2_triage(self._proc(stderr=b'x'*3000))['stderr_size_bin'], 'UNDER_16K')
        self.assertEqual(ui._attempt2_triage(self._proc(stderr=b'x'*17000))['stderr_size_bin'], 'UNDER_64K')
        self.assertEqual(ui._attempt2_triage(self._proc(stderr=b'x'*66000))['stderr_size_bin'], 'OVER_64K')

    def test_stage_marker_allowlist(self):
        self.assertIn('NAVIGATE_DISPATCH',cdp._ATTEMPT2_STAGES)
        with self.assertRaises(ValueError): cdp._attempt2_stage('HI\nPRIVATE')
        with patch.object(cdp.os,'write') as write:
            cdp._attempt2_stage('NAVIGATE_REPLY')
            write.assert_called_once_with(2,b'PIQ4DC_STAGE NAVIGATE_REPLY\n')

    def test_server_accept_event_parsed_but_not_attributed_to_chrome(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'d4_frontend.log'
            p.write_bytes(b'PIQ4DC_HTTP READY\nPIQ4DC_HTTP TCP ACCEPT\nPIQ4DC_HTTP INDEX SEEN\n')
            x=agent._phase4dc_http_snapshot(Path(td))
            self.assertTrue(x['instrumentation_active'])
            self.assertEqual(x['tcp_accepted'],1)
            self.assertEqual(x['index_seen'],1)
            d=agent._phase4dc_http_difference({k:0 if type(v) is int else v for k,v in x.items()}, x)
            self.assertEqual(d['tcp_accepted'],1)
            ui._validate_server_trace(d)

    def test_legacy_server_trace_shape_still_valid(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'d4_frontend.log'
            p.write_bytes(b'PIQ4DC_HTTP READY\n')
            r=agent._phase4dc_http_snapshot(Path(td))
            d=agent._phase4dc_http_difference(r,r)
            d.pop('tcp_accepted')
            ui._validate_server_trace(d)

    def test_server_trace_rejects_bad_tcp_count(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'d4_frontend.log').write_bytes(b'PIQ4DC_HTTP READY\n')
            r=agent._phase4dc_http_snapshot(Path(td))
            d=agent._phase4dc_http_difference(r,r)
            d['tcp_accepted']=True
            with self.assertRaises(ui.BrowserUiError):ui._validate_server_trace(d)

    def test_specific_helper_failures_are_retained(self):
        names=('CDP_INVALID_JSON','CDP_NO_SESSION','CDP_EMPTY_DOM',
               'CDP_PIPE_CLOSED','CDP_NET_ERR_SOCKET_NOT_CONNECTED',
               'CDP_RUNTIME_ERROR','CDP_REPLY_TIMEOUT')
        for name in names:
            with self.subTest(name=name):
                p=self._proc(stderr=(name+'\n').encode('ascii'))
                self.assertEqual(ui._attempt2_triage(p)['helper_code'],name)

    def test_unknown_helper_lines_never_escape(self):
        p=self._proc(stderr=b'cdp_error http://example.private/token\n')
        self.assertEqual(ui._attempt2_triage(p)['helper_code'],'NOT_REPORTED')

    def test_parent_kernel_event_deltas_survive_helper_death(self):
        first={'pids_max':4,'memory_high':3,'memory_max':5,
               'memory_oom':1,'memory_oom_kill':1}
        last={'pids_max':6,'memory_high':4,'memory_max':5,
              'memory_oom':2,'memory_oom_kill':2}
        d=ui._attempt2_kernel_delta(first,last)
        self.assertEqual(d['pids_max'],2)
        self.assertEqual(d['memory_oom_kill'],1)
        x=ui._attempt2_triage(self._proc(rc=-9),kernel_before=first,kernel_after=last)
        self.assertEqual(x['helper_return'],'SIGNALLED')
        self.assertEqual(x['kernel_events']['memory_oom_kill'],1)
        ui._validate_attempt2_triage(x)

    def test_parent_kernel_unknown_is_not_zero(self):
        x=ui._attempt2_triage(self._proc(rc=-9))
        self.assertIsNone(x['kernel_events']['memory_oom_kill'])
        self.assertIsNone(x['kernel_events']['pids_max'])

    def test_parent_kernel_reads_exact_fixed_cgroup_path(self):
        with tempfile.TemporaryDirectory() as td:
            node=(Path(td)/'user.slice'/'user-1901.slice'/'user@1901.service'
                 /'projectiq.slice'/'projectiq-eval.slice')
            node.mkdir(parents=True)
            (node/'memory.events').write_text('high 0\nmax 3\noom 1\noom_kill 1\n')
            (node/'pids.events').write_text('max 4\n')
            result=ui._attempt2_kernel_events(root=Path(td),uid=1901)
            self.assertEqual(result['memory_oom_kill'],1)
            self.assertEqual(result['pids_max'],4)
            self.assertIsNone(ui._attempt2_kernel_events(root=Path(td),uid=1902)['pids_max'])

    def test_cdp_marker_window_includes_late_failure_after_stage_markers(self):
        stderr = b''.join(b'PIQ4DC_STAGE NAVIGATE_DISPATCH\n' for _ in range(30))
        stderr += b'CDP_NET_ERR_SOCKET_NOT_CONNECTED\n'
        self.assertGreater(len(stderr),512)
        self.assertEqual(ui._cdp_failure_issue(119,stderr),'BROWSER_CDP_NET_ERR_SOCKET_NOT_CONNECTED')
        self.assertEqual(ui._attempt2_triage(self._proc(stderr=stderr))['helper_code'],
                         'CDP_NET_ERR_SOCKET_NOT_CONNECTED')

    def test_cdp_triage_fail_only_report_contract(self):
        from projectiq_executioner.tests.test_phase4dc_network_forensics import bundle
        b=bundle()
        r=ui._empty(b,issue='BROWSER_CDP_UNAVAILABLE')
        r['cdp_triage']=ui._attempt2_triage(self._proc(stderr=b'CDP_NO_SESSION\n'))
        ui.validate_browser_ui_report(b,r)
        r['status']='PASS'
        r['issue_code']=None
        r['checks']=[{'tag':x['tag'],'matched':True} for x in b['checks']]
        with self.assertRaises(ui.BrowserUiError):
            ui.validate_browser_ui_report(b,r)

    def test_other_cdp_network_error_still_classified(self):
        p=self._proc(stderr=b'CDP_NET_ERR_SOCKET_NOT_CONNECTED\nPIQ4DC_STAGE NAVIGATE_REPLY\n')
        self.assertEqual(ui._cdp_failure_issue(p.returncode,p.stderr), 'BROWSER_CDP_NET_ERR_SOCKET_NOT_CONNECTED')


if __name__ == '__main__': unittest.main()
