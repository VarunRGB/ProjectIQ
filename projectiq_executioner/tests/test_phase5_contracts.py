"""Cross-platform output safety and fail-closed contracts."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path
import unittest

from projectiq_executioner.phase5.contracts import safe_rel,validate_report,ReportContractError
from projectiq_executioner.phase5.pipeline import analyze_project
from projectiq_executioner.phase5.report import markdown


class TestContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'src.py').write_text('def f(x):\n    return x + 1\n',encoding='utf8')
        self.report=analyze_project(self.root)

    def test_schema_valid(self):
        validate_report(self.report)
        self.assertEqual(self.report['schema_version'],'0.3')
        self.assertEqual(self.report['analysis']['source_execution'],'NOT_PERFORMED')

    def test_windows_paths_not_in_evidence(self):
        self.assertTrue(safe_rel('src/deep/file.cpp'))
        for path in ('src\\file.py','../out.py','C:/Windows/file.py','/absolute.py',
                     'src//file.py','src/./file.py','src/../file.py'):
            self.assertFalse(safe_rel(path),path)

    def test_guard_false_execution(self):
        b=copy.deepcopy(self.report)
        b['analysis']['source_execution']='PERFORMED'
        with self.assertRaises(ReportContractError):validate_report(b)

    def test_guard_false_score(self):
        b=copy.deepcopy(self.report)
        b['quality']['status']='PROVISIONAL'
        b['quality']['score']=101
        with self.assertRaises(ReportContractError):validate_report(b)

    def test_guard_forged_source(self):
        b=copy.deepcopy(self.report)
        b['source']['kind']='github_pinned'
        b['source']['commit_sha']=None
        with self.assertRaises(ReportContractError):validate_report(b)

    def test_guard_invalid_function(self):
        b=copy.deepcopy(self.report)
        b['functions'][0]['path']='../../etc/passwd'
        with self.assertRaises(ReportContractError):validate_report(b)

    def test_safe_markdown(self):
        r=copy.deepcopy(self.report)
        r['source']['repository']='unsafe|`<script>'
        validate_report(r)
        md=markdown(r)
        self.assertNotIn('<script>',md)
        self.assertIn('unsafe\\|\\`&lt;script&gt;',md)

    def test_determinism(self):
        self.assertEqual(self.report,analyze_project(self.root))

    def test_no_source(self):
        (self.root/'src.py').unlink()
        r=analyze_project(self.root)
        self.assertEqual(r['status'],'NO_SOURCE')
        self.assertIsNone(r['quality']['score'])

if __name__=='__main__':unittest.main()
