"""Phase 1 regression and safety tests. No external network or student-code execution."""
from __future__ import annotations

import contextlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.analyzer import analyze_directory
from projectiq_executioner.cli import main
from projectiq_executioner.github_client import GitHubClient, IngestionError, parse_github_url
from projectiq_executioner.indexer import SourceIndex
from projectiq_executioner.models import RepositorySource
from projectiq_executioner.report import write_reports
from projectiq_executioner.safe_archive import ArchiveError, ArchiveLimits, extract_snapshot


def write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def zip_files(path: Path, files: dict[str, str | bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            # On Windows, ZipInfo(name) converts backslashes to '/' before
            # storing the entry. Set the raw filename AFTER construction so
            # security tests can create genuinely malformed ZIP paths.
            info = zipfile.ZipInfo("placeholder")
            info.filename = name
            info.orig_filename = name
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, content)


class GitHubInputTests(unittest.TestCase):
    def test_accepted_urls(self):
        self.assertEqual(parse_github_url("https://github.com/VarunRGB/Mini-Task-Collaboration-App.git"),
                         ("VarunRGB", "Mini-Task-Collaboration-App"))
        self.assertEqual(parse_github_url("https://github.com/x/Repo-7/"), ("x", "Repo-7"))

    def test_rejected_urls(self):
        urls = [
            "http://github.com/o/r", "https://github.com.evil.net/o/r", "https://github.com/o/r/tree/main",
            "https://github.com/o/r?ref=x", "https://user:pass@github.com/o/r", "https://github.com:8443/o/r",
            "https://github.com/o/%2e%2e", "https://github.com/o/r#head", "git@github.com:o/r.git",
            "https://github.com/o/r/../../etc/passwd", "https://127.0.0.1/o/r",
        ]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(IngestionError):
                parse_github_url(url)

    def test_default_branch_is_not_assumed(self):
        client = GitHubClient()
        client._get_json = lambda path: ({"private": False, "default_branch": "master"}
                                          if path.endswith("/repos/o/r")
                                          else {"sha": "a" * 40})
        s = client.resolve("https://github.com/o/r.git")
        self.assertEqual(s.resolved_ref, "master")
        self.assertEqual(s.commit_sha, "a" * 40)
        self.assertIsNone(s.requested_ref)

    def test_custom_ref_and_private_rejection(self):
        client = GitHubClient()
        seen = []
        def getter(path):
            seen.append(path)
            return {"private": False, "default_branch": "main"} if path == "/repos/o/r" else {"sha": "1" * 40}
        client._get_json = getter
        self.assertEqual(client.resolve("https://github.com/o/r", "feature/xyz").requested_ref, "feature/xyz")
        self.assertIn("/repos/o/r/commits/feature%2Fxyz", seen)
        client._get_json = lambda path: {"private": True, "default_branch": "main"}
        with self.assertRaises(IngestionError):
            client.resolve("https://github.com/o/r")


class SafeArchiveTests(unittest.TestCase):
    def test_safe_archive_extracts_under_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            z = tmp / "good.zip"
            zip_files(z, {"repo-abcd/README.md": "hello", "repo-abcd/src/app.py": "print('not executed')"})
            root = extract_snapshot(z, tmp / "out")
            self.assertEqual((root / "src/app.py").read_text(), "print('not executed')")
            self.assertEqual(root.name, "checkout")

    def test_traversal_and_absolute_paths_are_rejected(self):
        for name in ("repo/../escape.txt", "repo/x/../../escape", "/tmp/out", "repo/C:/evil.txt", "repo/a\\b.txt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                z = tmp / "bad.zip"
                zip_files(z, {"repo/good": "ok", name: "bad"})
                with self.assertRaises(ArchiveError):
                    extract_snapshot(z, tmp / "out")

    def test_windows_case_collision_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            z = tmp / "collision.zip"
            zip_files(z, {"repo/A.txt": "one", "repo/a.txt": "two"})
            with self.assertRaises(ArchiveError): extract_snapshot(z, tmp / "out")

    def test_archive_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            z = tmp / "link.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("repo/file.txt", "hi")
                link = zipfile.ZipInfo("repo/link")
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                f.writestr(link, "../outside")
            with self.assertRaises(ArchiveError): extract_snapshot(z, tmp / "out")

    def test_expanded_size_limit_and_multiple_roots(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            z = tmp / "large.zip"
            zip_files(z, {"repo/x": "a" * 2000})
            with self.assertRaises(ArchiveError):
                extract_snapshot(z, tmp / "out", ArchiveLimits(max_uncompressed_bytes=100))
            zip_files(z, {"repo/x": "a", "different/y": "b"})
            with self.assertRaises(ArchiveError): extract_snapshot(z, tmp / "out2")


class ManifestTests(unittest.TestCase):
    def test_phase1_avoids_fake_features_and_php_server_variable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "README.md", "# Tasks\n\n## Features\n- Users can:\n  - Add tasks\n- Admins can:\n  - Delete tasks\n- Proper project structure with separate folders\n")
            write(root, "public/index.html", "<html><body>Task app</body></html>")
            write(root, "public/app.js", "fetch('../logic/get_tasks.php');")
            write(root, "logic/get_tasks.php", "<?php echo '[]'; ?>")
            write(root, "logic/check_method.php", "<?php if ($_SERVER['REQUEST_METHOD'] === 'GET') { echo 'ok'; } ?>")
            m = analyze_directory(root)
            features = {x.name for x in m.features}
            self.assertIn("Add tasks", features)
            self.assertIn("Delete tasks", features)
            self.assertNotIn("Users can", features)
            self.assertNotIn("Admins can", features)
            self.assertNotIn("REQUEST_METHOD", m.environment_variables)
            self.assertFalse(any("project structure" in t.lower() for t in m.proposed_test_targets))
            self.assertTrue(any("frontend calls use GET; not server-verified" in t for t in m.proposed_test_targets))
            self.assertTrue(any("logical components" in t for t in m.unknowns))

    def test_php_env_var_still_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "app.php", "<?php $x = $_ENV['DATABASE_URL']; $y = getenv('APP_SECRET'); ?>")
            m = analyze_directory(root)
            self.assertEqual(m.environment_variables, ['APP_SECRET', 'DATABASE_URL'])

    def test_php_mysql_static_site_and_feature_discovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "README.md", "# Any Project\n\n## Features\n- Register users and login\n- Add and update tasks\n\n## Installation\nUse MySQL\n")
            write(root, "auth/login.php", "<?php session_start(); $data=json_decode(file_get_contents('php://input'), true); ?>")
            write(root, "db/db.php", "<?php $pass = 'DO_NOT_LEAK_THIS_SECRET'; $pdo = new PDO('mysql:host=localhost', 'root', $pass); ?>")
            write(root, "logic/add_task.php", "<?php $body = file_get_contents('php://input'); ?>")
            write(root, "public/index.html", "<title>Tasks</title>")
            write(root, "public/js/app.js", "fetch('../auth/login.php',{method:'POST'}); fetch('../logic/add_task.php',{method:'POST'});")
            write(root, "sql/schema.sql", "CREATE TABLE tasks (id INT NOT NULL) ENGINE=InnoDB;")
            m = analyze_directory(root)
            self.assertEqual(m.architecture, "fullstack_web")
            self.assertIn("PHP", m.languages)
            self.assertIn("MySQL", {t.name for t in m.technologies})
            self.assertIn("public/index.html", m.pages)
            self.assertIn("sql/schema.sql", m.database_schema_files)
            self.assertTrue(any(r.path == "auth/login.php" for r in m.routes))
            self.assertTrue(any(r.path == "../auth/login.php" and r.method == "POST" for r in m.routes))
            self.assertTrue(any(f.origin == "readme" and "Register" in f.name for f in m.features))
            self.assertTrue(any(f.code == "POSSIBLE_HARDCODED_CREDENTIAL" for f in m.findings))
            output = root / "reports"
            j, md = write_reports(m, output)
            self.assertNotIn("DO_NOT_LEAK_THIS_SECRET", j.read_text())
            self.assertNotIn("DO_NOT_LEAK_THIS_SECRET", md.read_text())
            self.assertIn('"phase": 1', j.read_text())
            self.assertNotIn('"status": "PASS"', j.read_text())

    def test_react_node_mongo_monorepo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "apps/web/package.json", json.dumps({"dependencies": {"react":"*", "vite":"*"}, "scripts":{"dev":"vite", "test":"vitest"}, "devDependencies":{"vitest":"*"}}))
            write(root, "apps/api/package.json", json.dumps({"dependencies": {"express":"*", "mongoose":"*"}, "scripts":{"start":"node app.js"}}))
            write(root, "apps/api/server.js", "const app=express(); app.get('/health', (req,res)=>res.send('ok')); app.post('/api/todos',(req,res)=>{});")
            write(root, "apps/web/src/App.jsx", "import React from 'react'; export default function App(){return <div/>}")
            write(root, "apps/web/src/main.test.js", "test('x',()=>{})")
            write(root, "pnpm-lock.yaml", "lockfileVersion: '9.0'")
            m = analyze_directory(root)
            tech = {t.name for t in m.technologies}
            self.assertTrue({"React", "Express", "MongoDB", "Vite", "pnpm"} <= tech)
            self.assertEqual(m.architecture, "fullstack_web")
            self.assertTrue(any(r.path == "/health" and r.method == "GET" for r in m.routes))
            self.assertTrue(any(t.path == "apps/web/src/main.test.js" for t in m.tests))
            self.assertIn("apps/web/package.json", m.package_scripts)
            self.assertTrue(any(s.root == "apps/web" and s.kind == "frontend" for s in m.services))
            self.assertTrue(any(s.root == "apps/api" and s.kind == "backend" for s in m.services))

    def test_fastapi_stack_and_env_name_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "requirements.txt", "fastapi>=0.100\npytest>=7\npsycopg2>=2\n")
            write(root, "app.py", "import os\nfrom fastapi import FastAPI\napp=FastAPI()\n@app.get('/users')\ndef users(): return []\nkey = os.getenv('API_TOKEN')\n")
            write(root, "test_app.py", "def test_users(): assert True\n")
            m = analyze_directory(root)
            self.assertEqual(m.architecture, "backend_service")
            self.assertIn("API_TOKEN", m.environment_variables)
            self.assertIn("/users", [r.path for r in m.routes])
            self.assertTrue(any(t.name == "PostgreSQL" for t in m.technologies))
            self.assertEqual(len(m.tests), 1)

    def test_java_spring_and_static_html(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "backend/pom.xml", "<project><artifactId>spring-boot-starter-web</artifactId></project>")
            write(root, "backend/src/main/java/Controller.java", "@RestController\n@GetMapping('/health')\npublic String ok(){}")
            write(root, "frontend/index.html", "<!doctype html><title>Hello</title>")
            m = analyze_directory(root)
            self.assertIn("Spring Boot", {t.name for t in m.technologies})
            self.assertIn("/health", [r.path for r in m.routes])
            self.assertEqual(m.architecture, "fullstack_web")
            self.assertIn("frontend/index.html", m.pages)

    def test_nextjs_api_and_package_scripts_are_not_leaked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "package.json", json.dumps({"dependencies":{"next":"*", "react":"*"},
                "scripts":{"start":"DB_PASSWORD=SUPER_SECRET_VALUE next start", "test":"jest"}}))
            write(root, "app/api/users/route.ts", "export async function GET(req) { return Response.json([]); }")
            write(root, "app/page.tsx", "export default function Page() { return null; }")
            m = analyze_directory(root)
            self.assertEqual(m.architecture, "fullstack_web")
            self.assertTrue(any(r.path == "/api/users" and r.method == "GET" for r in m.routes))
            self.assertIn("package.json#scripts.start", {e.path for e in m.entrypoints})
            self.assertNotIn("SUPER_SECRET_VALUE", json.dumps(m.to_dict()))

    def test_client_call_methods_not_confused_across_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "public/index.html", "ok")
            write(root, "public/app.js", "fetch('/status'); fetch('/tasks', { method: 'POST' });")
            m = analyze_directory(root)
            self.assertTrue(any(r.path == "/status" and r.method == "GET" for r in m.routes))
            self.assertTrue(any(r.path == "/tasks" and r.method == "POST" for r in m.routes))

    def test_unexecuted_and_unknown_not_fabricated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "src/main.cpp", "int main(){return 0;}")
            m = analyze_directory(root)
            self.assertEqual(m.architecture, "cli_or_library")
            self.assertEqual(m.routes, [])
            self.assertFalse(any(t.path.endswith('.test.py') for t in m.tests))
            self.assertTrue(any("No repository-owned automated test suite" in x for x in m.unknowns))

    def test_sensitive_files_not_read_and_symlinks_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, ".env", "API_KEY=TOP_SECRET")
            write(root, ".env.example", "API_KEY=replace_me")
            write(root, "app.py", "import os\nsecret = os.getenv('API_KEY')\n")
            index = SourceIndex.build(root)
            self.assertIsNone(index.read(".env"))
            m = analyze_directory(root)
            self.assertIn("API_KEY", m.environment_variables)
            self.assertTrue(any(f.code == "TRACKED_ENV_FILE" for f in m.findings))
            self.assertNotIn("TOP_SECRET", json.dumps(m.to_dict()))
            try: (root / "symlink.py").symlink_to(root / "app.py")
            except (OSError, NotImplementedError): return  # Windows without symlink privileges
            scanned = SourceIndex.build(root)
            self.assertNotIn("symlink.py", scanned.files)

    def test_cli_local_writes_both_reports(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root / "subject", "main.py", "print('not executed')")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([str(root / "subject"), "--out", str(root / "report")])
            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "report/manifest.json").exists())
            self.assertTrue((root / "report/manifest.md").exists())
            self.assertIn("NOT PERFORMED", stdout.getvalue())

    def test_cli_remote_archive_integration_mocked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stdout = io.StringIO()
            def fake_resolve(client, url, ref):
                return RepositorySource("github", "owner/example", "https://github.com/owner/example", None, "master", "c"*40)
            def fake_download(client, source, destination):
                zip_files(destination, {"example-snapshot/README.md":"# Example\n## Features\n- Add notes\n", "example-snapshot/public/index.html":"<h1>OK</h1>"})
            with patch.object(GitHubClient, "resolve", fake_resolve), patch.object(GitHubClient, "download", fake_download), contextlib.redirect_stdout(stdout):
                status = main(["https://github.com/owner/example.git", "--out", str(root / "out")])
            self.assertEqual(status, 0)
            output = json.loads((root / "out/manifest.json").read_text())
            self.assertEqual(output["source"]["commit_sha"], "c"*40)
            self.assertEqual(output["architecture"], "frontend_web")
            self.assertEqual(output["features"][0]["name"], "Add notes")


    def test_nextjs_external_typescript_api_wrapper_and_pages(self):
        """Next.js frontend plus independently hosted backend: do not invent local DB."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "package.json", json.dumps({"dependencies": {
                "next": "16.2.1", "react": "19.2.4", "next-cloudinary": "6.0",
                "tailwindcss": "4.0"}, "scripts": {"dev": "next dev"}}))
            write(root, "src/app/page.tsx", "export default function Page(){ return null; }")
            write(root, "src/app/shop/[id]/page.tsx", "export default function Page(){ return null; }")
            write(root, "src/app/(store)/cart/page.tsx", "export default function Page(){ return null; }")
            write(root, "src/app/login/page.tsx", "export default function Page(){ return null; }")
            write(root, "src/lib/api.ts", """
const BASE_URL = "https://sample-backend.example.invalid";
async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const res = await fetch(`${BASE_URL}${endpoint}`, options);
    return res.json();
}
export const authApi = {
  login: (body: any) => request("/login", {method:"POST", body: JSON.stringify(body)}),
  logout: () => localStorage.clear(),
};
export const cartApi = {
  getCart: () => request("/cart"),
  addToCart: (body: any) => request("/cart/add", {method:"POST", body}),
  remove: (id: string) => request(`/cart/remove/${id}`, {method:"DELETE"}),
  dynamic: (category: string) => request(`/plants${category ? `?category=${category}` : ""}`),
};
""")
            m = analyze_directory(root)
            self.assertEqual(m.architecture, "frontend_web")
            self.assertEqual({s.kind for s in m.services}, {"frontend", "external_dependency"})
            self.assertNotIn("MongoDB", {x.name for x in m.technologies})
            self.assertNotIn("Express", {x.name for x in m.technologies})
            self.assertTrue({"Tailwind CSS", "Cloudinary integration"} <= {t.name for t in m.technologies})
            client = {(r.path,r.method) for r in m.routes if r.kind=="client_http_call"}
            self.assertTrue({("/login","POST"), ("/cart", "GET"),
                             ("/cart/add","POST"), ("/cart/remove/:param","DELETE")} <= client)
            self.assertIn(("/plants", "GET"), client)  # Empty-query branch is a known URL.
            page_routes = {r.path for r in m.routes if r.kind == "page_route"}
            self.assertEqual(page_routes,{"/", "/shop/:id", "/cart", "/login"})
            self.assertTrue(any("external HTTP API" in item for item in m.unknowns))
            self.assertTrue(any("external/client API" in target for target in m.proposed_test_targets))
            self.assertTrue(any("UI route" in target for target in m.proposed_test_targets))
            self.assertNotIn("sample-backend.example.invalid", json.dumps(m.to_dict()))

    def test_typescript_generic_wrapper_and_optional_query_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "src/lib/api.ts", '''
async function request<T>(endpoint: string, opts: RequestInit = {}): Promise<T> {
  return fetch(`https://example.com${endpoint}`, opts).then(r => r.json());
}
export const api = {
  login: (data: unknown) => request<any>("/login", {method: "POST", body: JSON.stringify(data)}),
  list: (category = "") => request(`/plants${category ? `?category=${category}` : ""}`),
  unsafe: (suffix: string) => request(`/danger${suffix ? `/delete` : ""}`),
};
''')
            m = analyze_directory(root)
            calls = {(r.method, r.path) for r in m.routes if r.kind == "client_http_call"}
            self.assertIn(("POST", "/login"), calls)
            self.assertIn(("GET", "/plants"), calls)
            self.assertNotIn(("GET", "/danger"), calls)

    def test_complete_typescript_api_client_pattern_corpus(self):
        """Catch missing typed calls and query-aware URL templates together."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, "src/lib/api.ts", '''
const BASE_URL = "https://api.example.invalid";
async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  return fetch(`${BASE_URL}${endpoint}`, options);
}
export const authApi = {
  signup: (body: any) => request("/signup", {method: "POST"}),
  login: async (body: any) => { return await request<any>("/login", {method: "POST"}); },
};
export const plantApi = {
  getPlants: (category="") => request(`/plants${category ? `?category=${category}` : ""}`),
  add: (body: any) => request("/plants/add", {method: "POST"}),
  update: (id: string, body: any) => request(`/plants/update/${id}`, {method: "PUT"}),
  toggle: (id: string) => request(`/plants/toggle/${id}`, {method: "POST"}),
  remove: (id: string) => request(`/plants/delete/${id}`, {method: "DELETE"}),
};
export const cartApi = {
  get: () => request("/cart"),
  add: () => request("/cart/add", {method: "POST"}),
  remove: () => request("/cart/remove", {method: "POST"}),
};
export const orderApi = {
  checkout: () => request("/orders/checkout", {method: "POST"}),
  get: () => request("/orders"),
};
export const adminApi = {
  orders: () => request("/admin/orders"),
  dashboard: () => request("/admin/dashboard"),
  update: (id: string) => request(`/admin/orders/${id}/status`, {method: "POST"}),
};
''')
            m = analyze_directory(root)
            calls = {(r.method, r.path) for r in m.routes if r.kind == "client_http_call"}
            self.assertEqual(len(calls), 15)
            self.assertIn(("GET", "/plants"), calls)
            self.assertIn(("POST", "/login"), calls)
            self.assertIn(("POST", "/admin/orders/:param/status"), calls)
            self.assertTrue(any(s.kind == "external_dependency" for s in m.services))

    def test_nextjs_not_confused_with_file_based_backend(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root,"package.json",json.dumps({"dependencies":{"next":"*", "react":"*"}}))
            write(root,"src/app/api/items/route.ts", "export async function GET(){return Response.json([])}")
            write(root,"src/app/shop/page.tsx", "export default function Page(){return null}")
            m=analyze_directory(root)
            self.assertEqual(m.architecture,"fullstack_web")
            self.assertIn(("/api/items","GET"),{(r.path,r.method) for r in m.routes})
            self.assertIn("/shop",{r.path for r in m.routes if r.kind=="page_route"})
            self.assertFalse(any(s.kind=="external_dependency" for s in m.services))

    def test_plain_react_does_not_assume_next_page_routes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write(root,"package.json",json.dumps({"dependencies":{"react":"*", "vite":"*"}}))
            write(root,"src/app/login/page.tsx","export default function Page(){return null}")
            m=analyze_directory(root)
            self.assertFalse(any(r.kind=="page_route" for r in m.routes))

    def test_direct_fetch_does_not_take_method_from_next_call(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write(root,"public/app.js","""
fetch('/users');
fetch('/orders', {method:'POST'});
axios.get('/data');
axios.delete('/data/42');
""")
            write(root,"public/index.html","hi")
            m=analyze_directory(root)
            client={(r.path,r.method) for r in m.routes if r.kind=="client_http_call"}
            self.assertIn(("/users","GET"),client)
            self.assertIn(("/orders","POST"),client)
            self.assertIn(("/data","GET"),client)
            self.assertIn(("/data/42","DELETE"),client)


if __name__ == "__main__":
    unittest.main()
