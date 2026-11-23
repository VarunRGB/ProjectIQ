"""Phase 5 v0.3 regressions: trustworthy scoring, complexity, credential redaction."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase5.pipeline import analyze_project
from projectiq_executioner.phase5.complexity import complexity
from projectiq_executioner.phase5.contracts import ReportContractError, validate_report
from projectiq_executioner.phase5.functions import discover
from projectiq_executioner.phase5.report import markdown
from projectiq_executioner.phase5.security import secret_file_type


def estimate(code):
    spans,scope,why=discover(code,'Python')
    assert spans, (scope,why)
    return complexity(spans[0])[2:]


class BigOTruth(unittest.TestCase):
    def test_no_overloaded_binary_ops(self):
        samples=['def f(a,b):\n    return a+b\n',
                 'def f(n):\n    return "x"*n\n',
                 'def f(a,b):\n    return a*b\n',
                 'def f(a):\n    return a[:]\n',
                 'def f(a):\n    return a[0]\n',
                 'def f(a):\n    return a==0\n',
                 'def f(a):\n    return sorted(a)\n',
                 'def f(a):\n    for x in a:\n        pass\n']
        for code in samples:
            with self.subTest(code=code):self.assertEqual(estimate(code)[0],'UNKNOWN')

    def test_typed_scalar_constant_subset(self):
        self.assertEqual(estimate('def f(x: int, y: int):\n    return x+y\n')[0],'O(1)')
        self.assertEqual(estimate('def f():\n    return 42\n')[0],'O(1)')

    def test_bounded_loops_and_two_dimensions(self):
        self.assertEqual(estimate('def f(n: int):\n    for x in range(0,n,1):\n        pass\n')[0],'O(n)')
        self.assertEqual(estimate('def f(a: list[int], b: list[int]):\n    for i in a:\n        for j in b:\n            t=i*j\n')[0],'O(n*m)')
        self.assertEqual(estimate('def f(a: list[int]):\n    for i,x in enumerate(a):\n        pass\n')[0],'O(n)')
        self.assertEqual(estimate('def f(a: list[int]):\n    for x in reversed(a):\n        pass\n')[0],'O(n)')

    def test_typed_sorted_and_untyped_sorted(self):
        self.assertEqual(estimate('def f(a: list[int]):\n    return sorted(a)\n')[0],'O(n*log(n))')
        self.assertEqual(estimate('def f(a: list[str]):\n    return sorted(a)\n')[0],'UNKNOWN')

    def test_amortized_list_append(self):
        result=estimate('def f(n: int):\n    out=[]\n    for i in range(n):\n        out.append(i)\n    return out\n')
        self.assertEqual(result,('O(n)','MEDIUM','AMORTIZED_APPEND'))
        self.assertEqual(estimate('def f(out: list[int],n:int):\n    for i in range(n):\n        out.append(i)\n')[0],'UNKNOWN')
        self.assertEqual(estimate('def f(n:int):\n    out=[]\n    out=custom()\n    out.append(n)\n')[0],'UNKNOWN')


class ExposureSafety(unittest.TestCase):
    def test_tracked_env_is_flagged_without_reading_contents(self):
        secret='DO_NOT_PUBLISH_VALUE_'+'C9'*25
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.env').write_text('DATABASE_PASSWORD='+secret+'\n')
            (root/'.env.example').write_text('DATABASE_PASSWORD=placeholder\n')
            (root/'app.py').write_text('def f():\n    return 1\n')
            report=analyze_project(root)
            self.assertIn('TRACKED_ENV_FILE',[x['code'] for x in report['security']['findings']])
            payload=json.dumps(report)+markdown(report)
            self.assertNotIn(secret,payload)
            self.assertNotIn('DATABASE_PASSWORD=',payload)
            self.assertNotIn('.env.example',str(report['security']['findings']))
            self.assertEqual(report['summary']['indexed_files'],3)
            self.assertEqual(report['summary']['source_candidates'],1)

    def test_known_provider_token_redaction(self):
        token='ghp_'+'A1b2C3D4'*6
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config.py').write_text('TOKEN="'+token+'"\n')
            report=analyze_project(root)
            findings=report['security']['findings']
            self.assertTrue(any(x['code']=='GITHUB_TOKEN_PATTERN' and x['line']==1 for x in findings))
            self.assertNotIn(token,json.dumps(report)+markdown(report))
            self.assertTrue(all(set(x)=={'path','line','code','confidence'} for x in findings))

    def test_no_false_env_example_or_public_cert(self):
        self.assertIsNone(secret_file_type('.env.sample'))
        self.assertIsNone(secret_file_type('nested/.env.production.template'))
        self.assertIsNone(secret_file_type('server.crt'))
        self.assertEqual(secret_file_type('deploy/.env.production'),'TRACKED_ENV_FILE')
        self.assertEqual(secret_file_type('keys/id_ed25519'),'POTENTIAL_SECRET_FILE')

    def test_security_report_rejects_exfiltration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'.env').write_text('SECRET=abc123\n');(p/'a.py').write_text('def f():\n return 1\n')
            r=analyze_project(p)
            bad=copy.deepcopy(r)
            bad['security']['findings'][0]['raw_secret']='abc123'
            with self.assertRaises(ReportContractError):validate_report(bad)
            bad=copy.deepcopy(r)
            bad['security']['findings'][0]['line']='abc123'
            with self.assertRaises(ReportContractError):validate_report(bad)


class ScoringTruth(unittest.TestCase):
    def test_procedural_php_gets_low_confidence_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for i in range(12):
                (p/f'endpoint_{i}.php').write_text('<?php\nif ($x) { echo 1; }\nif ($y) { echo 2; }\n')
            (p/'dashboard.js').write_text('function f() { return 1; }\n')
            (p/'style.css').write_text('body { color: black; }\n')
            r=analyze_project(p)
            self.assertEqual(r['coverage']['procedural_php_files'],12)
            self.assertEqual(r['quality']['status'],'PROVISIONAL_LIMITED')
            self.assertIsInstance(r['quality']['score'],int)
            self.assertEqual(r['quality']['confidence'],'LOW')
            self.assertIsNone(r['quality']['dimensions']['complexity'])
            self.assertNotIn('complexity',r['quality']['weights_used'])
            self.assertIn('NOT_MEASURED',markdown(r))

    def test_tests_do_not_dilute_production_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); (p/'app.py').write_text('def f(n: int):\n'+''.join('    if n > 1: n-=1\n' for _ in range(15))+'    return n\n')
            first=analyze_project(p)
            (p/'tests').mkdir()
            (p/'tests'/'test_many.py').write_text(''.join('def test_%d():\n    return 1\n' %i for i in range(100)))
            second=analyze_project(p)
            self.assertEqual(first['quality']['score'],second['quality']['score'])
            self.assertEqual(first['quality']['dimensions'],second['quality']['dimensions'])
            self.assertEqual(second['summary']['test_files'],1)
            self.assertGreater(second['summary']['test_code_lines'],0)

    def test_exact_duplicate_lines_measured_not_group_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            content='\n'.join('let value_%d = data_%d + complex_math_%d;'%(i,i,i) for i in range(8))+'\n'
            (p/'a.js').write_text(content);(p/'b.js').write_text(content)
            r=analyze_project(p)
            self.assertEqual(r['summary']['duplicate_groups'],1)
            self.assertGreaterEqual(r['coverage']['duplicate_production_lines'],16)
            self.assertLessEqual(r['coverage']['duplicate_production_ratio'],1)
            self.assertLess(r['quality']['dimensions']['duplication'],100)

    def test_minicollab_shape_scores_with_one_unreadable_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for i in range(12):
                (p/f'api_{i}.php').write_text('<?php\nif ($role) { echo 1; }\nif ($status) { echo 2; }\n')
            for i in range(3):
                (p/f'view_{i}.js').write_text('function start(){return 1;}\n')
            for i in range(4):
                (p/f'page_{i}.html').write_text('<main>page</main>\n')
                (p/f'style_{i}.css').write_text('main { color: red; }\n')
            (p/'schema.sql').write_bytes(b'SELECT \xff\n')
            r=analyze_project(p)
            self.assertEqual(r['summary']['source_candidates'],24)
            self.assertEqual(r['summary']['analyzed_files'],23)
            self.assertEqual(r['coverage']['procedural_php_files'],12)
            self.assertEqual(r['quality']['status'],'PROVISIONAL_LIMITED')
            self.assertIsInstance(r['quality']['score'],int)
            self.assertIsNone(r['quality']['dimensions']['complexity'])
            self.assertIn('LOW',r['quality']['confidence'])
            validate_report(r)

    def test_bom_sql_is_read_and_real_bad_encoding_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'utf8.sql').write_bytes(b'\xef\xbb\xbf-- dump\nCREATE TABLE a (id INT);\n')
            (p/'utf16.sql').write_bytes(('\ufeff-- dump\nSELECT 1;\n').encode('utf-16'))
            (p/'bad.sql').write_bytes(b'broken \xff\n')
            r=analyze_project(p)
            self.assertEqual(r['summary']['analyzed_files'],2)
            self.assertEqual(r['coverage']['skipped_reasons'],{'NON_UTF8':1})
            self.assertEqual(r['coverage']['function_eligible_files'],0)
            self.assertTrue(any(f['path']=='utf16.sql' and f['status']=='ANALYZED' for f in r['files']))


class AdditionalRegression(unittest.TestCase):
    def test_docs_scan_detects_api_key_without_echo(self):
        token='sk_live_'+'X8pQ7rL5mN6tA4bV3cZ2dY1k9w'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'README.md').write_text('Integration token: '+token+'\n')
            (p/'app.py').write_text('def start():\n    return 1\n')
            r=analyze_project(p)
            self.assertEqual(r['security']['scanned_additional_files'],1)
            self.assertIn('STRIPE_SECRET_KEY_PATTERN',[f['code'] for f in r['security']['findings']])
            self.assertNotIn(token,json.dumps(r)+markdown(r))

    def test_forged_empty_security_report_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'.env').write_text('A=B\n')
            (p/'app.py').write_text('def run():\n    return 1\n')
            r=analyze_project(p)
            b=copy.deepcopy(r);b['security']['status']='NO_INDICATORS_FOUND'
            with self.assertRaises(ReportContractError):validate_report(b)

    def test_brace_heuristic_rejects_unknown_object_operations(self):
        from projectiq_executioner.phase5.functions import discover
        src='function f(n){ let text=""; for(let i=0;i<n;i++){text+=i;} return text;}'
        functions,_,_=discover(src,'JavaScript')
        self.assertTrue(functions)
        self.assertEqual(complexity(functions[0])[2],'UNKNOWN')
        cpp='int f(int n){ SomeClass x; for(int i=0;i<n;i++){x = x + i;} return 0; }'
        fs,_,_=discover(cpp,'C++')
        self.assertTrue(fs)
        self.assertEqual(complexity(fs[0])[2],'UNKNOWN')

    def test_ast_share_gate_does_not_score_brace_complexity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'a.py').write_text('def small(n: int):\n    return n\n')
            (p/'big.js').write_text('function big(n) {\n'+''.join('  let item_%d = n;\n'%i for i in range(65))+'}\n')
            r=analyze_project(p)
            self.assertLess(r['coverage']['ast_complexity_share'],0.70)
            self.assertEqual(r['quality']['status'],'PROVISIONAL_LIMITED')
            self.assertIsNone(r['quality']['dimensions']['complexity'])

    def test_php_procedural_control_markers_not_function_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'endpoint.php').write_text('<?php\nif ($x) { echo 1; }\nforeach ($items as $i) { echo $i; }\n')
            r=analyze_project(p)
            row=r['files'][0]
            self.assertEqual(row['procedural_decision_markers'],2)
            self.assertEqual(row['procedural_loop_markers'],1)
            self.assertEqual(row['function_count'],0)
            self.assertIn('Procedural PHP control-flow markers',markdown(r))

    def test_tampered_score_weights_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'a.py').write_text('def f():\n'+''.join('    v%d=%d\n'%(i,i) for i in range(15))+'    return 42\n')
            r=analyze_project(p)
            self.assertIsNotNone(r['quality']['score'])
            b=copy.deepcopy(r)
            k=list(b['quality']['weights_used'])
            if len(k)>1:
                b['quality']['weights_used'][k[0]]=0.9
                b['quality']['weights_used'][k[1]]=0.1
                with self.assertRaises(ReportContractError):validate_report(b)


if __name__=='__main__':unittest.main()
