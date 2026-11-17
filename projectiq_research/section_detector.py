"""Detect Literature Review and References sections across common report layouts.

This is intentionally conservative inside a bibliography: an uppercase paper title,
page header, or numbered citation must not terminate the References section.
"""

import re
from typing import Dict, List, Optional, Tuple

from .models import ParsedDocument, Section


LITERATURE_ALIASES = {
    "literature review", "review of literature", "literature survey", "related work",
    "related works", "related studies", "background and related work",
    "background and related works", "previous studies", "prior studies",
    "prior work", "existing work", "existing studies", "review of related literature",
    "review of related studies", "review of related work", "related literature",
    "related research", "prior research", "previous research", "existing approaches",
    "review of previous work", "related and prior work", "state of the art", "state of art",
}
REFERENCES_ALIASES = {
    "references", "reference", "reference list", "list of references", "bibliography",
    "works cited", "works consulted", "cited references", "sources",
    "references and bibliography", "bibliography and references",
}

# Recognised boundaries are safe even inside a bibliography; extend this list
# for local report conventions without making every capitalised line a heading.
_OTHER_SECTION_HEADINGS = {
    "abstract", "acknowledgement", "acknowledgements", "acknowledgment",
    "acknowledgments", "introduction", "problem statement", "research objectives",
    "objectives", "scope", "methodology", "proposed methodology", "methods",
    "materials and methods", "system design", "implementation", "experiments",
    "results", "discussion", "results and discussion", "conclusion", "conclusions",
    "future work", "limitations", "appendix", "appendices", "annexure",
    "annexures", "supplementary material", "supplemental material",
    "supplementary information", "additional information", "author biographies",
    "biographies", "biographies of authors", "biography of authors",
    "about the authors", "author information", "author contributions",
    "conflict of interest", "conflicts of interest", "declarations",
}
_ALL_KNOWN = LITERATURE_ALIASES | REFERENCES_ALIASES | _OTHER_SECTION_HEADINGS


def _compact_heading(value: str) -> str:
    """Undo character/word spacing artifacts in PDF heading extraction only.

    Some two-column PDFs yield `VI. R ELA TED WORK` or `I NTRODUCTION`.
    Comparing the compact *entire* heading against a bounded alias set is
    safer than fuzzy-matching arbitrary body lines.
    """
    return re.sub(r"[^a-z0-9]", "", value.casefold())


_COMPACT_ALIASES = {_compact_heading(alias): alias for alias in _ALL_KNOWN}
_COMPACT_LITERATURE = {_compact_heading(alias) for alias in LITERATURE_ALIASES}
_COMPACT_REFERENCES = {_compact_heading(alias) for alias in REFERENCES_ALIASES}


def _normalise_heading(line: str) -> str:
    value = line.strip().strip("*_")
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = re.sub(r"^\s*(?:chapter|section)\s+(?:\d+|[ivxlcdm]+)\s*[:.)–-]?\s*", "", value, flags=re.I)
    value = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.)]?\s+", "", value)
    value = re.sub(r"^\s*[A-Z][.)]\s+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" :–-\t")
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


def _known_heading(normalised: str) -> bool:
    if normalised in _ALL_KNOWN or _compact_heading(normalised) in _COMPACT_ALIASES:
        return True
    # Appendix A / Appendix I / Annexure 2 and common supplementary sections.
    return bool(re.fullmatch(
        r"(?:appendix|appendices|annexure|annexures)\s+(?:[a-z]|[ivxlcdm]+|\d+)(?:\s+[a-z ]{1,60})?",
        normalised,
    ))


def _looks_like_bibliography_entry(line: str) -> bool:
    stripped = line.strip()
    if re.match(r"^\[\d{1,4}\]\s*\S", stripped):
        return True
    if re.match(r"^\d{1,4}[.)]\s+", stripped):
        # Numbered paper labels and page-number-like continuation lines must not
        # become headings. Known headings were already recognised above.
        return True
    if re.match(r"^(?:doi\s*:|https?://|www\.)", stripped, flags=re.I):
        return True
    return False


def _is_heading_boundary(
    line: str,
    line_number: int,
    heading_lines: set[int],
    *,
    in_references: bool = False,
    previous_line: str = "",
    next_line: str = "",
) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    normalized = _normalise_heading(stripped)
    if _known_heading(normalized):
        return True
    # Numbered section headings must be checked BEFORE bibliography-number
    # detection. Otherwise "2. APPROACH" is incorrectly treated as reference
    # [2] and the preceding Related Work section swallows the methodology.
    # Only do this outside the bibliography, where numbered lines are entries.
    numbered = re.match(r"^\d{1,2}(?:\.\d{1,2}){0,3}[.)]?\s+(.+)$", stripped)
    if numbered and not in_references:
        rest = numbered.group(1).strip()
        letters = [char for char in rest if char.isalpha()]
        words = re.findall(r"[A-Za-z][A-Za-z\-]*", rest)
        heading_case = (bool(letters) and all(char.isupper() for char in letters))
        title_case = (bool(words) and all(
            word[0].isupper() or word.lower() in {"and", "of", "for", "in", "to", "the", "a"}
            for word in words
        ))
        if (2 <= len(rest) <= 90 and len(words) <= 12
                and (heading_case or title_case or line_number in heading_lines)
                and not re.search(r"\b(?:19|20)\d{2}\b|\bdoi\b|https?://", rest, re.I)
                and not re.search(r"[.!?,;:]$", rest) and "," not in rest):
            return True

    if _looks_like_bibliography_entry(stripped):
        return False

    if re.match(r"^#{1,6}\s+\S", stripped):
        return True

    # Inside references, PDF extractors may mark a wrapped all-caps *paper*
    # title as a heading. Ignore that hint unless this is an isolated heading.
    if in_references:
        return (line_number in heading_lines
                and not previous_line.strip() and not next_line.strip()
                and len(stripped.split()) <= 9
                and not re.search(r"[.!?,;]$", stripped))

    if line_number in heading_lines:
        return True
    letters = [char for char in stripped if char.isalpha()]
    return (bool(letters) and len(stripped) <= 90 and len(stripped.split()) <= 10
            and all(char.isupper() for char in letters)
            and not re.search(r"[.!?,;]$", stripped))


def _section_for_heading(line: str) -> Optional[str]:
    normalized = _normalise_heading(line)
    if normalized in LITERATURE_ALIASES or _compact_heading(normalized) in _COMPACT_LITERATURE:
        return "literature_review"
    if normalized in REFERENCES_ALIASES or _compact_heading(normalized) in _COMPACT_REFERENCES:
        return "references"
    return None


def detect_sections(document: ParsedDocument) -> Tuple[Section, Section, List[str]]:
    """Return (literature_review, references, notes).

    Keeps original line positions intact; newlines after a heading are retained
    so reference and hyperlink offsets still correspond to the input document.
    """
    lines = document.text.splitlines()
    boundaries: List[Tuple[int, str, Optional[str]]] = []
    in_references = False
    for index, line in enumerate(lines):
        prev = lines[index - 1] if index > 0 else ""
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if _is_heading_boundary(
            line, index + 1, document.heading_lines,
            in_references=in_references, previous_line=prev, next_line=nxt,
        ):
            section_key = _section_for_heading(line)
            boundaries.append((index, line.strip(), section_key))
            # Another recognised heading terminates the references section.
            in_references = section_key == "references"

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
        next_boundary_line = (boundaries[position + 1][0]
                              if position + 1 < len(boundaries) else len(lines))
        # Important: do not strip *leading* newlines, as that would corrupt the
        # line offsets later used to attach links to individual references.
        content = "\n".join(lines[line_index + 1:next_boundary_line]).rstrip()
        sections[section_key] = Section(
            key=section_key,
            found=True,
            heading=heading,
            content=content,
            start_line=line_index + 1,
            end_line=max(line_index + 1, next_boundary_line),
        )

    for key, label in (("literature_review", "Literature Review"),
                       ("references", "References")):
        if not sections[key].found:
            notes.append(f"{label} section was not found.")
        elif len(found_positions[key]) > 1:
            notes.append(f"Multiple {label} headings were found; Phase 1 extracted the first section only.")

    return sections["literature_review"], sections["references"], notes
