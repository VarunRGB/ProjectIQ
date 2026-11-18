"""Defensive archive extraction of pinned GitHub snapshots."""
from __future__ import annotations
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int = 12000
    max_uncompressed_bytes: int = 120 * 1024 * 1024
    max_single_file_bytes: int = 20 * 1024 * 1024
    max_path_length: int = 350


def extract_snapshot(archive: Path, target: Path, limits: ArchiveLimits | None = None) -> Path:
    """Return checkout dir. Reject zip-slip, symlinks, duplicates, zip bombs.

    Archive contents are data only: never execute scripts or invoke install hooks.
    """
    limits = limits or ArchiveLimits()
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            files = []
            roots = set()
            seen = set()
            total = 0
            for item in zf.infolist():
                # ZIP member names are normalised by Python's ZipInfo on
                # Windows: a raw backslash becomes '/' before item.filename
                # is exposed. Check the local-header bytes as well, so an
                # unsafe archive name cannot be silently normalised.
                zf.fp.seek(item.header_offset)
                local_header = zf.fp.read(30)
                if len(local_header) != 30 or local_header[:4] != b"PK\x03\x04":
                    raise ArchiveError("Invalid ZIP local file header.")
                raw_name_length = int.from_bytes(local_header[26:28], "little")
                if raw_name_length > limits.max_path_length * 4:
                    raise ArchiveError("ZIP local filename is too long.")
                raw_name = zf.fp.read(raw_name_length)
                if len(raw_name) != raw_name_length or b"\\" in raw_name or b"\x00" in raw_name:
                    raise ArchiveError("Unsafe raw ZIP member filename.")
                name = item.filename
                if (not name or "\\" in name or "\x00" in name or len(name) > limits.max_path_length
                        or name.startswith("/") or "//" in name):
                    raise ArchiveError("Unsafe archive path encountered.")
                p = PurePosixPath(name)
                parts = p.parts
                if not parts or any(s in {".", "..", ""} for s in parts) or any(":" in part for part in parts):
                    raise ArchiveError("Archive contains a traversal or drive path.")
                roots.add(parts[0])
                mode = item.external_attr >> 16
                if stat.S_IFMT(mode) == stat.S_IFLNK:
                    raise ArchiveError("Archive contains symlinks; refusing extraction.")
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ArchiveError("Archive contains unsupported file types.")
                if item.is_dir():
                    continue
                if len(parts) < 2:
                    raise ArchiveError("Unexpected file outside repository root.")
                relative = "/".join(parts[1:])
                key = relative.casefold()
                if key in seen:
                    raise ArchiveError("Duplicate/case-colliding archive paths.")
                seen.add(key)
                if item.file_size > limits.max_single_file_bytes:
                    raise ArchiveError("Archive member exceeds per-file size limit.")
                total += item.file_size
                if total > limits.max_uncompressed_bytes:
                    raise ArchiveError("Archive exceeds expanded size limit.")
                if len(files) >= limits.max_files:
                    raise ArchiveError("Archive contains too many files.")
                files.append((item, parts[1:]))
            if len(roots) != 1 or not files:
                raise ArchiveError("Expected one non-empty repository directory in archive.")
            checkout = target / "checkout"
            checkout.mkdir(exist_ok=True)
            for info, parts in files:
                dest = checkout.joinpath(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                actual = 0
                with zf.open(info) as src, dest.open("xb") as out:
                    while chunk := src.read(128 * 1024):
                        actual += len(chunk)
                        if actual > limits.max_single_file_bytes or actual > info.file_size:
                            raise ArchiveError("Archive content size does not match metadata.")
                        out.write(chunk)
                if actual != info.file_size:
                    raise ArchiveError("Archive member was truncated.")
            return checkout
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError) as exc:
        raise ArchiveError("Malformed, encrypted, or unreadable GitHub archive.") from exc
