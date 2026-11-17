"""Bounded, citation-aware evidence preparation for Phase 3.

All passages come from the student report, not the bibliography. We keep
source line numbers and section provenance. Neither summarizer nor LLM is used.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List

from .models import ParsedDocument, ResearchExtraction

# Headings have two independent properties: their *surface wording* and their
# *structural position*. Numbered/Markdown subsections inherit an enclosing
# methodological role unless they explicitly introduce another kind of content.
# This keeps unknown but legitimate method subheadings usable without treating
# arbitrary unnumbered prose as a method heading.
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:(?:chapter|section)\s+)?(\d+(?:\.\d+)*)(?:[.):\u2013\u2014-])?\s+(.+?)\s*$", re.I
)
_ROMAN_HEADING_RE = re.compile(r"^\s*([IVXLCDM]+)[.)]\s+(.+?)\s*$", re.I)
_MARKDOWN_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")

# These are *families of research-section intentions*, not a closed list of
# acceptable titles or subject-specific keywords. An unknown numbered child of
# a method section inherits the method role via the outline hierarchy.
_SECTION_FAMILIES = (
    ("references", re.compile(r"^(?:references|bibliography|works cited|literature cited|appendix(?:es)?|supplementary materials?)$", re.I)),
    ("front_matter", re.compile(r"^(?:keywords?|index terms|general terms|categories and subject descriptors|authors?|author information|affiliations?)$", re.I)),
    ("closing", re.compile(r"(?:^|\b)(?:conclusions?|limitations?|future work|acknowledg(?:e)?ments?|author contributions|conflicts? of interest|funding)(?:\b|$)", re.I)),
    ("results", re.compile(r"(?:^|\b)(?:results?|findings|discussion|evaluation|performance evaluation|experiments?(?!al (?:setup|design|procedure|method))|ablation|benchmarks?|validation|error analysis|quantitative analysis|qualitative analysis)(?:\b|$)", re.I)),
    ("abstract", re.compile(r"^(?:abstract|executive summary|summary|synopsis)$", re.I)),
    ("literature", re.compile(r"(?:^|\b)(?:literature (?:review|survey)|review of (?:the )?literature|related (?:work|studies|research)|prior (?:work|research|studies)|previous (?:work|studies)|state of (?:the )?art|existing (?:approaches|methods|systems|work)|theoretical framework)(?:\b|$)", re.I)),
    ("background", re.compile(r"^(?:background|preliminaries|foundations|theoretical background|conceptual background|problem context)$", re.I)),
    ("introduction", re.compile(r"^(?:introduction|overview|motivation|problem statement|problem definition|problem formulation|research questions?|research objectives?|objectives?|aims?(?: and objectives)?|contributions?|scope of (?:the )?study)$", re.I)),
    ("methodology", re.compile(
        r"(?:^|\b)(?:method(?:ology|ologies|s)?|approach(?:es)?|proposed (?:solution|system|work|technique)|"
        r"materials and methods|experimental (?:setup|design|procedure|method)|research design|study design|"
        r"system (?:overview|model|design|architecture)|model (?:design|architecture|development|training|selection)|"
        r"algorithm(?:s|ic framework)?|procedures?|protocols?|implementation|architecture|"
        r"framework|pipeline|workflow|training|inference|classification|"
        r"feature (?:extraction|engineering|selection)|pre[- ]?processing|post[- ]?processing|"
        r"data (?:collection|acquisition|preparation|processing|preprocessing|augmentation|sources|description)|"
        r"dataset(?:s)?|sampling|instrumentation|apparatus|simulation (?:setup|method|model)|"
        r"empirical strategy|estimation strategy|analytical strategy|computational (?:methods?|procedure|approach)|"
        r"numerical (?:methods?|scheme)|implementation details|system configuration|"
        r"experimental protocol|methodological framework|statistical (?:analysis|methods?)|"
        r"analysis plan|study (?:population|setting|participants)|participants|materials|"
        r"case study design|measurements?|data analysis|data sources)(?:\b|$)", re.I
    )),
)


@dataclass(frozen=True)
class _Heading:
    title: str
    role: str
    depth: int
    number: tuple[int, ...] | None = None
    # Structural headings are evidence of a boundary even if their exact
    # vocabulary isn't known. A plain prose line never gets this privilege.


# PDF text can contain inter-character spaces within section headings, for
# example `R ELA TED WORK`, `I NTRODUCTION`, `B ACKGROUND &M OTIVATION`.
# This fallback is only used for exact, compact heading-family equivalents;
# it does not fuzzy-match arbitrary research vocabulary.
_COMPACT_ROLE_HEADINGS = {
    "introduction": "introduction",
    "background": "background",
    "backgroundmotivation": "background",
    "backgroundandmotivation": "background",
    "relatedwork": "literature",
    "relatedworks": "literature",
    "relatedstudies": "literature",
    "relatedresearch": "literature",
    "literaturereview": "literature",
    "reviewofliterature": "literature",
    "literaturesurvey": "literature",
    "priorwork": "literature",
    "previouswork": "literature",
    "stateoftheart": "literature",
    "methods": "methodology",
    "methodology": "methodology",
    "proposedapproach": "methodology",
    "approach": "methodology",
    "training": "methodology",
    "classification": "methodology",
    "implementation": "methodology",
    "results": "results",
    "discussion": "results",
    "evaluation": "results",
    "references": "references",
    "bibliography": "references",
    "conclusions": "closing",
    "conclusion": "closing",
    "acknowledgments": "closing",
    "acknowledgements": "closing",
    "limitations": "closing",
    "limitationsdiscussions": "closing",
}


def _heading_role(title: str) -> str:
    canonical = re.sub(r"[_–—-]+", " ", title).strip(" \t.:;").casefold()
    canonical = re.sub(r"\s+", " ", canonical)
    compact = re.sub(r"[^a-z0-9]", "", canonical)
    if compact in _COMPACT_ROLE_HEADINGS:
        return _COMPACT_ROLE_HEADINGS[compact]
    for role, pattern in _SECTION_FAMILIES:
        if pattern.search(canonical):
            return role
    return "other"


def _parse_heading(line: str) -> _Heading | None:
    value = line.strip()
    if not value or len(value) > 125:
        return None
    numbered = _NUMBERED_HEADING_RE.fullmatch(value)
    roman = None if numbered else _ROMAN_HEADING_RE.fullmatch(value)
    markdown = None if numbered or roman else _MARKDOWN_HEADING_RE.fullmatch(value)
    structural = bool(numbered or roman or markdown)
    number = None
    if numbered:
        number = tuple(int(component) for component in numbered.group(1).split("."))
        if number[0] > 150:  # Years, street addresses and page noise are not section indices.
            return None
        title = numbered.group(2)
        depth = len(number)
    elif roman:
        title = roman.group(2)
        depth = 1
    elif markdown:
        title = markdown.group(2)
        depth = len(markdown.group(1))
        prefix = _NUMBERED_HEADING_RE.fullmatch(title)
        if prefix:
            title = prefix.group(2)
            number = tuple(int(component) for component in prefix.group(1).split("."))
    else:
        title = value
        depth = 1
    # Disallow sentence-like numbered list items and long body lines. Section
    # titles in ordinary PDFs rarely end in sentence punctuation.
    title = title.strip(" \t#")
    title = title.lstrip(".:–—- ")
    if not title or len(title) > 105 or len(title.split()) > 13:
        return None
    if re.search(r"[.!?]$", title) and not title.endswith("."):
        return None
    if re.search(r"[.!?]$", title) and title.endswith("."):
        title = title[:-1].rstrip()
        if not title or (structural and len(title.split()) > 5):
            return None
    if any(x in title for x in ("[", "]", "=", "\u2022")):
        return None
    role = _heading_role(title)
    # Ordinary prose can contain words such as 'method', 'algorithm' or
    # 'results'. Without numbering/Markdown, demand a compact heading-like
    # line, not just a taxonomy word appearing somewhere in a sentence.
    if not structural:
        words = title.split()
        if len(words) > 8 or re.search(r"[.!?;]$", value) or "," in title:
            return None
        if words and words[0].casefold() in {"we", "our", "this", "these", "those", "they", "it"}:
            return None
        letters = [c for c in title if c.isalpha()]
        upper = bool(letters and all(c.isupper() for c in letters))
        important = [w for w in words if w.casefold() not in {"and", "of", "the", "for", "to", "in", "a", "an", "with"}]
        title_case = bool(important and sum(w[0].isupper() for w in important) / len(important) >= .7)
        core_head = bool(role != "other" and len(words) <= 4 and
                         _heading_role(words[0]) == role)
        if not (upper or title_case or core_head):
            return None
    # Unknown standalone sentences are never headings, even if title-cased.
    # Unknown *numbered* or Markdown headings are trusted as structural breaks.
    if role == "other" and not structural:
        letters = [c for c in title if c.isalpha()]
        if not letters or len(letters) < 6 or not all(c.isupper() for c in letters):
            return None
    if role == "other" and structural and title.endswith(('.', ':', ';', ',')):
        return None
    if role == "other" and numbered and title[0].islower():
        return None
    # Single numeric list entries must not accidentally absorb arbitrary prose.
    if role == "other" and numbered and depth == 1 and title.split()[0].casefold() in {
        "we", "the", "our", "this", "these", "first", "second", "third",
    }:
        return None
    return _Heading(title.lower(), role, depth, number)


def _heading(line: str) -> str:
    """Compatibility helper returning a lowercase title or empty string."""
    parsed = _parse_heading(line)
    return parsed.title if parsed else ""


def is_method_section(name: str) -> bool:
    """Shared role check used by the relevance scorer's evidence safeguards."""
    return _heading_role(name) == "methodology"


def _line_annotation(lines: List[str]) -> tuple[Dict[str, List[str]], List[str]]:
    """Aggregate semantic sections and preserve a per-line effective section.

    Numbered subheading under Methods may have a completely unknown name,
    e.g. '3.1 Candidate class creation'; it inherits the parent's method role.
    A new top-level heading (including unknown headings) ends the old role.
    Explicit Results/References always override ancestors.
    """
    blocks: Dict[str, List[str]] = {}
    labels: List[str] = []
    stack: list[tuple[int, str, str]] = []  # depth, effective role, label
    active_role = ""
    active_label = ""
    current_top_number: int | None = None
    for line in lines:
        heading = _parse_heading(line)
        # A numbered sequence restarting inside chapter 4 is usually an
        # algorithm/list of steps, not chapters 1-4 all over again.
        if (heading is not None and heading.number and heading.depth == 1
                and current_top_number is not None
                and heading.number[0] <= current_top_number
                and heading.role == "other"):
            heading = None
        elif (heading is not None and heading.number and heading.depth == 1
              and heading.role == "other" and active_role == "methodology"
              and current_top_number is None):
            heading = None
        if heading:
            if heading.number and heading.depth == 1:
                current_top_number = max(current_top_number or 0, heading.number[0])
            while stack and stack[-1][0] >= heading.depth:
                stack.pop()
            parent_role = stack[-1][1] if stack else ""
            parent_label = stack[-1][2] if stack else ""
            role = (parent_role if heading.role == "other" and parent_role else heading.role)
            active_role = role
            # An unknown child should retain an inherited *method label* so
            # downstream adoption checks don't mistake it for a generic section.
            active_label = (parent_label if heading.role == "other" and parent_label else heading.title)
            stack.append((heading.depth, active_role, active_label))
        elif active_role and line.strip() and not _is_page_furniture(line):
            if active_role in {"abstract", "introduction", "background", "literature", "methodology"}:
                blocks.setdefault(active_role, []).append(line.strip())
        labels.append(active_label)
    return blocks, labels


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
        parsed = _parse_heading(value)
        if value and (not parsed or parsed.role == "other") and not _is_page_furniture(value):
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
    return _line_annotation(lines)[0]


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
        if not value or value.isdigit():
            continue
        parsed = _parse_heading(value)
        if parsed and (parsed.number is not None or parsed.role in {
                "abstract", "introduction", "background", "literature", "references", "front_matter"}):
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
        if (not part or "@" in part or " • " in part or
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
    return _line_annotation(lines)[1]


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
    methods_text = " ".join(sections.get("methodology", []))
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
            if alias and is_method_section(section):
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
