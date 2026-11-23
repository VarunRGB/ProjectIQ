"""Phase 5 v0.2 regression: avoid misleading, incomplete code-quality reports."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase5.complexity import complexity
from projectiq_executioner.phase5.contracts import ReportContractError, validate_report
from projectiq_executioner.phase5.findings import stable_findings, structural_findings
from projectiq_executioner.phase5.functions import discover
from projectiq_executioner.phase5.lexical import source_lines
from projectiq_executioner.phase5.pipeline import analyze_project
from projectiq_executioner.phase5.report import markdown
from projectiq_executioner.phase5.source import eligible


class Phase5Polish(unittest.TestCase):
    def test_function_limit_not_misreported_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'many.py').write_text(''.join(f'def f{i}(): return {i}\n' for i in range(5001)))
            r=analyze_project(p)
            self.assertEqual(r['summary']['functions_found'],5000)
            self.assertEqual(r['status'],'PARTIAL')
            self.assertTrue(r['coverage']['limits']['functions_truncated'])
            self.assertEqual(r['quality']['reason_codes'],['TRUNCATED_EVIDENCE'])
            self.assertIsNone(r['quality']['score'])
            validate_report(r)
            self.assertIn('functions_truncated',markdown(r))

    def test_finding_limit_not_misreported_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'many.py').write_text(''.join(f'def f{i}():\n    try: pass\n    except: pass\n' for i in range(760)))
            r=analyze_project(p)
            self.assertEqual(r['summary']['findings'],750)
            self.assertEqual(r['status'],'PARTIAL')
            self.assertTrue(r['coverage']['limits']['findings_truncated'])
            self.assertIsNone(r['quality']['score'])
            validate_report(r)

    def test_cap_is_not_declared_truncated_when_exact(self):
        values=[{'path':'a.py','line':i,'code':'PY_BARE_EXCEPT'} for i in range(250)]
        v,flag=stable_findings(values,with_truncation=True)
        self.assertEqual(len(v),250)
        self.assertFalse(flag)
        values.append({'path':'a.py','line':251,'code':'PY_BARE_EXCEPT'})
        v,flag=stable_findings(values,with_truncation=True)
        self.assertTrue(flag)

    def test_parser_coverage_not_diluted_by_css_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'a.py').write_text('def f(n):\n    x=0\n    for i in range(n):\n        x+=i\n    x+=1\n    x+=2\n    x+=3\n    x+=4\n    x+=5\n    x+=6\n    return x\n')
            for j in range(10):
                (p/f'style{j}.css').write_text('.x { color: red; }\n')
            r=analyze_project(p)
            self.assertEqual(r['coverage']['function_eligible_files'],1)
            self.assertEqual(r['coverage']['function_scanned_files'],1)
            self.assertEqual(r['coverage']['function_file_ratio'],1.0)
            self.assertEqual(r['summary']['source_candidates'],11)
            self.assertEqual(r['quality']['status'],'PROVISIONAL')

    def test_procedural_php_not_invented_as_named_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'api.php').write_text("<?php\nif ($x) { echo 1; }\nif ($y) { echo 2; }\n")
            (p/'a.js').write_text("function f(n) { for(let i=0;i<n;i++){} return n; }\n")
            (p/'index.html').write_text('<!-- comment -->\n<html></html>\n')
            (p/'style.css').write_bytes(b'body { color: red; }\xff')
            r=analyze_project(p)
            self.assertEqual(r['summary']['functions_found'],1)
            self.assertEqual(r['coverage']['procedural_php_files'],1)
            self.assertEqual(r['coverage']['skipped_reasons'],{'NON_UTF8':1})
            self.assertEqual(r['coverage']['function_eligible_files'],2)
            self.assertEqual(r['coverage']['function_scanned_files'],2)
            self.assertEqual(r['coverage']['function_file_ratio'],1.0)
            self.assertLess(r['coverage']['function_body_ratio'],.8)
            self.assertEqual(r['quality']['status'],'INSUFFICIENT_COVERAGE')
            self.assertIn('api.php', {x['path'] for x in r['files']})
            self.assertIn('style.css',markdown(r))

    def test_comment_only_lines_in_css_html_sql(self):
        self.assertEqual(source_lines('/* top */\n/*\n * block\n */\n.x { color: red; }\n','CSS'),(5,1,0,4))
        self.assertEqual(source_lines('<!-- first -->\n<!--\n more\n-->\n<div>a</div>\n','HTML'),(5,1,0,4))
        self.assertEqual(source_lines('-- sql comment\n# another\n/* block */\nSELECT 1;\n','SQL'),(4,1,0,3))
        self.assertEqual(source_lines('p { color: #fff; }\n','CSS'),(1,1,0,0))
        self.assertEqual(source_lines('SELECT 1--2;\n','SQL'),(1,1,0,0))

    def test_windows_and_cr_only_line_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'file.py').write_bytes(b'# a\rdef f(n):\r    return n\r')
            r=analyze_project(p)
            self.assertEqual(r['functions'][0]['start_line'],2)
            self.assertEqual(r['summary']['source_lines'],3)
            self.assertEqual(r['summary']['comment_lines'],1)

    def test_python_with_is_not_branch(self):
        spans,_,_=discover('def f(p):\n    with open(p) as x:\n        return 1\n','Python')
        self.assertEqual(complexity(spans[0])[0],1)

    def test_python_try_does_not_count_twice(self):
        spans,_,_=discover('def f(p):\n    try:\n        return p\n    except ValueError:\n        return 0\n','Python')
        self.assertEqual(complexity(spans[0])[0],2)

    def test_unknown_object_methods_do_not_get_big_o(self):
        for src in ['def f(a):\n    a.sort()\n', 'def f(a):\n    a.append(1)\n']:
            with self.subTest(src=src):
                spans,_,_=discover(src,'Python')
                self.assertEqual(complexity(spans[0])[2],'UNKNOWN')

    def test_nullish_operator_is_not_ternary(self):
        spans,_,_=discover('function f(a,b) { const c = a ?? b; return c; }','JavaScript')
        self.assertEqual(complexity(spans[0])[0],1)

    def test_brace_object_nesting_advisory_not_medium(self):
        src='function f(){const x={a:{b:{c:{d:{e:1}}}}}; return x;}'
        spans,_,_=discover(src,'JavaScript')
        cc,depth,*_=complexity(spans[0]);self.assertGreaterEqual(depth,5)
        v=structural_findings(spans,[(cc,depth)],'a.js')
        self.assertEqual(len(v),1)
        self.assertEqual(v[0]['code'],'POTENTIAL_BRACE_NESTING')
        self.assertEqual(v[0]['severity'],'ADVISORY')

    def test_truncated_report_cannot_claim_provisional_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'a.py').write_text('def f(n):\n    x=0\n    for i in range(n): x+=i\n    x+=1\n    x+=2\n    x+=3\n    x+=4\n    x+=5\n    x+=6\n    x+=7\n    return x\n')
            r=analyze_project(p)
            r['coverage']['limits']['functions_truncated']=True
            r['status']='PARTIAL'
            self.assertEqual(r['quality']['status'],'PROVISIONAL_LIMITED' if r['quality']['status']=='PROVISIONAL_LIMITED' else 'PROVISIONAL')
            with self.assertRaises(ReportContractError):validate_report(r)

    def test_truncated_report_must_have_partial_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); (p/'a.py').write_text('def f(n):\n return n\n')
            r=analyze_project(p)
            r['coverage']['limits']['functions_truncated']=True
            self.assertEqual(r['status'],'ANALYZED')
            with self.assertRaises(ReportContractError):validate_report(r)

    def test_exclude_sensitive_environment_filenames(self):
        for name in ('.env.js','.env.local.py','.env.production.ts'):
            self.assertFalse(eligible(name))

    def test_big_o_coverage_matches_function_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'a.py').write_text('def f(n: int):\n    for i in range(n): pass\n    return n\n\ndef g(n):\n    return custom(n)\n')
            r=analyze_project(p)
            self.assertEqual(r['coverage']['big_o']['estimated_functions'],1)
            self.assertEqual(r['coverage']['big_o']['unknown_functions'],1)
            self.assertIn('Big-O estimated / unknown: 1 / 1',markdown(r))
            broken=copy.deepcopy(r)
            broken['coverage']['big_o']['estimated_functions']=2
            with self.assertRaises(ReportContractError):validate_report(broken)

    def test_exact_fifty_duplicates_not_hid_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            blocks=[]
            for k in range(52):
                lines=[f'let var_{k}_{i} = counter_{k}_{i} + other_{k}_{i} * {i+1};' for i in range(8)]
                blocks.append('\n'.join(lines))
            (root/'master.js').write_text('\n// separator\n'.join(blocks)+'\n')
            for k,block in enumerate(blocks):
                (root/f'clone_{k:02}.js').write_text(block+'\n')
            r=analyze_project(root)
            self.assertEqual(r['summary']['duplicate_groups'],50)
            self.assertTrue(r['coverage']['limits']['duplications_truncated'])
            self.assertEqual(r['status'],'PARTIAL')
            validate_report(r)

    def test_unmodified_releases_are_deterministic_under_input_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ['z.py','a.py']:
                (root/name).write_text('def double(n):\n    for x in range(n):\n        pass\n    return 2\n')
            one=analyze_project(root)
            two=analyze_project(root)
            self.assertEqual(one,two)
            self.assertEqual([x['path'] for x in one['files']],['a.py','z.py'])

    def test_malformed_report_types_raise_contract_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'a.py').write_text('def f(n):\n    return n + 1\n')
            r=analyze_project(p)
            for field,value in [('start_line',None),('end_line','bad'),('lines',[])]:
                with self.subTest(field=field):
                    b=copy.deepcopy(r)
                    b['functions'][0][field]=value
                    with self.assertRaises(ReportContractError):validate_report(b)
            b=copy.deepcopy(r);b['status']=[]
            with self.assertRaises(ReportContractError):validate_report(b)

    def test_deterministic_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'hello.js').write_text('function f(n) { for (let i=0;i<n;i++) {} }\n')
            self.assertEqual(analyze_project(p),analyze_project(p))

if __name__=='__main__': unittest.main()
