"""Regression cases derived from the November 26 real-repository acceptance plans.
These are synthetic reproductions of the source layouts, not downloaded repos.
"""
from pathlib import Path
import json
from projectiq_executioner.phase8a import plan_directory, validate_plan


def plan(root, files):
    for name, content in files.items():
        path=root/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    result=plan_directory(root);validate_plan(result)
    assert result['execution_authorized'] is False
    assert result['vm_launches']==result['commands_executed']==result['functional_tests_executed']==0
    return result


def test_native_php_public_pages_and_browser_js(tmp_path):
    result=plan(tmp_path, {'auth/login.php':'<?php echo "login";', 'db/db.php':'<?php $db = new mysqli("localhost");',
        'logic/add_task.php':'<?php echo "task";', 'public/login.html':'<script src="js/login.js"></script>',
        'public/register.html':'<form></form>', 'public/js/login.js':'fetch("../auth/login.php")', 'sql/schema.sql':'CREATE TABLE tasks(id INT);'})
    assert len(result['components'])==1
    c=result['components'][0];assert c['ecosystem']=='php' and c['adapter_candidate']=='php_mysql_v1'
    assert c['root']=='.' and c['role_hint']=='web_fullstack'
    assert c['entrypoint_candidates']==['public/login.html','public/register.html']
    assert 'ENTRYPOINT_UNRESOLVED' not in c['blockers']
    assert 'PUBLIC_DOCUMENT_ROOT_REQUIRES_VALIDATION' in c['blockers']
    assert 'mysql' in c['service_hints'] and 'auth/login.php' in c['evidence_paths'] and 'sql/schema.sql' in c['evidence_paths']


def test_nested_native_php_and_separate_node_manifest(tmp_path):
    r=plan(tmp_path, {'legacy/public/home.html':'<html></html>','legacy/endpoints/action.php':'<?php echo 1;',
        'legacy/public/js/login.js':'fetch("../endpoints/action.php")',
        'api/package.json':'{"dependencies":{"express":"5"},"scripts":{"start":"node server.js"}}',
        'api/package-lock.json':'{"lockfileVersion":3,"packages":{}}','api/server.js':'app.listen(3000)'})
    assert {(c['root'],c['ecosystem']) for c in r['components']}=={('legacy','php'),('api','node')}
    c=next(c for c in r['components'] if c['ecosystem']=='php');assert c['entrypoint_candidates']==['legacy/public/home.html']
    assert 'LANDING_PAGE_SELECTION_UNRESOLVED' in c['blockers']


def test_php_with_root_html_landing(tmp_path):
    r=plan(tmp_path, {'index.html':'<script src="client.js"></script>', 'auth/login.php':'<?php echo 1;', 'client.js':'fetch("auth/login.php")'})
    assert [(c['root'],c['ecosystem']) for c in r['components']]==[('.','php')]
    assert r['components'][0]['entrypoint_candidates']==['index.html']


def test_next_external_api_origin_and_cloudinary(tmp_path):
    secret='https://example.invalid/private-token'
    r=plan(tmp_path, {'package.json':json.dumps({'dependencies':{'next':'16.2.1','react':'19.2.4','next-cloudinary':'^6'},'scripts':{'build':'next build','start':'next start'}}),
        'package-lock.json':'{"lockfileVersion":3,"packages":{}}', 'src/app/page.tsx':'export default function Page() {}',
        'src/lib/api.ts':f'const BASE_URL = "{secret}"; async function request(endpoint) {{return fetch(`${{BASE_URL}}${{endpoint}}`)}}'})
    c=r['components'][0];assert c['role_hint']=='frontend' and c['adapter_candidate'] is None
    assert set(c['service_hints'])=={'external_cloudinary','external_http_api'}
    assert {'EXTERNAL_HTTP_API_REQUIRES_VALIDATION','LOCAL_BACKEND_IMPLEMENTATION_NOT_ESTABLISHED'}<=set(c['blockers'])
    assert 'src/lib/api.ts' in c['evidence_paths'];assert secret not in json.dumps(r)
    assert not any('mongodb' in s for s in c['service_hints'])


def test_next_local_route_preserves_fullstack_hint(tmp_path):
    r=plan(tmp_path, {'package.json':'{"dependencies":{"next":"16"}}','src/app/page.tsx':'export default function Page(){}',
        'src/app/api/items/route.ts':'export function GET() {}', 'src/lib/api.ts':'const BASE_URL="https://example.invalid";'})
    c=r['components'][0];assert c['role_hint']=='web_fullstack'
    assert 'LOCAL_BACKEND_IMPLEMENTATION_NOT_ESTABLISHED' not in c['blockers']
    assert 'external_http_api' in c['service_hints']


def test_relative_and_loopback_urls_not_external(tmp_path):
    r=plan(tmp_path, {'package.json':'{"dependencies":{"next":"16"}}','src/app/page.tsx':'export default function Page(){}',
        'src/lib/api.ts':'const BASE_URL="http://localhost:3001"; fetch("/api/items"); fetch("http://127.0.0.1:8080/health");'})
    assert 'external_http_api' not in r['components'][0]['service_hints']


def test_static_browser_code_no_node(tmp_path):
    r=plan(tmp_path, {'index.html':'<script src="script.js"></script>', 'script.js':'fetch("/items")'})
    assert len(r['components'])==1 and r['components'][0]['ecosystem']=='static'
