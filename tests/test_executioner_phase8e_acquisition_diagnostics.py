import json, socket, ssl, subprocess, time
from types import SimpleNamespace
import pytest
from packaging.specifiers import InvalidSpecifier
from packaging.requirements import InvalidRequirement
from packaging.version import InvalidVersion
from projectiq_executioner.phase8c_final import pypi,dispatch
from projectiq_executioner.phase8d import __main__ as child,acquisition_probe

@pytest.mark.parametrize('exc,stage,code',[
    (socket.gaierror('private'), 'RESOLUTION','PYPI_DNS_LOOKUP_FAILED'),
    (ssl.SSLError('private'),'RESOLUTION','PYPI_TLS_FAILED'),
    (TimeoutError('private'),'FETCH','PYPI_NETWORK_TIMEOUT'),
    (InvalidSpecifier('private'),'RESOLUTION','PYPI_METADATA_SPECIFIER_INVALID'),
    (InvalidRequirement('private'),'RESOLUTION','PYPI_METADATA_REQUIREMENT_INVALID'),
    (InvalidVersion('private'),'RESOLUTION','PYPI_METADATA_VERSION_INVALID'),
    (json.JSONDecodeError('private','x',0),'RESOLUTION','PYPI_JSON_INVALID'),
    (ValueError('DUPLICATE_JSON_KEY'),'RESOLUTION','PYPI_JSON_INVALID'),
    (TypeError('private'),'RESOLUTION','PYPI_METADATA_STRUCTURE_INVALID'),
    (RuntimeError('private'),'RESOLUTION','PYPI_RESOLUTION_OR_FETCH_FAILED'),
    (RuntimeError('private'),'FETCH','PYPI_WHEEL_FETCH_FAILED'),
    (OSError('private'),'LOCK_WRITE','PYPI_LOCK_WRITE_FAILED'),
    (ValueError('PYPI_PROJECT_INDEX_SIZE_LIMIT'),'RESOLUTION','PYPI_PROJECT_INDEX_SIZE_LIMIT'),
])
def test_safe_diagnostics(exc,stage,code):
    assert pypi.diagnostic_error(exc,stage)==code and code in pypi.SAFE_ERRORS

@pytest.mark.parametrize('payload,expected',[(b'{"error_code":"PYPI_RESOLUTION_OR_FETCH_FAILED"}','PYPI_RESOLUTION_OR_FETCH_FAILED'),(b'{"error_code":"PYPI_TLS_FAILED"}','PYPI_TLS_FAILED'),(b'{"error_code":[]}','PYPI_CHILD_PROTOCOL_FAILED'),(b'private stderr-like output','PYPI_CHILD_PROTOCOL_FAILED')])
def test_parent_propagates_and_cleans(monkeypatch,tmp_path,payload,expected):
    bundle=tmp_path/'wheel.zip';bundle.write_bytes(b'x');staging=tmp_path/'.wheel-staging';staging.mkdir();(staging/'x').write_bytes(b'x')
    monkeypatch.setattr(dispatch.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=2,stdout=payload,stderr=b'private'))
    monkeypatch.setattr(dispatch.shutil,'disk_usage',lambda p:SimpleNamespace(free=10**9))
    with pytest.raises(ValueError,match='^'+expected+'$'):dispatch._download([],tmp_path,bundle,staging,python_child=True)
    assert not bundle.exists() and not staging.exists()

def test_parent_timeout(monkeypatch,tmp_path):
    def timeout(*a,**k):raise subprocess.TimeoutExpired('private',630)
    monkeypatch.setattr(dispatch.subprocess,'run',timeout);monkeypatch.setattr(dispatch.shutil,'disk_usage',lambda p:SimpleNamespace(free=10**9))
    with pytest.raises(ValueError,match='PYPI_CHILD_TIMEOUT'):dispatch._download([],tmp_path,tmp_path/'wheel.zip',tmp_path/'.wheel-staging',python_child=True)

def test_npm_protocol_unchanged(monkeypatch,tmp_path):
    monkeypatch.setattr(dispatch.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=2,stdout=b'',stderr=b'private'));monkeypatch.setattr(dispatch.shutil,'disk_usage',lambda p:SimpleNamespace(free=10**9))
    with pytest.raises(ValueError,match='REGISTRY_ACQUISITION_FAILED'):dispatch._download([],tmp_path,tmp_path/'npm.zip',tmp_path/'.registry-staging')

def test_child_error_json(monkeypatch,tmp_path,capsys):
    path=tmp_path/'input.json';path.write_text(json.dumps({'requirements':{'demo':'>=1'},'target_python':'3.12.3'}))
    def fail(*a):raise InvalidSpecifier('private')
    monkeypatch.setattr(child,'resolve_requirements',fail)
    assert child.main(['_resolve','--input',str(path),'--out',str(tmp_path)])==2
    assert json.loads(capsys.readouterr().out)=={'error_code':'PYPI_METADATA_SPECIFIER_INVALID'}

def test_historical_invalid_requires_python(monkeypatch):
    def row(v,rp):
        f=f'demo-{v}-py3-none-any.whl';return {'filename':f,'url':'https://files.pythonhosted.org/packages/aa/bb/'+'a'*64+'/'+f,'hashes':{'sha256':'b'*64},'size':100,'yanked':False,'requires-python':rp}
    obj={'meta':{'api-version':'1.1'},'name':'demo','files':[row('1.0','>=3.6.*'),row('2.0','>=3.12'),row('3.0',{})]}
    def get(h,p,l,d,consume,**kw):consume(json.dumps(obj).encode())
    monkeypatch.setattr(pypi,'_get',get)
    assert set(pypi.CompactMetadata(time.monotonic()+10,'3.12.3').get('demo')['releases'])=={'2.0'}

def test_probe_independent_cases(monkeypatch,tmp_path):
    calls=[]
    def fail(requirements,*args):calls.append(requirements);raise ValueError('PYPI_TLS_FAILED')
    monkeypatch.setattr(acquisition_probe,'_python_fetch',fail)
    result=acquisition_probe.probe(tmp_path/'probe',list(acquisition_probe.CASES))
    assert len(calls)==2 and result['vm_start_attempts']==0 and result['status']=='ACQUISITION_BLOCKED'
    assert all(r['error_code']=='PYPI_TLS_FAILED' for r in result['cases'])

def test_probe_verified_wheelhouse(monkeypatch,tmp_path):
    import hashlib,zipfile
    from projectiq_executioner.phase8a.planner import digest
    def fake_fetch(requirements,out,version):
        data=b'inert test bytes';name='django-5.1-py3-none-any.whl';wheel={'name':'django','version':'5.1','filename':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'url':'https://files.pythonhosted.org/packages/aa/bb/'+'a'*64+'/'+name};lock={'target_python':version,'direct_pins':{'django':'5.1'},'direct_requirements':requirements,'wheels':[wheel]};lock['lock_sha256']=digest(lock)
        bundle=out/'python-wheelhouse.zip'
        with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as z:z.writestr('wheels/'+name,data)
        (out/'python-lock.json').write_text(json.dumps(lock));return bundle,lock,{}
    monkeypatch.setattr(acquisition_probe,'_python_fetch',fake_fetch)
    result=acquisition_probe.probe(tmp_path/'probe',['fixture-django'])
    assert result['status']=='ACQUISITION_VERIFIED' and result['cases'][0]['wheel_count']==1
    assert not list((tmp_path/'probe'/'fixture-django').glob('verified-*'))
