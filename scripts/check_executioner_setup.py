"""Read-only ProjectIQ Executioner host/setup preflight (NO VM, NO student code).

Run from the repository root:
    python scripts/check_executioner_setup.py --mode static
    python scripts/check_executioner_setup.py --mode reviewed --vm-config vm_config.json
    python scripts/check_executioner_setup.py --mode reviewed --vm-config vm_config.json --check-image-sha

The output describes host prerequisites, NOT security attestation or approval.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any

SHA64 = re.compile(r"^[a-fA-F0-9]{64}$")
APPROVED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[a-f0-9]{40}$")
TOOL_KEYS = {
    "qemu-system-x86_64": "qemu_binary",
    "qemu-img": "qemu_img_binary",
    "ssh": "ssh_binary",
    "ssh-keygen": "ssh_keygen_binary",
}


def _row(name: str, status: str, detail: str) -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_config(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "Missing private operator VM configuration file"
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        return None, "Configuration file or parent is a symlink"
    try:
        if path.stat().st_size > 100_000:
            return None, "VM configuration too large"
        raw = path.read_text(encoding="utf-8-sig")
        def no_duplicates(pairs):
            out = {}
            for k, v in pairs:
                if k in out:
                    raise ValueError("duplicate JSON key")
                out[k] = v
            return out
        config = json.loads(raw, object_pairs_hook=no_duplicates)
        if not isinstance(config, dict):
            raise ValueError("root must be an object")
        return config, None
    except (OSError, UnicodeError, ValueError):
        return None, "Malformed or unreadable private operator VM configuration"


def _tool_exists(value: object, name: str) -> bool:
    if value is None:
        return shutil.which(name) is not None
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return True
    return shutil.which(value) is not None


def check_setup(mode: str, vm_config: Path | None = None, *,
                check_image_sha: bool = False) -> list[dict[str, str]]:
    """Assess prerequisites. Never start a process, download, install or write."""
    if mode not in ("static", "reviewed"):
        raise ValueError("mode must be static or reviewed")
    checks: list[dict[str, str]] = []
    if sys.version_info >= (3, 11):
        checks.append(_row("python", "PASS", f"Python {sys.version_info.major}.{sys.version_info.minor}"))
    else:
        checks.append(_row("python", "FAIL", "Python 3.11+ is required"))

    # Host checkout is the only assessed source; no untrusted repository import.
    repo_root = Path(__file__).resolve().parent.parent
    if (repo_root / "projectiq_executioner" / "orchestration" / "core.py").is_file():
        checks.append(_row("executioner_checkout", "PASS", "Executioner sources found"))
    else:
        checks.append(_row("executioner_checkout", "FAIL", "Run from a complete ProjectIQ checkout"))

    if mode == "static":
        checks.append(_row("vm", "SKIPPED", "STATIC mode never requires or starts QEMU"))
        checks.append(_row("github_access", "UNVERIFIED", "Public GitHub HTTPS connectivity not tested"))
        return checks

    if platform.system() == "Windows":
        checks.append(_row("host_platform", "PASS", "Windows host; WHPX still requires host verification"))
    else:
        checks.append(_row("host_platform", "WARN", "Reviewed VM acceptance was on Windows 11/WHPX; other hosts are unverified"))

    if importlib.util.find_spec("pycdlib") is not None:
        checks.append(_row("pycdlib", "PASS", "Host controller ISO library is available"))
    else:
        checks.append(_row("pycdlib", "FAIL", "Run python -m pip install -r requirements-executioner.txt"))

    if vm_config is None:
        checks.append(_row("vm_config", "FAIL", "Supply --vm-config with a private operator-owned config"))
        config = None
    else:
        config, reason = _load_config(Path(vm_config))
        checks.append(_row("vm_config", "FAIL" if reason else "PASS",
                           reason or "Private config parsed without running VM"))

    if config is None:
        return checks

    for name, key in TOOL_KEYS.items():
        ok = _tool_exists(config.get(key), name)
        checks.append(_row("tool:" + name, "PASS" if ok else "FAIL",
                           "Tool found" if ok else "Install tool or configure an operator-owned executable path"))

    sha = config.get("base_sha256")
    img = config.get("base_image")
    image_path = Path(img).expanduser() if isinstance(img, str) and img.strip() else None
    is_expected = isinstance(sha, str) and SHA64.fullmatch(sha) is not None
    exists = image_path is not None and image_path.is_file() and not image_path.is_symlink()
    if not is_expected:
        checks.append(_row("base_image", "FAIL", "base_sha256 must be the actual 64-character reviewed template hash"))
    elif not exists:
        checks.append(_row("base_image", "FAIL", "Configured sealed Ubuntu base image not found"))
    elif check_image_sha:
        try:
            is_match = _hash_file(image_path) == sha.lower()
            checks.append(_row("base_image", "PASS" if is_match else "FAIL",
                               "Image SHA-256 matched config" if is_match else "Image SHA-256 mismatch"))
        except OSError:
            checks.append(_row("base_image", "FAIL", "Could not read image for SHA-256 verification"))
    else:
        checks.append(_row("base_image", "UNVERIFIED", "Image exists; rerun with --check-image-sha to verify digest"))

    fw = config.get("uefi_firmware")
    if fw:
        fwsha = config.get("uefi_sha256")
        fwpath = Path(fw).expanduser() if isinstance(fw, str) else None
        if (not isinstance(fwsha, str) or SHA64.fullmatch(fwsha) is None
                or fwpath is None or not fwpath.is_file() or fwpath.is_symlink()):
            checks.append(_row("uefi_firmware", "FAIL", "UEFI ROM missing or SHA pin invalid"))
        elif check_image_sha:
            try:
                same = _hash_file(fwpath) == fwsha.lower()
            except OSError:
                same = False
            checks.append(_row("uefi_firmware", "PASS" if same else "FAIL",
                               "UEFI digest matched" if same else "UEFI digest mismatch/unreadable"))
        else:
            checks.append(_row("uefi_firmware", "UNVERIFIED", "UEFI exists; digest not checked"))

    approved = config.get("approved_snapshots")
    if not isinstance(approved, list) or any(not isinstance(x, str) or
               APPROVED.fullmatch(x) is None for x in approved) or len(set(approved)) != len(approved):
        checks.append(_row("approved_snapshots", "FAIL", "Expected unique Owner/Repo@40-character-SHA entries"))
    else:
        checks.append(_row("approved_snapshots", "PASS", f"{len(approved)} pinned entries; separate source review still required"))

    checks.append(_row("runtime_assets", "UNVERIFIED", "Node/npm/Python offline profiles and reviewed catalogs are per-project and not validated here"))
    checks.append(_row("sandbox_attestation", "UNVERIFIED", "NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS"))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", required=True, choices=("static", "reviewed"))
    parser.add_argument("--vm-config", type=Path)
    parser.add_argument("--check-image-sha", action="store_true",
                        help="Read and hash the image/UEFI ROM (may be large); NO VM launch")
    parser.add_argument("--json", action="store_true", help="Machine-readable checks")
    args = parser.parse_args(argv)
    if args.mode == "static" and (args.vm_config is not None or args.check_image_sha):
        parser.error("--vm-config and --check-image-sha apply to reviewed mode only")
    rows = check_setup(args.mode, args.vm_config, check_image_sha=args.check_image_sha)
    if args.json:
        print(json.dumps({"checks": rows, "failed": sum(x["status"] == "FAIL" for x in rows)}, indent=2))
    else:
        for r in rows:
            print(f"{r['status']:10s}  {r['check']:26s}  {r['detail']}")
        print("Read-only checks. No VM, student code, downloads or configuration changes performed.")
    return 2 if any(r["status"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
