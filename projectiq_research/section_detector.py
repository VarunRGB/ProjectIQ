"""Detect Literature Review and References sections using heading aliases."""

import re
from typing import Dict, List, Optional, Tuple

from .models import ParsedDocument, Section


def _normalise_heading(line: str) -> str:
    value = line.strip()
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = re.sub(r"^\s*chapter\s+(?:\d+|[ivxlcdm]+)\s*[:.)-]?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", value)
    value = re.sub(r"^\s*[A-Z][.)]\s+", "", value)
    value = value.strip().rstrip(":").strip()
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


LITERATURE_ALIASES = {
    "literature review", "review of literature", "literature survey", "related work",
    "related works", "background and related work", "previous studies", "prior work",
    "existing work", "state of the art", "state of art",
}
REFERENCES_ALIASES = {
    "references", "reference", "reference list", "list of references", "bibliography",
    "works cited", "works consulted", "cited references", "sources",
}

# These headings are used as section boundaries even though they are not outputs
# this agent is responsible for.
_OTHER_SECTION_HEADINGS = {
    "abstract", "acknowledgement", "acknowledgements", "introduction", "problem statement",
    "research objectives", "objectives", "scope", "methodology", "proposed methodology",
    "methods", "materials and methods", "system design", "implementation", "experiments",
    "results", "discussion", "results and discussion", "conclusion", "conclusions",
    "future work", "appendix", "appendices", "annexure", "annexures",
}


def _is_heading_boundary(line: str, line_number: int, heading_lines: set[int]) -> bool:
    if not line.strip():
        return False
    if line_number in heading_lines:
        return True
    if re.match(r"^\s*#{1,6}\s+\S", line):
        return True
    numbered_line = re.match(r"^\s*\d+(?:\.\d+)*[.)]?\s+(.+)$", line.strip())
    if numbered_line:
        remainder = numbered_line.group(1)
        # Bibliographies often use "1. Smith, J. (2024) ...". Do not split
        # the References section at those numbered citation entries.
        looks_like_reference = (
            bool(re.search(r"\b(?:19|20)\d{2}\b", remainder))
            and ("," in remainder or '"' in remainder or "et al" in remainder.lower()
                 or "doi" in remainder.lower() or "http://" in remainder.lower()
                 or "https://" in remainder.lower())
        )
        if not looks_like_reference and re.match(r"^[A-Z][^.!?]{0,100}$", remainder):
            return True
    normalized = _normalise_heading(line)
    if normalized in LITERATURE_ALIASES | REFERENCES_ALIASES | _OTHER_SECTION_HEADINGS:
        return True
    letters = [char for char in line if char.isalpha()]
    return bool(letters) and len(line.strip()) <= 90 and len(line.split()) <= 10 and all(
        char.isupper() for char in letters
    )


def _section_for_heading(line: str) -> Optional[str]:
    normalized = _normalise_heading(line)
    if normalized in LITERATURE_ALIASES:
        return "literature_review"
    if normalized in REFERENCES_ALIASES:
        return "references"
    return None


def detect_sections(document: ParsedDocument) -> Tuple[Section, Section, List[str]]:
    """Return (literature_review, references, notes).

    Section content ends at the next recognised heading, not at the end of the
    file. If a section is absent, its `found` flag is False and content is empty.
    """
    lines = document.text.splitlines()
    boundaries: List[Tuple[int, str, Optional[str]]] = []
    for index, line in enumerate(lines):
        line_number = index + 1
        if _is_heading_boundary(line, line_number, document.heading_lines):
            boundaries.append((index, line.strip(), _section_for_heading(line)))

    notes: List[str] = []
    sections: Dict[str, Section] = {
        "literature_review": Section("literature_review", False),
        "references": Section("references", False),
    }
    found_positions: Dict[str, List[int]] = {"literature_review": [], "references": []}

    for position, (line_index, heading, section_key) in enumerate(boundaries):
        if section_key is None:
            continue
        found_positions[section_key].append(position)
        if sections[section_key].found:
            continue

        next_boundary_line = len(lines)
        for later_index, _, _ in boundaries[position + 1:]:
            next_boundary_line = later_index
            break
        content = "\n".join(lines[line_index + 1:next_boundary_line]).strip()
        sections[section_key] = Section(
            key=section_key,
            found=True,
            heading=heading,
            content=content,
            start_line=line_index + 1,
            end_line=next_boundary_line if next_boundary_line > line_index + 1 else line_index + 1,
        )

    for key, label in (("literature_review", "Literature Review"), ("references", "References")):
        if not sections[key].found:
            notes.append(f"{label} section was not found.")
        elif len(found_positions[key]) > 1:
            notes.append(f"Multiple {label} headings were found; Phase 1 extracted the first section only.")

    return sections["literature_review"], sections["references"], notes
