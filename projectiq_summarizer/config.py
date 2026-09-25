"""Runtime settings. Every value can be overridden from the command line or
through environment variables, so nothing needs editing in code."""
import os
from dataclasses import dataclass


@dataclass
class Settings:
    # Where Ollama is listening and which local model to use.
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model: str = os.getenv("PROJECTIQ_MODEL", "llama3.1:8b")
    num_ctx: int = int(os.getenv("PROJECTIQ_NUM_CTX", "8192"))
    timeout_s: int = int(os.getenv("PROJECTIQ_TIMEOUT", "600"))

    # How many times the judge scores the same text. The spread between
    # these runs is one part of the confidence score. 1 is fastest.
    judge_samples: int = int(os.getenv("PROJECTIQ_SAMPLES", "3"))
    sample_temperature: float = 0.6
    seed: int = 42

    # Chunking for long reports (roughly 1,500 tokens per chunk).
    chunk_chars: int = 6000
    chunk_overlap_chars: int = 400

    # Above this length the judge sees the summary plus the opening and
    # closing of the report instead of the full text.
    max_judge_chars: int = 14000
