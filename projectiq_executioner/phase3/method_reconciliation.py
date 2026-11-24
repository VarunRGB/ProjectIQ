"""Conservative reconciliation of Phase 1 PHP file endpoints and local client calls.

A client-observed method is an intended operation, not server enforcement, an
oracle, or permission to execute. No source or operator catalog is modified.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import re

_LIMIT = 3000
_METHODS = frozenset({'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'})
_SERVER = frozenset({'file_endpoint', 'declared_http_route', 'file_based_route', 'declared_api_contract'})
_LITERAL = re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z')


def _relative_file(path):
    if not isinstance(path, str) or len(path) > 219 or not _LITERAL.fullmatch(path):
        return None
    if any(x in ('.', '..') for x in path.split('/')):
        return None
    return path


def _local_shape(path):
    """Same bounded local spelling convention as Phase 1, not URL resolution."""
    if not isinstance(path, str) or not 1 <= len(path) <= 220 or path.startswith('//'):
        return None
    if path.startswith('/'):
        path = path[1:]
    else:
        while path.startswith(('../', './')):
            path = path.split('/', 1)[1]
    rel = _relative_file(path)
    return '/' + rel if rel else None


def _evidence_has(record, path):
    evidence = record.get('evidence')
    return (isinstance(evidence, list) and len(evidence) <= 64 and
            any(isinstance(row, Mapping) and row.get('path') == path for row in evidence))


def reconcile_file_methods(manifest, routes):
    """Return copied records only when a unique Phase 1 link supports a method.

    Never guess a method using a filename, a catalog or a desired test ID.
    Validate the link against both raw records and their evidence locations.
    Ambiguous, conflicting, external, missing or capped evidence is left alone.
    """
    inventory = manifest.get('inventory')
    if not isinstance(inventory, Mapping) or inventory.get('static_source_scan_capped') is True:
        return routes
    links = inventory.get('api_links')
    if not isinstance(links, list) or len(links) > _LIMIT or len(routes) > _LIMIT:
        return routes
    servers, clients = defaultdict(list), defaultdict(list)
    for i, record in enumerate(routes):
        if not isinstance(record, Mapping):
            continue
        shape = _local_shape(record.get('path'))
        if shape is None:
            continue
        kind = record.get('kind')
        if isinstance(kind, str) and kind in _SERVER:
            servers[shape].append(i)
        elif kind == 'client_http_call':
            clients[shape].append(i)
    linked = defaultdict(set)
    for link in links:
        if not isinstance(link, Mapping) or link.get('server_kind') != 'file_endpoint' or link.get('status') != 'method_unverified' or link.get('server_method') != 'UNKNOWN':
            continue
        method = link.get('client_method')
        server_file = _relative_file(link.get('server_source'))
        client_file = _relative_file(link.get('client_source'))
        if not isinstance(method, str) or method not in _METHODS or not server_file or not server_file.lower().endswith('.php') or not client_file:
            continue
        shape = _local_shape(link.get('server_path'))
        if shape != '/' + server_file or _local_shape(link.get('client_path')) != shape:
            continue
        if len(servers[shape]) != 1:
            continue
        si = servers[shape][0]
        server = routes[si]
        if (server.get('kind') != 'file_endpoint' or server.get('method') != 'UNKNOWN'
                or server.get('path') != link.get('server_path') or not _evidence_has(server, server_file)):
            continue
        for ci in clients[shape]:
            client = routes[ci]
            if (client.get('path') == link.get('client_path') and client.get('method') == method
                    and _evidence_has(client, client_file)):
                linked[si].add((ci, method))
    replacements = {}
    for si, matches in sorted(linked.items()):
        server = routes[si]
        shape = _local_shape(server.get('path'))
        methods = {method for _, method in matches}
        # Include *all* observed client methods for this spelling in conflict
        # detection, not merely the successfully linked observations.
        if len(methods) != 1 or any(not isinstance(routes[ci].get('method'), str) or routes[ci].get('method') not in methods for ci in clients[shape]):
            continue
        method = next(iter(methods))
        replacements[si] = dict(server, method=method, _client_method_hint=True)
        for ci, _ in sorted(matches):
            replacements[ci] = dict(routes[ci], path=shape)
    if not replacements:
        return routes
    return [replacements.get(i, record) for i, record in enumerate(routes)]
