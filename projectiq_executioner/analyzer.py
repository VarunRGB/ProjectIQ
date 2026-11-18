"""General-purpose repository manifest builder using static, evidenced heuristics.

Phase 1 never imports, executes, installs, or compiles student code.
All model outputs are conservative: UNKNOWN is preferred to invented capabilities.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ is documented
    tomllib = None

from .indexer import SourceIndex
from .models import Evidence, Entrypoint, Feature, Finding, Manifest, RepositorySource, Route, Service, Technology, TestArtifact, SCHEMA_VERSION

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".php": "PHP",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "Sass",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".go": "Go",
    ".rs": "Rust", ".rb": "Ruby", ".cs": "C#", ".cpp": "C++", ".cc": "C++",
    ".cxx": "C++", ".hpp": "C++", ".h": "C/C++", ".c": "C",
    ".graphql": "GraphQL", ".gql": "GraphQL", ".proto": "Protocol Buffers", ".swift": "Swift",
    ".dart": "Dart", ".sql": "SQL", ".vue": "Vue SFC", ".svelte": "Svelte",
    ".ipynb": "Jupyter Notebook", ".sh": "Shell", ".ps1": "PowerShell",
}
CODE_EXTENSIONS = set(LANGUAGES) - {".css", ".scss", ".sass", ".html", ".htm", ".ipynb"}
JS_FRAMEWORKS = {
    "next": ("Next.js", "frontend"), "react": ("React", "frontend"),
    "vue": ("Vue", "frontend"), "nuxt": ("Nuxt", "frontend"),
    "@angular/core": ("Angular", "frontend"), "svelte": ("Svelte", "frontend"),
    "@sveltejs/kit": ("SvelteKit", "frontend"), "vite": ("Vite", "build"),
    "express": ("Express", "backend"), "fastify": ("Fastify", "backend"),
    "@nestjs/core": ("NestJS", "backend"), "koa": ("Koa", "backend"),
    "hono": ("Hono", "backend"),
    "react-router-dom": ("React Router", "routing"),
    "@angular/common": ("Angular", "frontend"),
    "@angular/common/http": ("Angular HttpClient", "http_client"),
    "ky": ("ky", "http_client"), "axios": ("Axios", "http_client"),
    "@nuxt/core": ("Nuxt", "frontend"),
    "@nestjs/common": ("NestJS", "backend"),
    "drizzle-orm": ("Drizzle ORM", "orm"),
    "@sveltejs/kit": ("SvelteKit", "frontend"), "mongoose": ("MongoDB", "database"),
    "mongodb": ("MongoDB", "database"), "pg": ("PostgreSQL", "database"),
    "mysql2": ("MySQL", "database"), "mysql": ("MySQL", "database"),
    "better-sqlite3": ("SQLite", "database"), "sqlite3": ("SQLite", "database"),
    "@prisma/client": ("Prisma", "orm"), "prisma": ("Prisma", "orm"),
    "sequelize": ("Sequelize", "orm"), "typeorm": ("TypeORM", "orm"),
    "redis": ("Redis", "database"),
    "jest": ("Jest", "test"), "vitest": ("Vitest", "test"),
    "@playwright/test": ("Playwright", "test"), "cypress": ("Cypress", "test"),
    "tailwindcss": ("Tailwind CSS", "styling"),
    "next-cloudinary": ("Cloudinary integration", "external_integration"),
    "mocha": ("Mocha", "test"), "supertest": ("Supertest", "test"),
}
PY_DEPS = {
    "flask": ("Flask", "backend"), "django": ("Django", "backend"),
    "fastapi": ("FastAPI", "backend"), "starlette": ("Starlette", "backend"),
    "streamlit": ("Streamlit", "frontend"), "gradio": ("Gradio", "frontend"),
    "sqlalchemy": ("SQLAlchemy", "orm"), "pymongo": ("MongoDB", "database"),
    "psycopg": ("PostgreSQL", "database"), "psycopg2": ("PostgreSQL", "database"),
    "mysql-connector-python": ("MySQL", "database"), "pymysql": ("MySQL", "database"),
    "pytest": ("pytest", "test"), "unittest": ("unittest", "test"),
    "celery": ("Celery", "worker"),
    "boto3": ("boto3 (AWS SDK)", "external_integration"),
    "pyjwt": ("PyJWT", "authentication"),
    "httpx": ("HTTPX", "http_client"), "requests": ("Requests", "http_client"),
    "litestar": ("Litestar", "backend"), "sanic": ("Sanic", "backend"),
}
TEST_FILES = {
    "pytest.ini": "pytest", "tox.ini": "tox", "phpunit.xml": "PHPUnit",
    "phpunit.xml.dist": "PHPUnit", "playwright.config.ts": "Playwright",
    "playwright.config.js": "Playwright", "jest.config.js": "Jest",
    "jest.config.ts": "Jest", "vitest.config.ts": "Vitest",
    "vitest.config.js": "Vitest", "cypress.config.ts": "Cypress",
    "cypress.config.js": "Cypress", ".rspec": "RSpec", "phpunit.xml.dist": "PHPUnit",
}
SETUP_NAMES = {
    "readme.md", "readme.rst", "readme.txt", "package.json", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock", "requirements.txt",
    "pyproject.toml", "pipfile", "poetry.lock", "uv.lock", "composer.json",
    "composer.lock", "dockerfile", "compose.yaml", "compose.yml", "docker-compose.yaml",
    "docker-compose.yml", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "go.mod", "cargo.toml",
    "gemfile", "pubspec.yaml", "makefile", "manage.py", "drizzle.config.ts",
    "schema.prisma", "prisma/schema.prisma", "astro.config.mjs",
    "nuxt.config.ts", "svelte.config.js", "angular.json", ".env.example",
    ".env.sample", ".env.template", ".python-version", ".nvmrc",
}
ENV_RE = [
    re.compile(r"\b(?:process\.env|import\.meta\.env|os\.environ(?:\.get)?|os\.getenv|System\.getenv)\s*(?:\.([A-Z][A-Z0-9_]+)|\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]|\[\s*['\"]([A-Z][A-Z0-9_]+)['\"])", re.I),
    re.compile(r"\b(?:getenv|env)\s*\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]", re.I),
    re.compile(r"\$_ENV\s*\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]\s*\]", re.I),
]
PASSWORD_LITERAL_RE = re.compile(
    r"(?:\$(?:db_)?(?:pass|password|passwd|pwd)|\b(?:DB_PASSWORD|MYSQL_PASSWORD|DATABASE_PASSWORD|DB_PASS)\b)\s*(?:=|=>|:)\s*['\"]([^'\"]{5,})['\"]",
    re.I,
)


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _ev(path: str, source: str, offset: int = 0, reason: str = "Static repository evidence") -> Evidence:
    return Evidence(path, _line(source, offset), reason)


def _clean_label(value: str, length: int = 115) -> str:
    # Descriptions are untrusted repository content. Keep as inert plain text.
    v = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    v = re.sub(r"[`*~<>]", "", v)
    v = re.sub(r"\s+", " ", v).strip(" :\t\r\n")
    v = re.sub(r"(?i)\b(password|token|secret|api[_-]?key)\b\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", v)
    return v[:length]


# A bounded, read-only recognizer for literal JavaScript/TypeScript HTTP calls.
# It deliberately does not evaluate expressions, execute code, or infer responses.
def _js_call_args(source: str, open_paren: int, limit: int = 1800) -> str | None:
    if open_paren >= len(source) or source[open_paren] != "(":
        return None
    depth = 1
    quote = None
    escaped = False
    for i in range(open_paren + 1, min(len(source), open_paren + limit)):
        ch = source[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[open_paren + 1:i]
    return None


def _optional_query_template(args: str) -> str | None:
    """Recognize a static path with an optional dynamic query string.

    Example: `/plants${category ? `?category=${category}` : ""}` has a
    reachable `/plants` branch. Unknown dynamic URL suffixes stay unknown.
    This is deliberately narrow and never evaluates untrusted expressions.
    """
    m = re.match(r"\s*`(?P<base>/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)\$\{(?P<expr>.+)\}`\s*(?:,|$)",
                 args, re.S)
    if not m:
        return None
    expr = m.group("expr").strip()
    identifier = r"[A-Za-z_$][\w.$]*"
    query = r"`\?[A-Za-z_][^`]*`"
    empty = r"(?:\"\"|''|``)"
    choice_a = rf"{identifier}\s*\?\s*{query}\s*:\s*{empty}"
    choice_b = rf"{identifier}\s*\?\s*{empty}\s*:\s*{query}"
    if re.fullmatch(choice_a, expr, re.S) or re.fullmatch(choice_b, expr, re.S):
        return m.group("base")
    return None


def _http_literal(args: str) -> str | None:
    """Accept only literal URLs/path templates with conservative placeholders.

    Dynamic query templates or concatenated expressions have unknown URLs and
    must NOT become fabricated test endpoints.
    """
    m = re.match(r"\s*(['\"`])", args)
    if not m:
        return None
    q = m.group(1)
    begin = m.end()
    if q == "`":
        # Support simple interpolations in template strings, but not nested
        # backticks/complex conditional template expressions.
        match = re.match(r"((?:[^`\\]|\\.)*)`", args[begin:], re.S)
        if not match:
            return _optional_query_template(args)
        value = match.group(1)
        if "`" in value:
            return None
        # Do not turn suffix expressions such as /download${suffix} into
        # fabricated paths like /download:param. A variable might be a query
        # string, a filename suffix, or something entirely different.
        if re.search(r"[A-Za-z0-9_-]\$\{[A-Za-z_$][\w.$]*\}", value):
            return None
        value = re.sub(r"\$\{[A-Za-z_$][\w.$]*\}", ":param", value)
        if "${" in value:
            return _optional_query_template(args)
    else:
        match = re.match(r"((?:[^" + re.escape(q) + r"\\]|\\.)*)" + re.escape(q), args[begin:], re.S)
        if not match:
            return None
        value = match.group(1)
    # Inert URL/path labels only; never include credentials, query parameters,
    # fragments, or raw arbitrary expressions from a student's project.
    if value.startswith("https://") or value.startswith("http://"):
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        return (parsed.path or "/").split("?", 1)[0].split("#", 1)[0]
    if not value.startswith(("/", "./", "../")):
        return None
    value = value.split("?", 1)[0].split("#", 1)[0]
    if len(value) > 220 or any(x in value for x in ("<", ">", "\n", "\r")):
        return None
    return value


def _http_literal_variants(args: str, source: str = "") -> list[str]:
    """Expand one finite literal ternary inside an otherwise literal URL.

    E.g. `/auth/${mode === 'login' ? 'login' : 'register'}` is demonstrably
    one of two API paths. This never runs code or assumes which branch is taken.
    Arbitrary expressions return no candidates.
    """
    literal = _http_literal(args)
    if literal is not None:
        return [literal]
    m = re.match(r"\s*`([^`\r\n]{1,250})`\s*(?:,|$)", args, re.S)
    if not m:
        return []
    value = m.group(1)
    # A `const suffix = condition ? `?key=...` : ''` can alter only the
    # query string, not the endpoint path. Require a literal query branch,
    # a literal empty branch, and an immutable local binding.
    suffix = re.search(r"\$\{([A-Za-z_$][\w$]*)\}$", value)
    if suffix and source:
        name = suffix.group(1)
        # A semicolon is optional in JS. Require the query-only binding to
        # finish at a real statement boundary, not in the middle of an
        # unrelated expression. The binding must precede the call site.
        opt = re.compile(
            r"\bconst\s+" + re.escape(name) +
            r"\s*=\s*[^;\n]{1,160}\?\s*`\?[A-Za-z_][^`\r\n]*`"
            r"\s*:\s*(?:''|\"\")\s*(?:;|(?=\r?\n)|$)"
        )
        if opt.search(source):
            parsed = _http_literal('`' + value[:suffix.start()] + '`')
            return [parsed] if parsed else []
    pattern = re.compile(r"\$\{\s*[A-Za-z_$][\w.$]*\s*(?:===|==|!==|!=)\s*['\"][A-Za-z0-9_-]+['\"]\s*\?\s*['\"]([A-Za-z0-9_-]+)['\"]\s*:\s*['\"]([A-Za-z0-9_-]+)['\"]\s*\}")
    found = list(pattern.finditer(value))
    if len(found) != 1:
        return []
    match = found[0]
    routes = []
    for branch in (match.group(1), match.group(2)):
        candidate = value[:match.start()] + branch + value[match.end():]
        parsed = _http_literal('`' + candidate + '`')
        if parsed and parsed not in routes:
            routes.append(parsed)
    return routes


def _next_page_url(path: str) -> str | None:
    """Map Next.js App/Pages router source paths to candidate browser URLs."""
    m = re.fullmatch(r"(?:(?:apps|packages)/[^/]+/)?(?:src/)?app/(.+)/page\.(?:js|jsx|ts|tsx)", path)
    if m:
        segments = m.group(1).split("/")
    elif re.fullmatch(r"(?:(?:apps|packages)/[^/]+/)?(?:src/)?app/page\.(?:js|jsx|ts|tsx)", path):
        segments = []
    else:
        m = re.fullmatch(r"(?:(?:apps|packages)/[^/]+/)?(?:src/)?pages/(.+)\.(?:js|jsx|ts|tsx)", path)
        if not m:
            return None
        rel = m.group(1)
        if rel.startswith("api/") or rel.startswith("_"):
            return None
        segments = rel.split("/")
        if segments[-1] == "index":
            segments.pop()
    parts = []
    for part in segments:
        if re.fullmatch(r"\([^/]+\)", part):
            continue  # Next.js route groups are not URL segments.
        if part.startswith("@"):
            return None  # Parallel/intercepted routes need framework context.
        if re.fullmatch(r"\[\[\.\.\.[^]]+\]\]", part):
            return None  # Optional catch-all cannot be reduced to one static URL.
        dynamic = re.fullmatch(r"\[(\.\.\.)?([^]]+)\]", part)
        if dynamic:
            parts.append(":" + dynamic.group(2) + ("*" if dynamic.group(1) else ""))
        else:
            parts.append(part)
    return "/" + "/".join(parts)


def _api_contract_path(path: str) -> str | None:
    """OpenAPI placeholders are *path parameters*, never JS expressions."""
    if not path.startswith('/') or len(path)>220 or any(c in path for c in '\r\n<>$?#'):
        return None
    path = re.sub(r"\{([A-Za-z_]\w*)\}",r":\1",path)
    if re.search(r"[{}]",path) or ".." in path.split('/'): return None
    return path


def _inferred_page_label(url: str) -> str:
    return "UI page at " + url


class Analyzer:
    def __init__(self, index: SourceIndex, source: RepositorySource):
        self.index = index
        self.source = source
        self.files = sorted(index.files)
        self.technologies: dict[tuple[str, str], Technology] = {}
        self.routes: dict[tuple[str, str, str], Route] = {}
        self.features: dict[str, Feature] = {}
        self.tests: dict[str, TestArtifact] = {}
        self.pages: set[str] = set()
        self.entrypoints: dict[str, Entrypoint] = {}
        self.compose_services: dict[str, Service] = {}
        self.db_schemas: set[str] = set()
        self.setup: set[str] = set()
        self.scripts: dict[str, dict[str, str]] = {}
        self.env_vars: set[str] = set()
        self.env_var_details: dict[str, dict[str, object]] = {}
        self.api_links: list[dict[str, str]] = []
        self.findings: list[Finding] = []
        self.texts: dict[str, str | None] = {}
        self.truncated = False
        self.external_api_evidence: list[Evidence] = []
        self._seen_external_evidence: set[str] = set()
        self.unresolved_api_sites: list[dict[str, object]] = []

    def read(self, path: str) -> str | None:
        if path not in self.texts:
            self.texts[path] = self.index.read(path)
        return self.texts[path]

    def tech(self, name: str, category: str, path: str, text: str, offset: int = 0, confidence: str = "high") -> None:
        key = (name.lower(), category)
        e = _ev(path, text, offset, "Dependency, manifest, or source marker supports technology detection")
        if key not in self.technologies:
            self.technologies[key] = Technology(name, category, confidence, [e])
        elif not any(x.path == path and x.line == e.line for x in self.technologies[key].evidence):
            self.technologies[key].evidence.append(e)

    def route(self, value: str, method: str, kind: str, path: str, text: str, offset: int, confidence: str) -> None:
        val = value.strip()[:220]
        if not val or any(x in val for x in ("${", "<", "{", "}")):
            return
        key = (val, method, kind)
        e = _ev(path, text, offset, "Static route or HTTP client call")
        if key not in self.routes:
            self.routes[key] = Route(val, method, kind, confidence, [e])
        elif not any(x.path == path and x.line == e.line for x in self.routes[key].evidence):
            self.routes[key].evidence.append(e)

    def entrypoint(self, path: str, kind: str, confidence: str = "medium") -> None:
        if path not in self.entrypoints:
            self.entrypoints[path] = Entrypoint(path, kind, confidence, [Evidence(path.split("#",1)[0], 1, "Entrypoint or script-name convention")])

    def feature(self, name: str, origin: str, path: str, text: str, offset: int, confidence: str = "medium") -> None:
        name = _clean_label(name)
        key = name.lower()
        if len(name) < 4 or len(name) > 115:
            return
        # README bullet groups such as "Users can:" and "Admins can:" are
        # headings, not assertions. Do not turn them into fake test targets.
        if re.fullmatch(r"(?:[a-z][a-z -]{0,45})\s+(?:can|may|should|must)\s*:?", name, re.I):
            return
        if key not in self.features:
            self.features[key] = Feature(name, origin, confidence, [_ev(path, text, offset, "Documented feature or static implementation cue")])

    def _metadata(self) -> None:
        for p in self.files:
            name = Path(p).name.lower()
            if name in SETUP_NAMES or (".github/workflows/" in "/" + p.lower()):
                self.setup.add(p)
            if name == "dockerfile" or name.startswith("dockerfile."):
                self.tech("Docker (declared)", "orchestration", p, p, confidence="medium")
            if name.endswith(".sql") or name in {"schema.prisma"}:
                self.db_schemas.add(p)
            if name in {"swagger.json", "openapi.json", "openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml"}:
                self.setup.add(p)
            if name in {"pnpm-lock.yaml", "pnpm-workspace.yaml"}:
                self.tech("pnpm", "package_manager", p, p)
            elif name in {"yarn.lock"}:
                self.tech("Yarn", "package_manager", p, p)
            elif name in {"package-lock.json", "npm-shrinkwrap.json"}:
                self.tech("npm", "package_manager", p, p)
            elif name in {"bun.lockb", "bun.lock"}:
                self.tech("Bun", "package_manager", p, p)
            elif name in {"poetry.lock"}:
                self.tech("Poetry", "package_manager", p, p)
            elif name in {"uv.lock"}:
                self.tech("uv", "package_manager", p, p)
            elif name in {"composer.json"}:
                self.tech("Composer", "package_manager", p, p)
            elif name in {"cargo.toml"}:
                self.tech("Cargo", "package_manager", p, p)
            elif name == "go.mod":
                self.tech("Go Modules", "package_manager", p, p)
            if name in TEST_FILES:
                self.tests[p] = TestArtifact(p, "configuration", TEST_FILES[name])
            if re.search(r"(?:^|/)(?:[^/]+\.(?:feature|e2e-spec\.[jt]s|cy\.[jt]sx?|spec\.rb|test\.dart)|spec_[^/]+\.py|Test[^/]+\.java)$", p, re.I):
                self.tests[p] = TestArtifact(p, "test_source", self._guess_test_framework(p))
            if re.search(r"(?:^|/)(?:tests?|__tests__|spec|e2e|cypress)/", p, re.I) and Path(p).suffix.lower() in CODE_EXTENSIONS:
                self.tests[p] = TestArtifact(p, "test_source", self._guess_test_framework(p))
            elif (re.search(r"(?:^test_.*\.py$|_test\.py$|_test\.go$|Test\.java$|Tests\.cs$|\.(?:test|spec)\.[cm]?[jt]sx?$)", name, re.I)
                  and Path(p).suffix.lower() in CODE_EXTENSIONS):
                self.tests[p] = TestArtifact(p, "test_source", self._guess_test_framework(p))
            if p.lower().endswith((".html", ".htm")) and (any(("/"+seg+"/") in ("/"+p.lower()) for seg in
                    ("public", "templates", "views", "frontend", "web", "client", "ui", "static")) or "/" not in p):
                self.pages.add(p)
                self.entrypoint(p, "web_page")
            if re.search(r"(?:^|/)(?:pages|app)/.*(?:page|index)\.(?:jsx?|tsx?|vue|svelte)$", p):
                self.pages.add(p)
                self.entrypoint(p, "web_page")
            if name in {"manage.py", "artisan", "wsgi.py", "asgi.py", "index.php", "server.js", "server.ts", "app.py", "main.py", "main.go", "main.rs", "program.cs", "dockerfile"}:
                self.entrypoint(p, "startup_candidate")

    @staticmethod
    def _guess_test_framework(path: str) -> str | None:
        p = path.lower()
        if p.endswith(".py"): return "pytest/unittest (unconfirmed)"
        if p.endswith(".go"): return "Go test"
        if p.endswith(".php"): return "PHPUnit (unconfirmed)"
        if p.endswith(".java"): return "JUnit (unconfirmed)"
        if "playwright" in p or "e2e" in p: return "Playwright/Cypress (unconfirmed)"
        if p.endswith((".ts", ".tsx", ".js", ".jsx")): return "Jest/Vitest (unconfirmed)"
        return None

    def _package_manifests(self) -> None:
        for p in self.files:
            name = Path(p).name.lower()
            s = self.read(p) if name in {"package.json", "composer.json", "requirements.txt", "pyproject.toml", "go.mod", "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "pubspec.yaml", "gemfile"} or name.endswith(".csproj") else None
            if s is None: continue
            if name == "package.json" or name == "composer.json":
                try: data = json.loads(s)
                except ValueError:
                    self.findings.append(Finding("MANIFEST_PARSE_FAILED", "warning", f"Cannot parse {name} as JSON.", [_ev(p,s)]))
                    continue
                if not isinstance(data, dict): continue
                deps = {}
                for field in ("dependencies", "devDependencies", "peerDependencies", "require", "require-dev"):
                    if isinstance(data.get(field), dict): deps.update(data[field])
                for dep in deps:
                    info = JS_FRAMEWORKS.get(dep.lower())
                    if info: self.tech(*info, p, s, s.find('"' + dep + '"'))
                    elif dep.lower() in {"laravel/framework", "symfony/framework-bundle"}:
                        self.tech("Laravel" if dep.lower().startswith("laravel") else "Symfony", "backend", p, s)
                    elif dep.lower().startswith("phpunit/"):
                        self.tech("PHPUnit", "test", p, s)
                scripts = data.get("scripts")
                if isinstance(scripts, dict):
                    # Script bodies may contain embedded tokens or credentials; keep names only.
                    # Phase 2 will inspect commands in a restricted, isolated planner.
                    self.scripts[p] = {str(k): "[untrusted command withheld]" for k,v in scripts.items() if isinstance(v, str)}
                    for key in self.scripts[p]:
                        if key.lower() in {"start", "dev", "serve", "preview", "build", "test"}:
                            self.entrypoint(p + "#scripts." + key, "declared_script")
                    if name == "package.json" and "test" in scripts:
                        self.tests[p + "#scripts.test"] = TestArtifact(p + "#scripts.test", "test_command_declared", None)
                if name == "package.json":
                    self.tech("Node.js", "runtime", p, s)
                    if not any(Path(x).name.lower() in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb"} for x in self.files):
                        self.tech("npm (inferred)", "package_manager", p, s, confidence="medium")
            elif name == "requirements.txt":
                for m in re.finditer(r"^\s*([A-Za-z][\w.-]*)\s*(?:[=<>!~\[;#\n]|$)", s, re.M):
                    dep = m.group(1).lower().replace("_", "-")
                    if dep in PY_DEPS: self.tech(*PY_DEPS[dep], p, s, m.start())
                self.tech("pip", "package_manager", p, s)
            elif name == "pyproject.toml" and tomllib:
                try: data = tomllib.loads(s)
                except ValueError:
                    self.findings.append(Finding("MANIFEST_PARSE_FAILED", "warning", "Invalid pyproject.toml.", [_ev(p,s)]))
                    continue
                deps = data.get("project", {}).get("dependencies", []) if isinstance(data.get("project"), dict) else []
                for d in deps:
                    if isinstance(d, str):
                        dep = re.split(r"[<>=!~ ;\[]", d, maxsplit=1)[0].lower().replace("_", "-")
                        if dep in PY_DEPS: self.tech(*PY_DEPS[dep], p, s)
                tool = data.get("tool", {})
                if "poetry" in tool: self.tech("Poetry", "package_manager", p, s)
                if "pytest" in tool: self.tech("pytest", "test", p, s)
            elif name == "go.mod":
                self.tech("Go", "runtime", p, s)
            elif name == "cargo.toml":
                self.tech("Rust", "runtime", p, s)
            elif name == "gemfile":
                self.tech("Ruby", "runtime", p, s)
                if "rails" in s.lower(): self.tech("Ruby on Rails", "backend", p, s)
            elif name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
                self.tech("Java/JVM", "runtime", p, s)
                if "spring-boot" in s.lower() or "org.springframework.boot" in s:
                    self.tech("Spring Boot", "backend", p, s)
            elif name.endswith(".csproj"):
                self.tech(".NET", "runtime", p, s)
                if "Microsoft.NET.Sdk.Web" in s:
                    self.tech("ASP.NET Core", "backend", p, s)
            elif name == "pubspec.yaml":
                if re.search(r"^\s*flutter\s*:", s, re.M): self.tech("Flutter", "frontend", p, s)

    def _pydantic_settings_fields(self, path: str, source: str) -> None:
        """Discover Pydantic BaseSettings names and static default presence.

        Never expose values. This only handles directly annotated fields in
        literal BaseSettings class declarations, not inherited/custom aliases.
        """
        for cls in re.finditer(r"(?m)^(?P<indent>[ \t]*)class\s+\w+\s*\([^\n)]*\bBaseSettings\b[^\n)]*\)\s*:", source):
            base_indent = len(cls.group('indent').expandtabs(4))
            block = []
            offset = cls.end()
            for line in source[cls.end():].splitlines(keepends=True):
                stripped = line.strip()
                indent = len(line[:len(line) - len(line.lstrip())].expandtabs(4))
                if stripped and indent <= base_indent:
                    break
                block.append((offset, line))
                offset += len(line)
            block_text = ''.join(line for _, line in block)
            prefix_match = re.search(r"\benv_prefix\s*=\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", block_text)
            prefix = prefix_match.group(1) if prefix_match else ''
            for line_offset, line in block:
                # Only class-level annotated declarations; methods/properties,
                # local fields and computed accessors are not env vars.
                indent = len(line[:len(line) - len(line.lstrip())].expandtabs(4))
                if indent != base_indent + 4:
                    continue
                field = re.match(r"\s*([a-zA-Z_][\w]*)\s*:\s*([^#\n]+)", line)
                if not field or field.group(1).startswith('_'):
                    continue
                name = field.group(1)
                if name in {'model_config', 'Config'}:
                    continue
                declaration = field.group(2)
                # Avoid treating annotations in inline methods as fields.
                if declaration.lstrip().startswith(('ClassVar[', 'Callable[')):
                    continue
                env_name = (prefix + name).upper()
                if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}', env_name):
                    continue
                # '=' after the annotation is a default expression, but an
                # annotated Field(...) may express a required value as (...).
                # The first top-level assignment supplies a default, but
                # keyword arguments *inside* Field(...) are metadata, not a
                # default. Field(validation_alias='X') is still required.
                has_default = bool(re.search(r"\s=\s", declaration))
                call = re.search(r"\bField\s*\(\s*([^)]*)", declaration)
                if call:
                    inner = call.group(1)
                    if re.search(r"\bdefault_factory\s*=", inner):
                        has_default = True
                    elif re.search(r"\bdefault\s*=\s*\.\.\.", inner):
                        has_default = False
                    elif re.search(r"\bdefault\s*=", inner):
                        has_default = True
                    elif re.match(r"(?:\.\.\.|[A-Za-z_][\w]*\s*=|$)", inner):
                        has_default = False
                    else:
                        # A positional first argument to Field is the default.
                        has_default = True
                # An explicit literal alias overrides the field spelling.
                alias = re.search(r"\b(?:validation_alias|alias)\s*=\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", declaration)
                if alias:
                    env_name = alias.group(1)  # alias spelling is intentional
                self.env_vars.add(env_name)
                self.env_var_details[env_name] = {
                    'required_hint': not has_default,
                    'default_present': has_default,
                    'origin': 'pydantic_base_settings',
                    'path': path,
                    'line': _line(source, line_offset),
                }

    @staticmethod
    def _canonical_api_path(value: str) -> str | None:
        """Normalize only syntactically comparable path shapes, never hosts."""
        path = value.split('?', 1)[0].split('#', 1)[0]
        if not path.startswith(('/', './', '../')) and not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', path):
            return None
        while path.startswith('../') or path.startswith('./'):
            path = path.partition('/')[2]
        parts = path.strip('/').split('/')
        if any(x in ('', '..', '.') for x in parts):
            return '/' if path == '/' else None
        normalized = ['{parameter}' if re.fullmatch(r':(?:[A-Za-z_][\w]*)(?:\*)?', x) else x for x in parts]
        return '/' + '/'.join(normalized)

    def _match_api_routes(self) -> None:
        """Cross-reference client/server observations; does not prove behavior."""
        server_kinds = {'declared_http_route', 'file_based_route', 'file_endpoint'}
        servers = [r for r in self.routes.values() if r.kind in server_kinds]
        clients = [r for r in self.routes.values() if r.kind == 'client_http_call']
        self.api_links = []
        for client in clients:
            # A file known to use an external base URL is not evidence that
            # matching local route spellings refer to the same service.
            if any(e.path in self._seen_external_evidence for e in client.evidence):
                continue
            canon = self._canonical_api_path(client.path)
            if canon is None:
                continue
            matches = [r for r in servers
                       if self._canonical_api_path(r.path) == canon
                       and (r.method == client.method or r.method == 'UNKNOWN')]
            # Ambiguous server candidates must not be silently resolved.
            if len(matches) != 1:
                continue
            server = matches[0]
            self.api_links.append({
                'client_path': client.path, 'client_method': client.method,
                'client_source': client.evidence[0].path,
                'server_path': server.path, 'server_method': server.method,
                'server_source': server.evidence[0].path,
                'server_kind': server.kind,
                'confidence': ('medium' if server.method == 'UNKNOWN' or server.kind == 'file_endpoint' else 'high'),
                'status': ('method_unverified' if server.method == 'UNKNOWN' else 'static_match_only'),
            })
        self.api_links.sort(key=lambda x: (x['server_path'], x['client_method'], x['client_path']))

    def _source_markers(self) -> None:
        # Bounded per-file reads; static inspection only, no parser/eval/import of student code.
        candidates = [p for p in self.files if Path(p).suffix.lower() in CODE_EXTENSIONS | {".html", ".htm", ".sql", ".yml", ".yaml"}]
        max_inspect = 2400
        if len(candidates) > max_inspect:
            self.truncated = True
        for p in candidates[:max_inspect]:
            s = self.read(p)
            if s is None: continue
            suffix = Path(p).suffix.lower()
            low = s.lower()
            if suffix == ".php":
                self.tech("PHP", "runtime", p, s)
                if "new pdo" in low and "mysql:" in low: self.tech("MySQL", "database", p, s, low.find("mysql:"))
                if "mysqli" in low: self.tech("MySQL", "database", p, s, low.find("mysqli"))
                if "session_start" in low: self.tech("PHP sessions", "authentication", p, s, low.find("session_start"))
                if (re.search(r"(?:^|/)(?:api|auth|logic|routes|controllers)/", p, re.I)
                    or "content-type: application/json" in low) and not re.search(r"(?:^|/)routes/[^/]+\.php$",p,re.I) and "route::" not in low:
                    # A file-backed PHP endpoint exists, but its public URL depends on web-server mapping.
                    method = "UNKNOWN"
                    if "php://input" in s or "$_post" in low: method = "POST"
                    m = re.search(r"REQUEST_METHOD[^\n]{0,110}['\"](GET|POST|PUT|DELETE|PATCH)['\"]", s, re.I)
                    if m: method = m.group(1).upper()
                    self.route(p, method, "file_endpoint", p, s, 0, "medium" if not m else "high")
                    self.entrypoint(p, "php_file_endpoint")
            elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
                # Framework server route declarations are handled by conventions.py.
                # Recognize local wrapper functions only when they demonstrably
                # delegate to fetch(). This handles common TS API-client modules.
                wrappers = set()
                # Parse wrapper argument lists with balanced parentheses: defaults
                # such as token=localStorage.getItem(KEY) are very common.
                wrapper_prefixes: dict[str, str] = {}
                for fn in re.finditer(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(?:<[^>\n]+>)?\s*\(", s):
                    args = _js_call_args(s, fn.end() - 1)
                    if args is None or not re.search(r"\b(?:endpoint|path|url)\b", args):
                        continue
                    ending = fn.end() + len(args) + 1
                    brace = s.find("{", ending, ending + 250)
                    if brace < 0 or not re.fullmatch(r"\s*(?::[^{}\n]+)?\s*", s[ending:brace]):
                        continue
                    body_prefix = s[brace + 1: brace + 2000]
                    if not re.search(r"\bfetch\s*\(", body_prefix):
                        continue
                    wrappers.add(fn.group(1))
                    # Literal prefix resolution, not arbitrary JS evaluation.
                    base = re.search(r"\bfetch\s*\(\s*`\$\{([A-Za-z_$][\w$]*)\}\$\{(path|endpoint|url)\}`", body_prefix)
                    if base and re.search(r"\b" + re.escape(base.group(2)) + r"\b", args):
                        definition = re.search(r"\b(?:const|let)\s+" + re.escape(base.group(1)) + r"\s*=\s*(['\"])(/[^'\"\r\n]*)\1", s)
                        if definition:
                            wrapper_prefixes[fn.group(1)] = definition.group(2).rstrip('/')
                # Detect external API dependencies by *usage*, not by README claims.
                # Only evidence pointers are stored; never copy a URL or token.
                variable_urls = set()
                for url_match in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*['\"](https?://[^'\"\s]+)['\"]", s):
                    var = url_match.group(1)
                    if re.search(r"\b" + re.escape(var) + r"\b", s[url_match.end():]) and re.search(r"\b(?:fetch|axios|request)\s*\(", s):
                        variable_urls.add(url_match.start())
                for at in sorted(variable_urls):
                    if p not in self._seen_external_evidence:
                        self.external_api_evidence.append(_ev(p, s, at, "External HTTP base URL referenced by source; value deliberately withheld"))
                        self._seen_external_evidence.add(p)
                # Calls to fetch(), proven in-file wrappers, or HTTP client methods.
                # Unknown expressions are intentionally omitted, not guessed.
                call_name = r"(?:fetch|" + "|".join(re.escape(n) for n in sorted(wrappers)) + r")" if wrappers else r"fetch"
                # Typed calls such as request<Result>(...) use the same URL
                # semantics as untyped calls; the generic is not executable.
                for m in re.finditer(r"\b(" + call_name + r")\s*(?:<[^()\n]{1,200}>)?\s*\(", s):
                    args = _js_call_args(s, m.end() - 1)
                    if args is None:
                        continue
                    urls = _http_literal_variants(args, s[:m.start()])
                    if not urls:
                        continue
                    if re.match(r"\s*['\"`]https?://", args) and p not in self._seen_external_evidence:
                        self.external_api_evidence.append(_ev(p, s, m.start(), "External HTTP call observed; URL value deliberately withheld"))
                        self._seen_external_evidence.add(p)
                    method_match = re.search(r"\bmethod\s*:\s*['\"`](GET|POST|PUT|PATCH|DELETE)['\"`]", args, re.I)
                    for url in urls:
                        if m.group(1) in wrapper_prefixes and url.startswith('/'):
                            url = wrapper_prefixes[m.group(1)] + url
                        self.route(url, method_match.group(1).upper() if method_match else "GET",
                                   "client_http_call", p, s, m.start(), "medium")
                # axios.get('/x'), api.post('/y') if the alias is a local axios client.
                axios_aliases = {"axios"}
                for m in re.finditer(r"\b(?:const|let)\s+(\w+)\s*=\s*axios\.create\s*\(", s):
                    axios_aliases.add(m.group(1))
                if "axios" in s:
                    alias_re = "|".join(re.escape(alias) for alias in sorted(axios_aliases))
                    for m in re.finditer(r"\b(?:" + alias_re + r")\s*\.\s*(get|post|put|patch|delete)\s*\(", s):
                        args = _js_call_args(s, m.end()-1)
                        url = _http_literal(args) if args is not None else None
                        if url:
                            self.route(url, m.group(1).upper(), "client_http_call", p, s, m.start(), "medium")
                if "axios" in s and "axios.create" in s and p not in self._seen_external_evidence:
                    if re.search(r"\bbaseURL\s*:\s*['\"]https?://", s):
                        self.external_api_evidence.append(_ev(p, s, 0, "External axios base URL; value deliberately withheld"))
                        self._seen_external_evidence.add(p)
                if "express(" in s and "Express" not in {t.name for t in self.technologies.values()}:
                    self.tech("Express", "backend", p, s, s.find("express("), "medium")
                if "http.createServer" in s or "http2.createSecureServer" in s:
                    self.tech("Node.js HTTP server", "backend", p, s, confidence="medium")
                if suffix in {".tsx", ".jsx"} and ("from 'react'" in s or 'from "react"' in s):
                    self.tech("React", "frontend", p, s, confidence="medium")
            elif suffix == ".py":
                self._pydantic_settings_fields(p, s)
                # Concrete SDK/configuration usage, not guessing a service from a
                # filename or treating all boto3 dependencies as S3.
                s3 = re.search(r"\bboto3\s*\.\s*(?:client|resource)\s*\(\s*['\"]s3['\"]", s)
                if s3: self.tech("Amazon S3", "external_integration", p, s, s3.start())
                sqlite = re.search(r"\bsqlite(?:\+\w+)?:///", s, re.I)
                if sqlite: self.tech("SQLite", "database", p, s, sqlite.start())
                # Decorator routes and literal prefixes resolved in conventions.py.
                for signal, name in [("from flask", "Flask"), ("import flask", "Flask"), ("from fastapi", "FastAPI"), ("import django", "Django")]:
                    if signal in low: self.tech(name, "backend", p, s, low.find(signal), "medium")
            elif suffix == ".go":
                if "http.ListenAndServe" in s or '"net/http"' in s:
                    self.tech("Go net/http", "backend", p, s, confidence="medium")
                # Go router declarations handled by conventions.py.
            elif suffix == ".rs":
                if "axum::" in s:
                    self.tech("Axum", "backend", p, s, confidence="medium")
                if "actix_web::" in s:
                    self.tech("Actix Web", "backend", p, s, confidence="medium")
            elif suffix == ".cs":
                if "WebApplication.CreateBuilder" in s:
                    self.tech("ASP.NET Core", "backend", p, s, confidence="medium")
                # ASP.NET route declarations handled by conventions.py.
            elif suffix == ".java":
                if "@RestController" in s: self.tech("Spring Boot (probable)", "backend", p, s, s.find("@RestController"), "medium")
                # Spring route declarations handled by conventions.py.
            elif suffix == ".sql":
                if re.search(r"\bCREATE\s+TABLE\b", s, re.I):
                    self.db_schemas.add(p)
                if "mysql dump" in low or "engine=innodb" in low or "auto_increment" in low:
                    self.tech("MySQL", "database", p, s, 0, "high")
                elif "postgresql database dump" in low or "create extension" in low:
                    self.tech("PostgreSQL", "database", p, s, 0, "medium")
            elif suffix in {".yml", ".yaml"} and Path(p).name.lower() in {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}:
                self.tech("Docker Compose", "orchestration", p, s)
                # Only indentation-bound declarations directly inside the services map.
                start = re.search(r"^services:\s*$", s, re.M)
                if start:
                    for m in re.finditer(r"^  ([\w.-]+):\s*(?:#.*)?$", s[start.end():], re.M):
                        pos = start.end() + m.start()
                        name = m.group(1)
                        self.compose_services[name] = Service(name, "compose_declared", "Docker Compose service (image/build unverified)",
                            ".", "medium", [_ev(p, s, pos, "Declared service under compose services key")])
            if suffix in CODE_EXTENSIONS:
                for regex in ENV_RE:
                    for m in regex.finditer(s):
                        name = next((g for g in m.groups() if g), None)
                        if name: self.env_vars.add(name)
                # Presence only! Never put the matched secret into Evidence, JSON or console.
                if PASSWORD_LITERAL_RE.search(s):
                    m = PASSWORD_LITERAL_RE.search(s)
                    self.findings.append(Finding("POSSIBLE_HARDCODED_CREDENTIAL", "warning",
                        "Potential literal credential in source; rotate if real and move to managed configuration.",
                        [_ev(p, s, m.start(), "Credential-like assignment detected; value deliberately redacted")]))

        for p in self.files:
            n = Path(p).name.lower()
            if n in {".env", ".env.production", ".env.local", ".env.development"} or (n.startswith(".env.") and n not in {".env.example", ".env.sample", ".env.template"}):
                self.findings.append(Finding("TRACKED_ENV_FILE", "warning", "Environment configuration file appears in repository snapshot; inspect for secrets.", [Evidence(p, None, "File name only; contents not read")]))
            if n in {".env.example", ".env.sample", ".env.template"}:
                s = self.read(p)
                if s:
                    for m in re.finditer(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*=", s, re.M):
                        self.env_vars.add(m.group(1))

    def _convention_routes(self) -> None:
        """Cross-stack route and HTTP client recognition (static, bounded)."""
        from .conventions import discover_conventions

        extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
                      ".php", ".java", ".kt", ".cs", ".go", ".rb", ".rs", ".svelte", ".vue"}
        # Reuse *already scanned* source texts. Do not bypass the 2400-file cap
        # or increase the maximum permitted per-file size.
        eligible = {path: source for path, source in self.texts.items()
                    if source is not None and Path(path).suffix.lower() in extensions | {".html", ".htm"}}
        hits = discover_conventions(eligible, arg_parser=_js_call_args, url_parser=_http_literal)
        for hit in hits:
            source = eligible[hit.source]
            self.route(hit.path, hit.method, hit.kind, hit.source, source,
                       hit.offset, hit.confidence)
            if hit.mount_source and hit.mount_source in eligible:
                route = self.routes.get((hit.path, hit.method, hit.kind))
                if route:
                    mount_ev = _ev(hit.mount_source,eligible[hit.mount_source],
                                   hit.mount_offset or 0,"Literal framework mount prefix")
                    if not any(e.path==mount_ev.path and e.line==mount_ev.line
                               for e in route.evidence):
                        route.evidence.append(mount_ev)
            if hit.kind == "client_http_call" and hit.source.endswith(".py"):
                call_prefix = source[hit.offset:hit.offset+280]
                if re.search(r"https?://", call_prefix) and hit.source not in self._seen_external_evidence:
                    self.external_api_evidence.append(_ev(hit.source, source, hit.offset,
                        "External HTTP request referenced; hostname withheld"))
                    self._seen_external_evidence.add(hit.source)
            if hit.kind == "page_route":
                self.pages.add(hit.source)
                self.entrypoint(hit.source, "web_page")
                self.feature(_inferred_page_label(hit.path), "source_inferred",
                             hit.source, source, hit.offset, "low")
            if hit.kind == "file_based_route" and hit.source.endswith(("+server.ts", "+server.js")):
                self.tech("SvelteKit (inferred)", "backend", hit.source, source, hit.offset, "medium")
            if hit.kind == "file_based_route" and "/app/" in "/" + hit.source and hit.source.endswith(("route.ts", "route.js")):
                self.tech("Next.js API routes (inferred)", "backend", hit.source, source, hit.offset, "medium")

    def _unresolved_api_scan(self) -> None:
        """Surface suspicious dynamic network expressions instead of ignoring them.

        This is a bounded diagnostic, not a denominator for route coverage.
        Only source location and pattern class are retained; no argument text.
        """
        seen=set()
        for p, source in self.texts.items():
            if source is None: continue
            if not p.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
                continue
            for m in re.finditer(r"\bfetch\s*(?:<[^()\n]{1,180}>)?\s*\(",source):
                args = _js_call_args(source,m.end()-1)
                if args is not None and _http_literal(args) is None:
                    key=(p,_line(source,m.start()),"dynamic_fetch")
                    if key not in seen:
                        seen.add(key)
                        self.unresolved_api_sites.append({"path":p,"line":key[1],"kind":key[2]})
            if re.search(r"\b(?:express|fastify|koa|hono)\b", source, re.I):
                for m in re.finditer(r"\b(?:app|router|server)\s*\.\s*(?:get|post|put|patch|delete)\s*\(",source):
                    args = _js_call_args(source,m.end()-1)
                    if args is not None and _http_literal(args) is None:
                        key=(p,_line(source,m.start()),"dynamic_server_route")
                        if key not in seen:
                            seen.add(key)
                            self.unresolved_api_sites.append({"path":p,"line":key[1],"kind":key[2]})
            if len(self.unresolved_api_sites)>=100:
                break

    def _api_contracts(self) -> None:
        """Parse explicit API schemas without a YAML runtime or executing code."""
        names = {"openapi.json", "swagger.json", "openapi.yaml", "openapi.yml",
                 "swagger.yaml", "swagger.yml"}
        methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
        for p in self.files:
            name = Path(p).name.lower()
            if name not in names:
                continue
            raw = self.read(p)
            if not raw:
                continue
            self.tech("OpenAPI/Swagger contract", "api_contract", p, raw, confidence="medium")
            if name.endswith(".json"):
                try:
                    spec = json.loads(raw)
                except ValueError:
                    self.findings.append(Finding("OPENAPI_PARSE_FAILED", "warning",
                        "Could not parse API contract JSON.", [_ev(p,raw)]))
                    continue
                paths = spec.get("paths", {}) if isinstance(spec,dict) else {}
                if not isinstance(paths,dict): continue
                for path,ops in list(paths.items())[:500]:
                    if not isinstance(path,str) or not isinstance(ops,dict): continue
                    for method in methods.intersection(ops):
                        if not isinstance(ops[method],dict): continue
                        normalized = _api_contract_path(path)
                        if normalized:
                            needle = '"'+path+'"'
                            self.route(normalized,method.upper(),"declared_api_contract",
                                       p,raw,max(0,raw.find(needle)),"high")
            else:
                in_paths=False
                current: str | None = None
                lines=raw.splitlines(keepends=True)
                cursor=0
                for line in lines:
                    stripped=line.strip()
                    indent=len(line)-len(line.lstrip(' '))
                    if not in_paths:
                        if indent==0 and stripped=="paths:": in_paths=True
                    elif indent==0 and stripped and not stripped.startswith('#'):
                        in_paths=False; current=None
                    elif indent==2 and stripped.endswith(':'):
                        literal=stripped[:-1].strip().strip('"\'')
                        current=_api_contract_path(literal)
                    elif indent==4 and current:
                        method=stripped.split(':',1)[0].lower()
                        if method in methods and stripped.startswith(method+':'):
                            self.route(current,method.upper(),"declared_api_contract",
                                       p,raw,cursor,"medium")
                    cursor+=len(line)

        # gRPC RPC operations are not HTTP paths; mark them distinctly.
        for p in self.files:
            if not p.endswith('.proto'): continue
            raw = self.read(p)
            if not raw: continue
            package = re.search(r"(?m)^\s*package\s+([A-Za-z_][\w.]*)\s*;",raw)
            package_name=package.group(1)+'.' if package else ''
            for service in re.finditer(r"\bservice\s+(\w+)\s*\{([^}]{0,20000})\}",raw,re.S):
                for rpc in re.finditer(r"\brpc\s+(\w+)\s*\(",service.group(2)):
                    self.route(f"{package_name}{service.group(1)}.{rpc.group(1)}",
                               "RPC", "rpc_method",p,raw,service.start()+rpc.start(),"high")
            if "service " in raw: self.tech("gRPC / Protocol Buffers", "rpc_contract",p,raw,confidence="medium")
        # GraphQL root operation types are not necessarily published at /graphql.
        for p in self.files:
            if not p.endswith(('.graphql','.gql')): continue
            raw=self.read(p)
            if not raw: continue
            found=False
            for group in re.finditer(r"\btype\s+(Query|Mutation|Subscription)\s*\{([^}]{0,20000})\}",raw,re.S):
                for field in re.finditer(r"(?m)^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*[\[!A-Za-z_]",group.group(2)):
                    self.route(group.group(1)+'.'+field.group(1),group.group(1).upper(),
                               "graphql_operation",p,raw,group.start()+field.start(),"medium")
                    found=True
            if found: self.tech("GraphQL schema", "api_contract",p,raw,confidence="medium")

    def _framework_page_routes(self) -> None:
        has_next = any(t.name == "Next.js" for t in self.technologies.values())
        if not has_next:
            return
        for p in self.files:
            url = _next_page_url(p)
            if url is None:
                continue
            raw = self.read(p)
            self.route(url, "GET", "page_route", p, raw or p, 0, "medium")
            self.pages.add(p)
            self.entrypoint(p, "web_page")
            self.feature(_inferred_page_label(url), "source_inferred", p, raw or p, 0, "low")

    def _docs(self) -> None:
        for p in self.files:
            if Path(p).name.lower() not in {"readme.md", "readme.rst", "readme.txt"}: continue
            s = self.read(p)
            if not s: continue
            in_features = False
            for match in re.finditer(r"^.*$", s, re.M):
                line = match.group(0)
                heading = re.match(r"^\s*#{1,5}\s+(.+?)\s*$", line)
                if heading:
                    section = heading.group(1).lower()
                    in_features = any(k in section for k in ("feature", "functionality", "capabilit", "what it does"))
                    continue
                if in_features:
                    bullet = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)", line)
                    if bullet:
                        self.feature(bullet.group(1), "readme", p, s, match.start(), "documented")
            low = s.lower()
            for name, category in [("mysql", "database"), ("postgresql", "database"), ("mongodb", "database"), ("redis", "database")]:
                loc = low.find(name)
                if loc >= 0 and not any(t.name.lower() == name for t in self.technologies.values()):
                    self.tech({"mysql":"MySQL", "postgresql":"PostgreSQL", "mongodb":"MongoDB", "redis":"Redis"}[name], category, p, s, loc, "low")

    @staticmethod
    def _service_root(technology: Technology) -> str:
        path = technology.evidence[0].path
        manifest_names = {"package.json", "requirements.txt", "pyproject.toml", "composer.json",
                          "pom.xml", "build.gradle", "build.gradle.kts", "pubspec.yaml", "go.mod", "gemfile"}
        if Path(path).name.lower() in manifest_names:
            return Path(path).parent.as_posix()
        bits = path.split("/")
        if len(bits) >= 3 and bits[0] in {"apps", "packages", "services"}:
            return "/".join(bits[:2])
        if bits[0] in {"frontend", "backend", "web", "client", "server", "api", "ui"} and len(bits) > 1:
            return bits[0]
        return "."

    def _services(self) -> tuple[list[Service], str, str]:
        t = list(self.technologies.values())
        front = [x for x in t if x.category == "frontend"]
        back = [x for x in t if x.category == "backend"]
        db = [x for x in t if x.category == "database"]
        php_files = [p for p in self.files if p.lower().endswith(".php")]
        py_files = [p for p in self.files if p.lower().endswith(".py")]
        js_files = [p for p in self.files if p.lower().endswith((".js", ".ts", ".mjs"))]
        if php_files and not back:
            e = php_files[0]
            self.tech("PHP (native)", "backend", e, e, confidence="medium")
            back = [x for x in self.technologies.values() if x.category == "backend"]
        if not front and (self.pages or any(Path(p).suffix.lower() in {".tsx", ".jsx", ".vue", ".svelte"} for p in self.files)):
            p = sorted(self.pages)[0] if self.pages else next(p for p in self.files if Path(p).suffix.lower() in {".tsx", ".jsx", ".vue", ".svelte"})
            self.tech("HTML/JS frontend (inferred)", "frontend", p, p, confidence="medium")
            front = [x for x in self.technologies.values() if x.category == "frontend"]

        services: list[Service] = []
        grouped: dict[tuple[str,str], list[Technology]] = defaultdict(list)
        for technology in [*front, *back]:
            grouped[(technology.category, self._service_root(technology))].append(technology)
        db_names = [x.name.lower().replace(" ", "_") for x in db]
        for (kind, root), technologies in sorted(grouped.items()):
            names = sorted({x.name for x in technologies if x.name != "Vite"})
            root_label = root.replace("/", "_")
            name = kind if root == "." else (root_label if root.split("/")[-1].lower() == kind else root_label + "_" + kind)
            service_root = "public" if kind == "frontend" and root == "." and any(p.startswith("public/") for p in self.pages) else root
            e = technologies[0]
            services.append(Service(name, kind, ", ".join(names), service_root, "medium" if any(x.confidence != "high" for x in technologies) else "high",
                                    [ev for x in technologies[:3] for ev in x.evidence[:1]],
                                    db_names if kind == "backend" else [x.name for x in services if x.kind == "backend" and x.root == root]))
        # Database services are inferred from dependencies/schema, not confirmed running.
        for technology in db:
            services.append(Service(technology.name.lower().replace(" ", "_"), "database", technology.name,
                                    ".", technology.confidence, technology.evidence[:2]))
        services.extend(self.compose_services.values())
        # Cloud services are *declared dependencies*, not provisioned local
        # processes. Only label S3 when actual SDK usage is observed.
        s3 = next((x for x in t if x.name == "Amazon S3" and x.category == "external_integration"), None)
        if s3:
            services.append(Service("amazon_s3", "external_dependency", "Amazon S3 (cloud storage; unverified)",
                                    "external", s3.confidence, s3.evidence[:2]))
            for svc in services:
                if svc.kind == "backend" and any(ev.path.startswith(svc.root.rstrip('/') + '/') if svc.root != '.' else True for ev in s3.evidence):
                    if "amazon_s3" not in svc.depends_on: svc.depends_on.append("amazon_s3")
        # A frontend can depend on a backend in a separate directory when
        # static cross-file API reconciliation finds a unique matching route.
        # Client-only external APIs never create a fabricated local backend.
        if self.api_links:
            def under_root(path: str, root: str) -> bool:
                return root == '.' or path == root or path.startswith(root.rstrip('/') + '/')

            for svc in services:
                if svc.kind == 'frontend':
                    for backend in services:
                        if backend.kind != 'backend' or backend.name in svc.depends_on:
                            continue
                        if any(under_root(link['client_source'], svc.root) and
                               under_root(link['server_source'], backend.root)
                               for link in self.api_links):
                            svc.depends_on.append(backend.name)
        if self.external_api_evidence:
            services.append(Service(
                "external_http_api", "external_dependency",
                "External HTTP API (source not present)", "external", "medium",
                self.external_api_evidence[:3], [],
            ))
        if front and back:
            return services, "fullstack_web", "medium" if any(x.confidence != "high" for x in front + back) else "high"
        if front: return services, "frontend_web", "medium"
        if back: return services, "backend_service", "medium"
        if py_files or js_files or php_files or any(p.endswith((".cpp", ".java", ".go", ".rs")) for p in self.files):
            return services, "cli_or_library", "low"
        return services, "unknown", "low"

    def _preflight(self, services: list[Service], arch: str) -> tuple[list[str], list[str]]:
        unknowns = []
        targets = []
        if not self.tests:
            unknowns.append("No repository-owned automated test suite was detected; absence is not proof that none exists.")
        if arch in {"fullstack_web", "backend_service"}:
            if not any(Path(p).name.lower() in {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"} for p in self.files):
                unknowns.append("No Docker Compose manifest found; Phase 2 must determine an isolated startup plan.")
        if any(s.kind == "database" for s in services):
            unknowns.append("Database readiness, migrations, test fixtures, and credentials have not been validated.")
        if self.env_vars:
            unknowns.append("Configuration variables were observed; static required/default hints may be overridden by aliases, validators, profiles or runtime settings.")
        if services:
            unknowns.append("Inferred service records describe logical components, not confirmed separately deployable processes; actual service boundaries require Phase 2 validation.")
        if self.truncated:
            unknowns.append("Source inspection was capped; static findings may be incomplete for large repositories.")
        if self.unresolved_api_sites:
            unknowns.append("Some dynamic API expressions could not be resolved statically; see inventory.unresolved_api_sites. This is not a verified count of missing endpoints.")
        if self.external_api_evidence:
            unknowns.append("A client-side external HTTP API was detected; its backend source, database and contracts are not available in this snapshot. Never run mutating tests against the live endpoint without explicit authorization and isolation.")
        if not self.routes:
            unknowns.append("No supported static HTTP route patterns were discovered; dynamic routes may still exist.")
        # Client calls are not necessarily distinct server endpoints. Use them
        # to annotate likely methods, without asserting that those methods are
        # accepted by the server (or that the endpoint even responds).
        client_methods: dict[str, set[str]] = defaultdict(set)
        for route in self.routes.values():
            if route.kind != "client_http_call":
                continue
            path = route.path.split("?", 1)[0].split("#", 1)[0].lstrip("./")
            if path and route.method != "UNKNOWN":
                client_methods[path].add(route.method)
        linked_client_routes = {(link['client_path'], link['client_method']) for link in self.api_links}
        for route in sorted(self.routes.values(), key=lambda x:(x.path,x.method))[:80]:
            if route.kind == "page_route":
                targets.append(f"Inspect UI route {route.path} and establish expected behavior (not executed)")
                continue
            if route.kind == "client_http_call":
                if (route.path, route.method) in linked_client_routes:
                    continue  # One server test target, with corroborating client evidence.
                targets.append(f"Inspect external/client API contract for {route.method} {route.path} (server behavior unverified)")
                continue
            if route.kind == "form_action":
                targets.append(f"Inspect HTML form submission {route.method} {route.path} (behavior unverified)")
                continue
            if route.kind == "graphql_operation":
                targets.append(f"Inspect GraphQL {route.method} operation {route.path} (schema only; no endpoint assumed)")
                continue
            if route.kind == "rpc_method":
                targets.append(f"Inspect gRPC RPC method {route.path} (schema only; no server verified)")
                continue
            if route.kind == "declared_api_contract":
                targets.append(f"Verify published API contract for {route.method} {route.path} (not executed)")
                continue
            candidate = route.path.lstrip("./")
            observed = sorted(client_methods.get(candidate, set()))
            if route.method == "UNKNOWN" and observed:
                method_hint = f"UNKNOWN (frontend calls use {', '.join(observed)}; not server-verified)"
            else:
                method_hint = route.method
            targets.append(f"Inspect contract and expected behavior for {method_hint} {route.path}")
        for feature in sorted(self.features.values(), key=lambda x:x.name)[:15]:
            if feature.origin != "readme":
                continue
            # Keep documentation/architecture quality claims in the inventory,
            # but do not propose them as executable functional test cases.
            if re.search(r"\b(?:project|folder|directory) structure\b|\b(?:clean code|code style|naming convention)\b", feature.name, re.I):
                continue
            targets.append(f"Translate documented feature into observable assertions: {feature.name}")
        if not targets:
            targets.append("Obtain explicit requirements before generating functional assertions.")
        return unknowns, targets[:60]

    def analyze(self) -> Manifest:
        self._metadata()
        self._package_manifests()
        self._source_markers()
        self._convention_routes()
        self._api_contracts()
        self._unresolved_api_scan()
        self._framework_page_routes()
        self._docs()
        self._match_api_routes()
        services, arch, confidence = self._services()
        unknowns, targets = self._preflight(services, arch)
        from .file_roles import catalog_file_roles
        file_roles, role_counts = catalog_file_roles(
            self.files, page_files=self.pages, test_files=self.tests,
            route_files=(r.evidence[0].path for r in self.routes.values()
                         if r.kind in {"declared_http_route", "file_endpoint", "file_based_route"}),
            http_client_files=(r.evidence[0].path for r in self.routes.values()
                               if r.kind == "client_http_call"),
            database_files=self.db_schemas,
        )
        language_counts = dict(sorted(Counter(LANGUAGES[Path(p).suffix.lower()] for p in self.files
                                               if Path(p).suffix.lower() in LANGUAGES).items(), key=lambda x:(-x[1],x[0])))
        return Manifest(
            schema_version=SCHEMA_VERSION, phase=1,
            created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source=self.source, project_name=self.source.repository.split("/")[-1],
            architecture=arch, architecture_confidence=confidence,
            languages=language_counts,
            technologies=sorted(self.technologies.values(), key=lambda x:(x.category,x.name)),
            services=services,
            routes=sorted(self.routes.values(), key=lambda x:(x.kind,x.path,x.method)),
            pages=sorted(self.pages),
            entrypoints=sorted(self.entrypoints.values(), key=lambda x:(x.kind,x.path)),
            features=sorted(self.features.values(), key=lambda x:(x.origin,x.name.lower())),
            tests=sorted(self.tests.values(), key=lambda x:x.path),
            setup_files=sorted(self.setup), package_scripts=self.scripts,
            environment_variables=sorted(self.env_vars), database_schema_files=sorted(self.db_schemas),
            findings=self.findings, unknowns=unknowns,
            proposed_test_targets=targets,
            inventory={"indexed_files":len(self.files), "readable_files_examined":self.index.read_count,
                       "skipped_symlinks_or_unreadable":len(self.index.skipped),
                       "static_source_scan_capped":self.truncated,
                       "file_roles": file_roles, "file_role_counts": role_counts,
                       "unresolved_api_sites": self.unresolved_api_sites,
                       "environment_variable_details": self.env_var_details,
                       "api_links": self.api_links},
            limitations=[
                "Phase 1 is static analysis only: no student code, installs, tests, containers, or services were run.",
                "Repository documentation and source content are untrusted inputs, not executable instructions.",
                "Route and feature detection is heuristic; dynamic routing, generated code, and undocumented behavior may be missed.",
                "Package manifests indicate declared, not necessarily installed or working, dependencies.",
                "Phase 1 does not establish behavioral correctness, test coverage, security safety, or code quality.",
                "Only repository evidence is recorded; no expected functional outputs are invented.",
            ],
        )


def analyze_directory(directory: Path, source: RepositorySource | None = None) -> Manifest:
    root = Path(directory).resolve()
    src = source or RepositorySource(kind="local", repository=root.name)
    return Analyzer(SourceIndex.build(root), src).analyze()
