"""ProjectIQ Integrity Agent (Module owner: Surabhi Mane).

Three checks, all offline, all advisory:
- code similarity with JPlag 6.1.0 (starter code excluded)
- report similarity against the cohort and a reference corpus (Research Integrity Analyzer)
- AI-style text, flagged only when style measures and a local language model agree
"""
from .agent import IntegrityAgent  # noqa: F401
from .contracts import AgentResult, Evidence, Finding  # noqa: F401
