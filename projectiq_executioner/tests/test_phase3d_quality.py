"""Phase 3D quality: deduplication, source provenance, honest oracles and safety."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase3.generator import generate_test_plan, TestPlanInputError
from projectiq_executioner.tests.test_phase3_generator import SOURCE, manifest, candidate


def mk_manifest(*, routes=None, pages=None, features=None, proposals=None):
    return {"schema_version": "1.0", "phase": 1, "source": dict(SOURCE),
            "routes": routes or [], "pages": pages or [], "features": features or [],
            "proposed_test_targets": proposals or [], "entrypoints": [],
            "database_schema_files": [], "tests": [], "inventory": {}}


def write_source(root: Path, path: str, body: bytes):
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(body)


class TestPhase3DQuality(unittest.TestCase):
    def test_feature_proposal_collapses_into_inventory(self):
        m = mk_manifest(features=[{"name": "Smooth animations"}], proposals=[
            "Translate documented feature into observable assertions: Smooth animations",
            "Translate documented feature into observable assertions: Smooth animations",
        ])
        result = generate_test_plan(m)
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(result["status"], "DISCOVERY_ONLY")
        self.assertEqual(result["inventory"]["proposals"], [])
        self.assertEqual(len(result["inventory"]["features"][0]["related_proposals"]), 1)

    def test_route_proposal_attaches_without_creating_duplicate(self):
        m = mk_manifest(routes=[{"method": "GET", "path": "/api/users", "kind": "declared_http_route"}],
                        proposals=["Inspect contract and expected behavior for GET /api/users"])
        result = generate_test_plan(m)
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["inventory"]["proposals"], [])
        self.assertEqual(len(result["test_cases"][0]["related_proposals"]), 1)

    def test_dockerfile_and_npm_scripts_are_not_program_tests(self):
        m = mk_manifest()
        m["entrypoints"] = [{"path": "Dockerfile", "kind": "dockerfile"},
                            {"path": "package.json#scripts.start", "kind": "npm"},
                            {"path": "package.json#scripts.test", "kind": "npm"}]
        result = generate_test_plan(m)
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(result["status"], "DISCOVERY_ONLY")
        self.assertEqual(len(result["inventory"]["program_entrypoints"]), 3)

    def test_cli_entrypoint_without_routes_remains_candidate(self):
        m = mk_manifest()
        m["entrypoints"] = [{"path": "src/main.cpp", "kind": "main"}]
        result = generate_test_plan(m)
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["test_cases"][0]["category"], "PROGRAM_ENTRYPOINT")
        self.assertEqual(result["test_cases"][0]["status"], "NEEDS_ORACLE")

    def test_static_root_pages_add_real_status_oracles(self):
        m = mk_manifest(pages=["index.html", "about.html", "templates/private.html"])
        result = generate_test_plan(m, candidate(adapter="static_html_v1", probe="/index.html"))
        methods = [(x["target"]["path"], x["status"]) for x in result["test_cases"]]
        self.assertEqual(methods, [("/index.html", "READY_FOR_PHASE4"),
                                   ("/about.html", "READY_FOR_PHASE4")])
        self.assertTrue(all(x["oracle"]["status"] == "GROUNDED" for x in result["test_cases"]))
        self.assertEqual(result["inventory"]["page_sources"], [
            {"path": "about.html"}, {"path": "index.html"}, {"path": "templates/private.html"}])

    def test_static_hash_only_from_verified_source_and_no_raw_text(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            body = b'<!DOCTYPE html><title>Private secret text SHOULD NOT APPEAR</title>'
            write_source(root, "index.html", body)
            m = mk_manifest(pages=["index.html"])
            cand = candidate(adapter="static_html_v1", probe="/index.html")
            unverified = generate_test_plan(m, cand, source_root=root)
            verified = generate_test_plan(m, cand, source_root=root, source_verified=True)
            self.assertEqual(len(unverified["test_cases"][0]["assertions"]), 1)
            assertions = verified["test_cases"][0]["assertions"]
            self.assertEqual(assertions, [
                {"kind": "HTTP_BODY_SHA256_EQUALS", "expected": sha256(body).hexdigest()},
                {"kind": "HTTP_STATUS_EQUALS", "expected": 200},
            ])
            self.assertNotIn("Private secret", json.dumps(verified))

    def test_static_source_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "real.html", b"safe")
            try:
                (root / "index.html").symlink_to(root / "real.html")
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks unavailable")
            result = generate_test_plan(mk_manifest(pages=["index.html"]),
                    candidate(adapter="static_html_v1", probe="/index.html"),
                    source_root=root, source_verified=True)
            self.assertEqual(len(result["test_cases"][0]["assertions"]), 1)

    def test_openapi_get_200_from_pinned_source(self):
        m = mk_manifest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "openapi.json", json.dumps({
                "openapi": "3.0.3", "paths": {"/api/ping": {
                    "get": {"responses": {"200": {"description": "pong"}}}}}}
            ).encode())
            result = generate_test_plan(m, candidate(adapter="python_web_offline_v1", probe="invalid"),
                                        source_root=root, source_verified=True)
            self.assertEqual(result["summary"]["ready_for_phase4"], 1)
            test = result["test_cases"][0]
            self.assertEqual(test["target"], {"method": "GET", "path": "/api/ping", "label": None})
            self.assertEqual(test["assertions"], [{"kind": "HTTP_STATUS_EQUALS", "expected": 200}])
            self.assertEqual(test["oracle"]["basis"], "DECLARED_OPENAPI_GET_200")
            self.assertEqual(result["inventory"]["source_contracts"][0]["file"], "openapi.json")

    def test_unverified_source_openapi_is_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "openapi.json", json.dumps({"openapi": "3.1.0", "paths": {
                "/ping": {"get": {"responses": {"200": {"description": "OK"}}}}}}).encode())
            result = generate_test_plan(mk_manifest(), candidate(probe="invalid"), source_root=root)
            c = result["test_cases"][0]
            self.assertEqual(c["status"], "UNSUPPORTED_BY_RUNNER")
            self.assertIn("SOURCE_SNAPSHOT_NOT_VERIFIED", c["blockers"])
            self.assertEqual(result["summary"]["ready_for_phase4"], 0)

    def test_openapi_auth_dynamic_and_remote_do_not_yield_ready(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "openapi.json", json.dumps({
                "openapi": "3.0.0", "servers": [{"url": "https://api.external.test"}],
                "paths": {"/users/{id}": {"get": {"responses": {"200": {"description": "one"}}}},
                          "/secret": {"get": {"security": [{"basic": []}],
                                              "responses": {"200": {"description": "secret"}}}}}
            }).encode())
            result = generate_test_plan(mk_manifest(), candidate(probe="invalid"),
                                        source_root=root, source_verified=True)
            self.assertEqual(result["summary"]["ready_for_phase4"], 0)
            self.assertEqual(result["test_cases"][0]["assertions"], [])
            self.assertTrue(all(x["status"] == "NEEDS_ORACLE" for x in result["test_cases"]))

    def test_openapi_post_never_assumed_safe(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "openapi.json", json.dumps({"openapi": "3.0.0", "paths": {
                "/create": {"post": {"responses": {"200": {"description": "yes"}}}}}}).encode())
            result = generate_test_plan(mk_manifest(), candidate(probe="invalid"),
                                        source_root=root, source_verified=True)
            self.assertEqual(result["summary"]["ready_for_phase4"], 0)

    def test_openapi_input_file_limits(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "openapi.json", b' ' * 1_000_001)
            result = generate_test_plan(mk_manifest(), candidate(probe="invalid"),
                                        source_root=root, source_verified=True)
            self.assertEqual(result["summary"]["total"], 0)
            self.assertEqual(result["inventory"]["source_contracts"], [])

    def test_stable_output_order_and_ids(self):
        m = mk_manifest(routes=[
            {"method": "GET", "path": "/a", "kind": "declared_http_route"},
            {"method": "GET", "path": "/b", "kind": "client_http_call"},
            {"method": "GET", "path": "/a", "kind": "client_http_call"},
        ], pages=["a.html", "b.html"], features=[{"name":"z"},{"name":"a"}])
        other = deepcopy(m)
        for k in ("routes", "pages", "features"):
            other[k].reverse()
        one = generate_test_plan(m)
        two = generate_test_plan(other)
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))
        self.assertEqual(len({x["id"] for x in one["test_cases"]}), len(one["test_cases"]))

    def test_cannot_forge_verified_local_snapshot(self):
        m = mk_manifest()
        m["source"] = {"kind": "local", "repository": "myapp", "commit_sha": None}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TestPlanInputError):
                generate_test_plan(m, source_root=Path(d), source_verified=True)

    def test_no_adapter_does_not_turn_openapi_into_runnable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "openapi.json", json.dumps({"openapi": "3.0.0", "paths": {
                "/ping": {"get": {"responses": {"200": {"description": "OK"}}}}}}).encode())
            result = generate_test_plan(mk_manifest(), source_root=root, source_verified=True)
            self.assertEqual(result["summary"]["ready_for_phase4"], 0)
            self.assertTrue(result["test_cases"])
            self.assertEqual(result["test_cases"][0]["status"], "NEEDS_ORACLE")

    def test_empty_inventory_with_no_scenarios(self):
        result = generate_test_plan(mk_manifest())
        self.assertEqual(result["status"], "NO_TEST_TARGETS")
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(set(result["inventory"]), {
            "page_sources", "existing_tests", "program_entrypoints", "database_schemas",
            "features", "proposals", "source_contracts"})


class TestPhase3DContractValidation(unittest.TestCase):
    def test_valid_report_passes(self):
        from projectiq_executioner.phase3.contracts import validate_test_plan
        result = generate_test_plan(manifest(), candidate())
        self.assertIsNone(validate_test_plan(result))

    def test_ready_without_assertion_rejected(self):
        from projectiq_executioner.phase3.contracts import validate_test_plan, TestPlanContractError
        result = generate_test_plan(manifest(), candidate())
        result["test_cases"][0]["assertions"] = []
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(result)

    def test_duplicate_http_target_rejected(self):
        from projectiq_executioner.phase3.contracts import validate_test_plan, TestPlanContractError
        result = generate_test_plan(manifest(), candidate())
        extra = deepcopy(result["test_cases"][0])
        extra["id"] = "TC-" + "F" * 16
        result["test_cases"].append(extra)
        result["summary"]["total"] += 1
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(result)

    def test_result_never_claims_execution(self):
        from projectiq_executioner.phase3.contracts import validate_test_plan, TestPlanContractError
        result = generate_test_plan(manifest(), candidate())
        result["test_cases"][0]["execution"] = "PASS"
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(result)

    def test_cli_manifest_source_root_keeps_unverified(self):
        from projectiq_executioner.phase3.__main__ import main
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_source(root, "openapi.json", json.dumps({"openapi": "3.0.0", "paths": {
                "/ping": {"get": {"responses": {"200": {"description": "OK"}}}}}}).encode())
            m = root / "manifest.json"
            c = root / "candidate.json"
            m.write_text(json.dumps(mk_manifest()), encoding="utf-8")
            c.write_text(json.dumps(candidate(probe="invalid")), encoding="utf-8")
            with patch("builtins.print"):
                rc = main(["--manifest", str(m), "--candidate", str(c),
                           "--source-root", str(root), "--out", str(root / "out")])
            self.assertEqual(rc, 0)
            result = json.loads((root / "out" / "test_plan.json").read_text())
            self.assertFalse(result["execution_context"]["source_snapshot_verified"])
            self.assertEqual(result["summary"]["ready_for_phase4"], 0)

class TestPhase3DRepoPipeline(unittest.TestCase):
    def test_repo_mode_inspects_source_before_temporary_checkout_removed(self):
        """No GitHub or VM: use injection to test actual CLI checkout lifecycle."""
        from projectiq_executioner.phase3.__main__ import main
        from unittest.mock import patch
        from types import ModuleType, SimpleNamespace
        import sys

        fake_analyzer = ModuleType("projectiq_executioner.analyzer")
        fake_github = ModuleType("projectiq_executioner.github_client")
        fake_archive = ModuleType("projectiq_executioner.safe_archive")
        page = b'<!doctype html><title>test</title>'

        class Client:
            def __init__(self, *, timeout):
                self.timeout = timeout
            def resolve(self, repo, ref):
                return SimpleNamespace(repository="Fixture/Example", commit_sha=SOURCE["commit_sha"])
            def download(self, source, archive):
                archive.write_bytes(b"mock pinned archive")

        def extract(archive, into):
            checkout = into / "checkout"
            checkout.mkdir(parents=True)
            write_source(checkout, "index.html", page)
            return checkout

        def analyze(checkout, source):
            assert (checkout / "index.html").is_file()
            return SimpleNamespace(to_dict=lambda: mk_manifest(pages=["index.html"]))

        fake_github.GitHubClient = Client
        fake_archive.extract_snapshot = extract
        fake_analyzer.analyze_directory = analyze
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = root / "candidate.json"
            c.write_text(json.dumps(candidate(adapter="static_html_v1", probe="/index.html")))
            with patch.dict(sys.modules, {
                "projectiq_executioner.analyzer": fake_analyzer,
                "projectiq_executioner.github_client": fake_github,
                "projectiq_executioner.safe_archive": fake_archive,
            }), patch("builtins.print"):
                rc = main(["--repo", "https://github.com/Fixture/Example",
                           "--ref", SOURCE["commit_sha"], "--candidate", str(c),
                           "--out", str(root / "out")])
            self.assertEqual(rc, 0)
            result = json.loads((root / "out" / "test_plan.json").read_text())
            self.assertTrue(result["execution_context"]["source_snapshot_verified"])
            self.assertEqual(result["test_cases"][0]["assertions"][0],
                             {"kind": "HTTP_BODY_SHA256_EQUALS", "expected": sha256(page).hexdigest()})


if __name__ == "__main__":
    unittest.main()
