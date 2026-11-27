"""Compile fresh symbolic, per-component runtime plans. Never grant execution."""
from __future__ import annotations
import ast,re,tomllib
from pathlib import Path,PurePosixPath
from ..phase8a.planner import plan_directory,digest
from ..phase8a.scanner import inspect
from ..phase8b.planner import load_object
from ..phase8c2.broker import compile_inputs
from ..phase8c_final.pypi import target
from .requirements import discover
from .versions import node_constraint,python_constraint

NODE_BUILD={'next':'NODE_NEXT','vite':'NODE_VITE','react-scripts':'NODE_CRA','astro':'NODE_ASTRO','nuxt':'NODE_NUXT','@sveltejs/kit':'NODE_SVELTEKIT','@angular/core':'NODE_ANGULAR'}
NODE_HTTP={'express','fastify','koa','hono'}
PY_HTTP={'flask':'PY_FLASK','fastapi':'PY_ASGI','starlette':'PY_ASGI','django':'PY_DJANGO','streamlit':'PY_STREAMLIT'}
OPTIONAL={'NATIVE_C','NATIVE_CPP','JAVA_SINGLE','GO_SINGLE','RUST_SINGLE'}
SUPPORTED={'STATIC_HTTP','PHP_HTTP','NODE_HTTP','NODE_CLI','NODE_NEXT','NODE_VITE','NODE_CRA','NODE_ASTRO','NODE_NUXT','NODE_SVELTEKIT','NODE_ANGULAR','PY_FLASK','PY_ASGI','PY_DJANGO','PY_STREAMLIT','PY_CLI'}|OPTIONAL
SAFE_REL=re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z')


def safe_path(p):
    return isinstance(p,str) and len(p)<=240 and bool(SAFE_REL.fullmatch(p)) and all(x not in ('.','..') for x in p.split('/'))


def python_entries(files):
    rows=[];cli=[]
    for path,body in sorted(files.items()):
        if not path.endswith('.py') or not safe_path(path) or any(p in {'tests','test','examples','docs','migrations','scripts'} for p in path.split('/')[:-1]) or PurePosixPath(path).name.startswith('test_'):continue
        try:tree=ast.parse(body)
        except (SyntaxError,ValueError,RecursionError):continue
        imports={}
        for item in tree.body:
            if isinstance(item,ast.ImportFrom) and item.module in {'flask','fastapi','starlette.applications'}:
                for n in item.names:
                    if (item.module,n.name) in {("flask","Flask"),("fastapi","FastAPI"),("starlette.applications","Starlette")}:imports[n.asname or n.name]=item.module
            elif isinstance(item,ast.Import):
                for n in item.names:
                    if n.name in {'flask','fastapi','starlette.applications'}:imports[n.asname or n.name]=n.name
        for item in tree.body:
            if isinstance(item,(ast.Assign,ast.AnnAssign)) and isinstance(item.value,ast.Call):
                f=item.value.func;family=None
                if isinstance(f,ast.Name):family=imports.get(f.id)
                elif isinstance(f,ast.Attribute) and isinstance(f.value,ast.Name) and f.attr in {'Flask','FastAPI','Starlette'}:family=imports.get(f.value.id)
                if family:
                    for var in item.targets if isinstance(item,ast.Assign) else [item.target]:
                        if isinstance(var,ast.Name) and var.id.isidentifier():rows.append((PY_HTTP[family.split('.')[0]],path,var.id))
            if isinstance(item,ast.If) and isinstance(item.test,ast.Compare) and isinstance(item.test.left,ast.Name) and item.test.left.id=='__name__' and len(item.test.ops)==1 and isinstance(item.test.ops[0],ast.Eq) and len(item.test.comparators)==1 and isinstance(item.test.comparators[0],ast.Constant) and item.test.comparators[0].value=='__main__':cli.append(path)
    return sorted(set(rows)),sorted(set(cli))


def node_entry(files,manifest):
    scripts=manifest.get('scripts',{})
    if not isinstance(scripts,dict):raise ValueError('NODE_SCRIPT_SHAPE_INVALID')
    start=scripts.get('start')
    if start is not None:
        m=re.fullmatch(r'node\s+(?:\./)?([A-Za-z0-9_./-]+\.(?:js|mjs|cjs))',start) if isinstance(start,str) else None
        if not m or not safe_path(m[1]) or m[1] not in files:raise ValueError('NODE_START_COMMAND_UNSUPPORTED')
        return m[1]
    main=manifest.get('main')
    if isinstance(main,str):
        main=main.removeprefix('./')
        if safe_path(main) and main in files and main.endswith(('.js','.mjs','.cjs')):return main
    candidates=[n for n in ('server.js','app.js','index.js','src/server.js','src/app.js','src/index.js','server.mjs','app.mjs','index.mjs') if n in files]
    if len(candidates)!=1:raise ValueError('NODE_ENTRYPOINT_AMBIGUOUS_OR_MISSING')
    return candidates[0]



def infer_node_port(body):
    """Bounded heuristic; infer literal or uniquely declared constant ports.

    Never execute source or change guest listener/identity validation. Unknown
    dynamic expressions retain the historical PORT environment fallback.
    """
    # Mask comments and quoted text before matching. This is not a JS parser;
    # template expressions and complex scope resolution are deliberately omitted.
    token = re.compile(r"//[^\r\n]*|/\*[\s\S]*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`")
    body = token.sub(lambda m: re.sub(r'[^\r\n]', ' ', m[0]), body)
    values = set()
    for m in re.finditer(r'\.listen\s*\(\s*(?:\{\s*port\s*:\s*)?(\d{1,5}|[A-Za-z_$][\w$]*)(?=\s*[,})])',body):
        arg=m[1]
        if arg.isdecimal():values.add(int(arg));continue
        name=re.escape(arg)
        # A single simple const declaration, preceding the listener; do not
        # attempt to infer mutable, imported, or multiply declared variables.
        decls=list(re.finditer(r'(?<![\w$])(?:const|let|var)\s+'+name+r'(?![\w$])',body))
        if len(decls)!=1 or decls[0].start()>=m.start():continue
        literal=re.match(r'const\s+'+name+r'\s*=\s*(\d{1,5})\s*(?:;|(?=\r?\n|$))',body[decls[0].start():])
        if literal:values.add(int(literal[1]))
    if len(values)>1 or any(not 1024<=v<=65535 for v in values):return 8765,'NODE_LISTEN_PORT_AMBIGUOUS_OR_INVALID'
    return next(iter(values),8765),None

def _node(files,paths,version,blockers):
    manifest=load_object(files['package.json']) if 'package.json' in files else {}
    if not manifest and 'package.json' in paths:raise ValueError('NODE_MANIFEST_INVALID')
    dep={}
    for k in ('dependencies','devDependencies','optionalDependencies','peerDependencies'):
        d=manifest.get(k,{})
        if not isinstance(d,dict):raise ValueError('NODE_MANIFEST_INVALID')
        dep.update(d)
    engines=manifest.get('engines',{})
    if not isinstance(engines,dict):raise ValueError('NODE_ENGINE_INVALID')
    constraints=[]
    if 'node' in engines:constraints.append(engines['node'])
    for n in ('.nvmrc','.node-version'):
        if n in files:constraints.append(files[n].strip())
    if any(node_constraint(version,c) is not True for c in constraints):blockers.add('PROVISIONED_NODE_VERSION_INCOMPATIBLE_OR_UNRESOLVED')
    if any(PurePosixPath(x).name in {'.npmrc','npm-shrinkwrap.json','yarn.lock','pnpm-lock.yaml','bun.lock','bun.lockb','pnpm-workspace.yaml'} for x in paths):blockers.add('NPM_CONFIG_OR_OTHER_MANAGER_REQUIRES_REVIEW')
    pm=manifest.get('packageManager')
    npm_constraints=[]
    if 'npm' in engines:npm_constraints.append(engines['npm'])
    if pm is not None:
        if not isinstance(pm,str) or not re.fullmatch(r'npm@[0-9]+\.[0-9]+\.[0-9]+',pm):blockers.add('PACKAGE_MANAGER_UNSUPPORTED')
        else:npm_constraints.append(pm[4:])
    if any(node_constraint('v0.0.0',x) is None for x in npm_constraints):blockers.add('NPM_VERSION_CONSTRAINT_UNRESOLVED')
    scripts=manifest.get('scripts',{})
    if not isinstance(scripts,dict):raise ValueError('NODE_SCRIPT_SHAPE_INVALID')
    if set(scripts)&{'preinstall','install','postinstall','prepare','prebuild','postbuild','prestart','poststart'}:blockers.add('AUTHOR_LIFECYCLE_REQUIRES_REVIEW')
    if manifest.get('workspaces'):blockers.add('WORKSPACE_INSTALL_UNSUPPORTED')
    # Next and Astro can include Vite transitively or directly. Prefer explicit framework.
    builds=[x for x in ('next','react-scripts','astro','nuxt','@sveltejs/kit','@angular/core') if x in dep]
    if len(builds)>1:raise ValueError('AMBIGUOUS_NODE_FRAMEWORK')
    framework=builds[0] if builds else 'vite' if 'vite' in dep else None
    if framework and NODE_HTTP.intersection(dep):blockers.add('MIXED_NODE_SERVER_AND_BUILD_REQUIRES_COMPOSITION')
    if framework:
        kind=NODE_BUILD[framework];entry=None;probe='/'
        # Astro SSR cannot be launched by the static adapter.
        if kind=='NODE_SVELTEKIT' and '@sveltejs/adapter-node' not in dep:blockers.add('SVELTEKIT_NODE_ADAPTER_REQUIRED')
        if kind=='NODE_ANGULAR':
            if '@angular/cli' not in dep or '@angular/ssr' in dep or 'angular.json' not in files:blockers.add('ANGULAR_STATIC_CLI_REQUIRED')
            else:
                cfg=load_object(files['angular.json']);projects=cfg.get('projects',{})
                if not isinstance(projects,dict) or len(projects)!=1 or next(iter(projects.values()),{}).get('projectType')!='application':blockers.add('ANGULAR_SINGLE_APPLICATION_REQUIRED')
        if kind=='NODE_ASTRO' and any(re.search(r"output\s*:\s*['\"](?:server|hybrid)['\"]",files.get(n,'')) for n in ('astro.config.mjs','astro.config.ts','astro.config.js')):blockers.add('ASTRO_SSR_UNSUPPORTED')
    else:
        entry=node_entry(files,manifest);body=files[entry]
        kind='NODE_HTTP' if NODE_HTTP.intersection(dep) or re.search(r'(?:\bcreateServer|\.listen)\s*\(',body) else 'NODE_CLI'
        # This path uses direct Node execution, never arbitrary start script args.
        if kind=='NODE_CLI' and not manifest.get('main') and not scripts.get('start'):blockers.add('CLI_ENTRYPOINT_NOT_DECLARED')
        probe='/' if kind=='NODE_HTTP' else None
    if dep:
        if 'package-lock.json' not in files:raise ValueError('NPM_LOCK_REQUIRED')
        lock=load_object(files['package-lock.json']);inputs=compile_inputs(manifest,lock)
        for row in lock['packages'].values():
            if not isinstance(row,dict):continue
            if row.get('optional') or row.get('devOptional'):continue
            e=row.get('engines',{})
            if isinstance(e,dict) and 'node' in e and node_constraint(version,e['node']) is not True:blockers.add('LOCKED_DEPENDENCY_NODE_ENGINE_UNSUPPORTED')
        return kind,entry,None,probe,{'kind':'NPM','locked_archives':len(inputs['downloads']),'input_sha256':digest(inputs),'npm_constraints':npm_constraints},inputs
    if framework:raise ValueError('FRAMEWORK_NOT_DECLARED')
    # Missing declarations are advisory blockers, never guessed registry names.
    builtins={'assert','async_hooks','buffer','child_process','cluster','console','constants','crypto','dgram','diagnostics_channel','dns','domain','events','fs','http','http2','https','module','net','os','path','perf_hooks','process','punycode','querystring','readline','repl','stream','string_decoder','sys','test','timers','tls','trace_events','tty','url','util','v8','vm','wasi','worker_threads','zlib','inspector','sqlite'}
    for p,body in files.items():
        if p.endswith(('.js','.mjs','.cjs')):
            for name in re.findall(r"(?:require\s*\(\s*|import\s*\(\s*|from\s+|import\s+)['\"]([^'\"]+)['\"]",body):
                if not name.startswith(('./','../')) and name.removeprefix('node:').split('/')[0] not in builtins:blockers.add('NODE_THIRD_PARTY_IMPORTS_WITHOUT_DECLARATION')
    return kind,entry,None,probe,{'kind':'NONE','npm_constraints':npm_constraints},None


def _python(files,paths,version,allow_inferred,blockers):
    if any(PurePosixPath(x).name in {'pip.conf','pip.ini','poetry.lock','uv.lock','Pipfile.lock'} for x in paths):blockers.add('PYTHON_ALTERNATE_RESOLVER_OR_CONFIG_REQUIRED')
    if '.python-version' in files and python_constraint(version,files['.python-version'].strip(),file_pin=True) is not True:blockers.add('PROVISIONED_PYTHON_VERSION_INCOMPATIBLE_OR_UNRESOLVED')
    if 'pyproject.toml' in files:
        project=tomllib.loads(files['pyproject.toml']).get('project',{})
        if isinstance(project,dict) and 'requires-python' in project and python_constraint(version,project['requires-python']) is not True:blockers.add('PROVISIONED_PYTHON_VERSION_INCOMPATIBLE_OR_UNRESOLVED')
    requirements,origin,issues=discover(files,version,allow_inferred);blockers.update(issues)
    entries,cli=python_entries(files);kind=entry=var=probe=None
    if 'manage.py' in files and 'django' in requirements:
        kind,entry,probe='PY_DJANGO','manage.py','/'
    elif len(entries)==1:
        kind,entry,var=entries[0];probe='/'
        if kind=='PY_ASGI' and 'uvicorn' not in requirements:
            if allow_inferred:requirements['uvicorn']='';origin+=' + INFERRED_ASGI_SERVER'
            else:blockers.add('ASGI_SERVER_DEPENDENCY_UNDECLARED')
        if kind=='PY_FLASK' and 'flask' not in requirements:blockers.add('FLASK_DEPENDENCY_UNDECLARED')
    elif entries:raise ValueError('PYTHON_ENTRYPOINT_AMBIGUOUS')
    elif 'streamlit' in requirements:
        candidates=[p for p,b in files.items() if p.endswith('.py') and '/' not in p and re.search(r'\b(?:import streamlit|from streamlit import)\b',b)]
        if len(candidates)!=1:raise ValueError('PYTHON_ENTRYPOINT_AMBIGUOUS')
        kind,entry,probe='PY_STREAMLIT',candidates[0],'/_stcore/health'
    elif len(cli)==1:kind,entry='PY_CLI',cli[0]
    else:raise ValueError('PYTHON_ENTRYPOINT_AMBIGUOUS_OR_MISSING')
    if not safe_path(entry):raise ValueError('ENTRYPOINT_PATH_UNSUPPORTED')
    if kind in {'PY_ASGI','PY_FLASK'} and any(not p.isidentifier() for p in entry[:-3].split('/')):raise ValueError('PYTHON_MODULE_PATH_UNSUPPORTED')
    if len(requirements)>25:raise ValueError('DIRECT_DEPENDENCY_LIMIT')
    return kind,entry,var,probe,{'kind':'PYTHON' if requirements else 'NONE','origin':origin,'requirements':dict(sorted(requirements.items()))},dict(sorted(requirements.items()))


def _other(family,files,paths,blockers):
    if family in {'static','php'}:
        if family=='php' and 'composer.json' in paths:blockers.add('COMPOSER_RESOLVER_UNIMPLEMENTED')
        name='index.php' if family=='php' else 'index.html'
        candidates=[x for x in ('public/'+name,name) if x in paths]
        if not candidates:raise ValueError('PUBLIC_ENTRYPOINT_UNRESOLVED')
        entry=candidates[0];return ('PHP_HTTP' if family=='php' else 'STATIC_HTTP'),entry,None,'/',{'kind':'NONE'},None
    rules={'native':({'main.c':'NATIVE_C','main.cpp':'NATIVE_CPP','main.cc':'NATIVE_CPP'},('.c','.cpp','.cc')),'jvm':({'Main.java':'JAVA_SINGLE'},('.java','.kt')),'go':({'main.go':'GO_SINGLE'},('.go',)),'rust':({'main.rs':'RUST_SINGLE'},('.rs',))}
    if family not in rules:raise ValueError('RUNTIME_ADAPTER_NOT_IMPLEMENTED')
    choices,suffix=rules[family];candidates=[n for n in choices if n in files]
    if len(candidates)!=1 or sum(p.endswith(suffix) for p in files)!=1:raise ValueError('SINGLE_FILE_TOOLCHAIN_ONLY')
    if any(p in paths for p in ('CMakeLists.txt','Makefile','meson.build','pom.xml','build.gradle','build.gradle.kts','go.mod','Cargo.toml')):blockers.add('BUILD_SYSTEM_OR_MODULE_RESOLVER_NOT_IMPLEMENTED')
    return choices[candidates[0]],candidates[0],None,None,{'kind':'NONE'},None


def compile_runtime(root,*,source=None,node_version='v24.22.3',node_versions=None,python_version='3.12.3',allow_inferred=False):
    root=Path(root);target(python_version)
    versions=list(node_versions) if node_versions is not None else [node_version]
    if not 1<=len(versions)<=8 or any(not isinstance(v,str) or not re.fullmatch(r'v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}',v) for v in versions):raise ValueError('NODE_TARGET_INVALID')
    versions=sorted(set(versions),key=lambda v:tuple(map(int,v[1:].split('.'))),reverse=True)
    base=plan_directory(root,source=source);scan=inspect(root)
    if digest(scan.digests)!=base['snapshot_inspection']['inspected_content_sha256'] or digest(scan.paths)!=base['snapshot_inspection']['inventory_sha256'] or sorted(scan.issues)!=base['snapshot_inspection']['issues']:raise ValueError('SOURCE_CHANGED')
    records=[];inputs={};components=base['components'];roots={c['root'] for c in components}
    for c in components:
        r=c['root'];prefix='' if r=='.' else r+'/'
        nested=[q+'/' for q in roots if q!=r and q.startswith(prefix)]
        files={p[len(prefix):]:v for p,v in scan.files.items() if p.startswith(prefix) and not any(p.startswith(q) for q in nested)}
        paths={p[len(prefix):] for p in scan.paths if p.startswith(prefix) and not any(p.startswith(q) for q in nested)}
        blockers=set();kind=entry=var=probe=None;dep={'kind':'UNRESOLVED'};private=None;chosen_node=versions[0]
        if r!='.' and not safe_path(r):blockers.add('COMPONENT_ROOT_UNSUPPORTED')
        if scan.issues:blockers.add('BOUNDED_SOURCE_SCAN_INCOMPLETE')
        if len([x for x in components if x['root']==r])>1:blockers.add('MIXED_ECOSYSTEM_ROOT_REQUIRES_REVIEW')
        # DB/cloud dependencies and unknown environment values are not auto-provisioned.
        services=set(c['service_hints'])
        if services-{'sqlite'}:blockers.add('EXTERNAL_OR_DATABASE_SERVICE_UNRESOLVED')
        if set(c['environment_variable_names'])-{'PORT','HOST','HOSTNAME','NODE_ENV','CI','NEXT_TELEMETRY_DISABLED','PYTHONUNBUFFERED'}:blockers.add('ENVIRONMENT_CONFIGURATION_UNRESOLVED')
        if any(PurePosixPath(p).name in {'.gitmodules','Dockerfile','Containerfile','compose.yml','compose.yaml','docker-compose.yml','docker-compose.yaml'} for p in scan.paths):blockers.add('DEPLOYMENT_OR_SUBMODULE_POLICY_UNRESOLVED')
        # Reject package config in ancestors too, independent of 8A ownership.
        if c['ecosystem']=='node' and any(PurePosixPath(p).name=='.npmrc' and (r=='.' or p.startswith(prefix) or str(PurePosixPath(p).parent) in {'.',*map(str,PurePosixPath(r).parents)}) for p in scan.paths):blockers.add('NPM_CONFIG_REQUIRES_REVIEW')
        try:
            if c['ecosystem']=='node':
                # Inherit declared version constraints from enclosing repository roots.
                ancestors=[str(p) for p in PurePosixPath(r).parents] if r!='.' else []
                inherited=[scan.files[(a+'/' if a!='.' else '')+n].strip() for a in ancestors for n in ('.nvmrc','.node-version') if (a+'/' if a!='.' else '')+n in scan.files]
                alternatives=[]
                for v in versions:
                    b=set(blockers)
                    candidate=_node(files,paths,v,b)
                    if any(node_constraint(v,constraint) is not True for constraint in inherited):b.add('PROVISIONED_NODE_VERSION_INCOMPATIBLE_OR_UNRESOLVED')
                    alternatives.append((v,b,candidate))
                    if not b:break
                # Prefer a compatible candidate; retain non-version blockers honestly.
                chosen_node,blockers,chosen=min(alternatives,key=lambda x:(sum('VERSION' in b or 'ENGINE' in b for b in x[1]),len(x[1])))
                kind,entry,var,probe,dep,private=chosen
            elif c['ecosystem']=='python':kind,entry,var,probe,dep,private=_python(files,paths,python_version,allow_inferred,blockers)
            else:kind,entry,var,probe,dep,private=_other(c['ecosystem'],files,paths,blockers)
        except (ValueError,KeyError,TypeError,RecursionError) as exc:
            msg=str(exc);blockers.add(msg if re.fullmatch(r'[A-Z][A-Z0-9_]{2,100}',msg) else 'RUNTIME_INPUT_UNSUPPORTED')
        # Raw source-defined command strings or secrets are not emitted.
        service_port=8765
        if kind=='NODE_HTTP' and entry in files:
            service_port,port_blocker=infer_node_port(files[entry])
            if port_blocker:blockers.add(port_blocker)
        row={'service_port':service_port,'id':c['id'],'root':r,'ecosystem':c['ecosystem'],'framework_hints':c['framework_hints'],'adapter':kind,'entrypoint':entry,'application_symbol':var,'probe_path':probe,'dependency':dep,'toolchain':chosen_node if kind and kind.startswith('NODE_') else python_version if kind and kind.startswith('PY_') else 'GUEST_PROVISIONED','optional_toolchain':kind in OPTIONAL,'service_hints':sorted(services),'blockers':sorted(blockers),'status':'BLOCKED' if blockers or not kind else 'REVIEWED_CANDIDATE','permission':False}
        records.append(row);inputs[c['id']]=private
    relation=base['relationship_hypotheses']; composition='SINGLE_COMPONENT' if len(records)==1 else 'INDEPENDENT_COMPONENTS_ONLY_RELATIONSHIPS_UNVERIFIED'
    result={'schema_version':'8D.1','source':base['source'],'phase8a_plan_sha256':base['plan_sha256'],'snapshot_inspection':base['snapshot_inspection'],'components':records,'relationships':relation,'composition':composition,'available_node_versions':versions,'target_python':python_version,'status':'CANDIDATES_AVAILABLE' if any(x['status']=='REVIEWED_CANDIDATE' for x in records) else 'BLOCKED_OR_UNSUPPORTED','runtime_permission':False,'automatic_untrusted_execution':False,'vm_launches':0,'limitations':['Planning does not establish runtime readiness.','Multi-component execution is independent, not integrated service validation.','Only provisioned, compatible runtimes; no automatic OS toolchain installation.','All execution requires existing operator-approved exact SHA.']}
    result['plan_sha256']=digest(result);return base,result,inputs
