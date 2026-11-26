"""Dependency recipes: inert source inspection, never package execution."""
from __future__ import annotations
import json,re,hashlib,base64
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit
from ..phase8a import plan_directory,validate_plan
from ..phase8a.scanner import inspect
from ..phase8a.planner import digest

# Future resolver targets; listing a backend does not implement its resolver.
FORMATS={
 'npm':('package.json','package-lock.json','npm-shrinkwrap.json'),
 'pnpm':('package.json','pnpm-lock.yaml','pnpm-workspace.yaml'), 'yarn':('package.json','yarn.lock'), 'bun':('package.json','bun.lock','bun.lockb'),
 'pip':('requirements.txt','pyproject.toml','setup.py','setup.cfg'), 'uv':('pyproject.toml','uv.lock'), 'poetry':('pyproject.toml','poetry.lock'), 'pipenv':('Pipfile','Pipfile.lock'),
 'composer':('composer.json','composer.lock'), 'maven':('pom.xml',), 'gradle':('build.gradle','build.gradle.kts','settings.gradle','settings.gradle.kts'),
 'go':('go.mod','go.sum','go.work'), 'cargo':('Cargo.toml','Cargo.lock'), 'bundler':('Gemfile','Gemfile.lock'), 'nuget':('global.json','packages.lock.json'),
 'cmake':('CMakeLists.txt',),'make':('Makefile',),'meson':('meson.build',),'pub':('pubspec.yaml','pubspec.lock'),
 'swiftpm':('Package.swift','Package.resolved'),'mix':('mix.exs','mix.lock'),'stack':('stack.yaml','stack.yaml.lock'),'cabal':('cabal.project',),'deno':('deno.json','deno.jsonc','deno.lock')}
NODE={'node_vite_offline_v1','node_express_offline_v1','node_fullstack_offline_v1'}
PY='python_web_offline_v1'
SHA=re.compile(r'[0-9a-f]{64}\Z')
PIN=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[0-9][A-Za-z0-9_.+-]*\Z')
NAME=re.compile(r'(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+\Z',re.I)
MAX_LOCK_ENTRIES=10000

def load_object(text):
    def pairs(values):
        out={}
        for k,v in values:
            if k in out:raise ValueError('DUPLICATE_JSON_KEY')
            out[k]=v
        return out
    def fail(_):raise ValueError('NONFINITE_JSON')
    value=json.loads(text,object_pairs_hook=pairs,parse_constant=fail)
    if not isinstance(value,dict):raise ValueError('EXPECTED_OBJECT')
    return value

def _at(root,name):return name if root=='.' else root+'/'+name

def _registry_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<33 for c in value) or '\\' in value:return False
    try:
        u=urlsplit(value)
        return u.scheme=='https' and u.netloc=='registry.npmjs.org' and u.path.endswith('.tgz') and not u.query and not u.fragment
    except ValueError:return False

def _integrity(value):
    if not isinstance(value,str):return False
    try:
        alg,encoded=value.split('-',1)
        return alg=='sha512' and len(base64.b64decode(encoded,validate=True))==64
    except (ValueError,TypeError):return False

def npm_check(files,root):
    issues=set();meta={};pkg=_at(root,'package.json');lock=_at(root,'package-lock.json')
    if pkg not in files or lock not in files:return ['NPM_MANIFEST_OR_LOCK_UNREADABLE'],meta
    if _at(root,'npm-shrinkwrap.json') in files:issues.add('NPM_SHRINKWRAP_REQUIRES_SEPARATE_RESOLVER')
    try:
        p=load_object(files[pkg]);l=load_object(files[lock]);records=l.get('packages')
        if type(l.get('lockfileVersion')) is not int or l['lockfileVersion'] not in (2,3) or not isinstance(records,dict) or not isinstance(records.get(''),dict):raise ValueError('LOCK_SHAPE')
        if len(records)>MAX_LOCK_ENTRIES:raise ValueError('LOCK_LIMIT')
        meta={'lockfile_version':l['lockfileVersion'],'locked_package_records':len(records)-1,'integrity_scope':'DECLARED_SRI_NOT_DOWNLOADED'}
        if 'workspaces' in p or l.get('workspaces'):issues.add('NPM_WORKSPACE_REQUIRES_SEPARATE_RESOLVER')
        scripts=p.get('scripts',{})
        if not isinstance(scripts,dict):issues.add('NPM_SCRIPT_SHAPE_INVALID')
        elif set(scripts)&{'preinstall','install','postinstall','prepare','prepublish','prepublishOnly','prepack','postpack'}:issues.add('NPM_PROJECT_LIFECYCLE_REQUIRES_POLICY')
        for field in ('dependencies','devDependencies','optionalDependencies','peerDependencies'):
            d=p.get(field,{});locked=records[''].get(field,{})
            if not isinstance(d,dict) or not isinstance(locked,dict) or d!=locked:issues.add('NPM_ROOT_MANIFEST_LOCK_MISMATCH');continue
            for name,spec in d.items():
                if not NAME.fullmatch(name) or not isinstance(spec,str) or not re.fullmatch(r'[0-9A-Za-z.*^~<>=| +\-]+',spec) or spec.startswith(('git','http','file','link','workspace','npm')):issues.add('NPM_NONSTANDARD_DEPENDENCY_SPEC')
        for path,row in records.items():
            if path=='':continue
            if not isinstance(row,dict) or not path.startswith('node_modules/') or '..' in PurePosixPath(path).parts or '\\' in path or ':' in path or row.get('link') or row.get('inBundle'):
                issues.add('NPM_NONREGISTRY_LOCK_RECORD');continue
            if not isinstance(row.get('version'),str) or row['version'].startswith(('file:','link:','git:','git+','http:','https:','npm:')):issues.add('NPM_NONREGISTRY_LOCK_RECORD')
            if not _registry_url(row.get('resolved')):issues.add('NPM_NONREGISTRY_LOCK_RECORD')
            if not _integrity(row.get('integrity')):issues.add('NPM_STRONG_INTEGRITY_MISSING')
            if row.get('hasInstallScript'):meta['dependency_lifecycle_scripts_present']=True
    except (ValueError,TypeError,KeyError,RecursionError):issues.add('NPM_MANIFEST_OR_LOCK_INVALID')
    return sorted(issues),meta

def pip_check(files,root):
    req=_at(root,'requirements.txt');issues=set();names={}
    if req not in files:return ['PYTHON_REQUIREMENTS_UNREADABLE'],{}
    lines=files[req].splitlines()
    if len(lines)>1000 or len(files[req].encode('utf-8'))>16000:return ['PYTHON_REQUIREMENTS_LIMIT'],{}
    for raw in lines:
        line=raw.strip()
        if not line or line.startswith('#'):continue
        if not PIN.fullmatch(line):issues.add('PYTHON_NON_SIMPLE_EXACT_REQUIREMENT');continue
        name,version=line.split('==',1);key=re.sub(r'[-_.]+','-',name.split('[',1)[0]).lower()
        if key in names:issues.add('PYTHON_DUPLICATE_REQUIREMENT')
        names[key]=version
    if not names:issues.add('PYTHON_REQUIREMENTS_EMPTY')
    if len(names)>25:issues.add('PYTHON_REVIEWED_RESOLVER_PIN_LIMIT')
    return sorted(issues),{'direct_pins':len(names),'transitive_resolution':'PENDING_ISOLATED_BINARY_WHEEL_RESOLVER'}

def compile_dependencies(root,*,source=None,expected_plan=None):
    root=Path(root);plan=plan_directory(root,source=source);validate_plan(plan)
    if expected_plan is not None:
        validate_plan(expected_plan)
        if expected_plan!=plan:raise ValueError('PHASE8A_PLAN_MISMATCH')
    scan=inspect(root);files=scan.files
    if digest(scan.digests)!=plan['snapshot_inspection']['inspected_content_sha256'] or digest(scan.paths)!=plan['snapshot_inspection']['inventory_sha256'] or sorted(scan.issues)!=plan['snapshot_inspection']['issues']:raise ValueError('SOURCE_CHANGED_DURING_PLANNING')
    jobs=[];components=[]
    for c in plan['components']:
        rootname=c['root'];managers=c['package_manager_candidates'];manager=managers[0] if len(managers)==1 else None
        issues=[];metadata={};evidence=[];adapter=c['adapter_candidate']
        for m in managers:
            for name in FORMATS.get(m,()):
                rel=_at(rootname,name)
                if rel in scan.paths:evidence.append({'path':rel,'sha256':scan.digests.get(rel)})
        if len(managers)>1:issues.append('PACKAGE_MANAGER_AMBIGUOUS')
        if manager=='npm':issues,metadata=npm_check(files,rootname)
        elif manager=='pip':issues,metadata=pip_check(files,rootname)
        elif manager:issues.append('RESOLVER_NOT_IMPLEMENTED')
        elif not managers and c['ecosystem'] not in {'static','php'}:issues.append('DEPENDENCY_MANAGER_UNRESOLVED')
        if any(v['sha256'] is None for v in evidence):issues.append('DEPENDENCY_INPUT_UNREADABLE')
        if scan.issues:issues.append('BOUNDED_SCAN_INCOMPLETE')
        if manager in {'npm','pip'} and any(PurePosixPath(x).name in {'.npmrc','pip.conf','pip.ini'} and (rootname=='.' or x.startswith(rootname+'/') or PurePosixPath(x).parent in PurePosixPath(rootname).parents) for x in scan.paths):issues.append('REPOSITORY_PACKAGE_CONFIG_REQUIRES_REVIEW')
        if 'PACKAGE_MANAGER_CONFLICT' in c['blockers']:issues.append('PACKAGE_MANAGER_AMBIGUOUS')
        if issues:status='BLOCKED'
        elif manager in {'npm','pip'}:status='RECIPE_AVAILABLE' if adapter in NODE|{PY} else 'ADAPTER_EXPANSION_REQUIRED'
        elif c['ecosystem']=='static':status='NO_PACKAGE_PREPARATION_REQUIRED'
        elif c['ecosystem']=='php':status='BAKED_RUNTIME_PREREQUISITES_UNVERIFIED'
        else:status='BLOCKED'
        components.append({'component_id':c['id'],'root':rootname,'manager':manager,'manager_candidates':managers,'adapter_candidate':adapter,'status':status,'dependency_inputs':sorted(evidence,key=lambda v:v['path']),'dependency_metadata':metadata,'preparation_intent':({'artifact_family':'NPM_OFFLINE_CACHE','target':'LINUX_X64_NODE24','registry_policy':'PUBLIC_NPM_REGISTRY_ONLY','lifecycle_scripts':'DISABLED','requires_independent_validation':True} if manager=='npm' else {'artifact_family':'PYTHON_BINARY_WHEELHOUSE','target':'LINUX_X64_CPYTHON312','registry_policy':'PUBLIC_PYPI_ONLY','source_builds':'DISABLED','requires_independent_validation':True} if manager=='pip' else None),'blockers':sorted(set(issues)),'runtime_blockers':c['blockers'],'service_hints':c['service_hints']})
    eligible=[c for c in components if c['status']=='RECIPE_AVAILABLE'];ids={c['component_id'] for c in eligible}
    # Existing preparers accept a whole pinned snapshot, not arbitrary component roots.
    combined=plan['combined_adapter_candidates']
    if len(components)==2 and len(combined)==1 and set(combined[0]['components'])==ids and combined[0]['adapter_candidate']=='node_fullstack_offline_v1' and all(c['manager']=='npm' for c in eligible):
        jobs=[{'adapter':'node_fullstack_offline_v1','components':sorted(ids),'backend':'EXISTING_REVIEWED_NPM_PREPARER'}]
    elif len(components)==1 and len(eligible)==1:
        c=eligible[0];jobs=[{'adapter':c['adapter_candidate'],'components':[c['component_id']],'backend':'EXISTING_REVIEWED_NPM_PREPARER' if c['manager']=='npm' else 'EXISTING_REVIEWED_PYTHON_PREPARER'}]
    for c in eligible:
        if not any(c['component_id'] in j['components'] for j in jobs):c['status']='MULTI_COMPONENT_RESOLVER_REQUIRED'
    result={'schema_version':'8B.1','phase':'8B','source':plan['source'],'phase8a_plan_sha256':plan['plan_sha256'],'snapshot_inspection':plan['snapshot_inspection'],'components':components,'jobs':jobs,'status':'PREPARATION_RECIPE_AVAILABLE' if jobs else 'NO_PACKAGE_JOB_REQUIRED' if components and all(c['status'] in {'NO_PACKAGE_PREPARATION_REQUIRED','BAKED_RUNTIME_PREREQUISITES_UNVERIFIED'} for c in components) else 'PREPARATION_BLOCKED','execution_authorized':False,'dependency_preparation_authorized':False,'preparation_vms_started':0,'application_vms_started':0,'repository_commands_executed':0,'limitations':['Recipes are advisory; no package has been resolved or downloaded.','Only existing reviewed npm and binary-wheel preparers are bridged. Other managers are inventoried, not resolved.','Phase 8C automatic admission is not implemented. No runtime permission is granted.']}
    result['recipe_sha256']=digest(result)
    return plan,result
