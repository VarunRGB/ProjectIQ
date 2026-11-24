"""ProjectIQ unified evaluation foundation (v1.5 phase 1)."""
from .contracts import Evaluation, Submission, AgentResult, AIAnalysis
from .adapters import normalize
__all__=['Evaluation','Submission','AgentResult','AIAnalysis','normalize']
