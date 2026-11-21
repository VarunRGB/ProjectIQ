"""CLI: python -m projectiq_executioner.phase3 --manifest ... --out ..."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

from .generator import (
    TestPlanInputError, _empty, generate_test_plan, load_input_json,
    write_test_plan, MAX_CANDIDATE_BYTES,
)


def _error_code(exc: Exception) -> str:
    """Fixed safe diagnostic categories; don't persist arbitrary exceptions."""
    name = type(exc).__name__
    message = str(exc).lower()
    if name == "IngestionError":
        if "zip exceeds" in message or "download size limit" in message:
            return "REPOSITORY_ZIP_TOO_LARGE"
        if "rate-limited" in message or "rate limit" in message:
            return "GITHUB_RATE_LIMITED"
        if "not found" in message or "inaccessible" in message:
            return "GITHUB_REPOSITORY_NOT_ACCESSIBLE"
        if "network request failed" in message:
            return "GITHUB_NETWORK_FAILURE"
        return "GITHUB_INGESTION_FAILURE"
    if name == "ArchiveError":
        return "ARCHIVE_REJECTED"
    if name == "TestPlanInputError":
        return "INVALID_PLANNING_INPUT"
    return "INPUT_OR_ANALYSIS_FAILURE"


def _error_plan(code: str) -> dict:
    report = _empty({"kind": "unknown", "repository": "NOT_RESOLVED", "commit_sha": None}, None, None)
    report["status"] = "INPUT_ERROR"
    report["issues"] = [{"code": code, "stage": "input"}]
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase 3: deterministic test plans; no student execution or LLM")
    choices = p.add_mutually_exclusive_group(required=True)
    choices.add_argument("--manifest", type=Path, help="Existing Phase 1 manifest.json (offline)")
    choices.add_argument("--repo", help="Public GitHub repository URL; uses Phase 1 bounded ingestion")
    p.add_argument("--ref", help="Optional GitHub branch/tag/commit; prefer immutable SHA")
    p.add_argument("--candidate", type=Path, help="Optional Phase 2C candidate (must match source SHA)")
    p.add_argument("--source-root", type=Path,
                   help="Optional local checkout for inert source-contract inspection (unverified provenance)")
    p.add_argument("--out", type=Path, required=True, help="Fresh output directory")
    args = p.parse_args(argv)
    if args.manifest and args.ref:
        p.error("--ref is only supported with --repo")
    if args.repo and args.source_root:
        p.error("--source-root applies only to --manifest; --repo downloads a pinned snapshot")
    destination = args.out / "test_plan.json"
    if destination.exists():
        p.error("Refusing to overwrite existing test plan; use a new --out directory")

    try:
        candidate = load_input_json(args.candidate, limit=MAX_CANDIDATE_BYTES) if args.candidate else None
        if args.manifest:
            manifest = load_input_json(args.manifest)
            report = generate_test_plan(manifest, candidate,
                                        source_root=args.source_root, source_verified=False)
        else:
            # Reuse Phase 1's exact-SHA download and safe archive extraction.
            from projectiq_executioner.analyzer import analyze_directory
            from projectiq_executioner.github_client import GitHubClient
            from projectiq_executioner.safe_archive import extract_snapshot

            client = GitHubClient(timeout=25)
            source = client.resolve(args.repo, args.ref)
            with tempfile.TemporaryDirectory(prefix="projectiq_phase3_source_") as folder:
                root = Path(folder)
                archive = root / "snapshot.zip"
                client.download(source, archive)
                checkout = extract_snapshot(archive, root / "source")
                manifest = analyze_directory(checkout, source).to_dict()
                # Source remains live until all bounded inspection completes.
                report = generate_test_plan(manifest, candidate,
                                            source_root=checkout, source_verified=True)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        report = _error_plan(_error_code(exc))

    try:
        write_test_plan(report, destination)
    except FileExistsError:
        p.error("Refusing to overwrite existing test plan")
    except OSError:
        print("Unable to write Phase 3 JSON report", file=sys.stderr)
        return 2

    s = report["summary"]
    print("ProjectIQ Phase 3 — Deterministic Test Plan")
    print("  Status:       " + report["status"])
    print(f"  Total:        {s['total']}")
    print(f"  Phase 4-ready:{s['ready_for_phase4']:>4} (test specifications, not authorization)")
    print(f"  Need oracles: {s['needs_oracle']}")
    print(f"  Unsupported:  {s['unsupported_by_runner']}")
    print(f"  Inventory:    {sum(s['inventory_counts'].values())} evidence records")
    if report["issues"]:
        print("  Issues:       " + ", ".join(x["code"] for x in report["issues"]))
    print("  Execution:    NOT PERFORMED")
    print("  JSON:         " + str(destination))
    return 2 if report["status"] == "INPUT_ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
