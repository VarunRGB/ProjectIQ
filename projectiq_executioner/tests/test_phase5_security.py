"""Offline non-execution, skip, safe output, and score gating."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase5.pipeline import analyze_project
from projectiq_executioner.phase5.report import markdown
from projectiq_executioner.phase5.source import eligible


class TestSecurity(unittest.TestCase):
    def test_never_execute_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); marker=p/'EXECUTED'
            (p/'attack.py').write_text(f"open({str(marker)!r},'w').write('bad')\n"
                                       "def f(x): return x\n",encoding='utf8')
            r=analyze_project(p)
            self.assertFalse(marker.exists())
            self.assertEqual(r['analysis']['source_execution'],'NOT_PERFORMED')

    def test_oversize_skip_and_no_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'large.py').write_text('x'*350001,encoding='utf8')
            r=analyze_project(p)
            self.assertEqual(r['coverage']['skipped_reasons']['FILE_TOO_LARGE'],1)
            self.assertEqual(r['summary']['analyzed_files'],0)
            self.assertIsNone(r['quality']['score'])

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'real.txt').write_text('SECRET-PRIVATE-TEXT',encoding='utf8')
            try:(p/'link.py').symlink_to(p/'real.txt')
            except (OSError,NotImplementedError):self.skipTest('Symlinks unavailable')
            r=analyze_project(p)
            self.assertEqual(r['summary']['analyzed_files'],0)
            self.assertNotIn('SECRET-PRIVATE-TEXT',json.dumps(r))

    def test_skip_dependencies_and_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'node_modules').mkdir();(p/'generated').mkdir()
            (p/'node_modules'/'huge.js').write_text('function a() {}',encoding='utf8')
            (p/'generated'/'fake.py').write_text('def a():pass',encoding='utf8')
            (p/'good.py').write_text('def good():\n    return 42\n',encoding='utf8')
            r=analyze_project(p)
            self.assertEqual(r['summary']['source_candidates'],1)
            self.assertEqual(r['summary']['analyzed_files'],1)

    def test_secrets_not_in_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);secrets='PRIVATE_PASSWORD_NEVER_SHOW_98370'
            (p/'safe.py').write_text(f'password={secrets!r}\n'
                'def f(n):\n    return n\n',encoding='utf8')
            r=analyze_project(p)
            self.assertNotIn(secrets,json.dumps(r))
            self.assertNotIn(secrets,markdown(r))

    def test_short_source_not_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'f.py').write_text('def a():return 1\n')
            r=analyze_project(p)
            self.assertIsNone(r['quality']['score'])
            self.assertEqual(r['quality']['status'],'INSUFFICIENT_COVERAGE')

    def test_sufficient_source_provisional_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'f.py').write_text('''def sum_items(a):
    total=0
    for v in a:
        total+=v
    return total


def double(a):
    result=[]
    for v in a:
        result.append(v*2)
    return result
''',encoding='utf8')
            r=analyze_project(p)
            self.assertEqual(r['quality']['status'],'PROVISIONAL')
            self.assertTrue(0<=r['quality']['score']<=100)
            self.assertEqual(r['quality']['basis'],'HEURISTIC_NOT_A_GRADE')

    def test_unknown_language_is_not_graded_as_clean_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'Main.java').write_text('public class Main {\n  public static void main(String[] args) {}\n  int plus(int x) { return x+1; }\n  int times(int x) { return x*2; }\n}\n')
            r=analyze_project(root)
            self.assertEqual(r['coverage']['unsupported_files'],1)
            self.assertEqual(r['quality']['status'],'INSUFFICIENT_COVERAGE')
            self.assertEqual(r['status'],'PARTIAL')

    def test_unsupported_encoding_is_reported_not_misparsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'nonutf.py').write_bytes(b'def f():\xff\xfe\n    pass\n')
            r=analyze_project(root)
            self.assertEqual(r['coverage']['skipped_reasons']['NON_UTF8'],1)
            self.assertEqual(r['summary']['functions_found'],0)

    def test_safe_skip_patterns(self):
        for x in ('a.min.js','generated/sub.cpp','src/a.generated.ts','dist/a.js'):
            with self.subTest(x=x):self.assertFalse(eligible(x))

if __name__=='__main__':unittest.main()
