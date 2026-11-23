"""ProjectIQ Phase 5: evidence-scoped, non-executing code-quality analysis.

Phase 5 is deliberately independent of the VM runner. Its inputs are inert
snapshot files; it neither imports student modules nor runs project tooling.
"""

from .pipeline import analyze_project

__all__ = ['analyze_project']
