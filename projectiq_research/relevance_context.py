"""Bounded, citation-aware evidence preparation for Phase 3.

All passages come from the student report, not the bibliography. We keep
source line numbers and section provenance. Neither summarizer nor LLM is used.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List

from .models import ParsedDocument, ResearchExtraction

_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|(?:chapter\s+)?\d+(?:\.\d+)*[.)]?\s+|[IVXLCDM]+[.)]\s+)?"
    r"(abstract|introduction|background|literature review|review of literature|related work|"
    r"method|methods|methodology|materials and methods|proposed methodology|"
    r"proposed system|system architecture|implementation|experimental setup|"
    r"results(?: and discussion)?|discussion|conclusions?|references|bibliography|"
    r"acknowledgements?|keywords|index terms|biograph(?:y|ies)(?: of authors)?)\s*[:.]?\s*$",
    re.I,
)
_METHODS = ("method", "methods", "methodology", "materials and methods", "proposed methodology",
            "proposed system", "system architecture", "implementation", "experimental setup")


@dataclass
class ReportEvidence:
    title: str
    topic: str
    methodology: str
    literature: str
    citation_contexts: Dict[int, List[str]]
    citation_lines: Dict[int, List[int]]
    # New fields are optional, retaining the Phase 3 v1 public data contract.
    topic_passages: List[str] = field(default_factory=list)
    methodology_passages: List[str] = field(default_factory=list)
    citation_sections: Dict[int, List[str]] = field(default_factory=dict)
    # Method evidence that does not originate in citation-bearing lines.
    independent_methodology_passages: List[str] = field(default_factory=list)
    # Exact citation-bearing lines (+ continuation) for explicit adoption checks.
    citation_claims: Dict[int, List[str]] = field(default_factory=dict)
    # Complete methodology sentences, including citation-bearing statements.
    # These are candidate *claims*, not independently verified paper support.
    method_sentences: List[str] = field(default_factory=list)
    citation_aliases: Dict[int, List[str]] = field(default_factory=dict)


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit].rstrip()


def _heading(line: str) -> str:
    match = _HEADING_RE.fullmatch(line.strip())
    return match.group(1).lower() if match else ""


def _is_page_furniture(line: str) -> bool:
    """Ignore page numbers and standalone uppercase running headers, without
    recognizing any project-specific title or research subject."""
    value = line.strip()
    if re.fullmatch(r"(?i)(?:page|p\.)\s*\d+(?:\s*/\s*\d+)?", value):
        return True
    letters = [ch for ch in value if ch.isalpha()]
    return bool(letters and len(letters) >= 7 and all(c.isupper() for c in letters)
                and not _heading(value))


def _sentences(text: str) -> List[str]:
    """Reconstruct sentences across PDF line breaks, then split on boundaries."""
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", _clean(text, 1_000_000))
            if x.strip()]


def _citation_claim(lines: List[str], sections: List[str], index: int,
                    citation_marker: str) -> str:
    """Reconstruct the sentence containing a citation even if its verb is on
    another physical PDF line. Preserve only the relevant section and bound text."""
    if not 0 <= index < len(lines):
        return ""
    section = sections[index] if index < len(sections) else ""
    parts = []
    for i in range(max(0, index - 4), min(len(lines), index + 5)):
        if i >= len(sections) or sections[i] != section:
            continue
        value = lines[i].strip()
        if value and not _heading(value) and not _is_page_furniture(value):
            parts.append(value)
    combined = " ".join(parts)
    candidate_sentences = _sentences(combined)
    # Match the complete textual marker, not just an arbitrary bracket on
    # the same line. Handle wrapped markers using normalized whitespace.
    marker = _clean(citation_marker, 80)
    for sentence in candidate_sentences:
        if marker and marker in sentence:
            return _clean(sentence, 650)
    line_value = lines[index].strip()
    if line_value:
        for sentence in candidate_sentences:
            if line_value in sentence:
                return _clean(sentence, 650)
    return _clean(line_value, 650)


def _sections(lines: List[str]) -> Dict[str, List[str]]:
    blocks: Dict[str, List[str]] = {}
    current = None
    for line in lines:
        heading = _heading(line)
        if heading:
            current = heading
            blocks.setdefault(current, [])
        elif current is not None and line.strip() and not _is_page_furniture(line):
            blocks[current].append(line.strip())
    return blocks


def _title(lines: List[str]) -> str:
    """Select the document's title, not cover-page banners or page numbers.

    Use the region before Abstract when possible. Long, mixed-case headings
    are more reliable than running headers, uppercase notices and affiliation
    metadata. No subject- or fixture-specific phrases are used here.
    """
    prefix = lines[:40]
    abstract_at = next((i for i, value in enumerate(prefix) if _heading(value) == "abstract"), len(prefix))
    candidates = []
    for i, line in enumerate(prefix[:abstract_at]):
        value = line.strip()
        if not value or value.isdigit() or _heading(value):
            continue
        if re.fullmatch(r"(?i)(?:page|p\.)\s*\d+(?:\s*/\s*\d+)?", value):
            continue
        if ("@" in value or re.match(r"^\d{4}\b", value)
                or len(value) < 14 or len(value.split()) < 3):
            continue
        letters = [c for c in value if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            continue  # e.g. running banners and conference headers
        if "/" in value and value.count("/") >= 1:
            continue  # typical organizational label / banner
        candidates.append((i, value))
    if not candidates:
        return ""
    first_i, first = candidates[0]
    # PDF extraction often splits a title into two- or even one-word lines.
    # Check *adjacent source lines*, not just other long-title candidates.
    title = [first]
    for value in prefix[first_i + 1:min(first_i + 4, abstract_at)]:
        part = value.strip()
        if (not part or _heading(part) or "@" in part or " • " in part or
                len(part) > 100 or re.match(r"^\d+\s*Department\b", part, re.I) or
                (part.endswith(".") and len(part.split()) > 4)):
            break
        letters = [c for c in part if c.isalpha()]
        if not letters or all(c.isupper() for c in letters):
            break
        title.append(part)
    return _clean(" ".join(title), 240)


def _chunks(text: str, max_chars: int = 620, overlap_words: int = 12, max_chunks: int = 32) -> List[str]:
    """Bound a report's section to MiniLM-sized passages with modest overlap.

    Uniformly sample when an unusually long methods section exceeds our budget.
    Never silently feed multi-thousand-character strings to a 256-token model.
    """
    words = text.split()
    chunks: List[str] = []
    i = 0
    while i < len(words):
        j = i
        size = 0
        while j < len(words) and (size + len(words[j]) + 1 <= max_chars or j == i):
            size += len(words[j]) + 1
            j += 1
        chunk = " ".join(words[i:j])[:max_chars]
        if chunk:
            chunks.append(chunk)
        if j == len(words):
            break
        i = max(i + 1, j - overlap_words)
    if len(chunks) > max_chunks:
        positions = [round(idx * (len(chunks)-1) / (max_chunks-1)) for idx in range(max_chunks)]
        chunks = [chunks[idx] for idx in positions]
    return chunks


def _citation_context(lines: List[str], line_no: int, reference_start: int | None, window: int = 2) -> str:
    if line_no < 1 or line_no > len(lines):
        return ""
    lower, upper = max(0, line_no - window - 1), min(len(lines), line_no + window)
    snippet = []
    for idx in range(lower, upper):
        if reference_start is not None and idx + 1 >= reference_start:
            continue
        line = lines[idx].strip()
        if line and not line.isdigit() and not _heading(line):
            snippet.append(line)
    return _clean(" ".join(snippet), 650)


def _section_per_line(lines: List[str]) -> List[str]:
    current = ""
    sections = []
    for line in lines:
        heading = _heading(line)
        if heading:
            current = heading
        sections.append(current)
    return sections


def prepare_relevance_context(document: ParsedDocument, extraction: ResearchExtraction) -> ReportEvidence:
    """Build topic, method and per-reference citation evidence from a document."""
    lines = document.text.splitlines()
    ref_start = extraction.references.start_line if extraction.references.found else None
    body_lines = lines[:ref_start - 1] if ref_start else lines
    sections = _sections(body_lines)
    section_map = _section_per_line(body_lines)
    title = _title(body_lines)
    abstract = _clean(" ".join(sections.get("abstract", [])), 2200)
    intro = _clean(" ".join(sections.get("introduction", [])), 1300)
    background = _clean(" ".join(sections.get("background", [])), 600)
    literature = _clean(extraction.literature_review.content, 700) if extraction.literature_review.found else ""

    # The topical anchor comes from the research objective, not from the
    # bibliography, Literature Review or citation passages (which may be noisy
    # or adversarial). No task-specific vocabulary is hardcoded.
    topic_passages = []
    if title:
        topic_passages.append(_clean("Research topic: " + title, 380))
    if abstract:
        topic_passages.append(_clean("Research objective: " + title + ". " + abstract[:520], 740))
    elif intro:
        topic_passages.append(_clean("Research objective: " + title + ". " + intro[:470], 680))
    if not topic_passages:
        fallback = _clean(" ".join(body_lines[:35]), 740)
        if fallback:
            topic_passages.append(fallback)
    topic_passages = list(dict.fromkeys(p for p in topic_passages if p))
    topic = topic_passages[0] if topic_passages else ""

    # Keep method coverage across the entire chapter for diagnostics, but also
    # build an independent, citation-free evidence stream. A fabricated citation
    # inserted into a Methods paragraph cannot itself establish that a method
    # is genuinely used. Papers mentioned ONLY on citation-bearing lines may
    # conservatively receive partial relevance rather than method confirmation.
    methods_text = " ".join(" ".join(sections.get(name, [])) for name in _METHODS if sections.get(name))
    methodology_passages = _chunks(methods_text, max_chars=620, max_chunks=32)
    # PDF line breaks are arbitrary: a single sentence often spans three or
    # more lines. Remove *citation-bearing sentences*, rather than destroying
    # complete lines and concatenating unrelated fragments. A citation-bearing
    # sentence remains available separately for diagnostic/adoption analysis.
    # Keep any full uncited methodological sentences for independent matching.
    method_sentences = _sentences(methods_text)
    numeric_marker = re.compile(r"\[\s*\d{1,3}(?:\s*[-,;–—]\s*\d{1,3})*\s*\]")
    author_year_marker = re.compile(r"\b[A-Z][a-z]+(?:\s+et\s+al\.)?\s*\(\s*(?:19|20)\d{2}\s*\)")
    uncited_sentences = [sentence for sentence in method_sentences
                         if sentence.strip() and not numeric_marker.search(sentence)
                         and not author_year_marker.search(sentence)]
    independent_methodology_passages = _chunks(" ".join(uncited_sentences), max_chars=620, max_chunks=32)
    methodology = methodology_passages[0] if methodology_passages else ""

    contexts: Dict[int, List[str]] = {}
    citation_lines: Dict[int, List[int]] = {}
    citation_sections: Dict[int, List[str]] = {}
    citation_claims: Dict[int, List[str]] = {}
    citation_aliases: Dict[int, List[str]] = {}
    for link in extraction.citation_reference_matches:
        line_no = link.get("line_number")
        if not isinstance(line_no, int) or not 1 <= line_no <= len(body_lines):
            continue
        excerpt = _citation_context(lines, line_no, ref_start)
        claim = _citation_claim(body_lines, section_map, line_no - 1,
                                link.get("citation_text", ""))
        # Capture the alias immediately *before* the exact citation marker.
        # It is a student claim, not proof that the academic paper defines it.
        marker = _clean(link.get("citation_text", ""), 80)
        preceding = claim.split(marker, 1)[0] if marker and marker in claim else ""
        alias_matches = list(re.finditer(
            r"\b(?:[A-Za-z]{2,}(?:Net|Former)(?:[- ]?\d{1,4})?"
            r"|[A-Za-z]{2,}[- ]?\d{1,4})\b", preceding, re.I))
        alias = (alias_matches[-1].group(0) if alias_matches and
                 len(preceding) - alias_matches[-1].end() <= 12 else None)
        section = section_map[line_no - 1]
        for index in link.get("matched_paper_indices", []):
            if not isinstance(index, int) or not 0 <= index < len(extraction.papers):
                continue
            citation_lines.setdefault(index, [])
            if line_no not in citation_lines[index]:
                citation_lines[index].append(line_no)
                citation_sections.setdefault(index, []).append(section)
            citation_claims.setdefault(index, [])
            if claim and claim not in citation_claims[index] and len(citation_claims[index]) < 3:
                citation_claims[index].append(claim)
            if alias and section in _METHODS:
                citation_aliases.setdefault(index, [])
                if alias not in citation_aliases[index]:
                    citation_aliases[index].append(alias)
            contexts.setdefault(index, [])
            if excerpt and excerpt not in contexts[index] and len(contexts[index]) < 3:
                contexts[index].append(excerpt)
    return ReportEvidence(title, topic, methodology, literature, contexts, citation_lines,
                          topic_passages, methodology_passages, citation_sections,
                          independent_methodology_passages, citation_claims,
                          method_sentences, citation_aliases)
