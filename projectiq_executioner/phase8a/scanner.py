"""Bounded, inert snapshot inspection. Never import, build or execute target code."""
from __future__ import annotations
import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES = 12000
MAX_DIRS = 3000
MAX_DEPTH = 18
MAX_FILE_BYTES = 512 * 1024
MAX_READ_BYTES = 24 * 1024 * 1024
IGNORE = {'.git', 'node_modules', 'vendor', '.venv', 'venv', '__pycache__', '.next',
          'dist', 'build', 'target', 'bin', 'obj', '.gradle', '.idea', '.pytest_cache',
          'coverage', '.cache', '.turbo', '.nuxt', '.svelte-kit', '.dart_tool', 'Pods'}
MANIFESTS = {'package.json','package-lock.json','npm-shrinkwrap.json','yarn.lock','pnpm-lock.yaml','bun.lock','bun.lockb',
             'pnpm-workspace.yaml','turbo.json','nx.json','lerna.json','requirements.txt','requirements-dev.txt',
             'pyproject.toml','poetry.lock','uv.lock','Pipfile','Pipfile.lock','setup.py','setup.cfg',
             'composer.json','composer.lock','artisan','pom.xml','build.gradle','build.gradle.kts','settings.gradle',
             'settings.gradle.kts','gradlew','gradlew.bat','go.mod','go.sum','Cargo.toml','Cargo.lock','Gemfile',
             'Gemfile.lock','config.ru','Rakefile','CMakeLists.txt','Makefile','meson.build','pubspec.yaml',
             'pubspec.lock','project.godot','Dockerfile','Containerfile','compose.yml','compose.yaml','docker-compose.yml',
             'docker-compose.yaml','Procfile','global.json','Package.swift','mix.exs','mix.lock','stack.yaml',
             'cabal.project','deno.json','deno.jsonc','deno.lock','angular.json','manage.py','next.config.js',
             'next.config.mjs','next.config.ts','vite.config.js','vite.config.ts','nuxt.config.ts','svelte.config.js',
             'firebase.json','serverless.yml','serverless.yaml','.gitmodules','.env.example','.env.sample','.env.template',
             '.nvmrc','.node-version','.python-version','rust-toolchain.toml','rust-toolchain','go.work','gradle.properties',
             'README.md','README.txt','readme.md','index.html','index.php','main.py','app.py','server.py'}
SUFFIXES={'.py','.js','.jsx','.ts','.tsx','.mjs','.cjs','.php','.java','.kt','.kts','.go','.rs','.rb','.cs',
          '.cpp','.cc','.c','.h','.hpp','.swift','.dart','.vue','.svelte','.html','.sql','.csproj','.fsproj',
          '.sln','.cabal','.ex','.exs','.scala','.sh','.ps1','.bat','.ipynb','.r','.jl','.gd'}
SECRET = re.compile(r'(?i)(?:^|[._-])(?:secret|credentials?|private[-_]?key|id_rsa|id_ed25519)(?:[._-]|$)')

@dataclass
class Scan:
    files: dict[str,str] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)
    digests: dict[str,str] = field(default_factory=dict)
    issues: set[str] = field(default_factory=set)
    skipped: int = 0
    bytes_read: int = 0


def _linked(p: Path) -> bool:
    st = p.lstat()
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st,'st_file_attributes',0) & 0x400)


def inspect(root: Path) -> Scan:
    root = Path(root).absolute()
    if not root.is_dir() or _linked(root):
        raise ValueError('INVALID_OR_LINKED_SOURCE_ROOT')
    # Reject linked ancestor paths too; local CLI is an operator-controlled input.
    for parent in root.parents:
        if _linked(parent): raise ValueError('LINKED_SOURCE_ANCESTOR')
    scan=Scan(); dirs=0; entries=0
    def onerror(_):scan.issues.add('DIRECTORY_UNREADABLE')
    for folder, children, names in os.walk(root, followlinks=False, onerror=onerror):
        dirs+=1
        if dirs>MAX_DIRS:
            scan.issues.add('DIRECTORY_LIMIT'); break
        base=Path(folder)
        rel=base.relative_to(root)
        children.sort(); names.sort()
        kept=[]
        for name in children:
            if name in IGNORE or name.startswith('.') or SECRET.search(name): continue
            if len(rel.parts)>=MAX_DEPTH:
                scan.issues.add('DEPTH_LIMIT'); continue
            try:
                if _linked(base/name): scan.issues.add('LINKED_PATH_SKIPPED'); continue
            except OSError:
                scan.issues.add('UNREADABLE_PATH'); continue
            kept.append(name)
        children[:]=kept
        for name in names:
            entries+=1
            if entries>MAX_FILES:
                scan.issues.add('FILE_LIMIT'); break
            p=base/name; key=p.relative_to(root).as_posix()
            if name.startswith('.env') and name not in {'.env.example','.env.sample','.env.template'}: continue
            if SECRET.search(name) or name.lower().endswith(('.pem','.key','.pfx','.p12','.keystore')): continue
            try:
                st=p.lstat()
                if _linked(p): scan.issues.add('LINKED_PATH_SKIPPED'); continue
                if not stat.S_ISREG(st.st_mode): scan.issues.add('NONREGULAR_PATH_SKIPPED'); continue
                if len(key)>350 or ':' in key or '\\' in key or any(ord(c)<32 for c in key): scan.issues.add('UNSAFE_PATH_SKIPPED'); continue
                scan.paths.append(key)
                if name not in MANIFESTS and p.suffix.lower() not in SUFFIXES and not name.startswith(('Dockerfile.','requirements-')): continue
                if st.st_size>MAX_FILE_BYTES or scan.bytes_read+st.st_size>MAX_READ_BYTES:
                    scan.issues.add('READ_LIMIT'); scan.skipped+=1; continue
                # O_NOFOLLOW where supported. Recheck identity before accepting bytes.
                fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)|getattr(os,'O_NONBLOCK',0))
                with os.fdopen(fd,'rb') as f:
                    before=os.fstat(f.fileno())
                    if not stat.S_ISREG(before.st_mode) or (before.st_dev,before.st_ino)!=(st.st_dev,st.st_ino):
                        scan.issues.add('SOURCE_CHANGED'); continue
                    data=f.read(MAX_FILE_BYTES+1)
                    after=os.fstat(f.fileno())
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or len(data)!=before.st_size:
                    scan.issues.add('SOURCE_CHANGED'); continue
                if len(data)>MAX_FILE_BYTES or scan.bytes_read+len(data)>MAX_READ_BYTES:
                    scan.issues.add('READ_LIMIT'); continue
                scan.bytes_read+=len(data)
                scan.digests[key]=hashlib.sha256(data).hexdigest()
                if b'\0' in data: continue
                try: scan.files[key]=data.decode('utf-8-sig')
                except UnicodeDecodeError: scan.issues.add('NON_UTF8_TEXT_SKIPPED')
            except OSError: scan.issues.add('UNREADABLE_PATH')
        if entries>MAX_FILES: break
    scan.paths.sort()
    return scan
