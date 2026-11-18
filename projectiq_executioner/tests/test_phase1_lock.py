"""Regression tests for the final Phase 1 handoff, using inert source strings."""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from projectiq_executioner.analyzer import analyze_directory

def routes_for(files):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in files.items():
            path = root.joinpath(*name.split('/'))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        return analyze_directory(root)

def route_set(manifest, kind=None):
    return {(r.method, r.path) for r in manifest.routes if kind is None or r.kind == kind}


class EnvironmentDiscoveryTests(unittest.TestCase):
    def test_pydantic_settings_required_and_defaulted_names_without_values(self):
        m = routes_for({'backend/app/config.py': '''
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    jwt_secret: str
    s3_bucket_name: str
    aws_region: str = "secret-region-value"
    database_url: str = 'sqlite:///./live.db'
    jwt_expire_minutes: int = 120
    model_config = SettingsConfigDict(env_file='.env')

    @property
    def cors_origins(self) -> list[str]:
        return []
'''})
        details = m.inventory['environment_variable_details']
        self.assertTrue(details['JWT_SECRET']['required_hint'])
        self.assertTrue(details['S3_BUCKET_NAME']['required_hint'])
        self.assertFalse(details['DATABASE_URL']['required_hint'])
        self.assertTrue(details['AWS_REGION']['default_present'])
        self.assertEqual(details['JWT_SECRET']['path'], 'backend/app/config.py')
        self.assertNotIn('CORS_ORIGINS', m.environment_variables)
        self.assertNotIn('MODEL_CONFIG', m.environment_variables)
        self.assertNotIn('secret-region-value', str(m.to_dict()))
        self.assertNotIn('sqlite:///./live.db', str(m.to_dict()))

    def test_pydantic_settings_with_prefix_and_alias(self):
        m = routes_for({'app/config.py': '''
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
class Config(BaseSettings):
    key: str = Field(validation_alias='SERVICE_TOKEN')
    address: str = 'localhost'
    model_config = SettingsConfigDict(env_prefix='DEV_')
'''})
        details = m.inventory['environment_variable_details']
        self.assertIn('SERVICE_TOKEN', details)
        self.assertIn('DEV_ADDRESS', details)
        self.assertTrue(details['SERVICE_TOKEN']['required_hint'])
        self.assertFalse(details['DEV_ADDRESS']['required_hint'])

    def test_normal_dataclass_does_not_become_env_config(self):
        m = routes_for({'model.py': 'class Data:\n    jwt_secret: str\n'})
        self.assertNotIn('JWT_SECRET', m.environment_variables)


class ReconciliationTests(unittest.TestCase):
    def test_optional_query_download_is_resolved_and_correlated(self):
        m = routes_for({
            'frontend/package.json': '{"dependencies":{"react":"*","vite":"*"}}',
            'backend/requirements.txt': 'fastapi==0.115.12\n',
            'backend/app/main.py': '''
from fastapi import FastAPI
from .routes import files
app = FastAPI()
app.include_router(files.router)
''',
            'backend/app/routes/files.py': '''
from fastapi import APIRouter
router = APIRouter(prefix='/api/files')
@router.get('/{file_id}/download')
def download(): pass
''',
            'frontend/src/App.jsx': '''
const API = '/api';
async function request(path, options = {}, token = localStorage.getItem('x')) {
  return fetch(`${API}${path}`, {...options});
}
function download(file, versionId) {
  const suffix = versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''
  return request(`/files/${file.id}/download${suffix}`)
}
''',
        })
        self.assertIn(('GET', '/api/files/:param/download'), route_set(m, 'client_http_call'))
        self.assertIn(('GET', '/api/files/:file_id/download'), route_set(m, 'declared_http_route'))
        links = m.inventory['api_links']
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['status'], 'static_match_only')
        self.assertEqual(links[0]['server_path'], '/api/files/:file_id/download')
        self.assertEqual(sum('/api/files/' in t and 'download' in t for t in m.proposed_test_targets), 1)
        self.assertNotIn('version_id', links[0]['server_path'])

    def test_unresolved_nonquery_dynamic_suffix_does_not_fabricate_path(self):
        m = routes_for({'src/api.js': '''
async function request(path) {return fetch(path)}
const suffix = getUntrustedSuffix()
request(`/files/${id}/download${suffix}`)
'''})
        self.assertNotIn(('GET','/files/:param/download'), route_set(m))

    def test_php_relative_client_path_reconciles_without_confirming_unknown_method(self):
        m = routes_for({
            'auth/login.php': "<?php $x = $_POST['email'];",
            'public/js/login.js': "fetch('../auth/login.php', {method:'POST'});",
        })
        self.assertEqual(len(m.inventory['api_links']), 1)
        self.assertEqual(m.inventory['api_links'][0]['server_path'], 'auth/login.php')
        self.assertEqual(sum('auth/login.php' in t for t in m.proposed_test_targets), 1)

    def test_external_api_does_not_match_unrelated_local_route(self):
        m = routes_for({
            'src/api.ts': "const BASE_URL='https://example.test'; fetch(`${BASE_URL}/login`)",
            'backend/main.py': "from fastapi import FastAPI\napp=FastAPI()\n@app.get('/different')\ndef foo(): pass",
        })
        self.assertEqual(m.inventory['api_links'], [])

    def test_matching_api_does_not_claim_running_server(self):
        m = routes_for({
            'server.js': "const app=express(); app.get('/users/:id', f);",
            'src/api.js': "fetch('/users/123')"
        })
        self.assertEqual(m.inventory['api_links'], [])  # literal IDs are not param variables


if __name__ == '__main__':
    unittest.main()

class SafeLinkageTests(unittest.TestCase):
    def test_external_client_path_never_joins_matching_local_route(self):
        m = routes_for({
            'src/client.js': "const API='https://external.example'; fetch(`${API}/login`);",
            'backend/app.py': "from fastapi import FastAPI\napp=FastAPI()\n@app.get('/login')\ndef login(): pass",
        })
        self.assertEqual(m.inventory['api_links'], [])

    def test_distinct_backend_roots_not_all_linked_to_frontend(self):
        m = routes_for({
            'frontend/package.json': '{"dependencies":{"react":"*"}}',
            'backend-one/requirements.txt': 'fastapi==0.115.12',
            'backend-two/requirements.txt': 'fastapi==0.115.12',
            'backend-one/app.py': "from fastapi import FastAPI\napp=FastAPI()\n@app.get('/users')\ndef users(): pass",
            'backend-two/app.py': "from fastapi import FastAPI\napp=FastAPI()\n@app.get('/billing')\ndef billing(): pass",
            'frontend/api.js': "fetch('/users')",
        })
        front = next(s for s in m.services if s.kind == 'frontend')
        backends = [s for s in m.services if s.kind == 'backend']
        self.assertTrue(backends)
        # Must not indiscriminately link *every* backend in a monorepo.
        self.assertLessEqual(len(front.depends_on),1)

class CloudDropStyleFullClientTests(unittest.TestCase):
    def test_ten_common_operations_in_one_react_app(self):
        m = routes_for({
            'frontend/package.json': '{"dependencies":{"react":"*","vite":"*"}}',
            'frontend/src/App.jsx': '''
const TOKEN_KEY = 'app_token'
const API = '/api'
async function request(path, options = {}, token = localStorage.getItem(TOKEN_KEY)) {
    return fetch(`${API}${path}`, {...options});
}
async function appFunctions(file, presign, versionId, authMode) {
    await request('/files')
    await request('/auth/me', {}, 'token')
    await request(`/auth/${authMode === 'login' ? 'login' : 'register'}`, {method:'POST'})
    await request('/files/presign', {method:'POST'})
    await request(`/files/${presign.file_id}/complete`, {method:'POST'})
    await request(`/files/${file.id}/versions`)
    const suffix = versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''
    await request(`/files/${file.id}/download${suffix}`)
    await request(`/files/${file.id}/restore`, {method:'POST'})
    await request(`/files/${file.id}`, {method:'DELETE'})
}
''',
            'backend/requirements.txt': 'fastapi==0.115.12\n',
            'backend/app/main.py': '''
from fastapi import FastAPI
from .routes import auth, files
app=FastAPI()
app.include_router(auth.router)
app.include_router(files.router)
@app.get('/api/health')
def health(): return {'status':'ok'}
''',
            'backend/app/routes/auth.py': '''
from fastapi import APIRouter
router=APIRouter(prefix='/api/auth')
@router.post('/register')
def register(): pass
@router.post('/login')
def login(): pass
@router.get('/me')
def me(): pass
''',
            'backend/app/routes/files.py': '''
from fastapi import APIRouter
router=APIRouter(prefix='/api/files')
@router.get('')
def list_files(): pass
@router.post('/presign')
def presign(): pass
@router.post('/{file_id}/complete')
def complete(): pass
@router.get('/{file_id}/versions')
def versions(): pass
@router.get('/{file_id}/download')
def download(): pass
@router.post('/{file_id}/restore')
def restore(): pass
@router.delete('/{file_id}')
def delete(): pass
''',
        })
        self.assertEqual(len(route_set(m, 'declared_http_route')), 11)
        self.assertEqual(len(route_set(m, 'client_http_call')), 10)
        self.assertEqual(len(m.inventory['api_links']), 10)
        self.assertEqual(len({t for t in m.proposed_test_targets if 'Inspect contract' in t}), 11)
