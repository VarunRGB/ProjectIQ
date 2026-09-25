"""Split long text into overlapping chunks on paragraph boundaries."""
import re
from typing import List

_SENT = re.compile(r"(?<=[.!?])\s+")


def _paragraphs(text: str) -> List[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras or ([text.strip()] if text.strip() else [])


def _split_long(par: str, limit: int) -> List[str]:
    """Break one oversized paragraph at sentence ends (or hard-cut as a last resort)."""
    out, cur = [], ""
    for sent in _SENT.split(par):
        while len(sent) > limit:
            out.append(sent[:limit])
            sent = sent[limit:]
        if len(cur) + len(sent) + 1 > limit and cur:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def _tail(text: str, n: int) -> str:
    if n <= 0 or len(text) <= n:
        return ""
    t = text[-n:]
    sp = t.find(" ")
    return t[sp + 1:] if sp != -1 else t


def chunk_text(text: str, max_chars: int = 6000, overlap: int = 400) -> List[str]:
    if len(text) <= max_chars:
        return [text.strip()] if text.strip() else []
    pieces: List[str] = []
    for par in _paragraphs(text):
        pieces.extend(_split_long(par, max_chars) if len(par) > max_chars else [par])

    chunks, cur = [], ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 2 > max_chars:
            chunks.append(cur)
            carry = _tail(cur, overlap)
            cur = f"{carry}\n\n{piece}" if carry else piece
        else:
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks
