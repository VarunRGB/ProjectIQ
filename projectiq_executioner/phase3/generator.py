"""Deterministic test-case discovery from an inert Phase 1 repository manifest.

This module never imports/executes student code, calls an LLM, opens URLs,
installs dependencies, or launches VMs. The resulting test plan is NOT a
security approval or permission for Phase 4 to execute arbitrary inputs.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .source_contracts import _relative_path, static_page_hash, openapi_get_contracts
from .method_reconciliation import reconcile_file_methods

SCHEMA_VERSION = "1.1"
GENERATOR_VERSION = "0.2.1"
MAX_MANIFEST_BYTES = 8_000_000
MAX_CANDIDATE_BYTES = 200_000
MAX_TEST_CASES = 400
MAX_RECORDS_PER_FIELD = 3000
MAX_LABEL = 200

_SUPPORTED_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
_SERVER_ROUTE_KINDS = {"declared_http_route", "file_based_route", "file_endpoint", "declared_api_contract"}
_PAGE_ROUTE_KINDS = {"page_route"}
_CLIENT_ROUTE_KINDS = {"client_http_call", "form_action"}
_NON_HTTP_ROUTE_KINDS = {"graphql_operation", "rpc_method"}
_KNOWN_ADAPTERS = {
    "static_html_v1", "php_mysql_v1", "node_vite_offline_v1",
    "node_express_offline_v1", "node_fullstack_offline_v1", "python_web_offline_v1",
}
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,218}$")
_SHA = re.compile(r"^[a-fA-F0-9]{40}$")


class TestPlanInputError(ValueError):
    """Invalid/corrupted planning inputs; never run project code to recover."""


def load_input_json(path: str | Path, limit: int = MAX_MANIFEST_BYTES) -> dict:
    file = Path(path)
    if not file.is_file() or file.stat().st_size > limit:
        raise TestPlanInputError("Input JSON missing or exceeds configured size limit")
    try:
        value = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise TestPlanInputError("Input JSON could not be decoded") from exc
    if not isinstance(value, dict):
        raise TestPlanInputError("Input must be a JSON object")
    return value


def _bounded(value: Any, size: int = MAX_LABEL) -> str:
    """Plain inert label; never emit control characters or unbounded student text."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:size]


def _source(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != "1.0" or manifest.get("phase") != 1:
        raise TestPlanInputError("Phase 3 requires a Phase 1 schema_version 1.0 manifest")
    s = manifest.get("source")
    if not isinstance(s, Mapping) or s.get("kind") not in ("github", "local"):
        raise TestPlanInputError("Phase 1 source is missing or invalid")
    repo = s.get("repository")
    if not isinstance(repo, str) or not 1 <= len(repo) <= 240:
        raise TestPlanInputError("Source repository identity is invalid")
    commit = s.get("commit_sha")
    if s["kind"] == "github":
        if not isinstance(commit, str) or not _SHA.fullmatch(commit):
            raise TestPlanInputError("GitHub test generation requires a pinned full SHA")
        commit = commit.lower()
    else:
        commit = None
    return {"kind": s["kind"], "repository": repo, "commit_sha": commit}


def _same_source(source: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    other = candidate.get("source")
    return (isinstance(other, Mapping) and other.get("kind") == source["kind"]
            and other.get("repository") == source["repository"]
            and other.get("commit_sha") == source["commit_sha"])


def _path(value: Any, *, allow_relative: bool = False) -> str | None:
    """Canonical literal local paths only. Unknown/dynamic URLs are not guessed."""
    if not isinstance(value, str) or not 1 <= len(value) <= 220:
        return None
    if any(x in value for x in ("\\", "?", "#", "{", "}", "<", ">", "\x00")):
        return None
    if value.startswith(("http:", "https:", "//", "../", "./")):
        return None
    if not value.startswith("/"):
        if not allow_relative:
            return None
        value = "/" + value
    if "//" in value or any(part in (".", "..") for part in value.split("/")):
        return None
    return value if _PATH.fullmatch(value) else None


def _unbound(path: str) -> bool:
    return ":" in path or "%" in path or "*" in path


def _evidence(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    seen: dict[tuple[str, int | None, str], dict[str, Any]] = {}
    for obj in items[:32]:
        if not isinstance(obj, Mapping):
            continue
        source_path = _bounded(obj.get("path"), 220)
        if not source_path or source_path.startswith("/") or ".." in source_path.split("/"):
            continue
        line = obj.get("line")
        line = line if type(line) is int and 1 <= line <= 10_000_000 else None
        reason = _bounded(obj.get("reason"), 180)
        key = (source_path, line, reason)
        seen[key] = {"source": "repository", "path": source_path, "line": line, "reason": reason}
    return [seen[k] for k in sorted(seen, key=lambda x: (x[0], x[1] or 0, x[2]))]


def _identifier(category: str, method: str | None, target: str) -> str:
    fingerprint = json.dumps([category, method, target], separators=(",", ":"), ensure_ascii=True)
    return "TC-" + sha256(fingerprint.encode("utf-8")).hexdigest()[:16].upper()


def _make_case(*, category: str, title: str, method: str | None, target_path: str | None,
               target_label: str | None, evidence: list[dict], sources: list[str],
               status: str, blockers: list[str], assertions: list[dict] | None = None,
               oracle_basis: str | None = None, adapter: str | None = None) -> dict:
    key = target_path if target_path is not None else (target_label or "")
    return {
        "id": _identifier(category, method, key),
        "title": _bounded(title),
        "category": category,
        "status": status,
        "target": {"method": method, "path": target_path, "label": target_label},
        "assertions": assertions or [],
        "oracle": {
            "status": "GROUNDED" if assertions else "NOT_ESTABLISHED",
            "basis": oracle_basis,
        },
        "evidence": evidence,
        "evidence_kinds": sorted(set(sources)),
        "related_proposals": [],
        "required_adapter": adapter,
        "blockers": sorted(set(blockers)),
        "execution": "NOT_PERFORMED",
    }


def _empty(source: dict[str, Any], adapter: str | None, candidate_status: str | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": 3,
        "stage": "deterministic_test_generation",
        "status": "NO_TEST_TARGETS",
        "source": source,
        "generator": {"name": "projectiq_phase3_rules", "version": GENERATOR_VERSION,
                      "llm_used": False, "student_code_executed": False},
        "execution_context": {"adapter": adapter, "phase2_candidate_status": candidate_status,
                              "operator_approval": "NOT_EVALUATED",
                              "source_snapshot_verified": False},
        "summary": {"total": 0, "ready_for_phase4": 0, "needs_oracle": 0,
                    "unsupported_by_runner": 0, "categories": {},
                    "inventory_counts": {name: 0 for name in (
                        "page_sources", "existing_tests", "program_entrypoints",
                        "database_schemas", "features", "proposals", "source_contracts")}},
        "inventory": {"page_sources": [], "existing_tests": [], "program_entrypoints": [],
                      "database_schemas": [], "features": [], "proposals": [],
                      "source_contracts": []},
        "test_cases": [],
        "issues": [],
        "limitations": [
            "Test discovery does not establish application correctness.",
            "Client-derived HTTP methods describe intended requests, not server-enforced methods.",
            "Missing expected behavior is explicitly not a runnable assertion.",
            "A test plan never authorizes launching untrusted student code.",
            "Phase 4 must independently revalidate source, tests, runtime policy and oracles.",
        ],
    }


def generate_test_plan(manifest: Mapping[str, Any],
                       phase2_candidate: Mapping[str, Any] | None = None, *,
                       source_root: Path | None = None,
                       source_verified: bool = False) -> dict:
    """Produce stable JSON-ready test cases from any valid Phase 1 manifest.

    Only Phase 2 *readiness* probes with matching provenance and allowlisted
    adapters receive an assertion. Every other discovered behavior is a
    candidate, never a fabricated PASS/FAIL or invented functional oracle.
    """
    if not isinstance(manifest, Mapping):
        raise TestPlanInputError("Manifest must be an object")
    src = _source(manifest)
    if phase2_candidate is not None and not isinstance(phase2_candidate, Mapping):
        raise TestPlanInputError("Phase 2 candidate must be an object")
    adapter = None
    candidate_status = None
    probe = None
    if phase2_candidate is not None:
        if not _same_source(src, phase2_candidate):
            raise TestPlanInputError("Phase 1 and Phase 2 source provenance mismatch")
        candidate_status = _bounded(phase2_candidate.get("status"), 96)
        maybe_adapter = phase2_candidate.get("adapter")
        if maybe_adapter in _KNOWN_ADAPTERS and candidate_status == "CANDIDATE_NEEDS_VM_VERIFICATION":
            adapter = maybe_adapter
            probe = _path(phase2_candidate.get("probe_path"))

    report = _empty(src, adapter, candidate_status)
    # source_verified may be True only for a checkout tied to the pinned SHA
    # by the caller's trusted ingestion path. Phase 4 rechecks independently.
    if source_verified and (src["kind"] != "github" or source_root is None
                            or not Path(source_root).is_dir()):
        raise TestPlanInputError("Source verification requires a pinned GitHub checkout")
    report["execution_context"]["source_snapshot_verified"] = bool(source_verified)
    inventory = report["inventory"]
    cases: dict[tuple[str, str, str], dict] = {}
    issues: set[str] = set()

    def register(key: tuple[str, str, str], case: dict) -> None:
        """Merge duplicate evidence independent of inventory ordering."""
        previous = cases.get(key)
        if previous is None:
            cases[key] = case
            return
        all_evidence = previous["evidence"] + case["evidence"]
        index = {
            (x["source"], x["path"], x["line"], x["reason"]): x
            for x in all_evidence
        }
        previous["evidence"] = [index[k] for k in sorted(
            index, key=lambda x: (x[0], x[1], x[2] or 0, x[3]))]
        previous["evidence_kinds"] = sorted(set(previous["evidence_kinds"] + case["evidence_kinds"]))
        previous["blockers"] = sorted(set(previous["blockers"] + case["blockers"]))

    if phase2_candidate is None:
        issues.add("PHASE2_CANDIDATE_NOT_PROVIDED")
    elif not adapter:
        issues.add("NO_EXECUTABLE_PHASE2_ADAPTER")

    # One known Phase 2 probe is a READINESS check, never a functional assertion.
    if adapter and probe and not _unbound(probe):
        case = _make_case(
            category="HTTP_READINESS", title=f"Adapter readiness: GET {probe}",
            method="GET", target_path=probe, target_label=None,
            evidence=[{"source": "phase2_candidate", "path": "probe_path", "line": None,
                       "reason": "Fixed Phase 2 adapter startup readiness contract"}],
            sources=["phase2_candidate"], status="READY_FOR_PHASE4", blockers=[],
            assertions=[{"kind": "HTTP_STATUS_EQUALS", "expected": 200}],
            oracle_basis="PHASE2_ADAPTER_READINESS_CONTRACT", adapter=adapter)
        register(("HTTP_READINESS", "GET", probe), case)
    elif adapter:
        issues.add("PHASE2_READINESS_PROBE_UNUSABLE")

    # Inert, bounded extraction from actual repository source (when supplied).
    # The manifest itself is sufficient for generic discovery; source is optional.
    contracts, contract_issues = openapi_get_contracts(source_root, manifest)
    issues.update(contract_issues)
    inventory["source_contracts"] = [{"path": item["path"], "file": item["file"],
                                       "assertable": item["assertable"]}
                                     for item in contracts]
    contract_by_path = {item["path"]: item for item in contracts}

    routes = manifest.get("routes") or []
    if not isinstance(routes, list):
        issues.add("INVALID_ROUTE_INVENTORY")
        routes = []
    if len(routes) > MAX_RECORDS_PER_FIELD:
        issues.add("ROUTE_INVENTORY_TRUNCATED")
    # Reconcile an unknown PHP method using matched local client evidence.
    # This creates only a candidate, never an oracle or an approval.
    routes = reconcile_file_methods(manifest, routes)
    # Aggregate repeated server/client mentions for the same target.
    by_route: dict[tuple[str, str], dict] = {}
    for route in routes[:MAX_RECORDS_PER_FIELD]:
        if not isinstance(route, Mapping):
            issues.add("INVALID_ROUTE_RECORD")
            continue
        kind = route.get("kind")
        if kind not in _SERVER_ROUTE_KINDS | _PAGE_ROUTE_KINDS | _CLIENT_ROUTE_KINDS | _NON_HTTP_ROUTE_KINDS:
            issues.add("UNKNOWN_ROUTE_KIND_SKIPPED")
            continue
        raw_method = route.get("method")
        method = raw_method.upper() if isinstance(raw_method, str) else "UNKNOWN"
        if method not in _SUPPORTED_METHODS:
            method = "UNKNOWN"
        if kind in _NON_HTTP_ROUTE_KINDS:
            operation = _bounded(route.get("path"), 180)
            if operation:
                key = (kind, operation)
                case = _make_case(
                    category="NON_HTTP_API", title=f"Review {kind}: {operation}",
                    method=None, target_path=None, target_label=operation,
                    evidence=_evidence(route.get("evidence")), sources=[kind],
                    status="UNSUPPORTED_BY_RUNNER", blockers=["NO_TRUSTED_NON_HTTP_RUNNER", "EXPECTED_BEHAVIOR_UNKNOWN"])
                # Operation kinds can share names, so include kind in their stable ID.
                case["id"] = _identifier("NON_HTTP_API:" + kind, None, operation)
                register(("NON_HTTP_API", kind, operation), case)
            continue
        path = _path(route.get("path"), allow_relative=(kind == "file_endpoint"))
        if path is None:
            issues.add("UNSAFE_OR_UNRESOLVED_ROUTE_SKIPPED")
            continue
        key = (method, path)
        record = by_route.setdefault(key, {"sources": set(), "evidence": []})
        record["sources"].add(kind)
        if route.get("_client_method_hint") is True and kind == "file_endpoint":
            record["sources"].add("client_server_method_hint")
        record["evidence"].extend(_evidence(route.get("evidence")))

    # Declared OpenAPI GET operations are additional source-backed discovery,
    # not proof the server currently implements the documented behavior.
    for url, contract in sorted(contract_by_path.items()):
        valid = _path(url)
        if valid is None:
            issues.add("OPENAPI_UNSAFE_ROUTE_SKIPPED")
            continue
        record = by_route.setdefault(("GET", valid), {"sources": set(), "evidence": []})
        record["sources"].add("declared_api_contract")
        record["evidence"].append({"source": "repository", "path": contract["file"],
                                  "line": None, "reason": "Declared OpenAPI GET/200 response"})

    for (method, path), record in sorted(by_route.items()):
        kinds = record["sources"]
        has_server = bool(kinds & _SERVER_ROUTE_KINDS)
        is_page = bool(kinds & _PAGE_ROUTE_KINDS) and not has_server
        category = "UI_PAGE" if is_page else "HTTP_ROUTE"
        blockers = ["EXPECTED_BEHAVIOR_UNKNOWN"]
        if not has_server and not is_page:
            blockers.append("SERVER_ROUTE_NOT_VERIFIED")
        if _unbound(path):
            blockers.append("PATH_PARAMETERS_UNBOUND")
        if method == "UNKNOWN":
            blockers.append("HTTP_METHOD_UNKNOWN")
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            blockers.append("INPUT_FIXTURES_AND_STATE_RESET_REQUIRED")
        if not adapter:
            blockers.append("SUPPORTED_PHASE2_ADAPTER_NOT_ESTABLISHED")
        elif category == "UI_PAGE" and adapter not in {"static_html_v1", "node_vite_offline_v1", "node_fullstack_offline_v1", "php_mysql_v1", "python_web_offline_v1"}:
            blockers.append("PAGE_RUNNER_NOT_ESTABLISHED")
        contract = contract_by_path.get(path) if method == "GET" else None
        oracle_allowed = bool(contract and contract["assertable"] and has_server
                              and not _unbound(path) and adapter)
        assertions = ([{"kind": "HTTP_STATUS_EQUALS", "expected": 200}]
                      if oracle_allowed else [])
        if oracle_allowed:
            blockers.remove("EXPECTED_BEHAVIOR_UNKNOWN")
        if contract and not contract["assertable"]:
            blockers.extend(contract["limitations"])
        if oracle_allowed and not source_verified:
            blockers.append("SOURCE_SNAPSHOT_NOT_VERIFIED")
        case = _make_case(
            category=category, title=f"Verify {method} {path}",
            method=method if method != "UNKNOWN" else None,
            target_path=path, target_label=None,
            evidence=_evidence(record["evidence"]), sources=sorted(kinds),
            status=("READY_FOR_PHASE4" if oracle_allowed and source_verified
                    else "UNSUPPORTED_BY_RUNNER" if oracle_allowed else "NEEDS_ORACLE"),
            blockers=blockers, assertions=assertions,
            oracle_basis="DECLARED_OPENAPI_GET_200" if assertions else None,
            adapter=adapter)
        # Do not count readiness and a route declaration for the same GET
        # endpoint as two independent test cases. Preserve all evidence.
        readiness_key = ("HTTP_READINESS", "GET", path)
        if method == "GET" and readiness_key in cases:
            ready = cases[readiness_key]
            merged = {(x["source"], x["path"], x["line"], x["reason"]): x
                      for x in ready["evidence"] + case["evidence"]}
            ready["evidence"] = [merged[k] for k in sorted(merged,
                key=lambda k: (k[0], k[1], k[2] or 0, k[3]))]
            ready["evidence_kinds"] = sorted(set(ready["evidence_kinds"] + case["evidence_kinds"]))
            # Route discovery never overrides the established Phase 2 probe.
            # Its missing inputs/oracle are not blockers for an independent
            # trusted readiness assertion.
            continue
        register((category, method, path), case)

    # Non-test metadata is still available to faculty, but must not inflate
    # test counts. Keep discovered artifacts in separate, bounded inventories.
    def catalog(name: str, records: Any, extractor) -> None:
        if not isinstance(records, list):
            issues.add("INVALID_" + name.upper() + "_INVENTORY")
            return
        if len(records) > MAX_RECORDS_PER_FIELD:
            issues.add(name.upper() + "_INVENTORY_TRUNCATED")
        dedup = {}
        for obj in records[:MAX_RECORDS_PER_FIELD]:
            item = extractor(obj)
            if item is not None:
                key = json.dumps(item, sort_keys=True, ensure_ascii=True)
                dedup[key] = item
        inventory[name] = [dedup[key] for key in sorted(dedup)]

    def file_item(value, *, field="path", extras=()):
        raw = value.get(field) if isinstance(value, Mapping) else value
        path = _relative_path(raw)
        if path is None:
            return None
        item = {"path": path}
        for key in extras:
            v = value.get(key)
            if isinstance(v, str):
                item[key] = _bounded(v, 90)
        return item

    catalog("page_sources", manifest.get("pages", []), file_item)
    catalog("database_schemas", manifest.get("database_schema_files", []), file_item)
    catalog("existing_tests", manifest.get("tests", []),
            lambda obj: file_item(obj, extras=("kind", "framework")) if isinstance(obj, Mapping) else None)

    def entrypoint_item(obj):
        if not isinstance(obj, Mapping):
            return None
        path = obj.get("path")
        # Pseudo-paths such as package.json#scripts.test are metadata, not
        # filenames. Preserve them as bounded labels, never read/execute them.
        if not isinstance(path, str) or not path or len(path) > 220:
            return None
        if "#" in path:
            if _relative_path(path.split("#", 1)[0]) is None:
                return None
            return {"path": _bounded(path, 220), "kind": _bounded(obj.get("kind"), 90)}
        return file_item(obj, extras=("kind",))

    catalog("program_entrypoints", manifest.get("entrypoints", []), entrypoint_item)

    features = manifest.get("features", [])
    if not isinstance(features, list):
        issues.add("INVALID_FEATURE_INVENTORY")
        features = []
    feature_map = {}
    for feature in features[:MAX_RECORDS_PER_FIELD]:
        if not isinstance(feature, Mapping):
            continue
        label = _bounded(feature.get("name"), 180)
        if label:
            k = label.casefold()
            ev = _evidence(feature.get("evidence"))
            if k not in feature_map:
                feature_map[k] = {"name": label, "evidence": ev}
            else:
                item = feature_map[k]
                item["name"] = min(label, item["name"])
                by_ev = {(x["source"], x["path"], x["line"], x["reason"]): x
                         for x in item["evidence"] + ev}
                item["evidence"] = [by_ev[key] for key in sorted(by_ev,
                    key=lambda x: (x[0], x[1], x[2] or 0, x[3]))]
    inventory["features"] = [feature_map[k] for k in sorted(feature_map)]

    proposals = manifest.get("proposed_test_targets", [])
    if not isinstance(proposals, list):
        issues.add("INVALID_PROPOSED_TARGET_INVENTORY")
        proposals = []
    dedup_proposals = {}
    for proposal in proposals[:MAX_RECORDS_PER_FIELD]:
        label = _bounded(proposal, 180)
        if not label:
            continue
        # Only bookkeeping: a README proposal is not a second test case.
        dedup_proposals[label.casefold()] = label
    # Merge Phase 1 proposals onto the matching scenario or feature, rather
    # than counting a proposed test as a second test of the same behavior.
    remaining_proposals = []
    for key in sorted(dedup_proposals):
        label = dedup_proposals[key]
        prefix = "inspect contract and expected behavior for "
        feature_prefix = "translate documented feature into observable assertions: "
        attached = False
        if key.startswith(prefix):
            route = label[len(prefix):]
            method, sep, path = route.partition(" ")
            path = _path(path)
            if sep and path and method.upper() in _SUPPORTED_METHODS:
                for case in cases.values():
                    if case["target"]["method"] == method.upper() and case["target"]["path"] == path:
                        case["related_proposals"].append(label)
                        attached = True
                        break
        elif key.startswith(feature_prefix):
            name = label[len(feature_prefix):].casefold().rstrip(" .…")
            for feature in inventory["features"]:
                actual = feature["name"].casefold()
                if name and (actual.startswith(name) or name.startswith(actual)):
                    feature.setdefault("related_proposals", []).append(label)
                    attached = True
                    break
        if not attached:
            remaining_proposals.append(label)
    inventory["proposals"] = remaining_proposals

    # Only meaningful source program entrypoints generate a scenario, and only
    # if no more specific HTTP/non-HTTP behavior was discovered. A Dockerfile,
    # npm script or arbitrary configuration path is NOT a program test.
    executable_extensions = {".py", ".js", ".mjs", ".cjs", ".java", ".go", ".rs",
                             ".cpp", ".cc", ".c", ".cs", ".rb", ".php", ".sh"}
    if not by_route and not any(x[0] == "NON_HTTP_API" for x in cases):
        for item in inventory["program_entrypoints"]:
            path = item["path"]
            if "#" in path or Path(path).suffix.lower() not in executable_extensions:
                continue
            register(("PROGRAM_ENTRYPOINT", "", path), _make_case(
                category="PROGRAM_ENTRYPOINT", title=f"Determine behavior for {path}",
                method=None, target_path=None, target_label=path,
                evidence=[{"source": "repository", "path": path, "line": None,
                           "reason": "Discovered source entrypoint"}],
                sources=["entrypoint_inventory"], status="NEEDS_ORACLE",
                blockers=["PROGRAM_INPUTS_AND_OUTPUTS_NOT_ESTABLISHED",
                          "TRUSTED_RUNNER_NOT_CONFIGURED"], adapter=adapter))

    # An established static HTML adapter serves source-root HTML files as
    # literal resources. Build only GET/200 contracts for root-level HTML.
    # A source hash is added only for a verified pinned checkout.
    if adapter == "static_html_v1":
        for item in inventory["page_sources"]:
            label = item["path"]
            if "/" in label or not label.lower().endswith((".html", ".htm")):
                continue
            url = _path(label, allow_relative=True)
            if url is None:
                continue
            current = cases.get(("HTTP_READINESS", "GET", url))
            if current is None:
                current = _make_case(
                    category="UI_PAGE", title=f"Static page: GET {url}",
                    method="GET", target_path=url, target_label=None,
                    evidence=[{"source": "repository", "path": label, "line": None,
                               "reason": "Phase 1 root HTML page in static adapter"}],
                    sources=["page_inventory", "static_adapter_contract"],
                    status="READY_FOR_PHASE4", blockers=[],
                    assertions=[{"kind": "HTTP_STATUS_EQUALS", "expected": 200}],
                    oracle_basis="STATIC_ADAPTER_RESOURCE_CONTRACT", adapter=adapter)
                # Avoid two GET tests for same path (route vs page).
                prior_key = ("UI_PAGE", "GET", url)
                route_key = ("HTTP_ROUTE", "GET", url)
                prior = cases.pop(prior_key, None) or cases.pop(route_key, None)
                if prior:
                    merged = {(x["source"], x["path"], x["line"], x["reason"]): x
                              for x in current["evidence"] + prior["evidence"]}
                    current["evidence"] = [merged[k] for k in sorted(merged,
                        key=lambda k: (k[0], k[1], k[2] or 0, k[3]))]
                    current["evidence_kinds"] = sorted(set(current["evidence_kinds"] + prior["evidence_kinds"]))
                register(("UI_PAGE", "GET", url), current)
            digest = static_page_hash(source_root, label) if source_verified else None
            if digest:
                current["assertions"] = [a for a in current["assertions"]
                                         if a["kind"] != "HTTP_BODY_SHA256_EQUALS"]
                current["assertions"].append({"kind": "HTTP_BODY_SHA256_EQUALS", "expected": digest})
                current["assertions"].sort(key=lambda x: x["kind"])
                current["evidence_kinds"] = sorted(set(current["evidence_kinds"] + ["pinned_source_bytes"]))
                # A hash is an exact byte oracle, not a semantic UI assertion.

    # Prioritize actionable HTTP cases over large amounts of README prose.
    priority = {name: rank for rank, name in enumerate((
        "HTTP_READINESS", "HTTP_ROUTE", "UI_PAGE", "PROGRAM_ENTRYPOINT", "NON_HTTP_API"))}
    ordered = sorted(cases.values(), key=lambda x: (
        priority.get(x["category"], 99), x["target"]["path"] or x["target"]["label"] or "",
        x["target"]["method"] or "", x["id"]))
    if len(ordered) > MAX_TEST_CASES:
        issues.add("TEST_PLAN_TRUNCATED")
        # Deterministic priority: retain runnable readiness before advisory tests.
        ready = [x for x in ordered if x["status"] == "READY_FOR_PHASE4"]
        rest = [x for x in ordered if x["status"] != "READY_FOR_PHASE4"]
        ordered = (ready + rest)[:MAX_TEST_CASES]
    report["test_cases"] = ordered
    status_counts = Counter(x["status"] for x in ordered)
    categories = Counter(x["category"] for x in ordered)
    report["summary"] = {
        "total": len(ordered),
        "ready_for_phase4": status_counts["READY_FOR_PHASE4"],
        "needs_oracle": status_counts["NEEDS_ORACLE"],
        "unsupported_by_runner": status_counts["UNSUPPORTED_BY_RUNNER"],
        "categories": dict(sorted(categories.items())),
        "inventory_counts": {name: len(value) for name, value in sorted(inventory.items())},
    }
    report["issues"] = [{"code": code, "stage": "test_generation"} for code in sorted(issues)]
    report["status"] = ("GENERATED" if ordered else "DISCOVERY_ONLY"
                        if any(inventory.values()) else "NO_TEST_TARGETS")
    from .contracts import validate_test_plan
    validate_test_plan(report)
    return report


def write_test_plan(plan: Mapping[str, Any], output: str | Path) -> Path:
    """Do not replace existing run evidence. Use unique per-run output dirs."""
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
    return dest
