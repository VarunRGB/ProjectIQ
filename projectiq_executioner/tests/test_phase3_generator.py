"""Phase 3 unit tests: deterministic planning only; no network, VM or LLM."""
from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase3 import generate_test_plan, write_test_plan
from projectiq_executioner.phase3.generator import TestPlanInputError
from projectiq_executioner.phase3.__main__ import main

SHA = "c" * 40
SOURCE = {"kind": "github", "repository": "Fixture/Example", "commit_sha": SHA}


def manifest():
    return {
        "schema_version": "1.0", "phase": 1, "source": dict(SOURCE),
        "routes": [
            {"method": "GET", "path": "/health", "kind": "declared_http_route",
             "evidence": [{"path": "src/server.py", "line": 12, "reason": "route decorator"}]},
            {"method": "GET", "path": "/health", "kind": "client_http_call"},
            {"method": "POST", "path": "/register", "kind": "declared_http_route"},
            {"method": "GET", "path": "/items/:id", "kind": "declared_http_route"},
            {"method": "GET", "path": "/cart", "kind": "client_http_call"},
        ],
        "pages": ["templates/home.html"],
        "tests": [{"path": "tests/test_app.py", "kind": "pytest", "framework": "pytest"}],
        "features": [{"name": "User registration", "evidence": [{"path": "README.md", "line": 23, "reason": "feature described"}]}],
        "database_schema_files": ["schema/init.sql"],
    }


def candidate(adapter="python_web_offline_v1", probe="/health"):
    return {"source": dict(SOURCE), "adapter": adapter,
            "status": "CANDIDATE_NEEDS_VM_VERIFICATION", "probe_path": probe}


class TestContracts(unittest.TestCase):
    def test_no_llm_or_execution_and_stable_shape(self):
        plan = generate_test_plan(manifest(), candidate())
        self.assertEqual(set(plan), {
            "schema_version", "phase", "stage", "status", "source", "generator",
            "execution_context", "summary", "test_cases", "inventory", "issues", "limitations"})
        self.assertEqual(plan["generator"]["llm_used"], False)
        self.assertEqual(plan["generator"]["student_code_executed"], False)
        self.assertEqual(plan["status"], "GENERATED")
        self.assertTrue(all(c["execution"] == "NOT_PERFORMED" for c in plan["test_cases"]))
        self.assertEqual(plan["summary"]["total"], len(plan["test_cases"]))
        self.assertEqual(plan["summary"]["ready_for_phase4"], 1)
        self.assertEqual(plan["summary"]["unsupported_by_runner"], 0)
        self.assertEqual(plan["summary"]["total"], 4)
        self.assertEqual(plan["summary"]["inventory_counts"]["existing_tests"], 1)

    def test_deterministic_bytes_independent_of_route_order(self):
        original = manifest()
        shuffled = deepcopy(original)
        shuffled["routes"].reverse()
        first = generate_test_plan(original, candidate())
        second = generate_test_plan(shuffled, candidate())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_route_dedup_and_evidence(self):
        p = generate_test_plan(manifest(), candidate())
        routes = [c for c in p["test_cases"] if c["target"]["path"] == "/health"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["category"], "HTTP_READINESS")
        self.assertEqual(routes[0]["evidence_kinds"],
                         ["client_http_call", "declared_http_route", "phase2_candidate"])
        self.assertTrue(any(x["path"] == "src/server.py" for x in routes[0]["evidence"]))
        self.assertEqual(routes[0]["oracle"]["basis"], "PHASE2_ADAPTER_READINESS_CONTRACT")

    def test_phase2_adapter_readiness_only_has_assertion(self):
        p = generate_test_plan(manifest(), candidate())
        runnable = [c for c in p["test_cases"] if c["status"] == "READY_FOR_PHASE4"]
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0]["category"], "HTTP_READINESS")
        self.assertEqual(runnable[0]["assertions"], [{"kind": "HTTP_STATUS_EQUALS", "expected": 200}])
        self.assertEqual(runnable[0]["oracle"]["basis"], "PHASE2_ADAPTER_READINESS_CONTRACT")
        self.assertFalse(any(c["assertions"] for c in p["test_cases"] if c["category"] != "HTTP_READINESS"))

    def test_candidate_without_adapter_still_has_discovery(self):
        p = generate_test_plan(manifest())
        self.assertGreater(p["summary"]["total"], 1)
        self.assertEqual(p["summary"]["ready_for_phase4"], 0)
        self.assertIn("PHASE2_CANDIDATE_NOT_PROVIDED", [i["code"] for i in p["issues"]])
        self.assertTrue(all(c["status"] != "READY_FOR_PHASE4" for c in p["test_cases"]))

    def test_unknown_adapter_does_not_become_runnable(self):
        p = generate_test_plan(manifest(), candidate(adapter="unsafe_random_runner"))
        self.assertEqual(p["summary"]["ready_for_phase4"], 0)
        self.assertEqual(p["execution_context"]["adapter"], None)

    def test_provenance_mismatch_rejected(self):
        bad = candidate()
        bad["source"]["commit_sha"] = "d" * 40
        with self.assertRaisesRegex(TestPlanInputError, "provenance"):
            generate_test_plan(manifest(), bad)

    def test_reject_unpinned_github_source(self):
        m = manifest()
        m["source"]["commit_sha"] = "main"
        with self.assertRaisesRegex(TestPlanInputError, "full SHA"):
            generate_test_plan(m)

    def test_local_manifest_supported(self):
        m = manifest()
        m["source"] = {"kind": "local", "repository": "my-project", "commit_sha": None}
        p = generate_test_plan(m)
        self.assertEqual(p["source"]["kind"], "local")
        self.assertIsNone(p["source"]["commit_sha"])
        self.assertTrue(p["test_cases"])

    def test_schema_invalid(self):
        m = manifest()
        m["schema_version"] = "2.0"
        with self.assertRaises(TestPlanInputError):
            generate_test_plan(m)

    def test_unresolved_and_external_routes_not_treated_as_tests(self):
        m = manifest()
        m["routes"] = [
            {"kind": "client_http_call", "method": "GET", "path": "https://evil.test/a"},
            {"kind": "file_endpoint", "method": "GET", "path": "../admin.php"},
            {"kind": "declared_http_route", "method": "GET", "path": "/ok"},
        ]
        p = generate_test_plan(m)
        routes = [x["target"]["path"] for x in p["test_cases"] if x["category"] == "HTTP_ROUTE"]
        self.assertEqual(routes, ["/ok"])
        self.assertIn("UNSAFE_OR_UNRESOLVED_ROUTE_SKIPPED", [x["code"] for x in p["issues"]])

    def test_mutation_routes_require_fixtures(self):
        p = generate_test_plan(manifest(), candidate())
        reg = [x for x in p["test_cases"] if x["target"]["path"] == "/register"][0]
        self.assertIn("INPUT_FIXTURES_AND_STATE_RESET_REQUIRED", reg["blockers"])
        self.assertEqual(reg["status"], "NEEDS_ORACLE")
        self.assertEqual(reg["assertions"], [])

    def test_client_only_route_has_no_verified_server(self):
        p = generate_test_plan(manifest())
        cart = [x for x in p["test_cases"] if x["target"]["path"] == "/cart"][0]
        self.assertIn("SERVER_ROUTE_NOT_VERIFIED", cart["blockers"])
        self.assertEqual(cart["oracle"]["status"], "NOT_ESTABLISHED")

    def test_dynamic_params_are_not_guessed(self):
        p = generate_test_plan(manifest(), candidate())
        route = [x for x in p["test_cases"] if x["target"]["path"] == "/items/:id"][0]
        self.assertIn("PATH_PARAMETERS_UNBOUND", route["blockers"])
        self.assertEqual(route["assertions"], [])

    def test_inventoried_pages_are_not_fabricated_urls(self):
        p = generate_test_plan(manifest())
        self.assertFalse(any(x["category"] == "PAGE_SOURCE" for x in p["test_cases"]))
        self.assertEqual(p["inventory"]["page_sources"], [{"path": "templates/home.html"}])

    def test_non_http_operations_not_runnable(self):
        m = manifest()
        m["routes"] = [{"kind": "graphql_operation", "path": "Query.me", "method": "UNKNOWN"}]
        p = generate_test_plan(m)
        entries = [x for x in p["test_cases"] if x["category"] == "NON_HTTP_API"]
        self.assertEqual(entries[0]["status"], "UNSUPPORTED_BY_RUNNER")
        self.assertIsNone(entries[0]["target"]["path"])

    def test_generic_cpp_entrypoint_and_proposed_target(self):
        m = manifest()
        m["routes"], m["pages"], m["features"] = [], [], []
        m["tests"], m["database_schema_files"] = [], []
        m["entrypoints"] = [{"path": "src/main.cpp", "kind": "main"}]
        m["proposed_test_targets"] = ["Check sorting result for ascending input"]
        p = generate_test_plan(m)
        self.assertEqual(p["status"], "GENERATED")
        self.assertEqual({x["category"] for x in p["test_cases"]},
                         {"PROGRAM_ENTRYPOINT"})
        self.assertEqual(p["inventory"]["proposals"], ["Check sorting result for ascending input"])
        self.assertEqual(p["summary"]["ready_for_phase4"], 0)
        self.assertTrue(all(not x["assertions"] for x in p["test_cases"]))

    def test_graphql_and_rpc_same_name_have_distinct_ids(self):
        m = manifest()
        m["routes"] = [{"kind": "graphql_operation", "path": "FetchItem"},
                       {"kind": "rpc_method", "path": "FetchItem"}]
        p = generate_test_plan(m)
        ids = [x["id"] for x in p["test_cases"] if x["category"] == "NON_HTTP_API"]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(ids), len(set(ids)))

    def test_empty_valid_manifest_returns_same_envelope(self):
        m = manifest()
        for k in ("routes", "pages", "tests", "features", "database_schema_files"):
            m[k] = []
        p = generate_test_plan(m)
        self.assertEqual(p["status"], "NO_TEST_TARGETS")
        self.assertEqual(p["summary"]["total"], 0)
        self.assertEqual(p["test_cases"], [])

    def test_reports_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "test_plan.json"
            write_test_plan(generate_test_plan(manifest()), out)
            with self.assertRaises(FileExistsError):
                write_test_plan(generate_test_plan(manifest()), out)

    def test_truncated_catalog_bounded(self):
        m = manifest()
        m["features"] = [{"name": "Feature " + str(i)} for i in range(1000)]
        p = generate_test_plan(m)
        self.assertLessEqual(len(p["test_cases"]), 400)
        self.assertEqual(len(p["inventory"]["features"]), 1000)

    def test_duplicate_feature_ids_stable(self):
        m = manifest()
        m["features"] = [{"name": "Login"}, {"name": "Login"}]
        p = generate_test_plan(m)
        self.assertEqual(len(p["inventory"]["features"]), 1)
        self.assertFalse(any(c["category"] == "FEATURE" for c in p["test_cases"]))

    def test_bad_inventory_never_crashes(self):
        m = manifest()
        m["routes"] = [42, None, {"kind": "unknown", "path": "/x"}]
        m["pages"] = "not a list"
        p = generate_test_plan(m)
        self.assertIn("INVALID_ROUTE_RECORD", [x["code"] for x in p["issues"]])
        self.assertIn("INVALID_PAGE_SOURCES_INVENTORY", [x["code"] for x in p["issues"]])


class TestCli(unittest.TestCase):
    def test_manifest_mode_writes_report(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = root / "manifest.json"
            m.write_text(json.dumps(manifest()), encoding="utf-8")
            with patch("builtins.print"):
                rc = main(["--manifest", str(m), "--out", str(root / "out")])
            self.assertEqual(rc, 0)
            result = json.loads((root / "out" / "test_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "GENERATED")
            self.assertEqual(result["summary"]["ready_for_phase4"], 0)

    def test_bad_input_has_fixed_json_error_envelope(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = root / "invalid.json"
            m.write_text("not-json", encoding="utf-8")
            with patch("builtins.print"):
                rc = main(["--manifest", str(m), "--out", str(root / "out")])
            self.assertEqual(rc, 2)
            result = json.loads((root / "out" / "test_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "INPUT_ERROR")
            self.assertEqual(result["test_cases"], [])
            self.assertEqual(result["issues"][0]["code"], "INVALID_PLANNING_INPUT")

    def test_candidate_mode(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = root / "manifest.json"
            c = root / "candidate.json"
            m.write_text(json.dumps(manifest()), encoding="utf-8")
            c.write_text(json.dumps(candidate()), encoding="utf-8")
            with patch("builtins.print"):
                rc = main(["--manifest", str(m), "--candidate", str(c),
                           "--out", str(root / "out")])
            self.assertEqual(rc, 0)
            report = json.loads((root / "out" / "test_plan.json").read_text())
            self.assertEqual(report["summary"]["ready_for_phase4"], 1)

    def test_no_reuse_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = root / "manifest.json"
            m.write_text(json.dumps(manifest()), encoding="utf-8")
            with patch("builtins.print"):
                main(["--manifest", str(m), "--out", str(root / "out")])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["--manifest", str(m), "--out", str(root / "out")])


if __name__ == "__main__":
    unittest.main()
