"""Remove citation markers from a sentence before it is compared with a reference.

ProjectIQ has ONE citation parser: projectiq_research.citation_extractor (research
agent). Finding citations, linking them to the reference list and reporting
uncited references all belong to the research agent; the integrity agent only
needs the markers out of the way so "[3]" or "(Smith, 2020)" does not change a
similarity score.
"""

from __future__ import annotations

import re

from projectiq_research.citation_extractor import extract_citations


def strip_citations(text: str) -> str:
    """Remove citation markers found by the research agent's extractor."""
    out = text
    for raw in sorted({c.text for c in extract_citations(text)}, key=len, reverse=True):
        out = out.replace(raw, " ")
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
