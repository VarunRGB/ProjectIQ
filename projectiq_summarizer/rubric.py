"""Load and validate a rubric file (JSON)."""
import json
from pathlib import Path
from typing import Optional

from .schemas import Rubric

DEFAULT_RUBRIC = Path(__file__).with_name("default_rubric.json")


def load_rubric(path: Optional[str] = None) -> Rubric:
    p = Path(path) if path else DEFAULT_RUBRIC
    rubric = Rubric.model_validate(json.loads(p.read_text(encoding="utf-8")))
    ids = [c.id for c in rubric.criteria]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Rubric {p} has duplicate criterion ids: {ids}")
    if not rubric.criteria:
        raise ValueError(f"Rubric {p} has no criteria")
    return rubric
