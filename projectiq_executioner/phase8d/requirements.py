"""Inert Python requirement normalization and conservative import inference."""
import ast,re,sys,tomllib
from pathlib import PurePosixPath
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from ..phase8c_final.pypi import target,NAME
from ..phase8c2.broker import require

# Never turn unknown import names into registry requests. Known mappings only.
IMPORT_MAP={'flask':'flask','fastapi':'fastapi','starlette':'starlette','uvicorn':'uvicorn','django':'django','streamlit':'streamlit','requests':'requests','httpx':'httpx','numpy':'numpy','pandas':'pandas','PIL':'pillow','cv2':'opencv-python','sklearn':'scikit-learn','bs4':'beautifulsoup4','yaml':'pyyaml','sqlalchemy':'sqlalchemy','pydantic':'pydantic','dotenv':'python-dotenv','matplotlib':'matplotlib','scipy':'scipy','jinja2':'jinja2','werkzeug':'werkzeug','click':'click','rich':'rich','pytest':'pytest','aiohttp':'aiohttp','flask_sqlalchemy':'flask-sqlalchemy','flask_cors':'flask-cors','bcrypt':'bcrypt','passlib':'passlib','joblib':'joblib'}
# Python 3.13 host stdlib contains additions not necessarily in 3.12. Exclude known additions.
STDLIB=set(sys.stdlib_module_names)-{'_interpreters','_interpchannels','_interpqueues','_pyrepl','_colorize','_suggestions','_opcode_metadata','_pydatetime','_pydecimal','_pylong','_sysconfig','_testexternalinspection','_interpreters','_typing'}
STDLIB.update({'cgi','cgitb','telnetlib','audioop','imghdr','mailcap','nntplib','pipes','sunau','aifc','chunk','crypt','ossaudiodev','sndhdr','spwd','uu','xdrlib','lib2to3','distutils'})
# distutils is removed in 3.12, not a supported stdlib import.
STDLIB.discard('distutils')


def normalize(lines,version):
    env=target(version);result={}
    require(isinstance(lines,list) and len(lines)<=1000,'REQUIREMENTS_LIMIT')
    for raw in lines:
        require(isinstance(raw,str) and len(raw)<=500,'REQUIREMENT_UNSUPPORTED')
        raw=raw.strip().lstrip('\ufeff')
        if not raw or raw.startswith('#'):continue
        require(not raw.startswith('-') and '\\' not in raw,'REQUIREMENT_UNSUPPORTED')
        r=Requirement(raw);name=canonicalize_name(r.name)
        require(NAME.fullmatch(name) and not r.url and not r.extras and '===' not in str(r.specifier),'REQUIREMENT_UNSUPPORTED')
        if r.marker:
            require(not re.search(r'\b(extra|platform_release|platform_version)\b',str(r.marker)),'REQUIREMENT_MARKER_UNSUPPORTED')
            if not r.marker.evaluate(env):continue
        result.setdefault(name,[]).append(str(r.specifier))
    require(len(result)<=25,'DIRECT_DEPENDENCY_LIMIT')
    return {n:','.join(sorted(set(s for s in clauses if s))) for n,clauses in sorted(result.items())}


def infer(files):
    imports=set();unknown=set();syntax=False
    local={p.split('/')[0].removesuffix('.py') for p in files if p.endswith('.py')}
    local|={PurePosixPath(p).stem for p in files if p.endswith('.py') and '/' not in p}
    for p,body in files.items():
        if not p.endswith('.py') or any(q in {'tests','test','examples','docs','migrations'} for q in p.split('/')[:-1]) or PurePosixPath(p).name.startswith('test_'):continue
        try:tree=ast.parse(body)
        except (SyntaxError,ValueError,RecursionError):syntax=True;continue
        for n in ast.walk(tree):
            if isinstance(n,ast.Import):imports.update(x.name.split('.')[0] for x in n.names)
            elif isinstance(n,ast.ImportFrom) and n.level==0 and n.module:imports.add(n.module.split('.')[0])
            # Dynamic imports are explicitly a limitation, not a guessed package.
            elif isinstance(n,ast.Call) and (isinstance(n.func,ast.Name) and n.func.id=='__import__' or isinstance(n.func,ast.Attribute) and n.func.attr=='import_module'):unknown.add('DYNAMIC_IMPORT')
    deps={IMPORT_MAP[n]:'' for n in imports-local-STDLIB if n in IMPORT_MAP}
    unknown.update(n for n in imports-local-STDLIB if n not in IMPORT_MAP)
    return dict(sorted(deps.items())),sorted(unknown),syntax


def discover(files,version,allow_inferred=False):
    """No setup.py execution. Prefer requirements then PEP 621 dependencies."""
    if 'requirements.txt' in files:
        if len(files['requirements.txt'].encode())>16000:raise ValueError('REQUIREMENTS_LIMIT')
        return normalize(files['requirements.txt'].splitlines(),version),'REQUIREMENTS_TXT',[]
    if 'pyproject.toml' in files:
        obj=tomllib.loads(files['pyproject.toml']);project=obj.get('project')
        require(isinstance(project,dict) and 'dependencies' not in project.get('dynamic',[]) and isinstance(project.get('dependencies',[]),list),'DYNAMIC_OR_UNSUPPORTED_PYPROJECT')
        return normalize(project.get('dependencies',[]),version),'PEP621',[]
    require(not any(x in files for x in ('setup.py','setup.cfg','Pipfile','poetry.lock','uv.lock','Pipfile.lock')),'UNSUPPORTED_PYTHON_MANIFEST')
    deps,unknown,syntax=infer(files)
    blockers=[]
    if syntax:blockers.append('PYTHON_AST_INCOMPLETE')
    if unknown:blockers.append('UNKNOWN_OR_DYNAMIC_IMPORTS')
    if deps and not allow_inferred:blockers.append('INFERRED_DEPENDENCIES_REQUIRE_OPERATOR_OPT_IN')
    return deps,'INFERRED_KNOWN_IMPORTS' if deps or unknown else 'NO_THIRD_PARTY_IMPORTS_OBSERVED',blockers
