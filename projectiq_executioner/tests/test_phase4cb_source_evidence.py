"""4C-B regression and adversarial tests: source evidence, not executable oracles."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from projectiq_executioner.phase3.generator import generate_test_plan
from projectiq_executioner.phase4.source_evidence import (
    SourceEvidenceError, _openapi_evidence, analyze_source_evidence,
)
from projectiq_executioner.phase4.source_oracle_stage import main

SRC = {'kind': 'github', 'repository': 'Fixture/Source', 'commit_sha': 'a'*40}


def make_plan(*routes, source=SRC):
    raw = {'schema_version': '1.0', 'phase': 1, 'source': source,
           'routes': [{'path': path, 'method': method, 'kind': kind,
                       'evidence': [{'path': file, 'line': line,
                                     'reason': 'Declares route'}] if file else []}
                      for method, path, file, line, kind in routes],
           'pages': [], 'entrypoints': [], 'features': [],
           'proposed_test_targets': [], 'tests': [], 'services': [],
           'database_schema_files': [], 'inventory': {}}
    return generate_test_plan(raw)


def route(method, path, file, line=1, kind='declared_http_route'):
    return (method, path, file, line, kind)


def entry(report, path):
    return next(x for x in report['targets'] if x['path'] == path)


class FixtureFiles:
    """Reusable file/plan fixtures without inherited test duplication."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'checkout'
        self.root.mkdir()

    def write(self, name, content):
        dest = self.root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding='utf-8')
        return dest

    def scan(self, *routes, source=SRC):
        return analyze_source_evidence(make_plan(*routes, source=source), self.root)



class ScannerTests(FixtureFiles, unittest.TestCase):
    def test_php_post_fields_status_session_and_database(self):
        self.write('auth/login.php', '''<?php
        $email = $_POST['email'];
        $password = filter_input(INPUT_POST, 'password');
        if (!$email) { http_response_code(422); }
        $_SESSION['uid'] = 4;
        $db->prepare('UPDATE users SET last_login = NOW()');
        header('Location: /dashboard.php');
        ''')
        r = self.scan(route('POST', '/auth/login.php', None))
        e = entry(r, '/auth/login.php')
        self.assertEqual(e['status'], 'EVIDENCE_FOUND')
        self.assertEqual({(x['location'], x['name']) for x in e['inputs']},
                         {('FORM', 'email'), ('FORM', 'password')})
        self.assertIn(('DECLARED_STATUS', 422), {(x['kind'], x['value']) for x in e['response_signals']})
        self.assertIn('SESSION_OR_AUTH_CONTEXT_POSSIBLE', e['requirements'])
        self.assertIn('CONTROLLED_DATABASE_STATE_REQUIRED', e['requirements'])
        self.assertIn('REDIRECT_PRESENT', [x['kind'] for x in e['response_signals']])
        self.assertNotIn('/dashboard.php', json.dumps(e))
        self.assertNotIn('last_login', json.dumps(e))

    def test_php_json_body_alias_from_actual_style_is_discovered(self):
        self.write('logic/add_task.php', '''<?php
session_start();
$data = json_decode(file_get_contents("php://input"), true);
$title = trim($data["title"]);
$deadline = trim($data["deadline"]);
$priority = trim($data["priority"]);
$user_id = $_SESSION["user_id"] ?? null;
$stmt = $pdo->prepare("INSERT INTO tasks (title) VALUES (?)");
echo json_encode(["success" => true]);
''')
        target = entry(self.scan(route('POST', '/logic/add_task.php', None)),
                       '/logic/add_task.php')
        self.assertEqual({(x['location'], x['name']) for x in target['inputs']},
                         {('JSON', 'title'), ('JSON', 'deadline'), ('JSON', 'priority')})
        self.assertTrue(all(x['required'] == 'UNKNOWN' for x in target['inputs']))
        self.assertIn('SESSION_OR_AUTH_CONTEXT_POSSIBLE', target['requirements'])
        self.assertIn('CONTROLLED_DATABASE_STATE_REQUIRED', target['requirements'])
        self.assertNotIn('DYNAMIC_REQUEST_INPUTS_UNRESOLVED', target['requirements'])
        self.assertEqual(target['review'], 'REQUIRED_BEFORE_ORACLE_PROMOTION')

    def test_php_login_json_secret_values_not_leaked(self):
        self.write('auth/login.php', '''<?php
$data = json_decode(file_get_contents('php://input'), true);
$email = trim($data['email']);
$password = trim($data['password']);
$_SESSION['user_id'] = 1;
echo json_encode(["success" => true]);
''')
        e = entry(self.scan(route('POST', '/auth/login.php', None)), '/auth/login.php')
        self.assertEqual({x['name'] for x in e['inputs']}, {'email', 'password'})
        self.assertEqual({x['location'] for x in e['inputs']}, {'JSON'})
        self.assertFalse(any(x['required'] == 'REQUIRED' for x in e['inputs']))
        self.assertEqual(e['response_signals'][0]['kind'], 'JSON_KEY_POSSIBLE')

    def test_php_json_object_mode_and_unrelated_variables(self):
        self.write('thing.php', '''<?php
$user = ["id" => 1];
$payload = json_decode(file_get_contents("php://input"));
$title = $payload->title;
$other = $user['id'];
''')
        e = entry(self.scan(route('POST', '/thing.php', None)), '/thing.php')
        self.assertEqual({x['name'] for x in e['inputs']}, {'title'})
        self.assertEqual(e['inputs'][0]['location'], 'JSON')

    def test_php_json_unresolved_dynamic_body_is_marked(self):
        self.write('dynamic.php', '''<?php
$data = json_decode(file_get_contents("php://input"), true);
$key = 'someField';
$value = $data[$key];
''')
        e = entry(self.scan(route('POST', '/dynamic.php', None)), '/dynamic.php')
        self.assertEqual(e['inputs'], [])
        self.assertIn('DYNAMIC_REQUEST_INPUTS_UNRESOLVED', e['requirements'])

    def test_php_json_does_not_assume_local_array_is_http_input(self):
        self.write('safe.php', '''<?php
$data = json_decode('\\"not from request\\"', true);
$title = $data['title'];
// $fake = json_decode(file_get_contents("php://input"), true);
''')
        e = entry(self.scan(route('POST', '/safe.php', None)), '/safe.php')
        self.assertEqual(e['inputs'], [])

    def test_php_mixed_json_and_form_endpoints_do_not_share_fields(self):
        sources = {
            'auth/login.php': ('$data["email"]', '$data["password"]'),
            'logic/add_task.php': ('$data["title"]', '$data["deadline"]', '$data["priority"]'),
            'logic/delete_task.php': ("$data['id']",),
            'logic/delete_task_user.php': ("$data['task_id']",),
            'logic/edit_task.php': ("$data['task_id']", '$data["title"]',
                                    '$data["deadline"]', '$data["priority"]'),
            'logic/update_status.php': ("$data['task_id']", "$data['status']"),
        }
        routes = []
        for path, accesses in sources.items():
            self.write(path, '<?php\n$data = json_decode(file_get_contents("php://input"), true);\n'
                       + '\n'.join('$value = ' + expression + ';' for expression in accesses))
            routes.append(route('POST', '/' + path, None))
        self.write('auth/register.php', "<?php $name = $_POST['name']; $email = $_POST['email'];")
        routes.append(route('POST', '/auth/register.php', None))
        report = self.scan(*routes)
        for path, accesses in sources.items():
            found = entry(report, '/' + path)
            expected = {access.split("[", 1)[1].removesuffix("]").strip("\"'")
                        for access in accesses}
            self.assertEqual({x['name'] for x in found['inputs']}, expected)
            self.assertEqual({x['location'] for x in found['inputs']}, {'JSON'})
        register = entry(report, '/auth/register.php')
        self.assertEqual({(x['location'], x['name']) for x in register['inputs']},
                         {('FORM', 'name'), ('FORM', 'email')})

    def test_php_form_get_and_request_unspecified(self):
        self.write('search.php', "<?php $x = $_GET['q']; $y = $_REQUEST['mode'];")
        result = entry(self.scan(route('GET', '/search.php', None)), '/search.php')
        self.assertEqual({i['location'] for i in result['inputs']}, {'QUERY', 'REQUEST_UNSPECIFIED'})

    def test_php_output_only_field_names_not_secret_values(self):
        self.write('login.php', "<?php echo json_encode(['success' => true, 'token' => 'SUPERSECRET123']);")
        result = entry(self.scan(route('POST', '/login.php', None)), '/login.php')
        self.assertIn('JSON_KEY_POSSIBLE', [x['kind'] for x in result['response_signals']])
        self.assertNotIn('SUPERSECRET123', json.dumps(result))

    def test_flask_route_specific_and_status(self):
        self.write('app.py', """@app.post('/api/tasks')
def create():
    value = request.json['title']
    db.session.add(task)
    return jsonify(created=True), 201
@app.get('/api/users')
def users():
    secret = request.args.get('token')
    return jsonify(users=[]), 200
""")
        result = self.scan(route('POST','/api/tasks','app.py'), route('GET','/api/users','app.py',5))
        task = entry(result, '/api/tasks')
        users = entry(result, '/api/users')
        self.assertEqual({x['name'] for x in task['inputs']}, {'title'})
        self.assertEqual({x['name'] for x in users['inputs']}, {'token'})
        self.assertEqual({x['value'] for x in task['response_signals'] if x['kind']=='DECLARED_STATUS'}, {201})
        self.assertEqual({x['value'] for x in users['response_signals'] if x['kind']=='DECLARED_STATUS'}, {200})
        self.assertEqual(task['scope'], ['ROUTE_DECLARATION_SLICE'])
        self.assertNotIn('token', [x['name'] for x in task['inputs']])
        self.assertNotIn('title', [x['name'] for x in users['inputs']])

    def test_express_route_specific_json_and_mongo(self):
        self.write('server.js', """app.post('/api/tasks', (req, res) => {
  const { title, priority } = req.body;
  Task.create({ title });
  return res.status(201).json({ created: true, id: task.id });
});
app.delete('/api/tasks/:id', (req,res) => {
  Task.deleteOne({ _id: req.params.id });
  return res.status(204).end();
});
""")
        report=self.scan(route('POST','/api/tasks','server.js'), route('DELETE','/api/tasks/:id','server.js',6))
        task = entry(report,'/api/tasks')
        delete = entry(report,'/api/tasks/:id')
        self.assertEqual({x['name'] for x in task['inputs']}, {'title','priority'})
        self.assertEqual({x['name'] for x in delete['inputs']}, {'id'})
        self.assertEqual({x['value'] for x in delete['response_signals'] if x['kind']=='DECLARED_STATUS'}, {204})
        self.assertIn('CONTROLLED_DATABASE_STATE_REQUIRED', task['requirements'])
        self.assertIn('CONTROLLED_DATABASE_STATE_REQUIRED', delete['requirements'])

    def test_only_unscoped_evidence_does_not_steal_other_route_fields(self):
        self.write('server.js', """const password = req.body.password;
app.post('/auth/login', (req,res) => { res.status(401).send(); });
""")
        report = self.scan(route('POST','/auth/register','server.js'))
        e=entry(report,'/auth/register')
        self.assertEqual(e['inputs'], [])
        self.assertEqual(e['scope'], ['ROUTE_SCOPE_NOT_ESTABLISHED'])

    def test_unmatched_post_decorator_does_not_match_get_route(self):
        self.write('app.py', """@app.route('/api/tasks', methods=['POST'])
def submit():
    value = request.form['password']
""")
        report=self.scan(route('GET','/api/tasks','app.py'))
        self.assertEqual(entry(report,'/api/tasks')['inputs'], [])

    def test_dynamic_source_unavailable_has_empty_evidence(self):
        result=self.scan(route('POST','/api/missing', 'nope.js'))
        row=entry(result,'/api/missing')
        self.assertEqual(row['status'], 'SOURCE_UNAVAILABLE')
        self.assertEqual(row['response_signals'],[])

    def test_source_root_symlink_rejected(self):
        other=Path(self.tmp.name)/'other'
        other.mkdir()
        link=Path(self.tmp.name)/'link'
        try:
            link.symlink_to(other, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlinks unavailable')
        with self.assertRaisesRegex(SourceEvidenceError, 'INVALID_SOURCE_ROOT'):
            analyze_source_evidence(make_plan(route('GET','/x','x.py')), link)

    def test_symlinked_source_file_is_not_read(self):
        outside=Path(self.tmp.name)/'secret.php'
        outside.write_text("<?php $x = $_POST['password'];", encoding='utf-8')
        try:
            (self.root/'secret.php').symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('Symlinks unavailable')
        row=entry(self.scan(route('POST','/secret.php', None)), '/secret.php')
        self.assertEqual(row['status'],'SOURCE_UNAVAILABLE')
        self.assertEqual(row['inputs'],[])

    def test_huge_source_not_read(self):
        self.write('huge.php', "<?php $foo = $_POST['x'];" + 'x'*260_000)
        self.assertEqual(entry(self.scan(route('POST','/huge.php', None)), '/huge.php')['status'], 'SOURCE_UNAVAILABLE')

    def test_no_runnable_assertions_or_secrets(self):
        self.write('auth/login.php', "<?php $x = $_POST['password'];")
        report=self.scan(route('POST','/auth/login.php', None))
        self.assertNotIn('test_bundle', report)
        self.assertEqual(report['operator_approval'],'NOT_EVALUATED')
        self.assertEqual(report['oracle_promotion'],'NOT_PERFORMED')
        self.assertEqual(report['execution'],'NOT_PERFORMED')

    def test_hash_and_order_are_deterministic(self):
        self.write('a.php', "<?php $x = $_POST['a'];")
        plan=make_plan(route('POST','/a.php', None))
        first=analyze_source_evidence(plan,self.root)
        second=analyze_source_evidence(deepcopy(plan),self.root)
        self.assertEqual(json.dumps(first, sort_keys=True),json.dumps(second, sort_keys=True))
        self.assertRegex(first['targets'][0]['source_files'][0]['sha256'],r'^[a-f0-9]{64}$')

    def test_bad_phase3_plan_rejected(self):
        plan=make_plan(route('POST','/a.php', None))
        plan['source']['commit_sha']='wrong'
        with self.assertRaisesRegex(SourceEvidenceError,'INVALID_PHASE3_PLAN'):
            analyze_source_evidence(plan,self.root)

    def test_local_source_cannot_claim_verified(self):
        local={'kind':'local','repository':'LocalCode','commit_sha':None}
        with self.assertRaisesRegex(SourceEvidenceError,'UNPINNED_SNAPSHOT_CANNOT_BE_VERIFIED'):
            analyze_source_evidence(make_plan(route('POST','/a.php',None), source=local), self.root, snapshot_verified=True)

    def test_comment_status_and_fields_ignored(self):
        self.write('a.php', "<?php\n// $_POST['junk']; http_response_code(200);\n$x=$_POST['real'];\n")
        e=entry(self.scan(route('POST','/a.php',None)),'/a.php')
        self.assertEqual([x['name'] for x in e['inputs']],['real'])
        self.assertEqual(e['response_signals'],[])

    def test_route_placeholders_are_advisory_and_required(self):
        report=self.scan(route('DELETE','/api/tasks/:task_id','unknown.js'))
        e=entry(report,'/api/tasks/:task_id')
        self.assertEqual([(x['location'],x['name']) for x in e['inputs']],[('PATH','task_id')])
        self.assertEqual(e['status'],'SOURCE_UNAVAILABLE')

    def test_does_not_infer_http_200_from_flask_jsonify(self):
        self.write('app.py', "@app.get('/ping')\ndef ping():\n    return jsonify(ok=True)\n")
        e=entry(self.scan(route('GET','/ping','app.py')),'/ping')
        self.assertFalse(any(s['kind']=='DECLARED_STATUS' for s in e['response_signals']))

    def test_php_missing_form_fields_does_not_guess_requiredness(self):
        self.write('form.php', "<?php if(isset($_POST['name'])) { echo 'ok'; }")
        e=entry(self.scan(route('POST','/form.php',None)),'/form.php')
        self.assertEqual(e['inputs'][0]['required'],'UNKNOWN')

    def test_unverified_local_checkout_is_marked(self):
        r=self.scan(route('GET','/api/x',None))
        self.assertFalse(r['snapshot_verified'])
        self.assertEqual(r['security_attestation'],'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')


class OpenAPITests(FixtureFiles, unittest.TestCase):
    def test_explicit_request_and_response_evidence(self):
        self.write('openapi.json',json.dumps({
            'openapi':'3.0.2',
            'paths': {'/api/tasks/{id}': {'post': {
                'parameters':[{'name':'id','in':'path','required':True}],
                'requestBody':{'content':{'application/json':{'schema':{
                    'properties':{'title':{'type':'string'},'priority':{'type':'number'}},
                    'required':['title']}}}},
                'responses': {'201': {'content': {'application/json':{'schema':{
                    'properties':{'id':{'type':'integer'}}}}}},
                    '422':{'description':'bad'}}}}}}))
        r=self.scan(route('POST','/api/tasks/:id',None))
        e=entry(r,'/api/tasks/:id')
        self.assertEqual(e['status'],'EVIDENCE_FOUND')
        self.assertIn(('JSON','title','REQUIRED'),
                      {(x['location'],x['name'],x['required']) for x in e['inputs']})
        self.assertIn(('JSON','priority','OPTIONAL'),
                      {(x['location'],x['name'],x['required']) for x in e['inputs']})
        self.assertEqual({x['value'] for x in e['response_signals'] if x['kind']=='DECLARED_STATUS'}, {201,422})
        self.assertIn('id',[x['value'] for x in e['response_signals'] if x['kind']=='JSON_KEY_POSSIBLE'])
        self.assertNotIn('test_bundle',r)

    def test_openapi_external_server_does_not_become_executable(self):
        self.write('openapi.json',json.dumps({'openapi':'3.0.0','servers':[{'url':'https://example.invalid'}],
                         'paths':{'/x':{'get':{'responses':{'200':{'description':'ok'}}}}}}))
        e=entry(self.scan(route('GET','/x',None)),'/x')
        self.assertIn('OPENAPI_AUTH_OR_SERVER_CONTEXT_REVIEW',e['requirements'])
        self.assertEqual(e['review'],'REQUIRED_BEFORE_ORACLE_PROMOTION')

    def test_openapi_bad_parameter_shape_must_not_crash(self):
        body=json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'parameters':{'bad':'shape'},
                             'responses':{'200':{}}}}}}).encode('utf-8')
        e=_openapi_evidence(body,'openapi.json','GET','/x')
        self.assertTrue(e is None or 'OPENAPI_REFERENCE_OR_PARAMETER_UNRESOLVED' in e['requirements'])

    def test_openapi_invalid_json_skipped(self):
        self.write('openapi.json','{bad json')
        r=self.scan(route('GET','/x',None))
        self.assertEqual(entry(r,'/x')['status'],'SOURCE_UNAVAILABLE')

    def test_openapi_method_mismatch_does_not_attribute(self):
        self.write('openapi.json',json.dumps({'openapi':'3.0.0','paths':{'/x':{
            'get': {'responses':{'200':{}}}}}}))
        e=entry(self.scan(route('POST','/x',None)),'/x')
        self.assertEqual(e['response_signals'],[])

    def test_openapi_ref_does_not_dereference(self):
        self.write('openapi.json',json.dumps({'openapi':'3.0.0','paths':{'/x':{
            'post': {'requestBody':{'content': {'application/json':{'schema':{'$ref':'https://evil.invalid'}}}},
                     'responses': {'200':{}}}}}}))
        e=entry(self.scan(route('POST','/x',None)),'/x')
        self.assertIn('OPENAPI_REFERENCE_OR_PARAMETER_UNRESOLVED',e['requirements'])
        self.assertNotIn('evil.invalid',json.dumps(e))


class CLITests(FixtureFiles, unittest.TestCase):
    def test_local_cli_creates_safe_evidence(self):
        self.write('auth/login.php', "<?php $a = $_POST['email'];")
        p=make_plan(route('POST','/auth/login.php',None))
        path=Path(self.tmp.name)/'plan.json'
        path.write_text(json.dumps(p),encoding='utf-8')
        out=Path(self.tmp.name)/'out'
        self.assertEqual(main(['--plan',str(path),'--source-root',str(self.root),'--out',str(out)]),0)
        obj=json.loads((out/'source_evidence.json').read_text(encoding='utf-8'))
        self.assertFalse(obj['snapshot_verified'])
        self.assertEqual(obj['summary']['evidence_found'],1)
        with self.assertRaises(SystemExit):
            main(['--plan',str(path),'--source-root',str(self.root),'--out',str(out)])

    def test_cli_invalid_input_does_not_write_report(self):
        path=Path(self.tmp.name)/'bad.json'
        path.write_text('{bad json')
        out=Path(self.tmp.name)/'badout'
        self.assertEqual(main(['--plan',str(path),'--source-root',str(self.root),'--out',str(out)]),2)
        self.assertFalse((out/'source_evidence.json').exists())

    def test_cli_source_mode_rejects_ref(self):
        from contextlib import redirect_stderr
        from io import StringIO
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(['--plan','x','--source-root',str(self.root),'--ref','a'*40,'--out',str(self.root/'out')])
        self.assertEqual(cm.exception.code,2)


if __name__=='__main__':
    unittest.main()

class AdditionalSafetyTests(FixtureFiles, unittest.TestCase):
    def test_no_source_is_explicitly_not_success(self):
        report=self.scan(route('POST','/unavailable','missing.js'))
        self.assertEqual(report['status'],'NO_USABLE_SOURCE_EVIDENCE')
        self.assertEqual(report['summary']['source_unavailable'],1)

    def test_unscoped_source_is_not_grounded_behavior(self):
        self.write('app.py', "def local():\n    request.json['password']\n")
        r=self.scan(route('POST','/mystery','app.py'))
        self.assertEqual(r['status'],'SOURCE_INSPECTED_NO_CONTRACT')
        self.assertEqual(entry(r,'/mystery')['response_signals'],[])

    def test_express_send_status_explicit(self):
        self.write('server.js', "app.delete('/api/tasks/:id', (req,res) => {\n  res.sendStatus(204);\n})\n")
        e=entry(self.scan(route('DELETE','/api/tasks/:id','server.js')),'/api/tasks/:id')
        self.assertEqual({s['value'] for s in e['response_signals'] if s['kind']=='DECLARED_STATUS'}, {204})

    def test_python_abort_status_explicit(self):
        self.write('app.py', "@app.get('/api/tasks')\ndef task():\n    abort(404)\n")
        e=entry(self.scan(route('GET','/api/tasks','app.py')),'/api/tasks')
        self.assertEqual({s['value'] for s in e['response_signals'] if s['kind']=='DECLARED_STATUS'}, {404})

    def test_dynamic_json_body_flagged_not_invented(self):
        self.write('app.py', "@app.post('/api/tasks')\ndef task():\n    body = request.get_json()\n")
        e=entry(self.scan(route('POST','/api/tasks','app.py')),'/api/tasks')
        self.assertEqual(e['inputs'],[])
        self.assertIn('DYNAMIC_REQUEST_INPUTS_UNRESOLVED',e['requirements'])

    def test_external_dependency_only_as_possible_requirement(self):
        self.write('app.py', "@app.post('/submit')\ndef f():\n    requests.post('https://example.invalid')\n")
        e=entry(self.scan(route('POST','/submit','app.py')),'/submit')
        self.assertIn('EXTERNAL_SERVICE_CONTEXT_POSSIBLE',e['requirements'])
        self.assertNotIn('example.invalid',json.dumps(e))

    def test_openapi_nonstring_response_keys_fail_safely(self):
        body=json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'responses':{
            '200':{}, 'default':{},'abc':{},'null':None}}}}}).encode()
        e=_openapi_evidence(body,'openapi.json','GET','/x')
        self.assertIn('OPENAPI_DEFAULT_RESPONSE_UNRESOLVED',e['requirements'])
        self.assertEqual({s['value'] for s in e['signals'] if s['kind']=='DECLARED_STATUS'},{200})

    def test_unsupported_non_http_is_not_converted_to_http(self):
        from projectiq_executioner.phase3.generator import generate_test_plan
        source=SRC
        raw={'schema_version':'1.0','phase':1,'source':source,'routes':[
             {'path':'Query.users','method':'CALL','kind':'graphql_operation','evidence':[]}],
             'pages':[],'entrypoints':[],'features':[],'proposed_test_targets':[],
             'tests':[],'services':[],'database_schema_files':[],'inventory':{}}
        plan=generate_test_plan(raw)
        report=analyze_source_evidence(plan,self.root)
        self.assertTrue(all(e['status']=='NON_HTTP_NOT_ANALYZED' for e in report['targets']))
        self.assertEqual(report['summary']['non_http'],len(report['targets']))

    def test_local_checkout_does_not_change_plan(self):
        self.write('form.php', "<?php $x=$_POST['title'];")
        plan=make_plan(route('POST','/form.php',None))
        before=deepcopy(plan)
        analyze_source_evidence(plan,self.root)
        self.assertEqual(plan,before)

    def test_source_no_outbound_network_or_subprocess(self):
        self.write('form.php', "<?php $x=$_POST['title'];")
        with mock.patch('urllib.request.urlopen',side_effect=AssertionError('network')):
            with mock.patch('subprocess.run',side_effect=AssertionError('process')):
                self.scan(route('POST','/form.php',None))

    def test_pinned_mode_rejects_different_repo_before_network(self):
        from projectiq_executioner.phase4.source_oracle_stage import _with_pinned_source
        p=make_plan(route('POST','/form.php',None))
        with self.assertRaisesRegex(SourceEvidenceError,'PINNED_SOURCE_IDENTITY_MISMATCH'):
            _with_pinned_source(p,repo='https://github.com/different/repository',ref='a'*40)

    def test_pinned_mode_rejects_branch_not_sha(self):
        from projectiq_executioner.phase4.source_oracle_stage import _with_pinned_source
        p=make_plan(route('POST','/form.php',None))
        with self.assertRaisesRegex(SourceEvidenceError,'PINNED_SOURCE_IDENTITY_MISMATCH'):
            _with_pinned_source(p,repo='https://github.com/Fixture/Source',ref='main')

    def test_http_get_no_source_does_not_assume_200(self):
        e=entry(self.scan(route('GET','/health','missing.py')),'/health')
        self.assertFalse(any(s['kind']=='DECLARED_STATUS' for s in e['response_signals']))

    def test_openapi_too_many_routes_skipped(self):
        self.write('openapi.json',json.dumps({'openapi':'3.0.1','paths':{
            '/endpoint'+str(i):{'get':{'responses':{'200':{}}}} for i in range(501)}}))
        e=entry(self.scan(route('GET','/endpoint0',None)),'/endpoint0')
        self.assertEqual(e['status'],'SOURCE_UNAVAILABLE')

    def test_empty_openapi_extraction_does_not_claim_response(self):
        self.write('openapi.json',json.dumps({'openapi':'3.0.1','paths':{'/a':{'get':{'responses':{}}}}}))
        e=entry(self.scan(route('GET','/a',None)),'/a')
        self.assertEqual(e['response_signals'],[])

    def test_source_input_stays_unknown_without_spec(self):
        self.write('server.js', "app.post('/data',(req,res) => { req.body.title; });")
        e=entry(self.scan(route('POST','/data','server.js')),'/data')
        self.assertEqual(e['inputs'][0]['required'],'UNKNOWN')

class ParserPrecisionTests(FixtureFiles, unittest.TestCase):
    def test_duplicate_openapi_keys_are_not_trusted(self):
        ambiguous = b'{"openapi":"3.0.0","paths":{"/x":{"get":{"responses":{"201":{}}}}},"paths":{"/x":{"get":{"responses":{"200":{}}}}}}'
        self.assertIsNone(_openapi_evidence(ambiguous,'openapi.json','GET','/x'))

    def test_nonfinite_openapi_is_rejected(self):
        bad = b'{"openapi":"3.0.0","unused":NaN,"paths":{"/x":{"get":{"responses":{"200":{}}}}}}'
        self.assertIsNone(_openapi_evidence(bad,'openapi.json','GET','/x'))

    def test_arbitrary_python_tuple_number_does_not_count_as_status(self):
        self.write('app.py', "@app.get('/x')\ndef f():\n    foo = (items, 201)\n    return jsonify(ok=True)\n")
        e=entry(self.scan(route('GET','/x','app.py')),'/x')
        self.assertFalse(any(s['kind']=='DECLARED_STATUS' for s in e['response_signals']))

    def test_commented_route_declaration_is_not_a_source_anchor(self):
        self.write('server.js', "// app.get('/x', (req,res)=>{\nconst v = req.body.password;\n")
        e=entry(self.scan(route('GET','/x','server.js')),'/x')
        self.assertEqual(e['inputs'],[])
        self.assertEqual(e['scope'],['ROUTE_SCOPE_NOT_ESTABLISHED'])
