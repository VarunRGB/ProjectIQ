"""One-call ProjectIQ Executioner evaluation, independent of the main orchestrator.

The main orchestrator should call evaluate(...), not individual Phase CLIs.
"""
from .core import OperatorContext, evaluate
from .contracts import OrchestrationError

__all__ = ['OperatorContext', 'OrchestrationError', 'evaluate']
