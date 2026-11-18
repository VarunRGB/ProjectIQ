"""Python -m projectiq_executioner URL --out <dir>."""
from __future__ import annotations
import argparse
import sys
import tempfile
from pathlib import Path

from .analyzer import analyze_directory
from .github_client import DownloadLimits, GitHubClient, IngestionError
from .report import write_reports
from .safe_archive import ArchiveError, ArchiveLimits, extract_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m projectiq_executioner",
        description="Phase 1: safely inspect a public GitHub repository and generate an evidence-based project manifest. No student code executes.",
    )
    parser.add_argument("target", help="HTTPS GitHub repository URL, or existing local directory (offline inspection)")
    parser.add_argument("--ref", help="GitHub branch, tag, or full commit SHA (default: remote default branch)")
    parser.add_argument("--out", type=Path, default=Path("outputs/executioner/phase1"), help="Output directory")
    parser.add_argument("--timeout", type=int, default=25, help="GitHub network timeout in seconds")
    parser.add_argument("--max-zip-mb", type=int, default=30, help="Maximum compressed archive MB")
    parser.add_argument("--max-expanded-mb", type=int, default=120, help="Maximum expanded archive MB")
    args = parser.parse_args(argv)
    if min(args.timeout, args.max_zip_mb, args.max_expanded_mb) < 1:
        parser.error("timeout and size limits must be positive")
    try:
        if args.target.startswith(("http://", "https://", "git@")):
            client = GitHubClient(timeout=args.timeout, limits=DownloadLimits(args.max_zip_mb * 1024 * 1024))
            source = client.resolve(args.target, args.ref)
            with tempfile.TemporaryDirectory(prefix="projectiq_phase1_") as tmp:
                base = Path(tmp)
                client.download(source, base / "snapshot.zip")
                checkout = extract_snapshot(base / "snapshot.zip", base / "source",
                    ArchiveLimits(max_uncompressed_bytes=args.max_expanded_mb * 1024 * 1024))
                manifest = analyze_directory(checkout, source)
        else:
            if args.ref:
                parser.error("--ref is supported only for GitHub URLs")
            manifest = analyze_directory(Path(args.target))
        json_path, md_path = write_reports(manifest, args.out)
    except (IngestionError, ArchiveError, ValueError, OSError) as exc:
        print(f"Phase 1 failed safely: {exc}", file=sys.stderr)
        return 2

    print("\nProjectIQ Executioner — Phase 1 (static analysis only)")
    print(f"  Repository:   {manifest.source.repository}")
    print(f"  Commit:       {manifest.source.commit_sha or 'local checkout'}")
    print(f"  Architecture: {manifest.architecture} [{manifest.architecture_confidence}]")
    print(f"  Languages:    {', '.join(manifest.languages) or 'unknown'}")
    print(f"  Technologies: {', '.join(t.name for t in manifest.technologies) or 'none detected'}")
    server_routes = sum(r.kind in {"file_endpoint", "declared_http_route", "file_based_route", "declared_api_contract"} for r in manifest.routes)
    non_http = sum(r.kind in {"graphql_operation", "rpc_method"} for r in manifest.routes)
    client_calls = sum(r.kind == "client_http_call" for r in manifest.routes)
    forms = sum(r.kind == "form_action" for r in manifest.routes)
    pages = sum(r.kind == "page_route" for r in manifest.routes)
    external = sum(s.kind == "external_dependency" for s in manifest.services)
    local = len(manifest.services) - external
    print(f"  Components:   {local} local/inferred + {external} external dependency records")
    file_roles = manifest.inventory.get("file_role_counts", {})
    print(f"  File catalog: {len(manifest.inventory.get('file_roles', {}))} files classified across {len(file_roles)} naming-based roles")
    print(f"  Surface:      {server_routes} server/contract HTTP records + {client_calls} client HTTP calls + {pages} page URL candidates")
    if forms: print(f"  Form actions: {forms} literal HTML form HTTP actions")
    if non_http: print(f"  Other APIs:   {non_http} GraphQL/gRPC operation records (not HTTP routes)")
    print(f"  Tests found:  {len(manifest.tests)} (not run)")
    print(f"  Warnings:     {len(manifest.findings)}; unknowns: {len(manifest.unknowns)}")
    print(f"  JSON:         {json_path}")
    print(f"  Markdown:     {md_path}")
    print("  Execution:    NOT PERFORMED (scheduled for later phases)")
    return 0
