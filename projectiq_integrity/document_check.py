"""Research document vs its references: how much was copied, and from where.

For a student's research or project document and a folder of the papers it uses:

1. Every sentence of the document (outside the reference list, quoted text and
   citation markers removed) is matched to its closest reference sentence by
   Research Integrity Analyzer's overlap_analyzer: word 3-gram overlap (lexical)
   plus MiniLM embedding similarity (semantic, works with different words).
2. Each match gets one category:
     verbatim       almost the same words (lexical overlap >= 0.80)
     near-verbatim  mostly the same words, a few changed (>= 0.45)
     paraphrase     different words, same meaning: MiniLM finds the candidate,
                    then DeBERTa NLI must confirm that EACH sentence implies the
                    other (mutual entailment)
   Matches MiniLM finds but DeBERTa does not confirm are listed as "similar
   meaning (not confirmed)" and are NOT counted as copying.
3. Copied chunks: runs of consecutive document sentences that copy consecutive
   sentences of the same reference.
4. Percentages, weighted by words: overall, per category and per reference.

This measures textual overlap for a human to review. It is not a plagiarism
verdict: quoted and properly attributed material is normal academic practice.
"""
import html
import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import ai_detection
from .config import DEFAULT, IntegritySettings
from .contracts import AgentResult, Evidence, Finding
from .documents import build_corpus, load_document, load_folder, location_label, source_sentences
from .evidence import Signal, build_findings
from .ria import models as ria_models
from projectiq_research.citation_extractor import extract_citations  # one citation parser for ProjectIQ
from .ria.citation_analyzer import strip_citations
from .ria.config import DEFAULT_SETTINGS as RIA_DEFAULTS, Settings as RiaSettings
from .ria.overlap_analyzer import analyze_overlap, lexical_overlap, ngram_jaccard, remove_quoted
from .ria.pdf_extractor import Document
from .ria.preprocessing import tokenize

logger = logging.getLogger(__name__)

VERBATIM, NEAR, PARA, SIMILAR = "verbatim", "near-verbatim", "paraphrase", "similar meaning (not confirmed)"
COPY_CATEGORIES = (VERBATIM, NEAR, PARA)
VERBATIM_JACCARD = 0.80
LIMITATION = ("Overlap with a source is not proof of misconduct: shared definitions, standard "
              "phrasing and properly attributed paraphrase are normal. Text in quotation marks "
              "was excluded. A faculty member must read both passages.")


@dataclass
class SentenceMatch:
    index: int                 # sentence number in the document
    location: str              # "p.4" or "para 7"
    text: str
    words: int
    category: Optional[str]    # one of the categories above, or None
    reference: str = ""
    reference_location: str = ""
    reference_text: str = ""
    reference_position: int = -1
    lexical: float = 0.0
    semantic: float = 0.0
    entail_forward: float = -1.0   # P(document sentence implies reference sentence); -1 = not checked
    entail_backward: float = -1.0
    cited: bool = False            # the sentence carries a citation marker


@dataclass
class CopiedChunk:
    reference: str
    first: int
    last: int
    sentences: int
    words: int
    document_location: str
    reference_location: str
    document_text: str
    reference_text: str
    cited: bool = False        # at least one sentence in the chunk has a citation marker


@dataclass
class DocumentReport:
    document: str
    references: List[str]
    sentences_checked: int
    words_checked: int
    overall_percent: float
    percent_by_category: Dict[str, float]
    percent_by_reference: Dict[str, float]
    percent_in_chunks: float
    chunks: List[CopiedChunk]
    matches: List[SentenceMatch]
    backends: Dict[str, str]
    notes: List[str] = field(default_factory=list)
    # AI-style check on the student's own writing (sentences not copied from a reference)
    ai_findings: List[dict] = field(default_factory=list)
    ai_notes: List[str] = field(default_factory=list)
    ai_detection: dict = field(default_factory=lambda: {"status": "NOT_AVAILABLE", "estimated_percent": None,
                                                         "interpretation": None, "confidence": None,
                                                         "flagged_passages": [], "limitations": []})

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
_DASHES = re.compile(r"[-\u2010-\u2015\u2212_/]+")
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def copy_form(text: str) -> str:
    """The text as it matters for copying: lower case, hyphens and dashes as spaces,
    straight quotes, single spaces. "real-time" and "Real time" are then the same, so
    dropping a hyphen cannot turn a copied sentence into "edited"."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    text = _DASHES.sub(" ", text)
    return " ".join(re.findall(r"[a-z0-9']+|[^\sa-z0-9']", text))


def copy_overlap(a: str, b: str, n: int) -> Tuple[float, bool]:
    """(word overlap, verbatim?) on the copy form.

    Word overlap (word triples, with a bonus when one sentence contains the other)
    decides near-verbatim. Verbatim is stricter: the same word triples (plain overlap
    0.80 or more, no containment bonus), or the same words where the only differences
    are spellings inside words: a typo, "colour"/"color". A swapped, added or removed
    word is never verbatim."""
    fa, fb = copy_form(a), copy_form(b)
    words = lexical_overlap(fa, fb, n)
    verbatim = ngram_jaccard(fa, fb, n) >= VERBATIM_JACCARD or spelling_only(fa.split(), fb.split())
    return words, verbatim


def spelling_only(ta: List[str], tb: List[str], max_changed: int = 2) -> bool:
    """True when two token lists differ only in up to max_changed misspelt words."""
    if not ta or not tb or abs(len(ta) - len(tb)) > 0:
        return False
    changed = 0
    for op, i1, i2, j1, j2 in SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op != "replace" or i2 - i1 != j2 - j1:
            return False
        for x, y in zip(ta[i1:i2], tb[j1:j2]):
            if SequenceMatcher(None, x, y).ratio() < 0.85:
                return False      # a different word, not a spelling of the same one
            changed += 1
    return changed <= max_changed


def _confirm_paraphrases(pairs: List[Tuple[str, str]], ria_settings: RiaSettings) -> Tuple[list, str]:
    """Run DeBERTa NLI both ways. Returns ([(forward, backward)], backend)."""
    if not pairs:
        return [], "not needed"
    try:
        nli = ria_models.get_nli(ria_settings)
        if not nli.is_neural:
            return [None] * len(pairs), f"{nli.backend} (not neural: paraphrases not confirmed)"
        fwd = nli.predict([a for a, _ in pairs], [b for _, b in pairs])
        bwd = nli.predict([b for _, b in pairs], [a for a, _ in pairs])
        return [(f["entailment"], b["entailment"]) for f, b in zip(fwd, bwd)], nli.backend
    except ria_models.ModelUnavailableError as exc:
        return [None] * len(pairs), f"unavailable ({exc})"


def match_sentences(doc: Document, references: Dict[str, Document], settings: IntegritySettings = DEFAULT,
                    ria_settings: RiaSettings = RIA_DEFAULTS) -> Tuple[List[SentenceMatch], Dict[str, str]]:
    corpus = build_corpus(references)
    if not corpus.sentences:
        return [], {"embeddings": "no reference text", "nli": "not run"}
    results = analyze_overlap(doc, corpus, ria_settings)
    positions = {name: {s.text: (k, s) for k, s in enumerate(source_sentences(d))} for name, d in references.items()}
    by_index = {s.index: s for s in doc.sentences}

    matches, to_confirm = [], []
    for r in results:
        sent = by_index.get(r.sentence_index)
        cleaned, _ = remove_quoted(strip_citations(r.paper_text))
        words = len(tokenize(cleaned))
        lexical, verbatim = copy_overlap(cleaned, r.reference_text, ria_settings.ngram_n)
        pos, ref_sent = positions.get(r.reference_file, {}).get(r.reference_text, (-1, None))
        m = SentenceMatch(
            index=r.sentence_index, location=location_label(doc, sent) if sent else "?", text=r.paper_text,
            words=words, category=None, reference=r.reference_file,
            reference_location=location_label(references[r.reference_file], ref_sent) if ref_sent else "?",
            reference_text=r.reference_text, reference_position=pos, lexical=round(lexical, 3),
            semantic=round(r.embedding_cosine, 3), cited=bool(extract_citations(r.paper_text)))
        if verbatim:
            m.category = VERBATIM
        elif lexical >= ria_settings.near_verbatim_jaccard:
            m.category = NEAR
        elif r.embedding_cosine >= settings.paraphrase_candidate_cosine:
            to_confirm.append((m, cleaned))
        matches.append(m)

    verdicts, nli_backend = _confirm_paraphrases([(c, m.reference_text) for m, c in to_confirm], ria_settings)
    for (m, _), v in zip(to_confirm, verdicts):
        if v is None:
            m.category = SIMILAR
            continue
        m.entail_forward, m.entail_backward = round(v[0], 3), round(v[1], 3)
        mutual = min(v) >= settings.paraphrase_entailment
        m.category = PARA if mutual else SIMILAR
    emb_backend = results[0].embedding_backend if results else "not run"
    return matches, {"embeddings": emb_backend, "nli": nli_backend}


# --------------------------------------------------------------------------- #
# Chunks and percentages
# --------------------------------------------------------------------------- #
def find_chunks(matches: List[SentenceMatch], settings: IntegritySettings = DEFAULT) -> List[CopiedChunk]:
    """Consecutive copied sentences that follow the same reference in order."""
    copied = sorted((m for m in matches if m.category in COPY_CATEGORIES), key=lambda m: m.index)
    order = [m.index for m in sorted(matches, key=lambda m: m.index)]
    rank = {idx: k for k, idx in enumerate(order)}   # position among checked sentences
    chunks, run = [], []

    def close(run):
        if len(run) >= settings.chunk_min_sentences:
            chunks.append(CopiedChunk(
                reference=run[0].reference, first=run[0].index, last=run[-1].index, sentences=len(run),
                words=sum(m.words for m in run),
                document_location=f"{run[0].location} to {run[-1].location}",
                reference_location=f"{run[0].reference_location} to {run[-1].reference_location}",
                document_text=" ".join(m.text for m in run),
                reference_text=" ".join(m.reference_text for m in run),
                cited=any(m.cited for m in run)))

    for m in copied:
        if run:
            prev = run[-1]
            same_ref = m.reference == prev.reference
            close_in_doc = rank[m.index] - rank[prev.index] <= 1 + settings.chunk_max_gap
            close_in_ref = (m.reference_position >= 0 and prev.reference_position >= 0
                            and 0 <= m.reference_position - prev.reference_position <= settings.chunk_ref_window)
            if not (same_ref and close_in_doc and close_in_ref):
                close(run)
                run = []
        run.append(m)
    if run:
        close(run)
    return chunks


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _rescue_front_matter(doc: Document, notes: List[str]) -> Document:
    """If the PDF reader found no headings, nearly everything is labelled front matter
    (title page, certificate, contents) and would be skipped. Check it all instead."""
    from dataclasses import replace

    from .ria.pdf_extractor import FRONT_MATTER

    countable = [s for s in doc.sentences if s.section != "References"]
    front = [s for s in countable if s.section == FRONT_MATTER]
    if countable and len(front) / len(countable) > 0.6:
        notes.append(f"Most of {doc.source} was read as front matter (no section headings found), "
                     "so the whole text was checked, including any title page or certificate.")
        return replace(doc, sentences=[replace(s, section="Body") if s.section == FRONT_MATTER else s
                                       for s in doc.sentences])
    return doc


def check_document(document: Path, references_dir: Path, settings: IntegritySettings = DEFAULT,
                   ria_settings: RiaSettings = RIA_DEFAULTS, check_ai: bool = True,
                   ai_scorer: Optional[ai_detection.PassageScorer] = None) -> DocumentReport:
    """The whole integrity check for one document: overlap with its references, then the
    AI-style check on the sentences the student wrote themselves."""
    notes: List[str] = []
    doc = _rescue_front_matter(load_document(Path(document)), notes)
    refs = load_folder(Path(references_dir))
    if not refs:
        notes.append(f"No readable reference documents in {references_dir}.")
    matches, backends = match_sentences(doc, refs, settings, ria_settings)
    total = sum(m.words for m in matches)
    copied = [m for m in matches if m.category in COPY_CATEGORIES]
    chunks = find_chunks(matches, settings)
    in_chunks = {i for c in chunks for i in range(c.first, c.last + 1)}
    by_ref: Dict[str, int] = {}
    for m in copied:
        by_ref[m.reference] = by_ref.get(m.reference, 0) + m.words
    if backends.get("embeddings") != "sentence-transformers":
        notes.append("MiniLM was not used, so sentences with different words cannot be found. "
                     "Run scripts/setup_offline.py.")
    if "not neural" in backends.get("nli", "") or "unavailable" in backends.get("nli", ""):
        notes.append("DeBERTa was not used, so no paraphrase was confirmed or counted.")
    notes.append("Percentages are shares of the document's words (quoted text and the reference "
                  "list excluded). They measure overlap for review, not misconduct.")

    ai_findings, ai_notes = [], []
    detection = None
    if check_ai:
        # Copied sentences are someone else's writing, so they are left out of the AI check.
        copied_idx = {m.index for m in copied}
        own_sentences = [(s.index, location_label(doc, s), s.text) for s in doc.body_sentences()
                         if s.index not in copied_idx]
        detection, _ = ai_detection.estimate(own_sentences, scorer=ai_scorer, settings=settings)
        if detection["status"] == "COMPLETED":
            ai_findings = [f.model_dump() for f in
                           build_findings(ai_detection.to_signal(detection, doc.source, settings))]
            ai_notes = [detection["interpretation"]]
        else:
            ai_notes = detection["limitations"][:1]
    return DocumentReport(
        document=doc.source, references=sorted(refs), sentences_checked=len(matches), words_checked=total,
        overall_percent=_pct(sum(m.words for m in copied), total),
        percent_by_category={c: _pct(sum(m.words for m in copied if m.category == c), total)
                             for c in COPY_CATEGORIES},
        percent_by_reference={r: _pct(w, total) for r, w in sorted(by_ref.items(), key=lambda kv: -kv[1])},
        percent_in_chunks=_pct(sum(m.words for m in copied if m.index in in_chunks), total),
        chunks=chunks, matches=matches, backends=backends, notes=notes,
        ai_findings=ai_findings, ai_notes=ai_notes,
        **({"ai_detection": detection} if detection else {}))


# --------------------------------------------------------------------------- #
# Findings for the Rubric Agent
# --------------------------------------------------------------------------- #
def to_signals(report: DocumentReport, settings: IntegritySettings = DEFAULT) -> List[Signal]:
    signals = []
    for c in report.chunks:
        signals.append(Signal(
            kind="copied_chunk",
            summary=(f"{c.sentences} consecutive sentences ({c.words} words, "
                     f"{_pct(c.words, report.words_checked)}% of the document) follow {c.reference} closely, "
                     + ("with a citation but without quotation marks." if c.cited else "with no citation.")),
            evidence=[Evidence(source="report", location=f"document {c.document_location}", quote=c.document_text),
                      Evidence(source="corpus", location=f"{c.reference} {c.reference_location}",
                               quote=c.reference_text)],
            scores={"near_verbatim": float(c.sentences), "paraphrase": 0.0,
                    "share_of_report": c.words / max(1, report.words_checked)},
            limitations=LIMITATION))
    for ref, pct in report.percent_by_reference.items():
        if pct < settings.reference_report_percent:
            continue
        hits = [m for m in report.matches if m.reference == ref and m.category in COPY_CATEGORIES]
        near = sum(m.category in (VERBATIM, NEAR) for m in hits)
        evidence = []
        for m in sorted(hits, key=lambda m: (m.category == PARA, -m.lexical))[:settings.max_pairs_quoted]:
            evidence.append(Evidence(source="report", location=f"document {m.location} ({m.category})", quote=m.text))
            evidence.append(Evidence(source="corpus", location=f"{ref} {m.reference_location}", quote=m.reference_text))
        signals.append(Signal(
            kind="similar_report_text",
            summary=(f"{pct}% of the document's words overlap with {ref}: {near} verbatim or near-verbatim "
                     f"sentence(s), {len(hits) - near} confirmed paraphrase(s)."),
            evidence=evidence,
            scores={"near_verbatim": float(near), "paraphrase": float(len(hits) - near), "share_of_report": pct / 100,
                    "lexical_backend": float(report.backends.get("embeddings") != "sentence-transformers")},
            limitations=LIMITATION))
    return signals


def to_agent_result(report: DocumentReport, submission_id: str, settings: IntegritySettings = DEFAULT) -> AgentResult:
    notes = [f"Document vs references: {report.overall_percent}% of {report.words_checked} words overlap "
             f"with {len(report.references)} reference(s); {report.percent_in_chunks}% sits in copied chunks."]
    notes += report.notes + report.ai_notes
    notes.append("All findings are advisory. The Integrity Agent never assigns or deducts marks.")
    findings = build_findings(to_signals(report, settings)) + [Finding(**f) for f in report.ai_findings]
    findings.sort(key=lambda f: f.confidence, reverse=True)
    return AgentResult(agent="integrity", submission_id=submission_id, findings=findings, notes=notes)


# --------------------------------------------------------------------------- #
# Readable HTML report
# --------------------------------------------------------------------------- #
COLOURS = {VERBATIM: "#f6c1c1", NEAR: "#f9dcb5", PARA: "#f5ef9e", SIMILAR: "#e3e8ef"}


def highlighted_document(report: DocumentReport) -> str:
    """The document's checked sentences as HTML, copied ones coloured by type, with
    the source in a hover tooltip. Used by the HTML report and the demo app."""
    e = html.escape
    body, last_loc = [], None
    for m in sorted(report.matches, key=lambda m: m.index):
        if last_loc is not None and m.location != last_loc:
            body.append("</p><p>")
        last_loc = m.location
        if m.category:
            nli = (f", DeBERTa both ways {m.entail_forward:.2f} / {m.entail_backward:.2f}"
                   if m.entail_forward >= 0 else "")
            tip = (f"{m.category}{' (cited)' if m.cited else ''}: {m.reference} {m.reference_location} | "
                   f"word overlap {m.lexical:.2f}, meaning {m.semantic:.2f}{nli}\n\nSource: {m.reference_text}")
            body.append(f"<span class='m' style='background:{COLOURS[m.category]}' title='{e(tip)}'>"
                        f"{e(m.text)}</span>")
        else:
            body.append(e(m.text))
    return "<p>" + " ".join(body) + "</p>"


def to_html(report: DocumentReport) -> str:
    e = html.escape
    rows_ref = "".join(f"<tr><td>{e(r)}</td><td>{p}%</td></tr>" for r, p in report.percent_by_reference.items())
    rows_cat = "".join(f"<tr><td><span class='sw' style='background:{COLOURS[c]}'></span>{c}</td><td>{p}%</td></tr>"
                       for c, p in report.percent_by_category.items())
    chunks = "".join(
        f"<tr><td>{c.sentences} sentences, {c.words} words<br><small>{e(c.document_location)}</small><br>"
        f"<small><b>{'cited, not quoted' if c.cited else 'no citation'}</b></small></td>"
        f"<td>{e(c.document_text)}</td><td><b>{e(c.reference)}</b> <small>{e(c.reference_location)}</small><br>"
        f"{e(c.reference_text)}</td></tr>" for c in report.chunks) or "<tr><td colspan=3>None found.</td></tr>"
    body = highlighted_document(report)
    notes = "".join(f"<li>{e(n)}</li>" for n in report.notes)
    det = report.ai_detection
    if det.get("status") == "COMPLETED":
        passages = "".join(
            f"<li><small>{e(p['location'])}, {p['words']} words, AI probability "
            f"{p['score']:.3f} (flagged at &gt;= {p['threshold']})"
            + (f", stock phrases: {e(', '.join(p['stock_phrases']))}" if p["stock_phrases"] else "")
            + f"</small><br>{e(p['text'])}</li>" for p in det["flagged_passages"])
        ai_html = (f"<p class='big'>{det['estimated_percent']}%</p><p>{e(det['interpretation'])} "
                   f"Confidence: {e(det['confidence'])}.</p><ul>{passages}</ul>"
                   + "".join(f"<p class='note'>{e(n)}</p>" for n in det["limitations"]))
    else:
        ai_html = (f"<p>AI percentage {e(det.get('status', 'NOT_AVAILABLE').replace('_', ' ').lower())}.</p>"
                   + "".join(f"<p class='note'>{e(n)}</p>" for n in det.get("limitations", [])[:1]))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Similarity report: {e(report.document)}</title>
<style>
body{{font:15px/1.55 system-ui,Segoe UI,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1d2433}}
h1{{font-size:22px;margin-bottom:4px}} h2{{font-size:17px;margin-top:28px}}
.big{{font-size:40px;font-weight:600}} table{{border-collapse:collapse;width:100%;margin:8px 0}}
td,th{{border:1px solid #d5dae3;padding:6px 8px;vertical-align:top;text-align:left}}
.sw{{display:inline-block;width:12px;height:12px;margin-right:6px;border-radius:2px}}
.m{{border-radius:3px;padding:1px 0;cursor:help}} .doc{{background:#fafbfc;border:1px solid #e3e6ea;padding:14px;border-radius:6px}}
small{{color:#5b6475}} .note{{color:#5b6475;font-size:13px}}
</style></head><body>
<h1>Similarity report: {e(report.document)}</h1>
<p class="note">Checked {report.sentences_checked} sentences ({report.words_checked} words) against
{len(report.references)} reference(s). Quoted text and the reference list are excluded.
Overlap is for a faculty member to review, not a plagiarism verdict.</p>
<div class="big">{report.overall_percent}%</div><div>of the document's words overlap with the references
({report.percent_in_chunks}% inside copied chunks)</div>
<h2>By type</h2><table>{rows_cat}<tr><td><span class='sw' style='background:{COLOURS[SIMILAR]}'></span>{SIMILAR}</td><td>not counted</td></tr></table>
<h2>By reference</h2><table><tr><th>Reference</th><th>Share of document</th></tr>{rows_ref or "<tr><td colspan=2>No overlap.</td></tr>"}</table>
<h2>Copied chunks</h2><table><tr><th>Size</th><th>Document</th><th>Reference</th></tr>{chunks}</table>
<h2>Document (hover a highlighted sentence to see its source)</h2>
<p class="note">Headings, captions and the reference list are left out. Text inside quotation marks is shown but never counted.</p>
<div class="doc">{body}</div>
<h2>AI-generated writing (the student's own sentences only)</h2>{ai_html}
<h2>Notes</h2><ul class="note">{notes}</ul>
<p class="note">Models: embeddings {e(report.backends.get("embeddings", ""))}; NLI {e(report.backends.get("nli", ""))}.</p>
</body></html>"""


def write_outputs(report: DocumentReport, out_dir: Path, submission_id: str,
                  settings: IntegritySettings = DEFAULT) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "_", submission_id)
    paths = {"report_json": out_dir / f"{stem}_similarity.json",
             "report_html": out_dir / f"{stem}_similarity.html",
             "agent_result": out_dir / f"{stem}.json"}
    paths["report_json"].write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    paths["report_html"].write_text(to_html(report), encoding="utf-8")
    paths["agent_result"].write_text(to_agent_result(report, submission_id, settings).model_dump_json(indent=2),
                                     encoding="utf-8")
    return paths
