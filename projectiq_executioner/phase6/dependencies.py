"""Operator-authored declarative dependency edges; never infer causal edges by name."""
from __future__ import annotations
from .contracts import fail, safe_id, source_key


def read_edges(value: dict, *, expected_source: tuple, allowed_ids: set[str]) -> dict[str,set[str]]:
    if value.get('schema_version') != '0.1' or source_key(value.get('source')) != expected_source:
        fail('DEPENDENCY_SOURCE_MISMATCH')
    rows = value.get('edges')
    if type(rows) is not list or len(rows) > 400:
        fail('INVALID_DEPENDENCY_EDGES')
    graph: dict[str,set[str]] = {}
    for item in rows:
        if type(item) is not dict or set(item) != {'test_id','requires'}:
            fail('INVALID_DEPENDENCY_EDGE')
        x,y = safe_id(item.get('test_id')), safe_id(item.get('requires'))
        if x not in allowed_ids or y not in allowed_ids or x == y:
            fail('DEPENDENCY_UNKNOWN_TEST_ID')
        graph.setdefault(x,set()).add(y)
    visiting,visited=set(),set()
    def check(v: str):
        if v in visiting:fail('DEPENDENCY_CYCLE')
        if v in visited:return
        visiting.add(v)
        for p in sorted(graph.get(v,())):check(p)
        visiting.remove(v);visited.add(v)
    for node in sorted(graph):check(node)
    return graph
