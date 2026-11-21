"""Bounded inert source-derived test oracles (no import, eval, shell or network).

Source content is not proof that an application behaves as documented.  Phase 4
must revalidate both source provenance and the declarative test before running.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

MAX_SOURCE_FILE_BYTES = 1_000_000
MAX_OPENAPI_FILES = 12
MAX_OPENAPI_PATHS = 500


def _relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 220:
        return None
    if value.startswith(("/", "\\")) or "\\" in value or "\x00" in value:
        return None
    parts = value.split("/")
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        return None
    return str(PurePosixPath(value))


def source_bytes(source_root: Path | None, relative: str, *, limit: int = MAX_SOURCE_FILE_BYTES) -> bytes | None:
    """Read a bounded, regular file within a caller-selected checkout; no symlinks."""
    rel = _relative_path(relative)
    if source_root is None or rel is None:
        return None
    root = Path(source_root)
    if not root.is_dir() or root.is_symlink():
        return None
    path = root
    try:
        for part in PurePosixPath(rel).parts:
            path = path / part
            if path.is_symlink():
                return None
        if not path.is_file() or path.stat().st_size > limit:
            return None
        if not path.resolve().is_relative_to(root.resolve()):
            return None
        with path.open("rb") as stream:
            body = stream.read(limit + 1)
        return body if len(body) <= limit else None
    except (OSError, RuntimeError, ValueError):
        return None


def static_page_hash(source_root: Path | None, relative: str) -> str | None:
    if not relative.lower().endswith((".html", ".htm")):
        return None
    body = source_bytes(source_root, relative)
    return sha256(body).hexdigest() if body is not None else None


def openapi_get_contracts(source_root: Path | None, manifest: Mapping[str, Any]) -> tuple[list[dict], list[str]]:
    """Extract ONLY direct local OpenAPI 3 JSON GET/200 declarations.

    No external, relative/remote $refs, path args, credentials, request bodies,
    server rewrites, or inferred expected responses. Only status 200 is claimed.
    """
    issues: set[str] = set()
    if source_root is None:
        return [], []
    inv = manifest.get("inventory")
    file_roles = inv.get("file_roles", {}) if isinstance(inv, Mapping) else {}
    # Phase 1 indexes spec files under file_roles on some stacks, and the
    # entrypoint inventory may also mention them. Avoid arbitrary recursive IO.
    candidates = set()
    if isinstance(file_roles, Mapping):
        candidates.update(x for x in list(file_roles)[:3000] if isinstance(x, str))
    setup = manifest.get("setup_files")
    if isinstance(setup, list):
        candidates.update(x for x in setup[:3000] if isinstance(x, str))
    candidates.update(("openapi.json", "swagger.json", "api/openapi.json", "docs/openapi.json"))
    files = sorted(x for x in candidates if _relative_path(x) and x.lower().endswith(".json")
                   and any(t in PurePosixPath(x).name.lower() for t in ("openapi", "swagger")))
    if len(files) > MAX_OPENAPI_FILES:
        issues.add("OPENAPI_FILE_INVENTORY_TRUNCATED")
    contracts = []
    for path in files[:MAX_OPENAPI_FILES]:
        body = source_bytes(source_root, path)
        if body is None:
            continue
        try:
            doc = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            issues.add("OPENAPI_JSON_UNREADABLE")
            continue
        if not isinstance(doc, dict) or not str(doc.get("openapi", "")).startswith("3."):
            continue
        paths = doc.get("paths")
        if not isinstance(paths, dict):
            continue
        if len(paths) > MAX_OPENAPI_PATHS:
            issues.add("OPENAPI_PATHS_TRUNCATED")
        remote = bool(doc.get("servers"))
        global_security = bool(doc.get("security"))
        for url, item in sorted(paths.items())[:MAX_OPENAPI_PATHS]:
            if not isinstance(url, str) or not isinstance(item, dict):
                continue
            op = item.get("get")
            if not isinstance(op, dict):
                continue
            responses = op.get("responses")
            if not isinstance(responses, dict) or not isinstance(responses.get("200"), dict):
                continue
            # A source-authored contract is advisory until execution. Reject
            # cases that need unbound inputs or request context.
            params = (item.get("parameters") or [])
            extra = op.get("parameters") or []
            if not isinstance(params, list) or not isinstance(extra, list):
                continue
            required = any(not isinstance(p, dict) or p.get("required") is True or "$ref" in p
                           for p in (params + extra))
            blocked = (remote or global_security or bool(item.get("servers"))
                       or bool(op.get("servers")) or bool(op.get("security"))
                       or required or "requestBody" in op or "$ref" in op
                       or "$ref" in responses["200"])
            contracts.append({
                "path": url, "file": path, "assertable": not blocked,
                "limitations": (["OPENAPI_CONTEXT_OR_REQUIRED_INPUTS_UNRESOLVED"] if blocked else []),
            })
    # Multiple specs may name the same route; one deterministic, conservative
    # decision. A single blocked declaration wins over a permissive one.
    by_url: dict[str, dict] = {}
    for item in contracts:
        url = item["path"]
        prev = by_url.get(url)
        if prev is None:
            by_url[url] = item
        elif not item["assertable"]:
            by_url[url] = item
    return [by_url[url] for url in sorted(by_url)], sorted(issues)
