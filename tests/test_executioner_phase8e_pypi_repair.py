import json,time
import pytest
from projectiq_executioner.phase8c_final import pypi as p
from projectiq_executioner.phase8d.resolver import resolve_requirements
from projectiq_executioner.phase8e.audit import classify_error

def row(name='demo',version='1.0',tag='py3-none-any',**kw):
    filename=f"{name.replace('-','_')}-{version}-{tag}.whl"
    return dict(filename=filename,url='https://files.pythonhosted.org/packages/aa/bb/'+'a'*64+'/'+filename,hashes={'sha256':'b'*64},size=100,yanked=False,**kw)

def index(name,rows):return {'meta':{'api-version':'1.1'},'name':name,'files':rows}

def fake_get(obj,calls):
    def get(host,path,limit,deadline,consume,**kw):
        calls.append((host,path,limit,kw));data=json.dumps(obj).encode();consume(data);return len(data)
    return get

def test_compact_index_filters_and_caches(monkeypatch):
    rows=[row(),row(version='2.0',tag='cp312-cp312-win_amd64'),row(version='3.0rc1'),row(version='4.0',**{'requires-python':'>=3.13'}),{**row(version='5.0'),'yanked':'withdrawn'},row(version='6.0',tag='cp312-cp312-manylinux_2_17_x86_64'),row(version='6.0')]
    calls=[];monkeypatch.setattr(p,'_get',fake_get(index('demo',rows),calls));m=p.CompactMetadata(time.monotonic()+10,'3.12.3');got=m.get('demo');assert set(got['releases'])=={'1.0','6.0'};assert 'cp312' in got['releases']['6.0'][0]['filename'];assert m.get('demo') is got;assert len(calls)==1 and calls[0][1]=='/simple/demo/' and calls[0][3]=={'simple':True}

@pytest.mark.parametrize('bad', [{'meta':{'api-version':'1.0'},'name':'demo','files':[]},index('other',[]),index('demo',None)])
def test_bad_index(monkeypatch,bad):
    monkeypatch.setattr(p,'_get',fake_get(bad,[]));m=p.CompactMetadata(time.monotonic()+10,'3.12.3')
    with pytest.raises(ValueError):m.get('demo')

def test_release_and_project_resolution(monkeypatch):
    calls=[]
    def get(host,path,limit,deadline,consume,**kw):
        calls.append(path);f=row();obj=index('demo',[f]) if kw.get('simple') else {'info':{'name':'demo','version':'1.0','requires_dist':[]},'urls':[dict(filename=f['filename'],url=f['url'],digests=f['hashes'],size=f['size'],packagetype='bdist_wheel',yanked=False)]};data=json.dumps(obj).encode();consume(data);return len(data)
    monkeypatch.setattr(p,'_get',get);lock=resolve_requirements({'demo':'>=1'},'3.12.3',p.CompactMetadata(time.monotonic()+10,'3.12.3'));assert lock['direct_pins']=={'demo':'1.0'};assert calls==['/simple/demo/','/pypi/demo/1.0/json']

class Response:
    status=200
    def getheader(self,k,default=None):return {'Content-Type':'application/vnd.pypi.simple.v1+json','Content-Length':'4'}.get(k,default)
    def read(self,n):raise AssertionError('oversize must be rejected before read')
class Connection:
    def request(self,method,path,headers):assert headers['Accept']=='application/vnd.pypi.simple.v1+json'
    def getresponse(self):return Response()
    def close(self):pass

def test_index_byte_limit():
    with pytest.raises(ValueError,match='PYPI_PROJECT_INDEX_SIZE_LIMIT'):p._get('pypi.org','/simple/demo/',3,time.monotonic()+1,lambda b:None,connection=lambda h:Connection(),simple=True)

@pytest.mark.parametrize('path',['/simple/../','/simple/demo/?q=x','/pypi/demo/json'])
def test_index_path_refusal(path):
    with pytest.raises(ValueError,match='PYPI_METADATA_PATH_DENIED'):p._get('pypi.org',path,100,time.monotonic()+1,lambda b:None,simple=True)

@pytest.mark.parametrize('code',['PYPI_SIZE_LIMIT','PYPI_PROJECT_INDEX_SIZE_LIMIT','PYPI_RELEASE_METADATA_SIZE_LIMIT','PYPI_WHEEL_SIZE_LIMIT','PYPI_INDEX_FORMAT_UNSUPPORTED','PYPI_INDEX_FILE_LIMIT'])
def test_error_classification(code):assert classify_error(code)=='DEPENDENCY'
