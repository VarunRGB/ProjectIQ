"""Defend against duplicate false-positives from tiny boilerplate."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from projectiq_executioner.phase5.pipeline import analyze_project


_BODY='''def calculate_statistics(values):
    total = sum(values)
    length = len(values)
    average = total / length
    maximum = max(values)
    minimum = min(values)
    variance = sum((value-average)**2 for value in values)
    maximum_gap = maximum - minimum
    minimum_offset = minimum - average
    spread_ratio = maximum_gap / length
    return average, variance, spread_ratio, minimum_offset
'''


class TestDuplication(unittest.TestCase):
    def test_cross_file_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'a.py').write_text(_BODY,encoding='utf8')
            (root/'b.py').write_text(_BODY,encoding='utf8')
            r=analyze_project(root)
            self.assertGreaterEqual(len(r['duplications']),1)
            self.assertEqual(r['duplications'][0]['path_a'],'a.py')
            self.assertEqual(r['duplications'][0]['path_b'],'b.py')
            self.assertNotIn('average, variance',str(r['duplications']))

    def test_tiny_functions_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'a.py').write_text('def f():return 1\n',encoding='utf8')
            (root/'b.py').write_text('def f():return 1\n',encoding='utf8')
            r=analyze_project(root)
            self.assertFalse(r['duplications'])

if __name__=='__main__':unittest.main()
