"""Split long text into overlapping chunks on paragraph boundaries."""
import re
from typing import List, Tuple

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

def _is_structure_heading(line: str) -> bool:
    """Recognise common report headings without classifying normal prose as headings."""
    s = line.strip()

    if not s or "\n" in s or len(s) > 180:
        return False

    # Markdown headings: # Introduction, ## Architecture, etc.
    if re.match(r"^#{1,6}\s+\S", s):
        return True

    # Explicit headings: Section 2, Chapter 3, Appendix A, etc.
    if re.match(
        r"^(?:section|chapter|appendix)\s+(?:\w+)",
        s,
        re.IGNORECASE,
    ):
        return True

    # Numbered report headings: 1. Introduction, 2.1 Database Design.
    numbered = re.match(
        r"^(\d+(?:\.\d+)*)(?:[.)])?\s+(.+)$",
        s,
    )
    if numbered:
        title = numbered.group(2).strip()
        words = title.split()

        # Avoid classifying ordinary numbered sentences as headings.
        if (
            1 <= len(words) <= 14
            and not title.endswith((".", "!", "?"))
        ):
            return True

    # Short ALL-CAPS headings.
    if (
        s.isupper()
        and any(ch.isalpha() for ch in s)
        and len(s.split()) <= 14
    ):
        return True

    # Short title-case headings, e.g. "System Architecture".
    if (
        len(s) <= 100
        and 2 <= len(s.split()) <= 10
        and not s.endswith((".", "!", "?", ",", ";"))
    ):
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", s)
        stop_words = {
            "a", "an", "and", "as", "at", "by", "for",
            "in", "of", "on", "or", "the", "to", "with",
        }
        significant = [w for w in words if w.lower() not in stop_words]

        if significant:
            title_case_count = sum(
                w[0].isupper() or w.isupper()
                for w in significant
            )

            if title_case_count / len(significant) >= 0.75:
                return True

    return False


def _structure_heading_depth(heading: str) -> int:
    """Infer heading hierarchy from Markdown or numbered headings."""
    s = heading.strip()

    markdown = re.match(r"^(#{1,6})\s+", s)
    if markdown:
        return len(markdown.group(1))

    numbered = re.match(
        r"^(\d+(?:\.\d+)*)(?:[.)])?\s+",
        s,
    )
    if numbered:
        return numbered.group(1).count(".") + 1

    return 1


def _structure_blocks(text: str) -> List[str]:
    """
    Split text into paragraphs and structural blocks.

    Headings become independent blocks. Consecutive lines belonging to
    paragraphs, lists, tables, or fenced code blocks remain grouped.
    """
    blocks: List[str] = []
    current: List[str] = []
    fence_marker = None

    def flush() -> None:
        if current:
            block = "\n".join(current).strip()
            if block:
                blocks.append(block)
            current.clear()

    for raw_line in text.splitlines():
        s = raw_line.strip()

        # Keep fenced code blocks together, including internal blank lines.
        if fence_marker is not None:
            current.append(raw_line.rstrip())

            if s.startswith(fence_marker):
                flush()
                fence_marker = None

            continue

        if s.startswith("```") or s.startswith("~~~"):
            flush()
            fence_marker = s[:3]
            current.append(raw_line.rstrip())
            continue

        if not s:
            flush()
        elif _is_structure_heading(s):
            flush()
            blocks.append(s)
        else:
            current.append(raw_line.rstrip())

    flush()
    return blocks


def chunk_text_structure_aware(
    text: str,
    max_chars: int = 6000,
    overlap: int = 400,
) -> List[str]:
    """
    Hybrid structure-aware chunking.

    Strategy:
    1. Detect headings and maintain a section hierarchy.
    2. Group each heading with its following content.
    3. Pack whole paragraphs and structural blocks into chunks.
    4. Split oversized blocks using the existing sentence-aware helper.
    5. Add section context when a chunk starts mid-section.
    6. Use overlap when a single oversized block must span chunks.

    The existing chunk_text() function remains unchanged.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be greater than zero")

    overlap = max(0, overlap)

    if len(text) <= max_chars:
        return [text.strip()] if text.strip() else []

    blocks = _structure_blocks(text)

    if not blocks:
        return []

    # Each record contains:
    # (content, section hierarchy, whether the content includes that hierarchy)
    records: List[Tuple[str, Tuple[str, ...], bool]] = []

    section_stack: List[Tuple[int, str]] = []
    pending_headings: List[str] = []

    for block in blocks:
        if _is_structure_heading(block):
            depth = _structure_heading_depth(block)

            # Replace the current heading at this level and any deeper level.
            while section_stack and section_stack[-1][0] >= depth:
                section_stack.pop()

            section_stack.append((depth, block))
            pending_headings.append(block)
            continue

        context = tuple(title for _, title in section_stack)

        # Keep consecutive headings attached to the first following content block.
        if pending_headings:
            combined = "\n\n".join(pending_headings + [block])
            has_full_context = len(pending_headings) >= len(context)
            pending_headings.clear()
        else:
            combined = block
            has_full_context = False

        records.append((combined, context, has_full_context))

    # Preserve trailing headings even if the document ends without body text.
    if pending_headings:
        context = tuple(title for _, title in section_stack)
        records.append(
            (
                "\n\n".join(pending_headings),
                context,
                len(pending_headings) >= len(context),
            )
        )

    chunks: List[str] = []
    current = ""

    for block, context, has_full_context in records:
        context_label = (
            "Section context: " + " > ".join(context)
            if context
            else ""
        )

        # Reserve space for context and overlap when an oversized block
        # has to be split into multiple pieces.
        needs_split = (
            len(block) > max_chars
            or (
                bool(context_label)
                and not has_full_context
                and len(block) + len(context_label) + 2 > max_chars
            )
        )

        if needs_split:
            context_reserve = len(context_label) + 2 if context_label else 0
            overlap_reserve = min(overlap, max_chars // 4)

            piece_limit = max(
                1,
                max_chars - context_reserve - overlap_reserve - 4,
            )
            pieces = _split_long(block, piece_limit)
        else:
            pieces = [block]

        for piece_index, piece in enumerate(pieces):
            is_continuation = piece_index > 0
            heading_is_present = has_full_context and piece_index == 0

            # Prefer adding an entire structural block to the current chunk.
            if current and len(current) + 2 + len(piece) <= max_chars:
                current += "\n\n" + piece
                continue

            previous_chunk = current

            if current:
                chunks.append(current)

            current = ""

            # When starting midway through a section, carry its heading path.
            prefix = (
                ""
                if heading_is_present or not context_label
                else context_label
            )

            # Apply overlap only when continuing an oversized block.
            # Natural paragraph/section boundaries do not receive redundant overlap.
            carry = ""

            if is_continuation and previous_chunk and overlap:
                separator_cost = 4 if prefix else 2
                available = max(
                    0,
                    max_chars - len(prefix) - len(piece) - separator_cost,
                )

                if available:
                    carry = _tail(previous_chunk, min(overlap, available))

            new_parts = [
                part
                for part in (carry, prefix, piece)
                if part
            ]
            current = "\n\n".join(new_parts)

    if current:
        chunks.append(current)

    return chunks
