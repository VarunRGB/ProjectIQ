"""The demo app runs and shows results (stand-in models; Streamlit's own test runner)."""
import sys
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import fake_models  # noqa: E402

APP = str(ROOT / "projectiq_integrity" / "app.py")


@pytest.fixture
def app(monkeypatch):
    fake_models.install(monkeypatch)
    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception
    return at


def test_document_tab_runs_on_sample(app):
    app.button(key="run_doc").click().run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Overlap with references"] == "32.9%"
    assert metrics["Inside copied chunks"] == "18.4%"
    assert any("Chunk 1" in e.label for e in app.expander)
    assert any("AI percentage" in i.value for i in app.info)    # AI section shown (model not loaded in tests)
