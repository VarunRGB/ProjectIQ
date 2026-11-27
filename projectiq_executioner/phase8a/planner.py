"""Generalized, deterministic discovery and proposed plans, not runtime permission."""
from __future__ import annotations
import hashlib
import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from collections import defaultdict
from .scanner import inspect
from .contracts import validate_plan

SCHEMA='8A.1'
MAX_COMPONENTS=150
NODE_FRAMEWORKS={
 'next':('Next.js','web_fullstack'), 'nuxt':('Nuxt','web_fullstack'), '@sveltejs/kit':('SvelteKit','web_fullstack'),
 '@angular/core':('Angular','frontend'), 'react-native':('React Native','mobile'), 'expo':('Expo','mobile'),
 'electron':('Electron','desktop'), 'vite':('Vite','frontend'), 'react-scripts':('Create React App','frontend'),
 'express':('Express','api'), 'fastify':('Fastify','api'), 'koa':('Koa','api'), 'hono':('Hono','api'),
 '@nestjs/core':('NestJS','api'), '@remix-run/node':('Remix','web_fullstack'), 'gatsby':('Gatsby','frontend'),
 'astro':('Astro','web_fullstack'), 'react':('React','frontend'), 'vue':('Vue','frontend'), 'svelte':('Svelte','frontend'),
 '@solidjs/start':('SolidStart','web_fullstack'), 'solid-js':('SolidJS','frontend'), 'webpack':('Webpack','frontend'),
 'ws':('WebSocket','service'), 'socket.io':('Socket.IO','service')}
PY_FRAMEWORKS={'flask':('Flask','api'),'fastapi':('FastAPI','api'),'django':('Django','web_fullstack'),
 'streamlit':('Streamlit','web_fullstack'),'gradio':('Gradio','web_fullstack'),'dash':('Dash','web_fullstack'),
 'starlette':('Starlette','api'),'aiohttp':('aiohttp','api'),'tornado':('Tornado','api'),
 'bottle':('Bottle','api'),'litestar':('Litestar','api'),'celery':('Celery','worker'),
 'pygame':('Pygame','desktop'),'jupyter':('Jupyter','notebook'),'notebook':('Jupyter','notebook')}
DBS={'mysql':'mysql','mysql2':'mysql','pymysql':'mysql','mysqlclient':'mysql','mysql-connector-python':'mysql',
 'pg':'postgresql','psycopg':'postgresql','psycopg2':'postgresql','psycopg2-binary':'postgresql','asyncpg':'postgresql',
 'mongodb':'mongodb','mongoose':'mongodb','pymongo':'mongodb','motor':'mongodb','redis':'redis','ioredis':'redis',
 'better-sqlite3':'sqlite','sqlite3':'sqlite','aiosqlite':'sqlite','firebase':'external_firebase','firebase-admin':'external_firebase',
 '@supabase/supabase-js':'external_supabase','supabase':'external_supabase','pika':'rabbitmq','amqplib':'rabbitmq',
 'kafkajs':'kafka','kafka-python':'kafka','elasticsearch':'elasticsearch','stripe':'external_payment','boto3':'external_aws',
 '@aws-sdk/client-s3':'external_aws','openai':'external_ai','anthropic':'external_ai',
 'cloudinary':'external_cloudinary','next-cloudinary':'external_cloudinary'}
RUNTIME_ADAPTERS={'static_html':'static_html_v1','native_php':'php_mysql_v1','node_vite':'node_vite_offline_v1',
 'python_web':'python_web_offline_v1','node_express':'node_express_offline_v1','vite_express':'node_fullstack_offline_v1'}
LOCKS={'npm':('package-lock.json','npm-shrinkwrap.json'),'pnpm':('pnpm-lock.yaml',),'yarn':('yarn.lock',),'bun':('bun.lock','bun.lockb')}
SAFE_VERSION=re.compile(r'^[0-9vVxX.*^~<>=| +,\-]{1,60}$')
SAFE_NAME=re.compile(r'^[A-Za-z0-9@_./+\-]{1,150}$')
ENV_RE=re.compile(r'''(?:process\.env\.([A-Za-z_][A-Za-z0-9_]*)|process\.env\[['"]([A-Za-z_][A-Za-z0-9_]*)['"]\]|import\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)|os\.environ\[['"]([A-Za-z_][A-Za-z0-9_]*)['"]\]|(?:os\.)?(?:getenv|environ\.get)\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]|getenv\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"])''')


def canonical(v):return json.dumps(v,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def parent(p):return str(PurePosixPath(p).parent)
def join(root,name):return name if root=='.' else root+'/'+name
def inside(path,root):return root=='.' or path.startswith(root+'/')
def safe_name(v):return isinstance(v,str) and SAFE_NAME.fullmatch(v) is not None and '..' not in v.split('/')
def obj(v):return v if isinstance(v,dict) else {}
def version(v):return v if isinstance(v,str) and SAFE_VERSION.fullmatch(v) else None

def parse_json(text):
    def pairs(rows):
        result={}
        for k,v in rows:
            if k in result:raise ValueError('DUPLICATE_JSON_KEY')
            result[k]=v
        return result
    def constant(_):raise ValueError('NONFINITE_JSON')
    try:
        value=json.loads(text,object_pairs_hook=pairs,parse_constant=constant)
        if not isinstance(value,dict):raise ValueError('OBJECT_REQUIRED')
        return value,None
    except (ValueError,RecursionError,TypeError):return {},'INVALID_JSON_MANIFEST'

def parse_toml(text):
    try:return obj(tomllib.loads(text)),None
    except (ValueError,RecursionError,TypeError):return {},'INVALID_TOML_MANIFEST'

def normalized_dep(s):return re.sub(r'[-_.]+','-',s.lower())
def python_deps(text):
    names=set(); flags=set()
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        if line.startswith('-') or '://' in line or ' @ ' in line:
            flags.add('PYTHON_NONSTANDARD_DEPENDENCY_SPEC');continue
        m=re.match(r'^([A-Za-z0-9_.-]+)',line)
        if m:names.add(normalized_dep(m.group(1)))
    return names,flags

def _role(frameworks,default):
    roles={role for _,role in frameworks}
    if 'mobile' in roles:return 'mobile'
    if 'desktop' in roles:return 'desktop'
    if 'web_fullstack' in roles or {'frontend','api'}<=roles:return 'web_fullstack'
    for role in ('api','frontend','worker','service','notebook'):
        if role in roles:return role
    return default

def _step(kind,tool=None,evidence=(),**kw):
    # Symbolic operations only, never executable command strings or authorizations.
    return {'kind':kind,'tool':tool,'evidence':sorted(set(evidence)),**kw}


def plan_directory(root: Path, *, source: dict|None=None) -> dict:
    """Inspect an operator-owned inert tree. `source` is provenance metadata only."""
    scan=inspect(root); files=scan.files; paths=set(scan.paths); roots=defaultdict(set)
    markers={'package.json':'node','pyproject.toml':'python','requirements.txt':'python','requirements-dev.txt':'python','Pipfile':'python','setup.py':'python','setup.cfg':'python',
             'composer.json':'php','artisan':'php','pom.xml':'jvm','build.gradle':'jvm','build.gradle.kts':'jvm',
             'go.mod':'go','Cargo.toml':'rust','Gemfile':'ruby','config.ru':'ruby','CMakeLists.txt':'native','Makefile':'native',
             'meson.build':'native','pubspec.yaml':'dart','project.godot':'godot','Package.swift':'swift','mix.exs':'elixir',
             'stack.yaml':'haskell','cabal.project':'haskell','deno.json':'deno','deno.jsonc':'deno','manage.py':'python'}
    for p in sorted(paths):
        name=PurePosixPath(p).name; r=parent(p)
        if name in markers:roots[r].add(markers[name])
        if name.startswith('requirements-') and name.endswith('.txt'):roots[r].add('python')
        if p.endswith(('.csproj','.fsproj')):roots[r].add('dotnet')
        if p.endswith('.cabal'):roots[r].add('haskell')
    # A manifest-free native PHP app can have HTTP endpoints in sibling folders
    # and only HTML landing pages in public/. Keep these together without treating
    # browser JavaScript as a separate Node application. Existing manifest-owned
    # subtrees retain their own identities. All grouping remains advisory.
    public_roots={parent(parent(p)) for p in paths
                  if PurePosixPath(p).suffix.lower() in {'.html','.php'}
                  and PurePosixPath(parent(p)).name=='public'}
    public_roots.update(parent(p) for p in paths if PurePosixPath(p).name=='index.html' and not any(part in {'public','tests','test','examples','fixtures','docs','assets'} for part in PurePosixPath(p).parts[:-1]))
    php_paths=[p for p in paths if p.endswith('.php') and not any(
        part in {'tests','test','examples','fixtures','docs','vendor'} for part in PurePosixPath(p).parts[:-1])]
    for r in sorted(public_roots,key=lambda q:(-len(PurePosixPath(q).parts),q)):
        if any(inside(join(r,'public/index.html'),rr) for rr in roots):continue
        if any(inside(p,r) and not any(inside(p,rr) for rr in roots) for p in php_paths):
            roots[r].add('php')
    # Discover manifest-free projects and independent subdirectories, without turning
    # every source folder of a manifest-owned app into a separate application.
    for p in sorted(paths):
        n=PurePosixPath(p).name; r=parent(p); ext=PurePosixPath(p).suffix.lower()
        if any(part in {'test','tests','examples','docs','fixtures','migrations','assets','public'} for part in PurePosixPath(p).parts[:-1]):continue
        family={'index.html':'static','index.php':'php','app.py':'python','main.py':'python','server.py':'python','app.js':'node','server.js':'node','main.go':'go','main.rs':'rust','main.c':'native','main.cpp':'native','Main.java':'jvm','main.dart':'dart'}.get(n)
        if not family:continue
        if any(inside(p,rr) and (family in fs or family=='static' and fs & {'node','python','php','deno','jvm','dotnet','go','rust','ruby','dart'}) for rr,fs in roots.items()):continue
        roots[r].add(family)
    if not roots:
        extmap={'.py':'python','.js':'node','.ts':'node','.php':'php','.java':'jvm','.kt':'jvm','.go':'go','.rs':'rust','.rb':'ruby','.c':'native','.cpp':'native','.cs':'dotnet','.swift':'swift','.dart':'dart','.html':'static','.ipynb':'notebook','.gd':'godot','.r':'r','.jl':'julia'}
        for p in sorted(paths):
            family=extmap.get(PurePosixPath(p).suffix.lower())
            if family:roots['.'].add(family)
        # Extension-only browser scripts are not evidence of a Node process.
        if roots['.'] & {'php','static'}:
            node_runtime=any(re.search(r"\b(?:require\s*\(\s*['\"](?:express|node:http|http|fastify|koa)['\"]|from\s+['\"](?:express|node:http|http|fastify|koa)['\"]|process\.argv|createServer\s*\()",files.get(p,''))
                for p in paths if PurePosixPath(p).suffix.lower() in {'.js','.ts','.mjs','.cjs'})
            if not node_runtime:roots['.'].discard('node')
        if 'php' in roots['.']:roots['.'].discard('static')
        if not roots['.']:del roots['.']
    root_order=sorted(roots,key=lambda r:(-len(PurePosixPath(r).parts),r))
    owned=defaultdict(list)
    for p in sorted(paths):
        owner=next((r for r in root_order if inside(p,r)),None)
        if owner is not None:owned[owner].append(p)
    components=[]; global_issues=set(scan.issues)
    for r in sorted(roots):
        for family in sorted(roots[r]):
            if len(components)>=MAX_COMPONENTS:
                global_issues.add('COMPONENT_LIMIT');break
            local=owned[r]; localset=set(local); fs=[]; deps=set(); issues=set(); evid=set(); steps=[]; entry=[]; managers=[]; runtime=None; candidate=None; toolchains=[]
            def has(n):return join(r,n) in localset
            def get(n):
                p=join(r,n)
                if p in localset:
                    evid.add(p)
                    if p not in files:issues.add('MANIFEST_UNREADABLE')
                return files.get(p,'')
            def parsed(n,parser=parse_json):
                if not has(n):return {}
                d,err=parser(get(n));
                if err:issues.add(err)
                return d
            def entries(names):
                for n in names:
                    p=join(r,n)
                    if p in localset and p not in entry:entry.append(p)
            def dep_step(tool):steps.append(_step('RESOLVE_DEPENDENCIES',tool,evid,requires_isolated_preparation=True))
            def framework(name,role):
                if (name,role) not in fs:fs.append((name,role))
            # Source patterns are advisory; they are never executable instructions.
            language_extensions={'node':{'.js','.jsx','.ts','.tsx','.mjs','.cjs','.vue','.svelte'},'deno':{'.js','.ts','.tsx'},'python':{'.py'},'php':{'.php'},'jvm':{'.java','.kt','.kts','.scala'},'go':{'.go'},'rust':{'.rs'},'ruby':{'.rb'},'dotnet':{'.cs','.fs'},'native':{'.c','.cpp','.cc','.h','.hpp'},'swift':{'.swift'},'dart':{'.dart'},'elixir':{'.ex','.exs'},'godot':{'.gd'},'r':{'.r'},'julia':{'.jl'}}
            source_paths=[p for p in local if p in files and PurePosixPath(p).suffix.lower() in language_extensions.get(family,set())]
            code='\n'.join(files[p] for p in source_paths)
            if family=='node':
                runtime='node'; manifest=parsed('package.json'); scripts=obj(manifest.get('scripts'))
                for field in ('dependencies','devDependencies','peerDependencies','optionalDependencies'):
                    for k,v in obj(manifest.get(field)).items():
                        if safe_name(k):deps.add(k.lower())
                        if isinstance(v,str) and (v.startswith(('git','http','file:','link:','workspace:','patch:')) or '://' in v):issues.add('NODE_NONSTANDARD_DEPENDENCY_SPEC')
                for dep,(name,role) in NODE_FRAMEWORKS.items():
                    if dep in deps:framework(name,role)
                if not fs and re.search(r'''(?:require\(['"]express['"]\)|from\s+['"]express['"])''',code):framework('Express','api')
                if has('angular.json'):framework('Angular','frontend')
                if any(has(x) for x in ('next.config.js','next.config.mjs','next.config.ts')):framework('Next.js','web_fullstack')
                if any(has(x) for x in ('vite.config.js','vite.config.ts')):framework('Vite','frontend')
                # Locks may live at a workspace root; no arbitrary parent traversal.
                anc=[r]+[q for q in sorted(roots,key=len,reverse=True) if q!=r and inside(join(r,'package.json'),q)]
                lockroot=next((q for q in anc if any(join(q,n) in paths for v in LOCKS.values() for n in v)),None)
                if lockroot is not None:
                    managers=[m for m,names in LOCKS.items() if any(join(lockroot,n) in paths for n in names)]
                    evid.update(join(lockroot,n) for names in LOCKS.values() for n in names if join(lockroot,n) in paths)
                    if lockroot!=r:issues.add('ANCESTOR_LOCK_WORKSPACE_MEMBERSHIP_UNVERIFIED')
                declared=manifest.get('packageManager')
                if isinstance(declared,str):
                    m=declared.split('@',1)[0]
                    if m in LOCKS:
                        if managers and m not in managers:issues.add('PACKAGE_MANAGER_CONFLICT')
                        if not managers:managers=[m]
                    else:issues.add('UNKNOWN_PACKAGE_MANAGER')
                if len(managers)>1:issues.add('MULTIPLE_PACKAGE_MANAGERS')
                if lockroot is None:issues.add('DEPENDENCY_LOCK_MISSING')
                workspace_declared=bool(manifest.get('workspaces') or has('pnpm-workspace.yaml'))
                if workspace_declared:issues.add('WORKSPACE_GRAPH_REQUIRES_VALIDATION')
                if manager_lock := next((n for n in ('package-lock.json','npm-shrinkwrap.json') if lockroot is not None and join(lockroot,n) in paths),None):
                    locktext=files.get(join(lockroot,manager_lock))
                    if locktext is None:issues.add('LOCK_CONTENT_NOT_INSPECTED')
                    else:
                        lockdata,lockerr=parse_json(locktext)
                        if lockerr or lockdata.get('lockfileVersion') not in (2,3):issues.add('NPM_LOCK_FORMAT_UNVERIFIED')
                if set(scripts)&{'preinstall','install','postinstall','prepare','prebuild','postbuild','prestart','poststart'}:issues.add('AUTHOR_CONTROLLED_LIFECYCLE_SCRIPTS')
                for key in ('main','module'):
                    v=manifest.get(key)
                    if safe_name(v) and join(r,v.removeprefix('./')) in localset:entries([v.removeprefix('./')])
                entries(['app.js','server.js','index.js','index.html','src/index.js','src/main.ts','src/main.tsx','src/main.jsx','src/server.ts','server.ts','app.ts','app/page.tsx','pages/index.tsx','src/app/page.tsx'])
                manager=managers[0] if len(managers)==1 else None
                dep_step(manager)
                if 'build' in scripts:steps.append(_step('BUILD_DECLARED_SCRIPT',manager,evid,script_key='build',requires_policy_validation=True))
                for key in ('start','dev','preview','serve'):
                    if key in scripts:steps.append(_step('START_DECLARED_SCRIPT_CANDIDATE',manager,evid,script_key=key,requires_policy_validation=True))
                names={n for n,_ in fs}
                if names&{'Next.js','Nuxt','SvelteKit','Remix','Astro','Angular','Create React App','React Native','Expo','Electron','NestJS','Gatsby','SolidStart'}:pass
                elif 'Vite' in names and _role(fs,'library')=='frontend' and manager=='npm':candidate=RUNTIME_ADAPTERS['node_vite']
                elif 'Express' in names and _role(fs,'library')=='api' and manager=='npm':candidate=RUNTIME_ADAPTERS['node_express']
                if not entry and not set(scripts)&{'start','dev','preview','serve'}:issues.add('ENTRYPOINT_UNRESOLVED')
                if not fs:issues.add('APPLICATION_ROLE_UNRESOLVED')
                if v:=version(obj(manifest.get('engines')).get('node')):
                    runtime='node '+v;toolchains.append({'tool':'node','constraint':v,'path':join(r,'package.json')})
                for n in ('.nvmrc','.node-version'):
                    if has(n):
                        if v:=version(get(n).strip()):toolchains.append({'tool':'node','constraint':v,'path':join(r,n)})
                        else:issues.add('TOOLCHAIN_VERSION_UNRESOLVED')
            elif family=='python':
                runtime='python'; py=parsed('pyproject.toml',parse_toml); proj=obj(py.get('project')); poetry=obj(obj(py.get('tool')).get('poetry'))
                for p in local:
                    if PurePosixPath(p).name in {'requirements.txt','requirements-dev.txt'} or PurePosixPath(p).name.startswith('requirements-'):
                        if p not in files:issues.add('MANIFEST_UNREADABLE');continue
                        evid.add(p); d,f=python_deps(files[p]);deps.update(d);issues.update(f)
                        if any(line.strip() and not line.strip().startswith('#') and not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+-]+(?:\s*#.*)?',line.strip()) for line in files[p].splitlines()):issues.add('PYTHON_DEPENDENCIES_NOT_EXACTLY_PINNED')
                for field in ('dependencies',):
                    for v in proj.get(field,[]) if isinstance(proj.get(field),list) else []:
                        if isinstance(v,str):d,f=python_deps(v);deps.update(d);issues.update(f)
                deps.update(normalized_dep(k) for k in obj(poetry.get('dependencies')) if safe_name(k))
                if has('Pipfile'):
                    pf=parsed('Pipfile',parse_toml);deps.update(normalized_dep(k) for k in obj(pf.get('packages')) if safe_name(k))
                for d,(n,role) in PY_FRAMEWORKS.items():
                    if d in deps or re.search(r'(?m)^\s*(?:from|import)\s+'+re.escape(d)+r'(?:\b|\.)',code):framework(n,role)
                if has('manage.py'):framework('Django','web_fullstack')
                for m,n in [('uv','uv.lock'),('poetry','poetry.lock'),('pipenv','Pipfile.lock')]:
                    if has(n):managers.append(m);evid.add(join(r,n))
                if has('requirements.txt'):managers.append('pip')
                if not managers:managers=['pip'];issues.add('DEPENDENCY_LOCK_MISSING')
                if len(managers)>1:issues.add('MULTIPLE_PACKAGE_MANAGERS')
                entries(['manage.py','app.py','main.py','server.py','wsgi.py','asgi.py','src/app.py','src/main.py'])
                for p in local:
                    if p in files and p.endswith('.py') and re.search(r'(?m)^\s*(?:app|application)\s*=\s*(?:Flask|FastAPI|Starlette)\s*\(',files[p]) and p not in entry:entry.append(p)
                if has('setup.py'):issues.add('AUTHOR_CONTROLLED_BUILD_HOOK');get('setup.py')
                if proj.get('scripts'):issues.add('DECLARED_PYTHON_CONSOLE_SCRIPTS_REQUIRE_VALIDATION')
                dep_step(managers[0] if len(managers)==1 else None)
                if any(n in {'Flask','FastAPI'} for n,_ in fs) and len([n for n,_ in fs if n in {'Flask','FastAPI'}])==1 and not any(n in {'Django','Streamlit','Gradio','Celery','Dash'} for n,_ in fs):candidate=RUNTIME_ADAPTERS['python_web']
                if not entry:issues.add('ENTRYPOINT_UNRESOLVED')
                if v:=version(proj.get('requires-python')):
                    runtime='python '+v;toolchains.append({'tool':'python','constraint':v,'path':join(r,'pyproject.toml')})
                if has('.python-version'):
                    if v:=version(get('.python-version').strip()):toolchains.append({'tool':'python','constraint':v,'path':join(r,'.python-version')})
                    else:issues.add('TOOLCHAIN_VERSION_UNRESOLVED')
                if 'pip' in managers:issues.add('PYTHON_TRANSITIVE_LOCK_UNVERIFIED')
                if entry:steps.append(_step('START_PYTHON_CANDIDATE','python',entry,requires_policy_validation=True))
            elif family=='php':
                runtime='php'; manifest=parsed('composer.json');deps.update(k.lower() for k in obj(manifest.get('require')) if safe_name(k));
                if 'laravel/framework' in deps or has('artisan'):framework('Laravel','web_fullstack')
                elif any(k.startswith('symfony/') for k in deps):framework('Symfony','web_fullstack')
                else:framework('Native PHP','web_fullstack');candidate=RUNTIME_ADAPTERS['native_php']
                if has('composer.json'):
                    managers=['composer'];dep_step('composer')
                    if not has('composer.lock'):issues.add('DEPENDENCY_LOCK_MISSING')
                    if manifest.get('scripts') or manifest.get('extra'):issues.add('AUTHOR_CONTROLLED_BUILD_HOOK')
                entries(['index.php','public/index.php','artisan']);
                if 'Native PHP' in {n for n,_ in fs}:
                    # HTML landing pages are HTTP entrypoint hints, not executable
                    # scripts or proof that a PHP handler is a server entrypoint.
                    if not entry:entries(['index.html','public/index.html','login.html','public/login.html','register.html','public/register.html'])
                    evid.update(source_paths[:50])
                    evid.update([p for p in local if p.endswith('.sql')][:50])
                    if not entry:
                        entry.extend(p for p in local if parent(p)==join(r,'public') and p.endswith('.html'))
                        entry=entry[:50]
                        if entry:issues.add('LANDING_PAGE_SELECTION_UNRESOLVED')
                    if any(p.startswith(join(r,'public')+'/') for p in entry):issues.add('PUBLIC_DOCUMENT_ROOT_REQUIRES_VALIDATION')
                if not entry:issues.add('ENTRYPOINT_UNRESOLVED')
                steps.append(_step('START_PHP_CANDIDATE','php',entry,requires_policy_validation=True))
            elif family=='static':
                runtime='static_http';framework('Static HTML','frontend');entries(['index.html']);candidate=RUNTIME_ADAPTERS['static_html'] if entry else None
                if not entry:issues.add('ENTRYPOINT_UNRESOLVED')
                steps.append(_step('SERVE_STATIC_CANDIDATE','static_http',entry,requires_policy_validation=True))
            else:
                spec={
                    'jvm':('java_or_kotlin','maven' if has('pom.xml') else 'gradle' if has('build.gradle') or has('build.gradle.kts') else None,['pom.xml','build.gradle','build.gradle.kts'],['Main.java','src/main/java/Main.java']),
                    'go':('go','go',['go.mod'],['main.go','cmd/server/main.go']), 'rust':('rust','cargo',['Cargo.toml'],['src/main.rs','main.rs']),
                    'ruby':('ruby','bundler',['Gemfile','config.ru'],['config.ru','app.rb','bin/rails']),
                    'dotnet':('dotnet','nuget',[],['Program.cs','Startup.cs']), 'native':('c_cpp','cmake' if has('CMakeLists.txt') else 'make' if has('Makefile') else 'meson' if has('meson.build') else None,['CMakeLists.txt','Makefile','meson.build'],['main.c','main.cpp','src/main.cpp']),
                    'dart':('dart_or_flutter','pub',['pubspec.yaml'],['lib/main.dart','bin/main.dart','main.dart']),
                    'godot':('godot',None,['project.godot'],[]),'swift':('swift','swiftpm',['Package.swift'],['Sources/main.swift','main.swift']),
                    'elixir':('elixir','mix',['mix.exs'],[]),'haskell':('haskell','stack' if has('stack.yaml') else 'cabal',['stack.yaml','cabal.project'],['Main.hs','app/Main.hs']),
                    'deno':('deno','deno',['deno.json','deno.jsonc'],['main.ts','main.js','server.ts']),
                    'notebook':('python','jupyter',[],[]),'r':('r',None,[],[]),'julia':('julia',None,[],[])}
                runtime,manager,manifests,enames=spec[family];managers=[manager] if manager else []
                mt='\n'.join(get(n) for n in manifests if has(n));entries(enames)
                hints={'jvm':[(r'spring[-.]boot|SpringBootApplication','Spring Boot','api'),(r'io\.ktor|ktor-server','Ktor','api'),(r'com\.android\.application|androidx','Android','mobile'),(r'quarkus','Quarkus','api'),(r'micronaut','Micronaut','api')],
                       'go':[(r'gin-gonic|gin\.Default','Gin','api'),(r'labstack/echo','Echo','api'),(r'gofiber','Fiber','api'),(r'net/http','Go HTTP','api')],
                       'rust':[(r'\baxum\b','Axum','api'),(r'actix[-_]web','Actix Web','api'),(r'\brocket\b','Rocket','api'),(r'\bbevy\b','Bevy','desktop')],
                       'ruby':[(r'\brails\b','Rails','web_fullstack'),(r'\bsinatra\b','Sinatra','api')],
                       'dotnet':[(r'Microsoft\.NET\.Sdk\.Web|WebApplication\.CreateBuilder','ASP.NET Core','api')],
                       'dart':[(r'\bflutter\b','Flutter','mobile')], 'elixir':[(r'\bphoenix\b','Phoenix','web_fullstack')],
                       'deno':[(r'Deno\.serve|@std/http','Deno HTTP','api')]}
                if family=='dotnet':
                    for p in local:
                        if p.endswith(('.csproj','.fsproj')):evid.add(p);mt+='\n'+files.get(p,'')
                for pattern,n,role in hints.get(family,[]):
                    if re.search(pattern,mt+'\n'+code,re.I):framework(n,role)
                if family=='go':
                    m=re.search(r'(?m)^go\s+([0-9]+(?:\.[0-9]+){1,2})\s*$',mt)
                    if m:toolchains.append({'tool':'go','constraint':m.group(1),'path':join(r,'go.mod')})
                if family=='rust' and has('rust-toolchain.toml'):
                    tc=parsed('rust-toolchain.toml',parse_toml);v=version(obj(tc.get('toolchain')).get('channel'))
                    if v:toolchains.append({'tool':'rust','constraint':v,'path':join(r,'rust-toolchain.toml')})
                    else:issues.add('TOOLCHAIN_VERSION_UNRESOLVED')
                if family=='godot':framework('Godot','game')
                if family=='notebook':framework('Notebook','notebook')
                if manager:dep_step(manager)
                steps.append(_step('BUILD_TOOLCHAIN_CANDIDATE',manager,evid,requires_policy_validation=True))
                if entry:steps.append(_step('START_TOOLCHAIN_CANDIDATE',runtime,entry,requires_policy_validation=True))
                else:issues.add('ENTRYPOINT_UNRESOLVED')
                issues.add('ADAPTER_EXPANSION_REQUIRED')
            # Evidence for environment keys only; no values, script text, or arbitrary URLs emitted.
            env=set();services=set(DBS[d] for d in deps if d in DBS)
            for m in ENV_RE.finditer(code):env.update(x for x in m.groups() if x)
            for n in ('.env.example','.env.sample','.env.template'):
                if has(n):
                    t=get(n);env.update(m.group(1) for m in re.finditer(r'(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=',t))
            for pattern,service in [(r'\b(?:mysqli|PDO\s*\(\s*[\'\"]mysql:|mysql:|mysql2)\b','mysql'),(r'\b(?:sqlite3|node:sqlite|sqlite:)\b','sqlite'),(r'\b(?:mongodb(?:\+srv)?://|mongoose|MongoClient)\b','mongodb'),(r'\b(?:postgres(?:ql)?://|psycopg|Npgsql)\b','postgresql'),(r'\b(?:redis://|RedisClient)\b','redis')]:
                if re.search(pattern,code,re.I):services.add(service)
            for k in env:
                for token,service in [('MONGO','mongodb'),('MYSQL','mysql'),('POSTGRES','postgresql'),('REDIS','redis'),('FIREBASE','external_firebase'),('SUPABASE','external_supabase')]:
                    if token in k.upper():services.add(service)
            external_http_paths=[]
            if family in {'node','deno','python','php'}:
                # A named API/base URL or literal HTTP client target is a hint,
                # not proof of reachability or of an independently missing backend.
                # Exclude loopback and relative paths. Never emit URL values.
                target_re=re.compile(r"(?i)(?:\b(?:BASE_URL|API_URL|API_BASE_URL|API_BASE|BACKEND_URL|API_ENDPOINT)\s*[:=]\s*|\b(?:fetch|axios\.(?:get|post|put|patch|delete)|requests\.(?:get|post|put|patch|delete))\s*\(\s*)['\"]https?://([^/'\"\s]+)")
                for p in source_paths:
                    for m in target_re.finditer(files[p]):
                        host=m.group(1).split('@')[-1].lower()
                        if re.fullmatch(r'(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\])(?::\d+)?',host):continue
                        external_http_paths.append(p);break
                if external_http_paths:
                    services.add('external_http_api');evid.update(external_http_paths[:50])
                    issues.add('EXTERNAL_HTTP_API_REQUIRES_VALIDATION')
            if env:issues.add('ENVIRONMENT_CONFIGURATION_REQUIRES_VALIDATION')
            if any(s.startswith('external_') for s in services):issues.add('EXTERNAL_SERVICE_REQUIREMENTS_UNRESOLVED')
            if services-{'sqlite'}:issues.add('SERVICE_PROVISIONING_REQUIRES_VALIDATION')
            if has('Dockerfile') or has('Containerfile'):issues.add('AUTHOR_CONTROLLED_CONTAINER_RECIPE');get('Dockerfile');get('Containerfile')
            role=_role(fs,'game' if family=='godot' else 'library_or_cli')
            if family=='node' and workspace_declared and not fs and not entry:role='workspace'
            if family=='node' and external_http_paths and 'Next.js' in {n for n,_ in fs}:
                local_server_hint=any(re.search(r'(?:^|/)(?:app/(?:[^/]+/)*route\.[jt]sx?|pages/api/)',p) or
                    re.search(r"['\"]use server['\"]",files.get(p,'')) for p in source_paths)
                if not local_server_hint:
                    role='frontend';issues.add('LOCAL_BACKEND_IMPLEMENTATION_NOT_ESTABLISHED')
            # Literal ports are only hints; never guess a service's binding or URL.
            port_records=[]; source_signals=[]
            for p in source_paths:
                t=files[p]
                for m in re.finditer(r'(?:\.listen\(\s*|\bport\s*=\s*|\bPORT\s*[:=]\s*)([0-9]{1,5})\b',t):
                    port=int(m.group(1))
                    if 1<=port<=65535 and len(port_records)<50:
                        record={'port':port,'path':p,'status':'LITERAL_HINT_UNVERIFIED'}
                        if record not in port_records:port_records.append(record)
                if ENV_RE.search(t) or any(re.search(r'\b'+re.escape(n.lower())+r'\b',t,re.I) for n,_ in fs if n not in {'Native PHP','Static HTML'}):
                    if len(source_signals)<50:source_signals.append(p)
            if len({row['constraint'] for row in toolchains})>1:issues.add('MULTIPLE_TOOLCHAIN_CONSTRAINTS_REQUIRE_RECONCILIATION')
            if port_records:evid.update(row['path'] for row in port_records)
            evid.update(source_signals)
            if candidate==RUNTIME_ADAPTERS['native_php'] and 'mysql' not in services:issues.add('PHP_MYSQL_SHAPE_NOT_ESTABLISHED')
            if candidate is None:issues.add('NO_EXISTING_ADAPTER_CANDIDATE')
            if candidate and (role in {'mobile','desktop','worker','notebook','game'} or any(s.startswith('external_') for s in services)):issues.add('ADAPTER_COMPATIBILITY_UNCONFIRMED')
            if candidate and not issues:status='ADAPTER_CANDIDATE'
            elif candidate:status='ADAPTER_CANDIDATE_WITH_BLOCKERS'
            else:status='EXPANSION_OR_REVIEW_REQUIRED'
            if role in {'frontend','api','web_fullstack','service'}:steps.append(_step('DISCOVER_HEALTH_PROBE',None,entry,requires_independent_probe_validation=True))
            ident='component-'+digest([r,family])[:16]
            components.append({'id':ident,'root':r,'ecosystem':family,'runtime_hint':runtime,'role_hint':role,
                'framework_hints':sorted(n for n,_ in fs),'toolchain_constraints':toolchains,'evidence_paths':sorted(evid|set(entry)),
                'entrypoint_candidates':sorted(entry)[:50],'package_manager_candidates':sorted(set(managers)),
                'service_hints':sorted(services),'dependency_name_hints':sorted(deps)[:300],'port_hints':port_records,'environment_variable_names':sorted(env)[:100],
                'adapter_candidate':candidate,'status':status,'blockers':sorted(issues),'proposed_operations':steps,
                'execution_authorized':False})
    # Separate orchestration declarations are discovery signals only. Do not interpret
    # author-controlled compose/docker instructions or follow declared paths.
    deployment=[]
    for p in sorted(paths):
        n=PurePosixPath(p).name
        if n in {'compose.yml','compose.yaml','docker-compose.yml','docker-compose.yaml','Dockerfile','Containerfile','Procfile','firebase.json','serverless.yml','serverless.yaml','.gitmodules'} or n.startswith('Dockerfile.'):
            t=files.get(p,'');service_hints=[]
            if 'compose' in n:
                # Only lexical hints for known image families; not a YAML parser or a deployment plan.
                for match in re.finditer(r'(?m)^\s*image\s*:\s*[\"\']?(?:(?:docker\.io/)?(?:library/)?)?(postgres|mysql|mariadb|mongo|redis|rabbitmq|elasticsearch)(?=[:@\s\"\']|$)',t,re.I):
                    service_hints.append({'postgres':'postgresql','mongo':'mongodb','mariadb':'mysql'}.get(match.group(1).lower(),match.group(1).lower()))
            deployment.append({'path':p,'kind':'compose' if 'compose' in n else 'deployment_hint','service_image_hints':sorted(set(service_hints)),'status':'UNVALIDATED_DECLARATION'})
    if deployment:global_issues.add('DEPLOYMENT_DECLARATIONS_REQUIRE_VALIDATION')
    front=[c for c in components if c['role_hint']=='frontend']; api=[c for c in components if c['role_hint']=='api']
    relationships=[]
    if len(front)==len(api)==1:
        relationships.append({'from':front[0]['id'],'to':api[0]['id'],'kind':'POSSIBLE_FRONTEND_BACKEND','status':'HYPOTHESIS_NOT_VERIFIED'})
    combined=[]
    if len(front)==len(api)==1 and front[0]['adapter_candidate']==RUNTIME_ADAPTERS['node_vite'] and api[0]['adapter_candidate']==RUNTIME_ADAPTERS['node_express']:
        combined.append({'adapter_candidate':RUNTIME_ADAPTERS['vite_express'],'components':[front[0]['id'],api[0]['id']],'status':'REQUIRES_EXISTING_ADAPTER_VALIDATION'})
    if len(components)>=MAX_COMPONENTS:global_issues.add('COMPONENT_LIMIT_OR_EXACT_CAP')
    if not components:global_issues.add('NO_COMPONENTS_DISCOVERED')
    # Hash describes inspected files, not full repository content or attestation.
    result={'schema_version':SCHEMA,'phase':'8A','status':'PLANNED_WITH_LIMITATIONS' if global_issues or any(c['blockers'] for c in components) else 'PLANNED',
        'source':source or {'kind':'local_unverified'},'snapshot_inspection':{'file_count':len(scan.paths),'text_files_read':len(scan.files),'bytes_read':scan.bytes_read,'inspected_content_sha256':digest(scan.digests),'inventory_sha256':digest(scan.paths),'scope':'BOUNDED_INSPECTED_FILES_ONLY','issues':sorted(scan.issues)},
        'components':components,'relationship_hypotheses':relationships,'combined_adapter_candidates':combined,'deployment_hints':deployment[:100],
        'planning_limitations':sorted(global_issues),'admission':{'status':'NOT_EVALUATED','runtime_permission':False,'dependency_preparation_permission':False,'required_gates':['DEPENDENCY_PREPARATION_POLICY','WORKER_SECURITY_VALIDATION','EXACT_SOURCE_AND_ADAPTER_VALIDATION']},'execution_authorized':False,'vm_launches':0,'commands_executed':0,'functional_tests_executed':0,
        'next_gate':'PHASE_8B_DEPENDENCY_PREPARATION_AND_8C_SECURITY_ADMISSION','limitations':['Discovery is heuristic and bounded; component and relationship hints are not verified runtime facts.','Adapter candidate does not establish compatibility, approval, or executable readiness.','All proposed operations are symbolic and require independent validation.','No dependencies installed, repository code executed, or functionality tested.']}
    result['plan_sha256']=digest(result)
    validate_plan(result)
    return result
