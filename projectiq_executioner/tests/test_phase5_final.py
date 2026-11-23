"""Phase 5 final release: scoped scores and cross-language redacted secret detection."""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase5.__main__ import main
from projectiq_executioner.phase5.pipeline import analyze_project
from projectiq_executioner.phase5.report import markdown
from projectiq_executioner.phase5.contracts import validate_report


class CrossLanguageSecurity(unittest.TestCase):
    def test_html_script_token_detected_without_value(self):
        token = 'ghp_' + 'A9b8C7d6' * 5
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'index.html').write_text(
                '<html><script>const key = "' + token + '";</script></html>\n',
                encoding='utf-8')
            result = analyze_project(p)
            self.assertIn(('GITHUB_TOKEN_PATTERN', 'index.html', 1),
                          {(x['code'], x['path'], x['line']) for x in result['security']['findings']})
            self.assertNotIn(token, json.dumps(result) + markdown(result))
            validate_report(result)

    def test_css_and_sql_patterns_are_scanned(self):
        google = 'AIza' + 'aB9_X' * 7
        stripe = 'sk_live_' + 'A1b2C3D4E5' * 3
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'site.css').write_text('/* example styling */\n--key: "' + google + '";\n')
            (p / 'dump.sql').write_text('-- audit row\nINSERT INTO config VALUES ("' + stripe + '");\n')
            result = analyze_project(p)
            codes = {(x['code'], x['path'], x['line']) for x in result['security']['findings']}
            self.assertIn(('GOOGLE_API_KEY_PATTERN', 'site.css', 2), codes)
            self.assertIn(('STRIPE_SECRET_KEY_PATTERN', 'dump.sql', 2), codes)
            dumped = json.dumps(result) + markdown(result)
            self.assertNotIn(google, dumped)
            self.assertNotIn(stripe, dumped)
            validate_report(result)

    def test_example_word_does_not_mask_a_provider_token(self):
        token = 'ghp_' + 'Q7r8Y9s0' * 5
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'README.md').write_text('example deployment token: ' + token + '\n')
            (p / 'app.py').write_text('def f():\n    return 2\n')
            result = analyze_project(p)
            self.assertIn('GITHUB_TOKEN_PATTERN', [x['code'] for x in result['security']['findings']])
            self.assertNotIn(token, json.dumps(result) + markdown(result))
            self.assertEqual(result['security']['scanned_additional_files'], 1)

    def test_placeholder_assignment_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'page.html').write_text('<script>const api_key = "example_placeholder_change_me";</script>\n')
            r = analyze_project(p)
            self.assertNotIn('HIGH_ENTROPY_SECRET_ASSIGNMENT', [x['code'] for x in r['security']['findings']])

    def test_assignment_with_unrelated_example_word_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            synthetic = ''.join(('Q7tr9Bk2', 'Pd4Vm6Xy', '8Zp1Na3S', 't5Wh7'))
            (p / 'page.html').write_text('<!-- example below -->\n<script> const api_key = "' + synthetic + '"; // example </script>\n')
            r = analyze_project(p)
            self.assertIn('HIGH_ENTROPY_SECRET_ASSIGNMENT',
                          [x['code'] for x in r['security']['findings']])

    def test_env_contents_never_read_or_echoed(self):
        token = 'ghp_' + 'Z1x2Y3w4' * 5
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / '.env.production').write_text('SECRET=' + token + '\n')
            (p / 'app.py').write_text('def f():\n    return 10\n')
            r = analyze_project(p)
            indicators = r['security']['findings']
            self.assertTrue(any(x['code'] == 'TRACKED_ENV_FILE' and x['path'] == '.env.production'
                                and x['line'] is None for x in indicators))
            self.assertFalse(any(x['code'] == 'GITHUB_TOKEN_PATTERN' for x in indicators))
            self.assertNotIn(token, json.dumps(r) + markdown(r))
            validate_report(r)

    def test_html_bad_encoding_makes_security_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'site.html').write_bytes(b'<html>\xff</html>')
            r = analyze_project(p)
            self.assertEqual(r['security']['status'], 'PARTIAL')
            self.assertEqual(r['summary']['analyzed_files'], 0)
            self.assertEqual(r['coverage']['skipped_reasons'], {'NON_UTF8': 1})

    def test_supplemental_security_findings_do_not_alter_quality(self):
        token = 'ghp_' + 'Aa09Bz87' * 5
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'main.py').write_text('def f():\n    return 1\n')
            base = analyze_project(p)
            (p / 'index.html').write_text('<script>const api_key="' + token + '";</script>\n')
            a = analyze_project(p)
            (p / 'index.html').write_text('<script>const api_key="[REDACTED]";</script>\n')
            b = analyze_project(p)
            self.assertEqual(a['quality'], b['quality'])
            self.assertNotEqual(base['security']['status'], a['security']['status'])


class ScoreInterpretation(unittest.TestCase):
    def test_limited_report_calls_out_score_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'; root.mkdir()
            for i in range(3):
                (root / f'api_{i}.php').write_text('<?php\n' + 'if ($ok) { echo 1; }\n' * 5)
            result = analyze_project(root)
            self.assertEqual(result['quality']['status'], 'PROVISIONAL_LIMITED')
            text = markdown(result)
            self.assertIn('Measured-dimensions-only indicator', text)
            self.assertIn('NOT an overall project grade', text)
            self.assertIn('Low-confidence interpretation', text)
            self.assertIn('NOT_MEASURED', text)
            validate_report(result)

    def test_limited_cli_calls_out_measured_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); source = p / 'source'; source.mkdir()
            (source / 'view.php').write_text('<?php\n' + 'if ($good) { echo 1; }\n' * 12)
            out = p / 'output'; buf = StringIO()
            with redirect_stdout(buf):
                rc = main(['--source', str(source), '--out', str(out)])
            self.assertEqual(rc, 0)
            self.assertIn('MEASURED DIMENSIONS ONLY', buf.getvalue())
            self.assertIn('NOT an overall quality grade', buf.getvalue())
            self.assertIn('Excluded: complexity', buf.getvalue())

    def test_complete_python_report_does_not_claim_graded_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'app.py').write_text('def f(n: int):\n' + ''.join(f'    v_{i} = {i}\n' for i in range(12)) + '    return n\n')
            r = analyze_project(p)
            text = markdown(r)
            self.assertIn('NOT a grade', text)
            self.assertIn('PROVISIONAL', r['quality']['status'])


if __name__ == '__main__':
    unittest.main()
