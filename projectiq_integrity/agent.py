"""The Integrity Agent: checks one student's research or project document against the
reference papers it uses, and returns an AgentResult for the Rubric Agent.

It gathers evidence only. It never assigns or changes a mark.

    from projectiq_integrity import run_integrity
    result = run_integrity("S101/report.pdf", "S101/references", submission_id="S101")
"""
from pathlib import Path
from typing import Optional, Union

from .ai_detection import PassageScorer
from .config import DEFAULT, IntegritySettings
from .contracts import AgentResult
from .document_check import check_document, to_agent_result

AGENT_NAME = "integrity"


def run_integrity(document: Union[str, Path], references_dir: Union[str, Path],
                  submission_id: Optional[str] = None, settings: IntegritySettings = DEFAULT,
                  ai_scorer: Optional[PassageScorer] = None) -> AgentResult:
    """Overlap with the references (verbatim, near-verbatim, DeBERTa-confirmed paraphrase,
    copied chunks) plus the AI percentage of the student's own sentences."""
    document = Path(document)
    report = check_document(document, Path(references_dir), settings=settings, ai_scorer=ai_scorer)
    return to_agent_result(report, submission_id or document.stem, settings)
