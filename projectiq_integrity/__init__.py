"""ProjectIQ Integrity Agent (Module owner: Surabhi Mane).

Checks a student's research or project document against the reference papers it uses,
fully offline, and returns advisory evidence for the Rubric Agent:
- verbatim and near-verbatim sentences
- paraphrases: found by MiniLM, confirmed by DeBERTa in both directions
- copied chunks and the percentage of the document taken from each reference
- AI-style writing, flagged only when style measures and a local language model agree
"""
from .agent import run_integrity  # noqa: F401
from .contracts import AgentResult, Evidence, Finding  # noqa: F401
