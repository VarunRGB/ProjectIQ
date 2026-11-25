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


class _FakeClassifier:
    """Stand-in for the Desklib model: AI-like (0.99) only for text with stock AI phrases."""

    available = True

    def __init__(self, *args, **kwargs):
        pass

    def missing(self):
        return []

    def __call__(self, text):
        from projectiq_integrity.ai_detection import stock_phrases

        return (0.99 if stock_phrases(text) else 0.05), 100


def test_ai_only_mode_runs_on_sample(monkeypatch):
    from projectiq_integrity import ai_detection

    fake_models.install(monkeypatch)
    monkeypatch.setattr(ai_detection, "Classifier", _FakeClassifier)
    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio[0].set_value("AI check only (one document, no references)").run()
    assert not at.exception
    assert not any(b.key == "run_doc" for b in at.button)          # no similarity check in this mode
    at.checkbox(key="ai_sample").check().run()
    at.button(key="run_ai").click().run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["AI-like share of the writing"] == "0.0%"
    assert metrics["Passages flagged"] == "0 / 2"
    assert any("No reference papers were given" in c.value for c in at.caption)


def test_ai_only_mode_without_model_says_not_available(app):
    app.radio[0].set_value("AI check only (one document, no references)").run()
    app.checkbox(key="ai_sample").check().run()
    app.button(key="run_ai").click().run()
    assert not app.exception
    assert any("not available" in i.value for i in app.info)
