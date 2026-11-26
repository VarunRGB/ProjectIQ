"""Local Streamlit demo: streamlit run projectiq_ieee/app.py"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from projectiq_ieee.checker import to_agent_result
from projectiq_ieee.formats import analyze_document, SUPPORTED_EXTENSIONS
from projectiq_ieee.chat import answer_question

st.set_page_config(page_title="ProjectIQ | IEEE Format Checker", page_icon="📄", layout="wide")
st.title("ProjectIQ · IEEE Format Checker")
st.caption("Upload PDF, Word, text or OpenDocument research papers for an explainable IEEE-style assessment. Not an official IEEE certification.")

uploaded = st.file_uploader(
    "Upload a research paper (PDF, DOCX, DOC, TXT, MD, RTF, ODT)",
    type=list(SUPPORTED_EXTENSIONS),
)
if uploaded is None:
    st.info("Upload a document to review its structure and supported formatting rules. TXT/MD results are structure-only. DOC/RTF/ODT need LibreOffice installed locally.")
    st.stop()

try:
    report = analyze_document(uploaded.getvalue(), filename=uploaded.name)
except Exception as error:
    st.error(f"Could not evaluate the paper: {error}")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Structure score" if report["assessment_scope"] == "structure_only" else "Format score", f"{report['score']}/100")
c2.metric("Assessment", report["rating"])
c3.metric("Measurable rubric weight", f"{report['evaluated_weight']}/100")
st.caption(f"Input: **{report['source_format']}** · Method: {report['analysis_method']}" )
if report["assessment_scope"] == "structure_only":
    st.warning("This is a content-structure score, **not** an IEEE formatting/compliance score. TXT/MD cannot reveal margins, columns, fonts or page size.")
elif report["assessment_scope"] == "style_and_structure":
    st.info("DOCX checks Word section/style settings. Actual pagination and visual placement need verification in an exported PDF.")
if report["layout_verdict"] == "Two-column layout NOT detected":
    st.warning("Important: a two-column body layout was not detected. This paper does not currently resemble the target generic two-column IEEE conference layout, regardless of its other rubric points.")
elif report["layout_verdict"] == "Two-column layout uncertain":
    st.info("Two-column detection was inconclusive. Please inspect the paper manually.")
elif report["layout_verdict"] == "Two-column section NOT configured":
    st.warning("No two-column Word section was configured. Check your target IEEE Word template.")
st.progress((report["score"] or 0) / 100)

st.subheader("Criterion breakdown")
for category in report["categories"]:
    value = "Not applicable" if category["possible"] == 0 else f"{category['earned']:g} / {category['possible']}"
    with st.expander(f"{category['category']} — {value}", expanded=(category["status"] == "needs_attention")):
        for check in [c for c in report["checks"] if c["category"] == category["category"]]:
            icon = {"pass": "✅", "warning": "⚠️", "fail": "❌", "not_applicable": "➖"}[check["status"]]
            points = "Not measurable" if not check["applicable"] else f"{check['earned']:g}/{check['possible']}"
            st.markdown(f"**{icon} {check['criterion']}** — {points}")
            st.write(check["detail"])
            if check["status"] not in ("pass", "not_applicable") and check["suggestion"]:
                st.caption("Suggestion: " + check["suggestion"])

with st.expander("Important limitations"):
    for note in report["notes"]:
        st.write("• " + note)
    st.write(report["disclaimer"])

st.download_button(
    "Download full JSON evaluation",
    data=json.dumps(report, indent=2, ensure_ascii=False),
    file_name=Path(uploaded.name).stem + "_ieee_evaluation.json",
    mime="application/json",
)
st.download_button(
    "Download ProjectIQ AgentResult",
    data=json.dumps(to_agent_result(report), indent=2, ensure_ascii=False),
    file_name=Path(uploaded.name).stem + "_ieee_agent_result.json",
    mime="application/json",
)


st.subheader("Ask the formatting bot")
st.caption("Ask about this paper's detected issues. Responses use the measured report, not an LLM.")
import hashlib
document_fingerprint = hashlib.sha256(uploaded.getvalue() + uploaded.name.encode("utf-8")).hexdigest()
if st.session_state.get("last_document_fingerprint") != document_fingerprint:
    st.session_state["last_document_fingerprint"] = document_fingerprint
    st.session_state["ieee_chat_history"] = []
for turn in st.session_state.get("ieee_chat_history", []):
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
question = st.chat_input("E.g., How can I improve the paper's IEEE score?")
if question:
    reply = answer_question(question, report)
    st.session_state["ieee_chat_history"].extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": reply},
    ])
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        st.markdown(reply)
