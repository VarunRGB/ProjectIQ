"""Conservative, evidence-backed discovery of common API and filesystem conventions.

This module inspects *text only*. It never imports/executes submitted code, evaluates
expressions, requests external URLs, or infers successful behavior. Prefix resolution
is intentionally limited to literal, demonstrable mounts and import paths.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Mapping

METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"
JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


@dataclass(frozen=True)
class RouteHint:
    path: str
    method: str
    kind: str
    source: str
    offset: int
    confidence: str = "medium"
    mount_source: str | None = None
    mount_offset: int | None = None


def _literal(value: str) -> str | None:
    """Return an actual string literal, not an expression or arbitrary code."""
    m = re.fullmatch(r"\s*(['\"`])([^'\"`\r\n]{0,240})\1\s*", value, re.S)
    return m.group(2) if m else None


def _clean_path(path: str | None, *, allow_empty: bool = False) -> str | None:
    if path is None:
        return "" if allow_empty else None
    path = path.strip()
    if not path:
        return "" if allow_empty else None
    if len(path) > 220 or any(c in path for c in ("\r", "\n", "\\", "${", "?", "#")):
        return None
    if "http://" in path or "https://" in path or path.startswith("//"):
        return None
    if not re.fullmatch(r"[/\w.\-:*{}\[\]()<>]+", path):
        return None
    # Framework dynamic parameter syntaxes; no expression evaluation.
    path = re.sub(r"<(?:(?:int|str|uuid|path|slug):)?([A-Za-z_]\w*)>", r":\1", path)
    path = re.sub(r"\{([A-Za-z_]\w*)\}", r":\1", path)
    path = re.sub(r"\[([A-Za-z_]\w*)\]", r":\1", path)
    if "{" in path or "}" in path or "[" in path or "]" in path or "<" in path or ">" in path:
        return None
    if ".." in path.split("/"):
        return None
    path = "/" + path.lstrip("/")
    path = re.sub(r"/{2,}", "/", path).rstrip("/") or "/"
    return path


def _join(prefix: str | None, path: str | None) -> str | None:
    pref = _clean_path(prefix, allow_empty=True)
    route = _clean_path(path, allow_empty=True)
    if pref is None or route is None:
        return None
    return _clean_path((pref.rstrip("/") or "") + "/" + route.lstrip("/"))


def _framework_path(relpath: str, *, root: str, suffix: str) -> str | None:
    """Convert a supported file-system route to a URL candidate."""
    bits = relpath.split("/")
    if root not in bits:
        return None
    # Select the LAST root segment, to tolerate apps/web/src/app/...
    index = len(bits) - 1 - bits[::-1].index(root)
    tail = bits[index+1:]
    if not tail or tail[-1] != suffix:
        return None
    tail = tail[:-1]
    url = []
    for segment in tail:
        if re.fullmatch(r"\([^)]+\)", segment):
            continue  # route groups
        if segment.startswith("@") or segment.startswith("(.)") or re.fullmatch(r"\[\[\.\.\..+\]\]", segment):
            return None  # ambiguous parallel/optional route
        if segment.startswith("[...") and segment.endswith("]"):
            segment = ":" + segment[4:-1] + "*"
        url.append(segment)
    return _clean_path("/" + "/".join(url))


def _add(out: list[RouteHint], source: str, offset: int, method: str, kind: str, path: str | None, confidence: str = "medium") -> None:
    normalized = _clean_path(path)
    if normalized:
        out.append(RouteHint(normalized, method.upper(), kind, source, offset, confidence))


def _js_routes_from_file(path: str, text: str) -> tuple[list[tuple[str, str, str, int]], set[str]]:
    """Return (owner, method, relative-path, offset) plus recognized router aliases."""
    out: list[tuple[str, str, str, int]] = []
    owners = {"app", "router", "server", "fastify"}
    # Prove user-defined aliases wherever possible.
    for match in re.finditer(r"\b(?:const|let|var)\s+([\w$]+)\s*=\s*(?:express\s*\(|(?:express|[\w$]+)\s*\.\s*Router\s*\(|fastify\s*\(|new\s+(?:Hono|Router|KoaRouter)\s*\()", text):
        owners.add(match.group(1))
    if not re.search(r"\b(express|fastify|koa|hono)\b|\.\s*Router\s*\(", text, re.I):
        # Other imported frameworks may still declare app.get; prefer medium confidence.
        owners = {x for x in owners if x in {"app", "router", "server", "fastify"}}
    pattern = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\.\s*(get|post|put|patch|delete|head|options|all)\s*\(\s*(['\"`])([^'\"`\r\n]{1,200})\3", re.I)
    for m in pattern.finditer(text):
        alias, method, raw = m.group(1), m.group(2).upper(), m.group(4)
        if alias not in owners: continue
        p = _clean_path(raw)
        if p: out.append((alias, method, p, m.start()))
    # express.Router().route('/foo').get(...).post(...)
    for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\.\s*route\s*\(\s*(['\"`])([^'\"`\r\n]{1,200})\2\s*\)([^;\n]{0,500})", text, re.I):
        if m.group(1) not in owners: continue
        p = _clean_path(m.group(3))
        if not p: continue
        for verb in re.finditer(r"\.\s*(get|post|put|patch|delete|head|options)\s*\(", m.group(4), re.I):
            out.append((m.group(1), verb.group(1).upper(), p, m.start() + verb.start()))
    # Fastify object route form: fastify.route({ method:'GET', url:'/x' })
    for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\.route\s*\(\s*\{([^}]{0,900})\}", text, re.S):
        if m.group(1) not in owners: continue
        props = m.group(2)
        method = re.search(r"\bmethod\s*:\s*['\"](" + METHODS + r")['\"]", props, re.I)
        url = re.search(r"\b(?:url|path)\s*:\s*['\"]([^'\"\r\n]+)['\"]", props)
        if method and url:
            p = _clean_path(url.group(1))
            if p: out.append((m.group(1), method.group(1).upper(), p, m.start()))
    return out, owners


def _imports(path: str, text: str, files: set[str]) -> dict[str, str]:
    """Resolve relative JS/TS imports; never follow paths outside the snapshot."""
    imported: dict[str,str] = {}
    decls = []
    for m in re.finditer(r"\bimport\s+([A-Za-z_$][\w$]*)\s+from\s+['\"](\.[^'\"]+)['\"]", text):
        decls.append((m.group(1),m.group(2)))
    for m in re.finditer(r"\bimport\s*\{([^}\n]+)\}\s*from\s*['\"](\.[^'\"]+)['\"]", text):
        for chunk in m.group(1).split(","):
            key = chunk.strip().split()[-1] if chunk.strip() else ""
            if key: decls.append((key, m.group(2)))
    for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*['\"](\.[^'\"]+)['\"]\s*\)", text):
        decls.append((m.group(1),m.group(2)))
    for alias, dest in decls:
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(path),dest))
        if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
            continue
        for choice in (normalized, *(normalized+ext for ext in JS_EXTS), *(normalized+"/index"+ext for ext in JS_EXTS)):
            if choice in files:
                imported[alias] = choice
                break
    return imported


def _js_declared_routes(sources: Mapping[str,str]) -> list[RouteHint]:
    allfiles = set(sources)
    declarations: dict[tuple[str,str],list[tuple[str,str,int]]] = {}
    owners_for_file: dict[str,set[str]] = {}
    for p,s in sources.items():
        if not p.endswith(JS_EXTS): continue
        decls, owners = _js_routes_from_file(p,s)
        owners_for_file[p] = owners
        for alias, method, url, offset in decls:
            declarations.setdefault((p,alias),[]).append((method,url,offset))

    # Each edge represents a *literal* mount: parent.router.use('/prefix', childRouter).
    # Path resolution is limited to known relative imports or same-file aliases.
    edges: list[tuple[tuple[str,str], tuple[str,str],str]] = []
    for p,s in sources.items():
        if not p.endswith(JS_EXTS): continue
        imported = _imports(p,s,allfiles)
        for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\.\s*use\s*\(\s*(['\"`])([^'\"`\r\n]{1,200})\2\s*,\s*([A-Za-z_$][\w$]*)\s*[,)]",s):
            parent, pref, child = m.group(1), _clean_path(m.group(3)), m.group(4)
            if pref is None or parent not in owners_for_file.get(p,set()): continue
            target_path = imported.get(child, p)
            if target_path == p:
                if child not in owners_for_file.get(p,set()) or child == parent: continue
                target_aliases = [child]
            else:
                candidates = [alias for f,alias in declarations if f == target_path]
                if not candidates:
                    # Router-only modules may just mount subrouters, with no
                    # local handlers. Resolve their exported Router variable.
                    candidates = [m.group(1) for m in re.finditer(
                        r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:express\.Router|Router)\s*\(",
                        sources[target_path])]
                if len(set(candidates)) != 1:
                    continue  # ambiguous file export; don't guess
                target_aliases = list(set(candidates))
            for alias in target_aliases:
                edges.append(((p,parent),(target_path,alias),pref))

    # Propagate root mount paths through nested routers. A cycle is bounded by
    # number of nodes and deduplication; ambiguous cyclic relationships are ignored.
    prefixes: dict[tuple[str,str],set[str]] = {}
    for owner in set(declarations) | {parent for parent,_,_ in edges}:
        if owner[1] in {"app", "server", "fastify"}:
            prefixes[owner] = {""}
    for _ in range(min(len(edges)+1, 64)):
        changed = False
        for parent,child,prefix in edges:
            parent_prefixes = prefixes.get(parent, set())
            for par in parent_prefixes:
                joined = _join(par,prefix)
                if joined and joined not in prefixes.setdefault(child,set()):
                    prefixes[child].add(joined)
                    changed = True
        if not changed: break
    # If imported router is mounted and has a full path, never also emit its
    # unprefixed path as a fabricated public endpoint.
    incoming = {child for _,child,_ in edges}
    out: list[RouteHint] = []
    for node, records in declarations.items():
        file, _ = node
        pfxs = prefixes.get(node)
        if not pfxs:
            if node in incoming: continue  # unresolved mounting chain
            pfxs = {""}
        for method,path,off in records:
            for pfx in sorted(pfxs):
                full = _join(pfx,path)
                if not full: continue
                confidence = "high" if pfx or node[1] in {"app", "server", "fastify"} else "medium"
                if method == "ALL": method = "UNKNOWN"
                _add(out,file,off,method,"declared_http_route",full,confidence)
    return out


def _api_file_routes(path: str,text: str) -> tuple[list[RouteHint],list[RouteHint]]:
    """Next, SvelteKit and Nuxt file-system route conventions."""
    out: list[RouteHint] = []
    pages: list[RouteHint] = []
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in JS_EXTS + (".vue", ".svelte"): return out,pages
    # Next app file route: app/api/route.ts, app/api/users/[id]/route.ts
    m = re.search(r"(?:^|/)(?:src/)?app/(.*)route\.(?:js|ts)$", path)
    if m and (m.group(1) == "" or m.group(1).endswith("/")):
        url = _framework_path(path,root="app",suffix=path.split("/")[-1])
        if url:
            for method in re.finditer(r"\bexport\s+(?:(?:async\s+)?function\s+|const\s+)("+METHODS+r")\s*(?:=|\()",text):
                _add(out,path,method.start(),method.group(1),"file_based_route",url,"high")
    # Next pages/api/users/[id].ts and pages/api/index.ts
    m = re.search(r"(?:^|/)(?:src/)?pages/api/(.*)\.(?:js|ts)$",path)
    if m and not m.group(1).startswith("_"):
        rel=m.group(1)
        if rel.endswith("/index"): rel=rel[:-6]
        if rel == "index": rel=""
        url = _clean_path("/api/" + rel)
        _add(out,path,0,"UNKNOWN","file_based_route",url,"medium")
    # SvelteKit src/routes/users/[id]/+server.ts / +page.svelte
    if re.search(r"(?:^|/)src/routes/(?:.*/)?\+server\.(?:js|ts)$",path):
        url = _framework_path(path,root="routes",suffix=path.split("/")[-1])
        if url:
            for m in re.finditer(r"\bexport\s+(?:(?:async\s+)?function\s+|const\s+)("+METHODS+r")\s*(?:=|\()",text):
                _add(out,path,m.start(),m.group(1),"file_based_route",url,"high")
    if re.search(r"(?:^|/)src/routes/(?:.*/)?\+page\.svelte$",path):
        url = _framework_path(path,root="routes",suffix="+page.svelte")
        _add(pages,path,0,"GET","page_route",url,"medium")
    # Nuxt 3 file routes, including index.vue and [id].vue. Activated only
    # when the repository declares a Nuxt config (in discover_conventions).
    # Nuxt server/api/items.get.ts, server/api/items/[id].post.ts
    m = re.search(r"(?:^|/)server/(api|routes)/(.*?)(?:\.(get|post|put|patch|delete|head|options))?\.(?:js|ts)$",path,re.I)
    if m:
        rel=m.group(2)
        if rel.endswith("/index"): rel=rel[:-6]
        if rel == "index": rel=""
        url = _clean_path(("/api/" if m.group(1)=="api" else "/")+rel)
        _add(out,path,0,(m.group(3) or "UNKNOWN"),"file_based_route",url,"medium")
    return out,pages


def _python_routes(path: str,text: str) -> list[RouteHint]:
    out: list[RouteHint] = []
    # APIRouter(prefix='/api') and Blueprint(..., url_prefix='/v1')
    prefixes: dict[str,str] = {}
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*(?:APIRouter|Blueprint)\s*\(([^)]{0,450})\)",text,re.S):
        prefix=re.search(r"\b(?:prefix|url_prefix)\s*=\s*(['\"])([^'\"\r\n]+)\1",m.group(2))
        if prefix:
            result=_clean_path(prefix.group(2))
            if result: prefixes[m.group(1)] = result
    include: dict[str,str] = {}
    for m in re.finditer(r"\b\w+\.include_router\s*\(\s*([A-Za-z_]\w*)\s*,\s*prefix\s*=\s*(['\"])([^'\"\r\n]+)\2",text):
        include[m.group(1)] = m.group(3)
    for m in re.finditer(r"@([A-Za-z_]\w*)\.(get|post|put|patch|delete|head|options|api_route)\s*\(\s*(['\"])([^'\"\r\n]*)\3",text,re.I):
        prefix = _join(include.get(m.group(1),""),prefixes.get(m.group(1),"")) or ""
        url = _join(prefix,m.group(4))
        _add(out,path,m.start(),m.group(2) if m.group(2).lower() != "api_route" else "UNKNOWN","declared_http_route",url,"high")
    # Flask @app.route('/hello', methods=['GET', 'POST'])
    for m in re.finditer(r"@([A-Za-z_]\w*)\.route\s*\(\s*(['\"])([^'\"\r\n]+)\2([^)]{0,600})\)",text,re.S):
        url = _join(prefixes.get(m.group(1),""),m.group(3))
        methods=re.search(r"\bmethods\s*=\s*[\[(]([^\])]{1,200})[\])]",m.group(4),re.I)
        verbs=re.findall(r"['\"]("+METHODS+r")['\"]",methods.group(1),re.I) if methods else ["GET"]
        for verb in verbs:
            _add(out,path,m.start(),verb,"declared_http_route",url,"high" if methods else "medium")
    # Django path('users/<int:id>/', views.user), not regular-expression re_path.
    if PurePosixPath(path).name == "urls.py":
        for m in re.finditer(r"\bpath\s*\(\s*(['\"])([^'\"\r\n]*)\1",text):
            url = _clean_path(m.group(2),allow_empty=True)
            _add(out,path,m.start(),"UNKNOWN","declared_http_route",url or "/","medium")
    return out



def _python_module_target(current_file: str, module: str, sources: Mapping[str,str]) -> str | None:
    """Resolve only repository-local Python modules referenced by literal imports."""
    if module.startswith("."):
        lead = len(module) - len(module.lstrip('.'))
        directory = posixpath.dirname(current_file)
        for _ in range(lead-1): directory = posixpath.dirname(directory)
        suffix = module.lstrip('.').replace('.', '/')
        candidate = posixpath.normpath(posixpath.join(directory,suffix))
    else:
        candidate = module.replace('.', '/')
    if candidate.startswith('../') or candidate.startswith('/'):
        return None
    for c in (candidate+'.py',candidate+'/__init__.py'):
        if c in sources: return c
    return None


def _python_project_routes(sources: Mapping[str,str]) -> list[RouteHint]:
    declared: dict[str,list[RouteHint]] = {
        p:_python_routes(p,s) for p,s in sources.items() if p.endswith('.py')
    }
    mounts: dict[str,list[tuple[str,int,str]]] = {}
    for p,s in sources.items():
        if not p.endswith('.py'): continue
        names: dict[str,str]={}
        for m in re.finditer(r"^\s*from\s+([.\w]+)\s+import\s+([^#\n]+)",s,re.M):
            mod, imported = m.group(1),m.group(2)
            for item in imported.split(','):
                fields = item.strip().split()
                if not fields or fields[0]=='*': continue
                alias = fields[-1] if len(fields)>=3 and fields[-2]=='as' else fields[0]
                target=_python_module_target(p,mod+'.'+fields[0],sources)
                if target is None:
                    target=_python_module_target(p,mod,sources)
                if target: names[alias]=target
        for m in re.finditer(r"^\s*import\s+([.\w]+)(?:\s+as\s+(\w+))?",s,re.M):
            target=_python_module_target(p,m.group(1),sources)
            if target: names[m.group(2) or m.group(1).split('.')[0]]=target
        for m in re.finditer(r"\b\w+\.include_router\s*\(\s*([\w.]+)(?:\s*,\s*prefix\s*=\s*(['\"])([^'\"\r\n]+)\2)?",s):
            ref,prefix=m.group(1),m.group(3) or ''
            var=ref.split('.')[0]
            target=names.get(var)
            if target and target!=p:
                mounts.setdefault(target,[]).append((prefix,m.start(),p))
    out=[]
    for file,routes in declared.items():
        for route in routes:
            if file in mounts:
                for prefix,at,mounting_file in mounts[file]:
                    full=_join(prefix,route.path)
                    if full:
                        out.append(RouteHint(full,route.method,route.kind,file,route.offset,
                                             'high',mounting_file,at))
            else:
                out.append(route)
    return out

def _java_routes(path: str,text: str) -> list[RouteHint]:
    out: list[RouteHint] = []
    if "@" not in text: return out
    cls = re.search(r"\b(?:public\s+)?class\s+(\w+)",text)
    head = text[:cls.start()] if cls else text[:1200]
    prefix_match = re.search(r"@RequestMapping\s*\(\s*(?:(?:value|path)\s*=\s*)?['\"]([^'\"]+)['\"]",head)
    prefix = prefix_match.group(1) if prefix_match else ""
    # GetMapping(value="/items") or GetMapping("/items"), including no-args GetMapping().
    for m in re.finditer(r"@(Get|Post|Put|Patch|Delete)Mapping\s*\(\s*(?:(?:value|path)\s*=\s*)?(['\"])([^'\"\r\n]*)\2",text,re.I):
        _add(out,path,m.start(),m.group(1),"declared_http_route",_join(prefix,m.group(3)),"high")
    for m in re.finditer(r"@(Get|Post|Put|Patch|Delete)Mapping\s*(?:\(\s*\))?(?![\w(])",text,re.I):
        _add(out,path,m.start(),m.group(1),"declared_http_route",_join(prefix,""),"high")
    for m in re.finditer(r"@RequestMapping\s*\(\s*(?:(?:path|value)\s*=\s*)?['\"]([^'\"]+)['\"]\s*,\s*method\s*=\s*RequestMethod\.("+METHODS+r")",text,re.I):
        # Don't duplicate class-level prefix (first RequestMapping appears before class)
        if cls and m.start()<cls.start(): continue
        _add(out,path,m.start(),m.group(2),"declared_http_route",_join(prefix,m.group(1)),"high")
    return out


def _nest_routes(path: str,text: str) -> list[RouteHint]:
    if "@Controller" not in text: return []
    prefix_match = re.search(r"@Controller\s*\(\s*(?:['\"]([^'\"\r\n]+)['\"])?\s*\)",text)
    prefix = prefix_match.group(1) if prefix_match else ""
    out=[]
    for m in re.finditer(r"@(Get|Post|Put|Patch|Delete|Head|Options)\s*\(\s*(?:['\"]([^'\"\r\n]*)['\"])?\s*\)",text,re.I):
        _add(out,path,m.start(),m.group(1),"declared_http_route",_join(prefix,m.group(2) or ""),"high")
    return out


def _csharp_routes(path: str,text: str) -> list[RouteHint]:
    out=[]
    for m in re.finditer(r"\b\w+\.Map(Get|Post|Put|Patch|Delete|Methods)\s*\(\s*['\"]([^'\"\r\n]+)['\"]",text,re.I):
        _add(out,path,m.start(),m.group(1) if m.group(1).lower()!="methods" else "UNKNOWN","declared_http_route",m.group(2),"high")
    # Conventional controller attributes: class-level route plus method-level [HttpGet("{id}")].
    cls=re.search(r"\bclass\s+([A-Za-z_]\w*)Controller\b",text)
    if cls:
        class_header=text[:cls.start()]
        base_match=list(re.finditer(r"\[Route\s*\(\s*['\"]([^'\"\r\n]+)['\"]\s*\)\s*\]",class_header))
        if base_match:
            base=base_match[-1].group(1).replace("[controller]",cls.group(1).lower())
            for m in re.finditer(r"\[Http(Get|Post|Put|Patch|Delete)\s*(?:\(\s*['\"]([^'\"\r\n]*)['\"]\s*\))?\s*\]",text[cls.end():],re.I):
                _add(out,path,cls.end()+m.start(),m.group(1),"declared_http_route",_join(base,m.group(2) or ""),"high")
    return out


def _other_server_routes(path: str,text: str) -> list[RouteHint]:
    out=[]
    if path.endswith(".php"):
        # Laravel route declarations, including closures/controller actions.
        for m in re.finditer(r"\bRoute::(get|post|put|patch|delete|options|any)\s*\(\s*['\"]([^'\"\r\n]+)['\"]",text,re.I):
            _add(out,path,m.start(),m.group(1) if m.group(1).lower()!="any" else "UNKNOWN","declared_http_route",m.group(2),"high")
        # Laravel apiResource has deterministic standard REST method/path conventions.
        for m in re.finditer(r"\bRoute::apiResource\s*\(\s*['\"]([^'\"\r\n]+)['\"]",text):
            base=_clean_path(m.group(1))
            if base:
                for method,extension in (("GET",""),("POST",""),("GET","/:id"),
                                          ("PUT","/:id"),("PATCH","/:id"),("DELETE","/:id")):
                    _add(out,path,m.start(),method,"declared_http_route",_join(base,extension),"medium")
    if path.endswith(".go"):
        # Standard net/http and Go 1.22+ method-aware ServeMux patterns.
        for m in re.finditer(r"\b(?:http\.)?(?:HandleFunc|Handle)\s*\(\s*['\"]([^'\"\r\n]+)['\"]",text):
            raw=m.group(1).strip()
            meth,pth=(raw.split(' ',1) if re.match(r'^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) ',raw) else ('UNKNOWN',raw))
            _add(out,path,m.start(),meth,"declared_http_route",pth,"medium")
        # Gin, Echo, Fiber and Chi conventional method declarations.
        for m in re.finditer(r"\b\w+\.(GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete)\s*\(\s*['\"]([^'\"\r\n]+)['\"]",text):
            if "github.com/gin-gonic/gin" in text or "github.com/gofiber/fiber" in text or "github.com/labstack/echo" in text or "github.com/go-chi/chi" in text:
                _add(out,path,m.start(),m.group(1),"declared_http_route",m.group(2),"medium")
    if path.endswith(".rs"):
        for m in re.finditer(r"#\[(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"\r\n]+)['\"]\s*\)\]",text):
            _add(out,path,m.start(),m.group(1),"declared_http_route",m.group(2),"high")
        # Axum Router::new().route("/path", get(handler))
        if 'axum' in text.lower():
            for m in re.finditer(r"\.route\s*\(\s*['\"]([^'\"\r\n]+)['\"]\s*,\s*(get|post|put|patch|delete)\s*\(",text):
                _add(out,path,m.start(),m.group(2),"declared_http_route",m.group(1),"medium")
    if path.endswith(".rb") and path.endswith("routes.rb"):
        for m in re.finditer(r"^\s*(get|post|put|patch|delete)\s+['\"]([^'\"\r\n]+)['\"]",text,re.M):
            _add(out,path,m.start(),m.group(1),"declared_http_route",m.group(2),"medium")
    return out


def _client_routes(path: str,text: str, proved_aliases: set[str], arg_parser: Callable[[str,int],str|None], url_parser: Callable[[str],str|None]) -> list[RouteHint]:
    out=[]
    if path.endswith(JS_EXTS):
        # Only clients proven by imports or constructor usage; avoid mistaking arbitrary .get() as HTTP.
        imports_client = bool(re.search(r"\b(?:import|require)\b[^\n]{0,160}\b(?:ky|axios|@angular/common/http|ofetch)\b",text))
        aliases=set(proved_aliases)
        for m in re.finditer(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:axios|ky)\.create\s*\(",text):
            aliases.add(m.group(1))
        if imports_client and "axios" in text: aliases.add("axios")
        if imports_client and "ky" in text: aliases.add("ky")
        if "@angular/common/http" in text and re.search(r"\bHttpClient\b",text):
            for m in re.finditer(r"\b(?:this\.)?(\w+)\s*:\s*HttpClient\b",text): aliases.add(m.group(1))
            aliases.add("http")  # commonly injected property, still import-confirmed
        for alias in sorted(aliases):
            for m in re.finditer(r"\b(?:this\.)?"+re.escape(alias)+r"\s*\.\s*(get|post|put|patch|delete|head|options)\s*(?:<[^()\n]{1,200}>)?\s*\(",text,re.I):
                args=arg_parser(text,m.end()-1)
                url=url_parser(args) if args else None
                if url: _add(out,path,m.start(),m.group(1),"client_http_call",url,"medium")
            for m in re.finditer(r"\b(?:this\.)?"+re.escape(alias)+r"\.request\s*\(\s*\{([^}]{1,1200})\}",text,re.S):
                props=m.group(1)
                u=re.search(r"\burl\s*:\s*(['\"`])([^'\"`\r\n]+)\1",props)
                meth=re.search(r"\bmethod\s*:\s*['\"]("+METHODS+r")['\"]",props,re.I)
                if u: _add(out,path,m.start(),meth.group(1) if meth else "UNKNOWN","client_http_call",u.group(2),"medium")
        if imports_client and "axios" in text:
            # axios({ url: '/users', method: 'POST' })
            for m in re.finditer(r"\baxios\s*\(\s*\{([^}]{1,1200})\}",text,re.S):
                props=m.group(1)
                u=re.search(r"\burl\s*:\s*(['\"`])([^'\"`\r\n]+)\1",props)
                meth=re.search(r"\bmethod\s*:\s*['\"]("+METHODS+r")['\"]",props,re.I)
                if u: _add(out,path,m.start(),meth.group(1) if meth else "GET","client_http_call",u.group(2),"medium")
        for name in ("$fetch", "ofetch", "useFetch"):
            if name not in text: continue
            # $fetch('/api/x'), ofetch('/api/x'), useFetch('/api/x')
            if name in {"ofetch","useFetch"} and not ("ofetch" in text or "#app" in text or "nuxt" in text.lower() or "$fetch" in text):
                continue
            for m in re.finditer(r"(?<![\w$])"+re.escape(name)+r"\s*(?:<[^()\n]{1,200}>)?\s*\(",text):
                args=arg_parser(text,m.end()-1)
                url=url_parser(args) if args else None
                if url:
                    meth=re.search(r"\bmethod\s*:\s*['\"]("+METHODS+r")['\"]",args,re.I)
                    _add(out,path,m.start(),meth.group(1) if meth else "GET","client_http_call",url,"medium")
    if path.endswith(".py"):
        modules=[]
        for m in re.finditer(r"\bimport\s+(requests|httpx)(?:\s+as\s+(\w+))?",text): modules.append(m.group(2) or m.group(1))
        session_aliases=[]
        for m in re.finditer(r"\b(\w+)\s*=\s*(requests|httpx)\.(?:Session|Client|AsyncClient)\s*\(",text):
            session_aliases.append(m.group(1))
        for mod in modules + session_aliases:
            for m in re.finditer(r"\b"+re.escape(mod)+r"\.(get|post|put|patch|delete|head|options)\s*\(\s*(['\"])([^'\"\r\n]+)\2",text,re.I):
                from urllib.parse import urlsplit
                raw=m.group(3)
                if raw.startswith(("http://","https://")):
                    raw=urlsplit(raw).path or "/"
                _add(out,path,m.start(),m.group(1),"client_http_call",raw,"medium")
    return out


def discover_conventions(sources: Mapping[str,str], *, arg_parser: Callable[[str,int],str|None], url_parser: Callable[[str],str|None]) -> list[RouteHint]:
    out = _js_declared_routes(sources) + _python_project_routes(sources)
    aliases_by_file: dict[str,set[str]] = {}
    for p,s in sources.items():
        if not p.endswith(JS_EXTS): continue
        imported = _imports(p,s,set(sources))
        for alias,target in imported.items():
            body=sources[target]
            if re.search(r"\bexport\s+default\s+(?:axios|ky)\.create\s*\(",body):
                aliases_by_file.setdefault(p,set()).add(alias)
            elif re.search(r"\bexport\s+default\s+(\w+)\s*;?",body):
                local=re.search(r"\bexport\s+default\s+(\w+)",body).group(1)
                if re.search(r"\b(?:const|let|var)\s+"+re.escape(local)+r"\s*=\s*(?:axios|ky)\.create\s*\(",body):
                    aliases_by_file.setdefault(p,set()).add(alias)
            else:
                # Named exports must match the specific imported symbol;
                # an unrelated export cannot establish an HTTP client alias.
                named=re.search(r"\bimport\s*\{([^}\n]+)\}\s*from\s*['\"]([^'\"]+)['\"]",s)
                if named:
                    for item in named.group(1).split(','):
                        words=item.strip().split()
                        if not words: continue
                        original=words[0]
                        local=words[-1] if len(words)>=3 and words[-2]=='as' else original
                        if local==alias and re.search(r"\bexport\s+const\s+"+re.escape(original)+r"\s*=\s*(?:axios|ky)\.create\s*\(",body):
                            aliases_by_file.setdefault(p,set()).add(alias)
    for path,source in sources.items():
        base=PurePosixPath(path).suffix.lower()
        if base in JS_EXTS or base in {".svelte", ".vue"}:
            routes,pages = _api_file_routes(path,source)
            out.extend(routes)
            out.extend(pages)
            if base in JS_EXTS:
                out.extend(_nest_routes(path,source))
        if base in {".java", ".kt"}: out.extend(_java_routes(path,source))
        if base == ".cs": out.extend(_csharp_routes(path,source))
        out.extend(_other_server_routes(path,source))
        out.extend(_client_routes(path,source,aliases_by_file.get(path,set()),arg_parser,url_parser))
    # HTML form submissions are browser HTTP actions but not API responses.
    # Only absolute literal action paths are considered; relative locations
    # depend on hosting/page URL and are intentionally left unresolved.
    for p,s in sources.items():
        if not p.endswith((".html",".htm",".php",".tsx",".jsx",".vue",".svelte")):
            continue
        for m in re.finditer(r"<form\b([^>]{0,1600})>",s,re.I|re.S):
            attrs=m.group(1)
            act=re.search(r"\baction\s*=\s*(['\"])([^'\"\r\n]+)\1",attrs,re.I)
            if not act or not act.group(2).startswith('/'): continue
            meth=re.search(r"\bmethod\s*=\s*(['\"])(GET|POST)\1",attrs,re.I)
            _add(out,p,m.start(),meth.group(2) if meth else 'GET','form_action',act.group(2),'medium')
    # Framework-specific page candidates inferred only with matching config.
    is_nuxt = any(re.search(r"(?:^|/)nuxt\.config\.[cm]?[jt]s$", p) for p in sources)
    if is_nuxt:
        for p in sources:
            m = re.search(r"(?:^|/)(?:src/)?pages/(.*)\.vue$",p)
            if m:
                rel=m.group(1)
                bits=rel.split("/")
                if bits[-1] == "index": bits.pop()
                url=_clean_path("/"+"/".join(bits))
                _add(out,p,0,"GET","page_route",url,"medium")
    # React Router + Vue Router + Angular Router literal route records.
    for p,s in sources.items():
        if not p.endswith(JS_EXTS): continue
        if re.search(r"\b(?:react-router-dom|createBrowserRouter|createRoutesFromElements|createHashRouter)\b",s):
            for m in re.finditer(r"<Route\b[^>]{0,400}\bpath\s*=\s*['\"]([^'\"]+)['\"]",s):
                _add(out,p,m.start(),"GET","page_route",m.group(1),"medium")
            for m in re.finditer(r"\bpath\s*:\s*['\"]([^'\"]+)['\"]",s):
                _add(out,p,m.start(),"GET","page_route",m.group(1),"low")
        if "createRouter" in s and ("vue-router" in s or "createWebHistory" in s):
            for m in re.finditer(r"\bpath\s*:\s*['\"]([^'\"]+)['\"]",s):
                _add(out,p,m.start(),"GET","page_route",m.group(1),"low")
        if "@angular/router" in s and "Routes" in s:
            for m in re.finditer(r"\bpath\s*:\s*['\"]([^'\"]*)['\"]",s):
                _add(out,p,m.start(),"GET","page_route",m.group(1) or "/","low")
    # Dict preserves first-encountered evidence while avoiding redundant detections.
    return list({(r.source,r.offset,r.path,r.method,r.kind):r for r in out}.values())
