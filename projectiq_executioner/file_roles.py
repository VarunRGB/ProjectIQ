"""File naming convention catalog: descriptive hints, never proof of behavior."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Iterable, Mapping


IMAGE_SUFFIXES={'.svg','.png','.jpg','.jpeg','.gif','.webp','.ico','.woff','.woff2','.ttf','.otf','.mp4','.mp3'}
STYLE_SUFFIXES={'.css','.scss','.sass','.less'}
SOURCE_SUFFIXES={'.js','.jsx','.ts','.tsx','.mjs','.cjs','.php','.py','.java','.kt','.cs','.go','.rb','.rs','.vue','.svelte','.dart','.swift','.cpp','.c','.sh','.ps1'}
MANIFEST_NAMES={'package.json','pyproject.toml','requirements.txt','composer.json','pom.xml','build.gradle','build.gradle.kts','go.mod','cargo.toml','gemfile','pubspec.yaml','angular.json','nuxt.config.ts','svelte.config.js'}
LOCK_NAMES={'package-lock.json','yarn.lock','pnpm-lock.yaml','bun.lock','bun.lockb','cargo.lock','composer.lock','poetry.lock','uv.lock','pipfile.lock','gemfile.lock'}
CONFIG_NAMES={'tsconfig.json','jsconfig.json','next.config.js','next.config.ts','next.config.mjs','vite.config.ts','vite.config.js','eslint.config.js','eslint.config.mjs','tailwind.config.js','tailwind.config.ts','postcss.config.js','postcss.config.mjs','.editorconfig','.prettierrc','pytest.ini','tox.ini','phpunit.xml','dockerfile','docker-compose.yaml','docker-compose.yml','compose.yaml','compose.yml'}


def catalog_file_roles(paths: Iterable[str], *, page_files: Iterable[str]=(), test_files: Iterable[str]=(),
                       route_files: Iterable[str]=(), http_client_files: Iterable[str]=(),
                       database_files: Iterable[str]=()) -> tuple[dict[str,list[str]],dict[str,int]]:
    """Classify common conventions without inspecting contents or inventing semantics.

    Multiple roles may apply to a file; any unmatched files get `other_file`.
    Lists use repository-relative paths only, no source excerpts or credentials.
    """
    pages=set(page_files)
    tests={p.split('#',1)[0] for p in test_files}
    routes=set(route_files)
    clients=set(http_client_files)
    databases=set(database_files)
    catalog: dict[str,list[str]]={}
    counts:Counter[str]=Counter()
    for p in sorted(paths):
        lower=p.lower(); bits=lower.split('/'); name=bits[-1]; ext=PurePosixPath(lower).suffix
        roles: set[str]=set()
        if lower.startswith('.github/workflows/') or lower.startswith('.gitlab-ci') or name=='jenkinsfile':
            roles.add('ci_workflow')
        if name.startswith('readme') or name.startswith('contributing') or name.startswith('license') or ext in {'.md','.rst','.adoc'}:
            roles.add('documentation')
        if name in LOCK_NAMES: roles.add('dependency_lockfile')
        if name in MANIFEST_NAMES: roles.add('dependency_manifest')
        if name in CONFIG_NAMES or name in {'.gitignore', '.dockerignore', 'config.py', 'config.php', 'settings.py', 'settings.php'} or name.endswith(('.config.js','.config.ts','.config.mjs','.config.json')) or name.startswith('.env'):
            roles.add('configuration')
        if p in tests or (name.startswith('test_') and ext=='.py') or name.endswith(('.test.ts','.test.tsx','.spec.ts','.spec.tsx','.test.js','.spec.js','.feature','.spec.rb','_test.go')) or re.search(r'(?:^|/)(?:test|tests|__tests__|e2e|spec|cypress)/',lower):
            roles.add('test')
        if p in pages or name in {'page.tsx','page.jsx','page.ts','page.js','+page.svelte'} or (ext in {'.html','.htm'} and 'public' in bits):
            roles.add('ui_page')
        if ('components' in bits or 'widgets' in bits) and ext in {'.tsx','.jsx','.vue','.svelte'}:
            roles.add('ui_component')
        if name in {'app.jsx', 'app.tsx', 'main.jsx', 'main.tsx', 'main.js', 'main.ts'} and ('src' in bits or len(bits)==1):
            roles.add('ui_app_entrypoint')
        if name in {'layout.tsx','layout.jsx','+layout.svelte','_app.tsx'} or ('layouts' in bits and ext in SOURCE_SUFFIXES):
            roles.add('ui_layout')
        if p in clients or (name in {'api.ts','api.js','http.ts','http.js','api_client.py','client.ts'} and ext in SOURCE_SUFFIXES):
            roles.add('http_client')
        if name == '__init__.py':
            roles.add('module_initializer')
        if p in routes or ((('routes' in bits or 'routers' in bits) and name != '__init__.py') and ext in SOURCE_SUFFIXES) or name in {'urls.py','+server.ts','+server.js'}:
            roles.add('api_or_route')
        if ('controllers' in bits or 'controller' in bits or 'handlers' in bits or name.endswith(('.controller.ts','.controller.js','controller.java','controller.cs'))) and ext in SOURCE_SUFFIXES:
            roles.add('controller_or_handler')
        if (('services' in bits and ext in SOURCE_SUFFIXES and 'ui_page' not in roles and 'ui_component' not in roles and name != '__init__.py') or name.endswith(('.service.ts','.service.js','.service.py'))):
            roles.add('service_logic')
        if (('models' in bits or 'entities' in bits or 'schemas' in bits or 'dto' in bits or 'types' in bits) and ext in SOURCE_SUFFIXES) or name in {'models.py', 'schemas.py', 'schema.py', 'types.py', 'models.ts'}:
            roles.add('model_or_type')
        if name in {'db.py','database.py','db.php','database.php','connection.py','database.ts','db.ts'}:
            roles.add('database_access')
        if name in {'security.py','auth.py','authentication.py'} and 'routes' not in bits and 'routers' not in bits:
            roles.add('authentication_logic')
        if 'deployment' in bits and (ext in {'.conf','.service','.json','.yaml','.yml'}):
            roles.add('deployment_config')
        if p in databases or name=='schema.prisma' or 'migrations' in bits or 'migration' in bits or ext=='.sql':
            roles.add('database_schema_or_migration')
        if name.startswith(('openapi.', 'swagger.')) or ext in {'.graphql','.gql','.proto'}:
            roles.add('api_contract')
        if ext in STYLE_SUFFIXES: roles.add('styling')
        if ext in IMAGE_SUFFIXES: roles.add('static_asset')
        if ext in SOURCE_SUFFIXES and not roles.intersection({'api_or_route','http_client','ui_page','ui_component','ui_layout','ui_app_entrypoint','controller_or_handler','service_logic','model_or_type','test','database_schema_or_migration','configuration','database_access','authentication_logic','module_initializer'}):
            roles.add('other_source')
        if not roles: roles.add('other_file')
        catalog[p]=sorted(roles)
        counts.update(roles)
    return catalog,dict(sorted(counts.items()))
