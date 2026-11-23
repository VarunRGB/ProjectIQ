"""AST, brace syntaxes, static lint and Big-O negative evidence."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase5.pipeline import analyze_project
from projectiq_executioner.phase5.functions import discover
from projectiq_executioner.phase5.complexity import complexity
from projectiq_executioner.phase5.lexical import source_lines,mask_cstyle


def fn_find(text,language,name):
    spans,scope,_=discover(text,language)
    values=[(span,complexity(span)) for span in spans if span.name==name]
    if not values:raise AssertionError((name,[(x.name,x.start_line) for x in spans]))
    return values[0]


class TestFunctions(unittest.TestCase):
    def test_ast_nested_class_async(self):
        code='''class Worker:
    async def run(self, a):
        def inner(y):
            return y + 1
        return inner(a)
'''
        spans,scope,why=discover(code,'Python')
        self.assertEqual(scope,'AST')
        self.assertEqual([s.name for s in spans],['Worker.run','Worker.run.inner'])
        self.assertEqual([s.start_line for s in spans],[2,3])

    def test_simple_n(self):
        code='''def loop(n: int):
    total = 0
    for i in range(n):
        total += i
    return total
'''
        fn,info=fn_find(code,'Python','loop')
        self.assertEqual(info[2],'O(n)')
        self.assertEqual(info[3],'MEDIUM')
        self.assertEqual(info[0],2)

    def test_nested_n_squared(self):
        code='''def nested(a: list[int]):
    s=0
    for x in a:
        for y in a:
            s += x+y
    return s
'''
        fn,info=fn_find(code,'Python','nested')
        self.assertEqual(info[2],'O(n^2)')
        self.assertGreaterEqual(info[1],2)

    def test_independent_loops(self):
        code='''def two(a: list[int],b: list[int]):
    s=0
    for x in a:
        s+=x
    for x in b:
        s+=x
    return s
'''
        self.assertIn(fn_find(code,'Python','two')[1][2],('O(n + m)','O(m + n)'))

    def test_nested_independent_inputs(self):
        code='''def cross(a: list[int],b: list[int]):
    for x in a:
        for y in b:
            z=x*y
'''
        self.assertEqual(fn_find(code,'Python','cross')[1][2],'O(n*m)')

    def test_sorted_standard(self):
        code='''def run(a: list[int]):
    return sorted(a)
'''
        self.assertEqual(fn_find(code,'Python','run')[1][2],'O(n*log(n))')

    def test_unknown_calls_recursion(self):
        for code in [
            'def f(a):\n    return custom(a)\n',
            'def f(a):\n    return f(a)\n',
            'def f(a):\n    while a:\n        a=a[1:]\n',
            'def f(a):\n    return [x*x for x in a]\n',
        ]:
            with self.subTest(code=code):self.assertEqual(fn_find(code,'Python','f')[1][2],'UNKNOWN')

    def test_js_ts_php_cpp(self):
        samples=[('JavaScript','''function add(a,b) { return a + b; }
const count = (n) => { for (let i=0;i<n;i++){} return n; }
class A { getValue() { return 4; } }
''',{'add','count','getValue'}),
        ('TypeScript','''export function compute(n: number): number { return n + 1; }
class C { calculate(): number { return 5; } }
''',{'compute','calculate'}),
        ('PHP','''<?php
function process($a) { if ($a) {return 2;} return 1; }
class C { public function hello($x) { return $x; } }
''',{'process','hello'}),
        ('C++','''#include <vector>
int main() { return 0; }
class A {
public:
    int sum(int n) { return n + 1; }
};
''',{'main','sum'})]
        for lang,code,names in samples:
            with self.subTest(lang=lang):
                spans,scope,_=discover(code,lang)
                self.assertEqual(scope,'HEURISTIC_PARTIAL')
                self.assertTrue(names.issubset({x.name for x in spans}),{x.name for x in spans})
                self.assertTrue(all(complexity(x)[2]=='UNKNOWN' for x in spans if x.name != 'count'))
                if lang=='JavaScript':
                    self.assertEqual(next(complexity(x)[2] for x in spans if x.name=='count'),'O(n)')

    def test_cpp_double_n_and_php_single_loop(self):
        cpp="""int count(int n) {
    int sum=0;
    for(int i=0; i<n; i++) {
        for(int j=0; j<n; j++) { sum += i+j; }
    }
    return sum;
}
"""
        self.assertEqual(fn_find(cpp,'C++','count')[1][2],'O(n^2)')
        php="""<?php function countItems($n) {
    $sum=0;
    for($i=0; $i<$n; $i++) { $sum += $i; }
    return $sum;
}
"""
        self.assertEqual(fn_find(php,'PHP','countItems')[1][2],'O(n)')

    def test_loop_unknown_function_calls(self):
        cpp="""int f(int n) { for(int i=0;i<n;i++) { customCall(i); } return 0; }"""
        self.assertEqual(fn_find(cpp,'C++','f')[1][2],'UNKNOWN')

    def test_loop_bound_mutation_rejected(self):
        cpp="""int danger(int n) {
    int total=0;
    for(int i=0;i<n;i++) {
        n++;
        total += i;
    }
    return total;
}
"""
        self.assertEqual(fn_find(cpp,'C++','danger')[1][2],'UNKNOWN')

    def test_comment_strings_not_javascript_lints(self):
        from projectiq_executioner.phase5.findings import language_lints
        js='''// debugger;
const s = "eval(123); var bad = 1";
function x() { return 2; }
'''
        self.assertEqual([x['code'] for x in language_lints(js,'JavaScript','f.js')],[])

    def test_python_lint(self):
        from projectiq_executioner.phase5.findings import language_lints
        py='''from math import *
def f(values=[]):
    try: return eval("1+1")
    except: return 0
'''
        found=language_lints(py,'Python','f.py')
        self.assertTrue({'PY_WILDCARD_IMPORT','PY_MUTABLE_DEFAULT',
                         'PY_EVAL_EXEC','PY_BARE_EXCEPT'}.issubset({x['code'] for x in found}))

    def test_source_line_counts(self):
        self.assertEqual(source_lines('a=1\n\n# comment\nb=2\n','Python'),(4,2,1,1))
        self.assertEqual(source_lines('let a=1;\n// comment\n\nlet b=2;\n','JavaScript'),(4,2,1,1))

    def test_syntax_error_no_false_functions(self):
        spans,scope,why=discover('def broken(:\n pass','Python')
        self.assertEqual(spans,[])
        self.assertEqual(why,'SYNTAX_ERROR')


class TestPipeline(unittest.TestCase):
    def test_mixed_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'a.py').write_text('def f(n):\n    for i in range(n):\n        pass\n',encoding='utf8')
            (root/'b.js').write_text('function add(a,b) { return a+b; }\n',encoding='utf8')
            (root/'c.php').write_text('<?php function hello($x) {return $x;}\n',encoding='utf8')
            (root/'d.cpp').write_text('int main() {return 0;}\n',encoding='utf8')
            report=analyze_project(root)
            self.assertEqual(report['summary']['source_candidates'],4)
            self.assertEqual(report['summary']['analyzed_files'],4)
            self.assertTrue({'Python','JavaScript','PHP','C++'}.issubset(set(report['languages'])))
            self.assertGreaterEqual(report['summary']['functions_found'],4)
            self.assertEqual(report['status'],'PARTIAL')

if __name__=='__main__':unittest.main()
