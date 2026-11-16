"""Temporary demo screen for the Integrity Agent (ProjectIQ, Module: Integrity).

    streamlit run projectiq_integrity/app.py

Runs on this computer only (http://localhost:8501): nothing is uploaded anywhere.
The final faculty review screen is Gaurav's module; this app exists to show and
test the Integrity Agent on its own.
"""
import html
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from projectiq_integrity.config import JPLAG_JAR, MODELS_DIR, REQUIRED_JAVA  # noqa: E402
from projectiq_integrity.document_check import (COLOURS, COPY_CATEGORIES, PARA, SIMILAR,  # noqa: E402
                                                check_document, highlighted_document, to_agent_result, to_html)

SAMPLES = ROOT / "tests" / "fixtures"
DOC_TYPES = ["pdf", "docx", "txt", "md"]
KIND_LABELS = {"copied_chunk": "Copied chunk", "similar_report_text": "Overlap with a source",
               "similar_code": "Similar code", "ai_style_text": "AI-style text"}

st.set_page_config(page_title="ProjectIQ · Integrity Agent", layout="wide")
st.markdown("""<style>
.block-container{padding-top:3.6rem;max-width:1300px}
.doc{background:#fafbfc;border:1px solid #e3e6ea;border-radius:6px;padding:14px 16px;
     font:15px/1.6 system-ui,Segoe UI,sans-serif;color:#1d2433}
.m{border-radius:3px;cursor:help}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.quote{border-left:3px solid #d5dae3;padding:4px 10px;margin:4px 0 10px;color:#1d2433;background:#fafbfc}
</style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Sidebar: what is installed (shows the offline story)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def environment_status() -> list:
    from projectiq_integrity.jplag_runner import java_major_version

    java = java_major_version()
    items = [
        ("MiniLM (meaning similarity)", (MODELS_DIR / "all-MiniLM-L6-v2").exists()),
        ("DeBERTa (confirms paraphrase)", (MODELS_DIR / "nli-deberta-v3-small").exists()),
        ("distilgpt2 (AI-style check)", (MODELS_DIR / "distilgpt2").exists()),
        ("JPlag 6.1.0 (code similarity)", JPLAG_JAR.exists()),
        (f"Java {java if java > 0 else 'not found'} (JPlag needs {REQUIRED_JAVA})", java >= REQUIRED_JAVA),
    ]
    return items


with st.sidebar:
    st.markdown("### ProjectIQ · Integrity Agent")
    st.caption("Everything runs on this computer. No document leaves it.")
    st.markdown("**Installed locally**")
    for label, ok in environment_status():
        st.markdown(f"{'✅' if ok else '⚠️'} {label}")
    if not all(ok for _, ok in environment_status()):
        st.caption("Missing items: run `python scripts/setup_offline.py` once while online.")
    st.divider()
    st.caption("Findings are evidence for a faculty member to review. "
               "The Integrity Agent never assigns or deducts marks.")


def save_uploads(files, folder: Path) -> list:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in files:
        p = folder / Path(f.name).name
        p.write_bytes(f.getbuffer())
        paths.append(p)
    return paths


def legend() -> str:
    names = {**{c: c for c in COPY_CATEGORIES}, SIMILAR: "similar meaning (not counted)"}
    return " ".join(f"<span class='sw' style='background:{COLOURS[c]}'></span>{names[c]}&nbsp;&nbsp;"
                    for c in (*COPY_CATEGORIES, SIMILAR))


def quote(container, text: str) -> None:
    """Show a passage as a quote block (text escaped, so it can never break the page)."""
    container.markdown(f"<div class='quote'>{html.escape(text)}</div>", unsafe_allow_html=True)


tab_doc, tab_class = st.tabs(["Research document vs its references", "Class comparison (code and reports)"])

# --------------------------------------------------------------------------- #
# Tab 1: one research document against its references
# --------------------------------------------------------------------------- #
with tab_doc:
    st.markdown("#### How much of a document was taken from its references, and from where")
    mode = st.radio("Input", ["Use the sample report", "Upload a document and its references"],
                    horizontal=True, label_visibility="collapsed")
    doc_path = refs_dir = None
    if mode == "Use the sample report":
        doc_path, refs_dir = SAMPLES / "research" / "S201_report.txt", SAMPLES / "research" / "references"
        st.caption(f"Sample: `{doc_path.name}` checked against "
                   f"{', '.join(p.name for p in sorted(refs_dir.iterdir()))}.")
    else:
        c1, c2 = st.columns(2)
        up_doc = c1.file_uploader("Student document", type=DOC_TYPES)
        up_refs = c2.file_uploader("Reference papers (select several)", type=DOC_TYPES, accept_multiple_files=True)
        if up_doc and up_refs:
            work = Path(tempfile.gettempdir()) / "projectiq_app"
            shutil.rmtree(work, ignore_errors=True)
            doc_path = save_uploads([up_doc], work / "doc")[0]
            refs_dir = work / "refs"
            save_uploads(up_refs, refs_dir)

    if st.button("Run similarity check", type="primary", disabled=doc_path is None, key="run_doc"):
        with st.spinner("Reading documents and comparing every sentence with the references..."):
            try:
                st.session_state["doc_report"] = check_document(doc_path, refs_dir)
            except Exception as exc:  # noqa: BLE001 - show any failure on screen
                st.session_state.pop("doc_report", None)
                st.error(f"The check failed: {exc}")

    rep = st.session_state.get("doc_report")
    if rep:
        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Overlap with references", f"{rep.overall_percent}%")
        m2.metric("Inside copied chunks", f"{rep.percent_in_chunks}%")
        m3.metric("Sentences checked", rep.sentences_checked, help=f"{rep.words_checked} words")
        m4.metric("References", len(rep.references))

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**By type** (share of the document's words)")
            st.dataframe(pd.DataFrame({"Type": list(rep.percent_by_category), "%": list(rep.percent_by_category.values())}),
                         hide_index=True, width="stretch")
        with c2:
            st.markdown("**By reference**")
            if rep.percent_by_reference:
                st.dataframe(pd.DataFrame({"Reference": list(rep.percent_by_reference),
                                           "%": list(rep.percent_by_reference.values())}),
                             hide_index=True, width="stretch")
            else:
                st.info("No overlap with any reference.")

        st.markdown(f"**Copied chunks** ({len(rep.chunks)})")
        if not rep.chunks:
            st.caption("No run of 3 or more copied sentences.")
        for k, c in enumerate(rep.chunks, 1):
            with st.expander(f"Chunk {k}: {c.sentences} sentences, {c.words} words from {c.reference} "
                             f"({'cited, not quoted' if c.cited else 'no citation'})", expanded=k == 1):
                a, b = st.columns(2)
                a.caption(f"Document, {c.document_location}")
                quote(a, c.document_text)
                b.caption(f"{c.reference}, {c.reference_location}")
                quote(b, c.reference_text)

        st.markdown("**The document** (hover a highlighted sentence to see its source and scores)")
        st.markdown(legend(), unsafe_allow_html=True)
        doc_html = (f"<style>.m{{border-radius:3px;cursor:help}} body{{margin:0}} "
                    f".doc{{background:#fafbfc;border:1px solid #e3e6ea;border-radius:6px;padding:14px 16px;"
                    f"font:15px/1.6 system-ui,Segoe UI,sans-serif;color:#1d2433}}</style>"
                    f"<div class='doc'>{highlighted_document(rep)}</div>")
        components.html(doc_html, height=420, scrolling=True)

        flagged = [m for m in rep.matches if m.category]
        with st.expander(f"Every matched sentence ({len(flagged)})"):
            if flagged:
                df = pd.DataFrame([{
                    "Where": m.location, "Type": m.category, "Cited": "yes" if m.cited else "",
                    "Sentence": m.text, "Source": f"{m.reference} {m.reference_location}",
                    "Source sentence": m.reference_text, "Word overlap": m.lexical, "Meaning": m.semantic,
                    "DeBERTa (doc→src / src→doc)": (f"{m.entail_forward:.2f} / {m.entail_backward:.2f}"
                                                    if m.entail_forward >= 0 else ""),
                } for m in sorted(flagged, key=lambda m: m.index)])
                types = st.multiselect("Show types", sorted(df["Type"].unique()), default=sorted(df["Type"].unique()))
                st.dataframe(df[df["Type"].isin(types)], hide_index=True, width="stretch")
            else:
                st.caption("No sentence matched a reference.")

        result = to_agent_result(rep, Path(rep.document).stem)
        with st.expander(f"What the Integrity Agent sends to the Rubric Agent ({len(result.findings)} findings)"):
            for f in result.findings:
                st.markdown(f"**{KIND_LABELS.get(f.kind, f.kind)}** · confidence {f.confidence:.2f}")
                st.write(f.summary)
                st.caption(f.limitations)
            st.json(json.loads(result.model_dump_json()), expanded=False)

        for n in rep.notes:
            st.caption(n)
        st.caption(f"Models used: embeddings {rep.backends.get('embeddings')}; NLI {rep.backends.get('nli')}.")
        d1, d2 = st.columns(2)
        d1.download_button("Download HTML report", to_html(rep), file_name=f"{Path(rep.document).stem}_similarity.html",
                           mime="text/html")
        d2.download_button("Download JSON", json.dumps(rep.to_dict(), indent=2),
                           file_name=f"{Path(rep.document).stem}_similarity.json", mime="application/json")

# --------------------------------------------------------------------------- #
# Tab 2: a whole class compared with each other
# --------------------------------------------------------------------------- #
with tab_class:
    st.markdown("#### Every student compared with every other: code (JPlag), report text, AI-style writing")
    use_sample = st.toggle("Use the sample class (5 students)", value=True)
    base = SAMPLES / "integrity"
    c1, c2 = st.columns(2)
    code_dir = c1.text_input("Code folder (one folder, .zip or .py per student)",
                             str(base / "code") if use_sample else "", disabled=use_sample)
    starter = c2.text_input("Starter code given to everyone (optional)",
                            str(base / "starter") if use_sample else "", disabled=use_sample)
    reports_dir = c1.text_input("Reports folder (one report per student)",
                                str(base / "reports") if use_sample else "", disabled=use_sample)
    refs_class = c2.text_input("Reference documents (optional)",
                               str(base / "references") if use_sample else "", disabled=use_sample)

    def opt(path: str):
        return Path(path) if path.strip() and Path(path).exists() else None

    if st.button("Run class comparison", type="primary", key="run_class",
                 disabled=not (opt(code_dir) or opt(reports_dir))):
        from projectiq_integrity.agent import IntegrityAgent

        with st.spinner("Running JPlag and comparing reports..."):
            try:
                agent = IntegrityAgent(Path(tempfile.gettempdir()) / "projectiq_app_class", code_dir=opt(code_dir),
                                       reports_dir=opt(reports_dir), references_dir=opt(refs_class),
                                       base_code=opt(starter)).prepare()
                st.session_state["class_results"] = {sid: agent.analyze(sid) for sid in agent.submission_ids()}
                st.session_state["class_notes"] = agent.notes
            except Exception as exc:  # noqa: BLE001
                st.session_state.pop("class_results", None)
                st.error(f"The comparison failed: {exc}")

    results = st.session_state.get("class_results")
    if results:
        st.divider()
        for n in st.session_state.get("class_notes", []):
            st.caption(n)
        table = pd.DataFrame([{
            "Student": sid, "Findings": len(r.findings),
            "Checks that flagged": ", ".join(sorted({KIND_LABELS.get(f.kind, f.kind) for f in r.findings})),
            "Highest confidence": max((f.confidence for f in r.findings), default=None),
        } for sid, r in results.items()])
        st.dataframe(table, hide_index=True, width="stretch")

        sid = st.selectbox("Look at one student", list(results))
        r = results[sid]
        if not r.findings:
            st.success(f"No integrity findings for {sid}.")
        for f in r.findings:
            with st.expander(f"{KIND_LABELS.get(f.kind, f.kind)} · confidence {f.confidence:.2f} · {f.summary}",
                             expanded=True):
                st.progress(f.confidence)
                pairs = [f.evidence[i:i + 2] for i in range(0, len(f.evidence), 2)]
                for pair in pairs:
                    cols = st.columns(len(pair))
                    for col, ev in zip(cols, pair):
                        col.caption(ev.location)
                        if f.kind == "similar_code":
                            col.code(ev.quote, language="python")
                        else:
                            quote(col, ev.quote)
                st.caption(f.limitations)
        with st.expander("Notes for this student"):
            for n in r.notes:
                st.caption(n)
