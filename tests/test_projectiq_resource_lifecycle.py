"""Mocked Ollama lifecycle tests. No external network, model or VM."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from projectiq_core.resources import release_ollama_model
from projectiq_core.adapters import normalize


def response(names):
    return NS(status_code=200,json=lambda:{'models':[{'name':name} for name in names]})


def client():
    return NS(base='http://localhost:11434',s=NS(model='llama3.1:8b'),http=Mock())


class Resources(unittest.TestCase):
    def test_release_only_target(self):
        c=client();c.http.get.side_effect=[response(['llama3.1:8b','other:latest']),response(['other:latest'])];c.http.post.return_value=NS(status_code=200)
        self.assertEqual(release_ollama_model(c),'UNLOAD_VERIFIED')
        c.http.post.assert_called_once_with(c.base+'/api/generate',json={'model':'llama3.1:8b','keep_alive':0,'stream':False},timeout=10,allow_redirects=False)
    def test_already_unloaded(self):
        c=client();c.http.get.return_value=response(['other:latest']);self.assertEqual(release_ollama_model(c),'ALREADY_UNLOADED');c.http.post.assert_not_called()
    def test_unverified(self):
        c=client();c.http.get.return_value=response(['llama3.1:8b']);c.http.post.return_value=NS(status_code=200)
        self.assertEqual(release_ollama_model(c,attempts=2,pause=0),'RELEASE_NOT_VERIFIED')
        self.assertEqual(c.http.get.call_count,3)
    def test_unreachable(self):
        c=client();c.http.get.side_effect=OSError();self.assertEqual(release_ollama_model(c),'RELEASE_NOT_VERIFIED')
    def test_remote_rejected(self):
        c=client();c.base='https://example.com';self.assertEqual(release_ollama_model(c),'RELEASE_NOT_VERIFIED');c.http.get.assert_not_called()
    def test_invalid_ps_not_success(self):
        c=client();c.http.get.return_value=NS(status_code=200,json=lambda:{'models':[{}]});self.assertEqual(release_ollama_model(c),'RELEASE_NOT_VERIFIED');c.http.post.assert_not_called()
    def test_default_tag(self):
        c=client();c.s.model='llama3.1';c.http.get.side_effect=[response(['llama3.1:latest']),response([])];c.http.post.return_value=NS(status_code=200);self.assertEqual(release_ollama_model(c),'UNLOAD_VERIFIED')
    def test_report_retained_on_unverified_release(self):
        d={'summary':{},'criteria':[{'score':8}],'confidence':{'score':50},'weighted_score':8,'collector_resource_lifecycle':{'ollama_release':'RELEASE_NOT_VERIFIED'}}
        r=normalize('summarizer',d,None);self.assertEqual(r.status,'PARTIAL');self.assertEqual(r.metrics[0].value,8);self.assertTrue(any('release not verified' in x for x in r.limitations))
    def test_finally_after_model_error(self):
        import sys, tempfile
        from pathlib import Path
        from projectiq_core.collector import live_sources,make_submission
        c=client();c.check=Mock();c.http.get.return_value=response([])
        modules={'projectiq_summarizer.config':NS(Settings=lambda **kw:NS(ollama_url=c.base)), 'projectiq_summarizer.llm':NS(OllamaClient=lambda s:c), 'projectiq_summarizer.loaders':NS(load_text=lambda p:'x'), 'projectiq_summarizer.pipeline':NS(evaluate=Mock(side_effect=RuntimeError('evaluation failed'))), 'projectiq_summarizer.rubric':NS(load_rubric=lambda p:None)}
        with tempfile.TemporaryDirectory() as t,patch.dict(sys.modules,modules),patch.dict('os.environ',{}):
            out=Path(t);report=out/'report.txt';report.write_text('Example');sub,_=make_submission(report,'Demo','demo',None,None)
            with self.assertRaises(RuntimeError):live_sources(sub,agents=['summarizer'])['summarizer'](report,out)
            c.http.get.assert_called_once();c.http.close.assert_called_once();self.assertIn('ALREADY_UNLOADED',(out/'ollama_release.json').read_text())
