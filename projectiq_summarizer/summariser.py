"""Map-reduce summariser.

Short texts are summarised in one call. Long reports are split into chunks,
each chunk is summarised on its own (map), and the chunk summaries are then
merged into one final summary (reduce). This keeps every call inside the
context window of a small local model."""
from typing import Tuple

from .chunking import chunk_text
from .config import Settings
from .schemas import ChunkSummary, FinalSummary

SYSTEM = (
    "You summarise student and trainee project reports for a faculty reviewer. "
    "Report only what the text actually says. Do not judge quality, do not "
    "praise or criticise, and do not add facts that are not in the text. "
    "Keep technical terms, numbers and names exactly as written. "
    "Reply with JSON only.")

MAP_PROMPT = """Summarise this part ({i} of {n}) of a project report.

Return JSON with:
- "summary": 2 to 4 sentences covering what this part says
- "key_points": 2 to 5 short factual points

TEXT:
\"\"\"
{text}
\"\"\""""

REDUCE_PROMPT = """Below are summaries of consecutive parts of one project report.
Merge them into a single summary of the whole report.

Return JSON with:
- "summary": 4 to 6 sentences: what was built or studied, how, and what the results were
- "key_points": 3 to 6 short factual points

PART SUMMARIES:
{parts}"""

SINGLE_PROMPT = """Summarise this project report text.

Return JSON with:
- "summary": 3 to 5 sentences: what was built or studied, how, and what the results were
- "key_points": 3 to 6 short factual points

TEXT:
\"\"\"
{text}
\"\"\""""


def summarise(text: str, llm, settings: Settings) -> Tuple[FinalSummary, int]:
    chunks = chunk_text(text, settings.chunk_chars, settings.chunk_overlap_chars)
    if len(chunks) <= 1:
        out = llm.chat_json(SYSTEM, SINGLE_PROMPT.format(text=text.strip()),
                            FinalSummary, temperature=0.0, seed=settings.seed)
        return out, 1

    partials = []
    for i, ch in enumerate(chunks, 1):
        part = llm.chat_json(SYSTEM, MAP_PROMPT.format(i=i, n=len(chunks), text=ch),
                             ChunkSummary, temperature=0.0, seed=settings.seed)
        partials.append(part)
    joined = "\n\n".join(
        f"Part {i}: {p.summary}\n" + "\n".join(f"- {k}" for k in p.key_points)
        for i, p in enumerate(partials, 1))
    out = llm.chat_json(SYSTEM, REDUCE_PROMPT.format(parts=joined), FinalSummary,
                        temperature=0.0, seed=settings.seed)
    return out, len(chunks)
