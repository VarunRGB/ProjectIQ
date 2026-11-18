"""Read-only source indexing; never follows local symlinks or reads secrets."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "__pycache__",
    ".next", ".nuxt", ".output", ".tox", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", "dist", "build", "target", "coverage", ".gradle",
    "bin", "obj", ".terraform", ".cache", "bower_components", ".bundle", ".svelte-kit",
}
SECRET_FILES = {".env", ".env.local", ".env.production", ".env.development", "id_rsa", "id_ed25519"}


@dataclass
class SourceIndex:
    root: Path
    files: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    read_count: int = 0
    max_text_bytes: int = 350_000

    @classmethod
    def build(cls, root: Path, max_files: int = 12000) -> "SourceIndex":
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("Local input must be an existing directory.")
        inst = cls(root)
        for folder, dirs, filenames in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not (Path(folder) / d).is_symlink())
            for name in sorted(filenames):
                abs_path = Path(folder) / name
                rel = abs_path.relative_to(root).as_posix()
                if abs_path.is_symlink():
                    inst.skipped.append(rel + " (symlink)")
                    continue
                try:
                    size = abs_path.stat().st_size
                except OSError:
                    inst.skipped.append(rel + " (unreadable)")
                    continue
                if len(inst.files) >= max_files:
                    raise ValueError("Repository file count exceeds the Phase 1 indexing limit.")
                inst.files[rel] = size
        return inst

    def read(self, relative: str) -> str | None:
        if relative not in self.files:
            return None
        basename = Path(relative).name
        if basename in SECRET_FILES or (basename.startswith(".env.") and not basename.endswith((".example", ".sample", ".template"))):
            return None
        if self.files[relative] > self.max_text_bytes:
            return None
        path = self.root.joinpath(*relative.split("/"))
        if path.is_symlink():
            return None
        try:
            data = path.read_bytes()
        except (OSError, PermissionError):
            return None
        if b"\x00" in data[:8192]:
            return None
        self.read_count += 1
        return data.decode("utf-8-sig", errors="replace")

    def has(self, name: str) -> bool:
        return name in self.files

    def named(self, names: set[str]) -> list[str]:
        return [p for p in self.files if Path(p).name.lower() in names]
