
"""Map-reduce summariser with per-call timing.

Short texts are summarised in one call. Long reports are split into chunks,
each chunk is summarised on its own (map), and the chunk summaries are then
merged into one final summary (reduce).

Timing instrumentation measures each llm.chat_json() call without changing
the prompts, arguments, or order of execution.
"""

import time
from typing import Tuple

from .chunking import chunk_text
from .config import Settings
from .schemas import ChunkSummary, FinalSummary


SYSTEM = (
    "You summarise student and trainee project reports for a faculty reviewer. "
    "Report only what the text actually says. Do not judge quality, do not "
    "praise or criticise, and do not add facts that are not in the text. "
    "Keep technical terms, numbers and names exactly as written. "
    "Reply with JSON only."
)


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


def timed_chat_json(stage, llm, *args, **kwargs):
    """Run one LLM call and print its elapsed time."""
    start = time.perf_counter()

    try:
        return llm.chat_json(*args, **kwargs)
    finally:
        elapsed = time.perf_counter() - start
        print(
            f"[TIMING] {stage}: {elapsed:.3f} seconds",
            flush=True,
        )


def summarise(
    text: str,
    llm,
    settings: Settings,
) -> Tuple[FinalSummary, int]:
    """Summarise report text and return the summary and chunk count."""
    total_start = time.perf_counter()

    chunks = chunk_text(
        text,
        settings.chunk_chars,
        settings.chunk_overlap_chars,
    )

    print("\n[TIMING] Summarisation started", flush=True)
    print(f"[TIMING] Input characters: {len(text):,}", flush=True)
    print(f"[TIMING] Number of chunks: {len(chunks)}", flush=True)

    if len(chunks) <= 1:
        out = timed_chat_json(
            "single-summary",
            llm,
            SYSTEM,
            SINGLE_PROMPT.format(text=text.strip()),
            FinalSummary,
            temperature=0.0,
            seed=settings.seed,
        )

        print(
            f"[TIMING] Total summarise(): "
            f"{time.perf_counter() - total_start:.3f} seconds",
            flush=True,
        )

        return out, 1

    # MAP: summarise each chunk individually.
    partials = []

    for i, ch in enumerate(chunks, 1):
        print(
            f"[TIMING] Map chunk {i}/{len(chunks)} "
            f"characters: {len(ch):,}",
            flush=True,
        )

        part = timed_chat_json(
            f"map chunk {i}/{len(chunks)}",
            llm,
            SYSTEM,
            MAP_PROMPT.format(
                i=i,
                n=len(chunks),
                text=ch,
            ),
            ChunkSummary,
            temperature=0.0,
            seed=settings.seed,
        )

        partials.append(part)

    # Prepare the partial summaries for the reduce call.
    joined = "\n\n".join(
        f"Part {i}: {p.summary}\n"
        + "\n".join(f"- {k}" for k in p.key_points)
        for i, p in enumerate(partials, 1)
    )

    print(
        f"[TIMING] Combined partial-summary characters: {len(joined):,}",
        flush=True,
    )

    # REDUCE: combine the partial summaries into one final summary.
    out = timed_chat_json(
        "reduce-final-summary",
        llm,
        SYSTEM,
        REDUCE_PROMPT.format(parts=joined),
        FinalSummary,
        temperature=0.0,
        seed=settings.seed,
    )

    print(
        f"[TIMING] Total summarise(): "
        f"{time.perf_counter() - total_start:.3f} seconds",
        flush=True,
    )

    return out, len(chunks)
