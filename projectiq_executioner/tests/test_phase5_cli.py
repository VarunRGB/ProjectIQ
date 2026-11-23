"""End-to-end CLI/output checks, pure local snapshots and reproducibility."""
from __future__ import annotations

from contextlib import redirect_stderr,redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import types
import sys
from unittest.mock import patch
import unittest

from projectiq_executioner.phase5.__main__ import main
from projectiq_executioner.phase5.contracts import validate_report


class TestCli(unittest.TestCase):
    def test_cli_outputs_repeatably(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'source';root.mkdir()
            (root/'code.py').write_text('''def test(n):
    result = 0
    for i in range(n):
        result += i
    return result
''')
            out=base/'reports'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--source',str(root),'--out',str(out)]),0)
            expected=(out/'code_quality.json').read_bytes()
            self.assertTrue((out/'code_quality.md').exists())
            validate_report(json.loads(expected))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--source',str(root),'--out',str(out)]),0)
            self.assertEqual(expected,(out/'code_quality.json').read_bytes())

    def test_refuse_output_inside_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a.py').write_text('def f():pass')
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--source',str(root),'--out',str(root/'report')]),2)

    def test_github_pinned_source_uses_phase1_safe_ingestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source_dir=base/'checkout';source_dir.mkdir()
            (source_dir/'f.py').write_text('def f(n):\n    for i in range(n):\n        pass\n')
            sha='a'*40
            class Source:
                repository='example/fixture'
                commit_sha=sha
            class Client:
                def __init__(self,**kwargs):pass
                def resolve(self,url,ref):
                    assert url=='https://github.com/example/fixture'
                    assert ref=='main'
                    return Source()
                def download(self,source,target):
                    target.write_bytes(b'fake-zipped-for-mocked-extractor')
            gh=types.ModuleType('projectiq_executioner.github_client')
            gh.GitHubClient=Client
            gh.DownloadLimits=lambda *args,**kwargs:object()
            archive=types.ModuleType('projectiq_executioner.safe_archive')
            archive.ArchiveLimits=lambda **kwargs:object()
            archive.extract_snapshot=lambda *args,**kwargs:source_dir
            out=base/'results'
            with patch.dict(sys.modules,{'projectiq_executioner.github_client':gh,
                                         'projectiq_executioner.safe_archive':archive}):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['--source','https://github.com/example/fixture',
                                           '--ref','main','--out',str(out)]),0)
            report=json.loads((out/'code_quality.json').read_text())
            self.assertEqual(report['source']['kind'],'github_pinned')
            self.assertEqual(report['source']['commit_sha'],sha)
            self.assertEqual(report['analysis']['source_execution'],'NOT_PERFORMED')

    def test_disallow_non_github_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--source','http://example.net/a','--out',tmp]),2)
                self.assertEqual(main(['--source','https://github.com.evil.test/foo',
                                       '--out',tmp]),2)

    def test_refuse_nondirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--source',str(Path(tmp)/'missing'),'--out',str(Path(tmp)/'out')]),2)

if __name__=='__main__':unittest.main()
