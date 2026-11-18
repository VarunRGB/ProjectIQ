"""Safe, readable Phase 1 Markdown report from the structured manifest."""
from __future__ import annotations
import json
from pathlib import Path

from .models import Evidence, Manifest


def _md(value: object) -> str:
    """Escape untrusted repository strings before using them in Markdown cells."""
    s = str(value).replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")
    s = s.replace("<", "&lt;").replace(">", "&gt;").replace("\r", " ").replace("\n", " ")
    return s


def _refs(evidences: list[Evidence]) -> str:
    return ", ".join(_md(f"{e.path}:{e.line}" if e.line else e.path) for e in evidences[:4]) or "—"


def _table(lines: list[str], headers: list[str], rows: list[list[object]]) -> None:
    lines.extend(["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"])
    for row in rows:
        lines.append("| " + " | ".join(_md(v) for v in row) + " |")
    if not rows:
        lines.append("\n_None identified from supported static patterns._")
    lines.append("")


def render_markdown(m: Manifest) -> str:
    lines = [
        f"# ProjectIQ Executioner — Phase 1 Repository Intelligence: {_md(m.project_name)}", "",
        f"**Schema:** `{m.schema_version}`  ", f"**Generated (UTC):** {_md(m.created_utc)}  ",
        "**Status:** STATIC_ANALYSIS_ONLY — no project code or tests executed", "",
        "## Repository and provenance", "",
        f"- Source: {_md(m.source.url or m.source.repository)}",
        f"- Reference: {_md(m.source.resolved_ref or 'local')}",
        f"- Resolved commit SHA: `{_md(m.source.commit_sha or 'not available (local scan)')}`",
        f"- Files indexed: {m.inventory['indexed_files']}", "",
        "## Architecture", "",
        f"**{_md(m.architecture)}** (confidence: {_md(m.architecture_confidence)})", "",
        "### Language inventory", "",
    ]
    _table(lines, ["Language", "Files"], [[k,v] for k,v in m.languages.items()])
    lines.extend(["### Technologies", ""])
    _table(lines, ["Name", "Category", "Confidence", "Evidence"],
           [[t.name,t.category,t.confidence,_refs(t.evidence)] for t in m.technologies])
    lines.extend(["### File role inventory (convention-based, not verified behavior)", ""])
    role_counts = m.inventory.get("file_role_counts", {})
    file_roles = m.inventory.get("file_roles", {})
    role_examples = {}
    for path, roles in file_roles.items():
        for role in roles:
            role_examples.setdefault(role, []).append(path)
    _table(lines, ["Role", "Files", "Example paths"],
           [[name, count, ", ".join(role_examples.get(name, [])[:4])]
            for name, count in role_counts.items()])
    lines.extend(["_File roles are naming and syntax clues, not proof of responsibilities._", ""])
    lines.extend(["### Inferred logical components (deployment topology unverified)", ""])
    _table(lines, ["Service", "Kind", "Stack", "Root", "Dependencies", "Evidence"],
           [[s.name,s.kind,s.technology,s.root,", ".join(s.depends_on),_refs(s.evidence)] for s in m.services])
    lines.extend(["## Surface discovery", "", "### Server routes, client calls and UI page route candidates (not unique working endpoints)", ""])
    _table(lines, ["Method", "Path", "Origin", "Confidence", "Evidence"],
           [[r.method,r.path,r.kind,r.confidence,_refs(r.evidence)] for r in m.routes])
    links = m.inventory.get('api_links', [])
    lines.extend(["### Statically correlated frontend/backend APIs (not execution proof)", ""])
    _table(lines, ["Client method", "Client URL pattern", "Server route pattern", "Server method", "Status", "Evidence paths"],
           [[link.get('client_method', '?'), link.get('client_path', ''),
             link.get('server_path', ''), link.get('server_method', '?'),
             link.get('status', '?'),
             str(link.get('client_source', '')) + ' ↔ ' + str(link.get('server_source', ''))]
            for link in links])
    lines.extend(["_Correlations are static path/method matches, not verified requests, authentication, responses or test results._", ""])
    lines.extend(["### Frontend page source files", ""])
    for page in m.pages: lines.append("- `" + _md(page) + "`")
    if not m.pages: lines.append("_None identified from supported static patterns._")
    lines.extend(["", "### Documented/inferred features", ""])
    _table(lines, ["Feature", "Origin", "Confidence", "Evidence"],
           [[f.name,f.origin,f.confidence,_refs(f.evidence)] for f in m.features])
    lines.extend(["## Test inventory — discovery only", ""])
    _table(lines, ["Artifact", "Kind", "Framework"], [[t.path,t.kind,t.framework or "Unknown"] for t in m.tests])
    lines.extend(["## Setup and environment inventory", "", "### Setup files", ""])
    for path in m.setup_files: lines.append("- `" + _md(path) + "`")
    if not m.setup_files: lines.append("_No recognized setup file._")
    lines.extend(["", "### Database schema files", ""])
    for path in m.database_schema_files: lines.append("- `" + _md(path) + "`")
    if not m.database_schema_files: lines.append("_No SQL schema file identified._")
    lines.extend(["", "### Environment variable names (values never exposed)", ""])
    for name in m.environment_variables: lines.append("- `" + _md(name) + "`")
    if not m.environment_variables: lines.append("_No named variables found via supported patterns._")
    env_details = m.inventory.get('environment_variable_details', {})
    if env_details:
        lines.extend(["", "### Static Pydantic Settings requirements (default values withheld)", ""])
        _table(lines, ["Variable", "Required hint", "Default present", "Evidence"],
               [[name, 'yes' if info.get('required_hint') else 'no',
                 'yes' if info.get('default_present') else 'no',
                 f"{info.get('path', '?')}:{info.get('line', '?')}"]
                for name, info in sorted(env_details.items()) if isinstance(info, dict)])
        lines.extend(["_Hints are based on field declarations; aliases, profile settings and validators may alter runtime requirements._", ""])
    lines.extend(["", "### Declared package scripts (not executed)", ""])
    for path, scripts in sorted(m.package_scripts.items()):
        lines.append("**`" + _md(path) + "`**")
        for key, cmd in sorted(scripts.items()):
            # Commands are untrusted data, not instructions. The CLI never invokes them.
            lines.append("- `" + _md(key) + "`: `" + _md(cmd) + "`")
    if not m.package_scripts: lines.append("_No declared package scripts._")
    lines.extend(["", "## Warnings / observations", ""])
    _table(lines, ["Code", "Severity", "Message", "Evidence"],
           [[f.code,f.severity,f.message,_refs(f.evidence)] for f in m.findings])
    lines.extend(["## Remaining unknowns for Phase 2", ""])
    for item in m.unknowns: lines.append("- " + _md(item))
    if not m.unknowns: lines.append("_No additional preflight uncertainties identified._")
    lines.extend(["", "## Phase 3 test targets (NOT executable tests)", ""])
    for target in m.proposed_test_targets: lines.append("- " + _md(target))
    lines.extend(["", "## Interpretation and limitations", ""])
    for item in m.limitations: lines.append("- " + _md(item))
    lines.extend(["", "**No success/failure or code-quality score is issued in Phase 1.**", ""])
    return "\n".join(lines)


def write_reports(m: Manifest, destination: Path) -> tuple[Path, Path]:
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    json_path, md_path = target / "manifest.json", target / "manifest.md"
    json_path.write_text(json.dumps(m.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(m), encoding="utf-8")
    return json_path, md_path
