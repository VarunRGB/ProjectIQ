"""Phase 4: reviewed-VM functional test execution contracts.

The 4A handoff does not launch a VM or run student code. Guest test execution
is separate and must be invoked only by the trusted Phase 2 VM controller.
"""
from .handoff import prepare_handoff, Phase4HandoffError

__all__ = ['prepare_handoff', 'Phase4HandoffError']
