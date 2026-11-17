"""Conservatively reconstruct web URLs broken across physical PDF lines.

This is used only for URL extraction, not to rewrite source text or its line
numbers. It avoids joining the following prose sentence to a URL.
"""
import re
from typing import Sequence

_TOKEN_RE = re.compile(r"^[^\s<>\[\]{}()]+")


def extend_wrapped_url(url: str, lines: Sequence[str], index: int) -> str:
    """Return a URL continued on subsequent lines when a wrap is evident.

    The caller must verify that `url` ends the current source line. The
    continuation token must look like a path/host fragment; not a sentence.
    """
    current = url
    for next_line in lines[index + 1:index + 5]:
        candidate_match = _TOKEN_RE.match(next_line.strip())
        if candidate_match is None:
            break
        fragment = candidate_match.group(0)
        if fragment.startswith(("http://", "https://", "www.")):
            break
        # A trailing slash/dot on the previous line is a good indication of
        # a wrapped URL. The continuation must still look URL-like.
        pathlike = "/" in fragment or "." in fragment or "?" in fragment or "=" in fragment
        extension = bool(re.fullmatch(r"(?:html?|pdf|php|aspx?|org|net|com|io|edu|gov|in)\.?", fragment, re.I))
        if not ((current.endswith(("/", ".", "-")) and (pathlike or extension))
                or (current.endswith(".") and extension)):
            break
        current += fragment
    return current
