"""Optional real ProjectIQ integration tests.  Run in the ProjectIQ repository.

These tests intentionally fail if the integration was not connected to the
submission pipeline.  The first test checks the IEEE adapter, while the
second calls collector.collect() with no other agent sources (no Ollama).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "demo_two_column.pdf"


def test_ieee_adapter_returns_score_and_checks():
    module = pytest.importorskip("projectiq_core.ieee_integration")
    if not EXAMPLE.exists():
        pytest.skip("demo_two_column.pdf is missing from ProjectIQ/examples")
    result = module.run_ieee_check(EXAMPLE)
    assert result["status"] == "COMPLETED", result
    assert 0 <= result["score"] <= 100
    assert result["max_score"] == 100
    assert result["checks"]


def test_collect_automatically_writes_ieee_json(tmp_path):
    """Checks the actual submission hook; no other models need to run."""
    collector = pytest.importorskip("projectiq_core.collector")
    if not EXAMPLE.exists():
        pytest.skip("demo_two_column.pdf is missing from ProjectIQ/examples")
    submission, report_bytes = collector.make_submission(
        EXAMPLE, title="IEEE submission smoke test", submission_id="IEEE-CI-001",
        repo=None, ref=None
    )
    out = tmp_path / "collection"
    collector.collect(submission=submission, report_bytes=report_bytes, out=out,
                      mode="LIVE", sources={}, progress=lambda message: None)
    ieee_path = out / "ieee_format.json"
    assert ieee_path.exists(), (
        "collector.collect() did not create ieee_format.json. "
        "Connect run_ieee_check(report) to the paper snapshot in "
        "projectiq_core/collector.py."
    )
    result = json.loads(ieee_path.read_text(encoding="utf-8"))
    assert result["status"] == "COMPLETED", result
    assert isinstance(result["score"], (int, float))
    assert 0 <= result["score"] <= 100
    assert result["checks"]
