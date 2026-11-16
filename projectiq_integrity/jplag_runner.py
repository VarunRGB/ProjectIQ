"""Code similarity with JPlag: prepare submissions, run the CLI, parse the result.

Built from Surabhi's automate-jplag setup, with three changes:
1. Files are NOT merged into one file per student. JPlag reads each student's
   folder directly, so every match keeps its real file name and line numbers.
2. Shared starter code is passed with --base-code, so code every student was
   given never counts as similarity.
3. The result (.jplag zip) is parsed into Findings with quoted fragments, not an
   Excel sheet.

JPlag runs once per cohort (all pairs); each submission's findings are then read
from the same result file.
"""
import json
import logging
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import DEFAULT, JPLAG_JAR, REQUIRED_JAVA, IntegritySettings
from .contracts import Evidence
from .evidence import Signal

logger = logging.getLogger(__name__)

SKIP_DIRS = {"__MACOSX", "__pycache__", ".git", ".venv", "venv", "env", "site-packages",
             "node_modules", ".ipynb_checkpoints", "build", "dist"}
CODE_LIMITATION = ("Structural similarity is not proof of copying: short programs, shared "
                   "tutorials and common library patterns also match. A human must compare "
                   "the fragments and talk to the students.")


class JPlagError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Java and JPlag checks
# --------------------------------------------------------------------------- #
def java_major_version(java: str = "java") -> int:
    """Major Java version, e.g. 21 for '21.0.12' and 8 for '1.8.0_392'. -1 if missing."""
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return -1
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return -1
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major


def check_environment(jar: Path = JPLAG_JAR, java: str = "java") -> None:
    version = java_major_version(java)
    if version < 0:
        raise JPlagError("Java was not found. Install JDK 21 and put its bin folder on PATH.")
    if version < REQUIRED_JAVA:
        raise JPlagError(f"Java {version} found; JPlag needs Java {REQUIRED_JAVA} or newer.")
    if not Path(jar).exists():
        raise JPlagError(f"JPlag jar not found at {jar}. Run: python scripts/setup_offline.py")


# --------------------------------------------------------------------------- #
# Preparing submissions
# --------------------------------------------------------------------------- #
def _safe_extract(zip_path: Path, dest: Path) -> None:
    """Extract a zip, refusing entries that would land outside dest."""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if dest not in target.parents and target != dest:
                logger.warning("Skipping unsafe zip entry %s in %s", member.filename, zip_path.name)
                continue
            zf.extract(member, dest)


def notebook_to_script(nb_path: Path) -> str:
    """Code cells of a .ipynb as plain Python (no nbconvert needed). Magics and
    shell lines are commented out so JPlag can parse the file."""
    nb = json.loads(nb_path.read_text(encoding="utf-8", errors="replace"))
    cells = nb.get("cells", [])
    parts = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source", "")
        src = "".join(src) if isinstance(src, list) else src
        lines = [("# " + ln) if ln.lstrip().startswith(("%", "!")) else ln for ln in src.splitlines()]
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def _copy_code_tree(src: Path, dest: Path) -> int:
    count = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".ipynb":
            out.with_suffix(".py").write_text(notebook_to_script(path), encoding="utf-8")
        else:
            shutil.copy2(path, out)
        count += 1
    return count


def prepare_submissions(source_dir: Path, work_dir: Path) -> Dict[str, int]:
    """Make one clean folder per student under work_dir.

    source_dir may hold student folders, .zip files, or single .py/.ipynb files.
    .rar files are skipped with a warning (ask students for .zip instead).
    Returns {submission_id: files copied}.
    """
    source_dir, work_dir = Path(source_dir), Path(work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    counts: Dict[str, int] = {}
    for entry in sorted(source_dir.iterdir()):
        if entry.name.startswith(".") or entry.name in SKIP_DIRS:
            continue
        sid = entry.stem if entry.is_file() else entry.name
        dest = work_dir / sid
        dest.mkdir(parents=True, exist_ok=True)
        if entry.is_dir():
            counts[sid] = _copy_code_tree(entry, dest)
        elif entry.suffix == ".zip":
            tmp = work_dir / f"_unzip_{sid}"
            _safe_extract(entry, tmp)
            inner = [p for p in tmp.iterdir() if p.name not in SKIP_DIRS]
            root = inner[0] if len(inner) == 1 and inner[0].is_dir() else tmp
            counts[sid] = _copy_code_tree(root, dest)
            shutil.rmtree(tmp)
        elif entry.suffix == ".ipynb":
            (dest / f"{sid}.py").write_text(notebook_to_script(entry), encoding="utf-8")
            counts[sid] = 1
        elif entry.suffix == ".py":
            shutil.copy2(entry, dest / entry.name)
            counts[sid] = 1
        else:
            logger.warning("Skipping %s: unsupported submission type (%s)", entry.name, entry.suffix)
            shutil.rmtree(dest)
            continue
        if counts.get(sid, 0) == 0:
            logger.warning("Submission %s has no files after cleaning", sid)
    return counts


# --------------------------------------------------------------------------- #
# Running JPlag
# --------------------------------------------------------------------------- #
def run_jplag(code_root: Path, result_file: Path, base_code: Optional[Path] = None,
              old_roots: Optional[List[Path]] = None, settings: IntegritySettings = DEFAULT,
              jar: Path = JPLAG_JAR, java: str = "java", timeout_s: int = 1800) -> Path:
    """Run JPlag on every pair of submissions in code_root.

    base_code: starter code given to every student (excluded from matches).
    old_roots: earlier years' submissions, compared against but not with each other.
    Returns the path of the .jplag result file.
    """
    check_environment(jar, java)
    result_file = Path(result_file).with_suffix(".jplag")
    result_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [java, "-jar", str(jar), str(Path(code_root).resolve()),
           "-l", settings.jplag_language, "-M", "RUN", "-n", "-1", "--overwrite",
           "-r", str(result_file.with_suffix("").resolve())]
    if base_code:
        cmd += ["-bc", str(Path(base_code).resolve())]
    if old_roots:
        cmd += ["-old", ",".join(str(Path(p).resolve()) for p in old_roots)]
    if settings.jplag_min_tokens:
        cmd += ["-t", str(settings.jplag_min_tokens)]
    logger.info("Running JPlag: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    log = proc.stdout + proc.stderr
    if proc.returncode != 0 or not result_file.exists():
        errors = [ln for ln in log.splitlines() if "ERROR" in ln] or log.splitlines()[-5:]
        raise JPlagError("JPlag failed:\n" + "\n".join(errors))
    return result_file


# --------------------------------------------------------------------------- #
# Parsing the .jplag result (JPlag 6.x format)
# --------------------------------------------------------------------------- #
@dataclass
class CodeMatch:
    file1: str
    lines1: Tuple[int, int]
    file2: str
    lines2: Tuple[int, int]
    tokens: int


@dataclass
class CodePair:
    id1: str
    id2: str
    max_similarity: float
    avg_similarity: float
    matches: List[CodeMatch]

    def other(self, sid: str) -> str:
        return self.id2 if sid == self.id1 else self.id1


@dataclass
class JPlagReport:
    version: str
    submissions: List[str]
    pairs: List[CodePair]
    token_counts: Dict[str, int]                       # submission -> total tokens
    base_code_tokens: Dict[str, int]                   # submission -> tokens matched to starter code
    failed: List[str]
    files: Dict[str, List[str]] = field(default_factory=dict)  # "S101/app.py" -> lines

    def pairs_for(self, sid: str) -> List[CodePair]:
        return sorted((p for p in self.pairs if sid in (p.id1, p.id2)),
                      key=lambda p: p.max_similarity, reverse=True)

    def fragment(self, path: str, lines: Tuple[int, int], max_lines: int) -> str:
        src = self.files.get(_posix(path), [])
        a, b = max(1, lines[0]), min(len(src), lines[1])
        chunk = src[a - 1:b]
        if len(chunk) > max_lines:
            chunk = chunk[:max_lines] + [f"... ({b - a + 1} lines in total)"]
        return "\n".join(chunk).rstrip()


def _posix(path: str) -> str:
    """JPlag on Windows names files with backslashes (S101\\app.py) while its result zip
    stores them with forward slashes. Use forward slashes everywhere."""
    return path.replace("\\", "/")


def parse_result(result_file: Path) -> JPlagReport:
    with zipfile.ZipFile(result_file) as zf:
        names = set(zf.namelist())
        overview = json.loads(zf.read("overview.json"))
        v = overview.get("jplag_version", {})
        version = f"{v.get('major')}.{v.get('minor')}.{v.get('patch')}"
        index = json.loads(zf.read("submissionFileIndex.json")).get("submission_file_indexes", {}) \
            if "submissionFileIndex.json" in names else {}
        token_counts = {sid: sum(f.get("token_count", 0) for f in files.values())
                        for sid, files in index.items()}
        base_tokens = {}
        for name in names:
            if name.startswith("basecode/") and name.endswith(".json"):
                sid = name[len("basecode/"):-len(".json")]
                base_tokens[sid] = sum(m.get("tokens", 0) for m in json.loads(zf.read(name)))
        files = {_posix(name[len("files/"):]): zf.read(name).decode("utf-8", errors="replace").splitlines()
                 for name in names if name.startswith("files/") and not name.endswith("/")}
        seen, pairs = set(), []
        for partners in overview.get("submission_ids_to_comparison_file_name", {}).values():
            for fname in partners.values():
                if fname in seen or fname not in names:
                    continue
                seen.add(fname)
                c = json.loads(zf.read(fname))
                matches = [CodeMatch(_posix(m["firstFile"]), (m["startInFirst"]["line"], m["endInFirst"]["line"]),
                                     _posix(m["secondFile"]), (m["startInSecond"]["line"], m["endInSecond"]["line"]),
                                     m.get("tokens", 0)) for m in c.get("matches", [])]
                sims = c.get("similarities", {})
                pairs.append(CodePair(c["id1"], c["id2"], float(sims.get("MAX", 0.0)),
                                      float(sims.get("AVG", 0.0)), matches))
    subs = sorted(overview.get("submission_id_to_display_name", {}) or token_counts)
    return JPlagReport(version, subs, pairs, token_counts, base_tokens,
                       list(overview.get("failed_submission_names", [])), files)


def code_signals(report: JPlagReport, sid: str, settings: IntegritySettings = DEFAULT) -> List[Signal]:
    """One Signal per other submission whose similarity reaches the threshold."""
    signals = []
    for pair in report.pairs_for(sid):
        if pair.max_similarity < settings.code_similarity_threshold or not pair.matches:
            continue
        other = pair.other(sid)
        mine_first = pair.id1 == sid
        evidence = []
        for m in sorted(pair.matches, key=lambda m: m.tokens, reverse=True)[:settings.max_fragments_per_pair]:
            my_file, my_lines = (m.file1, m.lines1) if mine_first else (m.file2, m.lines2)
            ot_file, ot_lines = (m.file2, m.lines2) if mine_first else (m.file1, m.lines1)
            evidence.append(Evidence(source="code", location=f"{my_file}:{my_lines[0]}-{my_lines[1]}",
                                     quote=report.fragment(my_file, my_lines, settings.max_quote_lines)))
            evidence.append(Evidence(source="cohort", location=f"{ot_file}:{ot_lines[0]}-{ot_lines[1]}",
                                     quote=report.fragment(ot_file, ot_lines, settings.max_quote_lines)))
        matched = sum(m.tokens for m in pair.matches)
        signals.append(Signal(
            kind="similar_code",
            summary=(f"{pair.max_similarity:.0%} structural code similarity with {other} "
                     f"({len(pair.matches)} matching fragment(s), {matched} tokens; starter code excluded)."),
            evidence=evidence,
            scores={"max_similarity": pair.max_similarity, "avg_similarity": pair.avg_similarity,
                    "matched_tokens": float(matched),
                    "smaller_submission_tokens": float(min(report.token_counts.get(sid, 0),
                                                           report.token_counts.get(other, 0)))},
            limitations=CODE_LIMITATION,
        ))
    return signals
