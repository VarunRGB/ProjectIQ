"""Generate frontend fixtures by running the REAL ProjectIQ unified backend.

Native agent outputs below are hand-built, synthetic but schema-faithful
(fictional student projects, fictional repositories). Everything downstream
of the native outputs is produced by the actual projectiq_core code:

    collect() -> build_evidence() -> evaluate() -> Phase 2 rubric engine

Only the Ollama rubric model is replaced by a deterministic mock that answers
through the real constrained wire schema; hardening and validation still run,
so proposals can be rejected and deferred exactly as in a live run.

Run from the repository root:
    python frontend/tools/generate_fixtures.py
Writes frontend/src/mock/fixtures/*.json. No network, VM, or model is used.
With --keep DIR, also keeps each complete output directory (DIR/demo-<name>) so the
local bridge (frontend/bridge/server.py) can serve and re-grade it with the real engine.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
INPUTS = HERE / 'fixture_inputs'
OUT = ROOT / 'frontend' / 'src' / 'mock' / 'fixtures'

from projectiq_core.collector import collect, make_submission, load_json  # noqa: E402
from projectiq_core.orchestrator import DEFAULT_RUBRIC, configure_rubric, evaluate  # noqa: E402
from projectiq_core.phase2.contracts import Rubric  # noqa: E402
from projectiq_core.phase2.hardening import grounded_summary  # noqa: E402
from projectiq_executioner.orchestration.matrix import build_test_case_matrix  # noqa: E402
from projectiq_executioner.orchestration.core import _recommendations  # noqa: E402
from projectiq_executioner.phase6.catalog import diagnosis  # noqa: E402

CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
ATTEST = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def words(t):
    return len(re.findall(r"[A-Za-z][A-Za-z'\-]*", t))


def sentence_with(text, needle):
    for s in re.split(r'(?<=[.!?])\s+', text.replace('\n', ' ')):
        if needle in s:
            return s.strip()
    raise KeyError(needle)


# --------------------------------------------------------------------------- #
# Native outputs: Summarizer (identical to the live SUMMARY_ONLY collector)
# --------------------------------------------------------------------------- #
def summarizer_native(report: Path):
    text = report.read_text(encoding='utf-8')
    return {'result_kind': 'SUMMARY_ONLY', 'name': report.name, 'model': 'grounded-extractive-L1',
            'summary': grounded_summary(text), 'word_count': len(text.split()), 'chunks': 1,
            'elapsed_s': 0.0412, 'collector_resource_lifecycle': {'ollama_release': 'NOT_USED'}}


# --------------------------------------------------------------------------- #
# Native outputs: Integrity (DocumentReport.to_dict + ai_detection contract)
# --------------------------------------------------------------------------- #
def integrity_campus(report: Path):
    text = report.read_text(encoding='utf-8')
    total = 1584
    s_kumar = sentence_with(text, 'Kumar and Singh describe')
    s_owasp = sentence_with(text, 'The OWASP Top Ten lists')
    s_db = sentence_with(text, 'The registrations table references users')
    s_patel = sentence_with(text, 'Patel and Joshi studied')
    s_nielsen = sentence_with(text, "Nielsen's heuristics remain")
    s_pw = sentence_with(text, 'Passwords are hashed with password_hash')
    s_tx = sentence_with(text, 'The event registration flow starts a transaction')
    s_role = sentence_with(text, 'Roles are stored in the users table')
    s_admin = sentence_with(text, 'The administrator dashboard runs one grouped query')
    s_three = sentence_with(text, 'CampusConnect follows a three-layer structure')
    matches = [
        dict(index=31, location='para 9', text=s_kumar, words=words(s_kumar), category='near-verbatim',
             reference='kumar_singh_2018.pdf', reference_location='p.3',
             reference_text='We describe a PHP and MySQL campus event system and observe that a relational schema with foreign keys prevented most of the data-consistency problems of the earlier spreadsheet process.',
             reference_position=44, lexical=0.71, semantic=0.93, entail_forward=0.91, entail_backward=0.87, cited=True),
        dict(index=33, location='para 9', text=s_owasp, words=words(s_owasp), category='paraphrase',
             reference='owasp_top10_2021.pdf', reference_location='p.5',
             reference_text='Broken Access Control moves up to the first position, and Injection remains among the most serious risks; use of parameterized queries and server-side access checks is recommended.',
             reference_position=12, lexical=0.28, semantic=0.86, entail_forward=0.82, entail_backward=0.64, cited=True),
        dict(index=34, location='para 9', text=s_patel, words=words(s_patel), category='similar meaning (not confirmed)',
             reference='patel_joshi_2022.pdf', reference_location='p.1',
             reference_text='Our experiments show that explicit confirmation feedback reduces duplicate form submissions.',
             reference_position=3, lexical=0.22, semantic=0.74, entail_forward=0.41, entail_backward=0.38, cited=True),
        dict(index=48, location='para 14', text=s_db, words=words(s_db), category='verbatim',
             reference='kumar_singh_2018.pdf', reference_location='p.6',
             reference_text=s_db, reference_position=97, lexical=0.97, semantic=0.99,
             entail_forward=0.98, entail_backward=0.98, cited=False),
        dict(index=49, location='para 14', text=s_tx, words=words(s_tx), category='near-verbatim',
             reference='kumar_singh_2018.pdf', reference_location='p.6',
             reference_text='The registration flow opens a transaction, counts the existing registrations for the event, compares this with capacity and inserts a row only if a seat remains.',
             reference_position=98, lexical=0.66, semantic=0.95, entail_forward=0.90, entail_backward=0.88, cited=False),
        dict(index=58, location='para 17', text=s_pw, words=words(s_pw), category='similar meaning (not confirmed)',
             reference='owasp_top10_2021.pdf', reference_location='p.14',
             reference_text='Store passwords using strong adaptive and salted hashing functions.',
             reference_position=88, lexical=0.18, semantic=0.69, entail_forward=-1.0, entail_backward=-1.0, cited=False),
        dict(index=30, location='para 9', text=s_nielsen, words=words(s_nielsen), category=None,
             reference='', reference_location='', reference_text='', reference_position=-1, lexical=0.05, semantic=0.31,
             entail_forward=-1.0, entail_backward=-1.0, cited=True, secondary_review=None),
        # Secondary low-threshold retrieval (merged main): advisory only, never counted.
        dict(index=52, location='para 15', text=s_role, words=words(s_role), category=None,
             reference='owasp_top10_2021.pdf', reference_location='p.6',
             reference_text='Access control enforces policy such that users cannot act outside of their intended permissions; deny by default except for public resources.',
             reference_position=31, lexical=0.09, semantic=0.58, entail_forward=-1.0, entail_backward=-1.0, cited=False,
             secondary_review={'status': 'MUTUAL_ENTAILMENT_CANDIDATE', 'counted_in_overlap': False, 'entail_forward': 0.71, 'entail_backward': 0.66}),
        dict(index=60, location='para 18', text=s_admin, words=words(s_admin), category=None,
             reference='kumar_singh_2018.pdf', reference_location='p.8',
             reference_text='An administrator view summarises registrations per club using an aggregate query.',
             reference_position=121, lexical=0.14, semantic=0.55, entail_forward=-1.0, entail_backward=-1.0, cited=False,
             secondary_review={'status': 'NOT_MUTUALLY_ENTAILED', 'counted_in_overlap': False, 'entail_forward': 0.62, 'entail_backward': 0.21}),
        dict(index=44, location='para 13', text=s_three, words=words(s_three), category=None,
             reference='kumar_singh_2018.pdf', reference_location='p.5',
             reference_text='The system is organised in three layers: presentation, application logic and data.',
             reference_position=80, lexical=0.12, semantic=0.49, entail_forward=-1.0, entail_backward=-1.0, cited=False,
             secondary_review={'status': 'NOT_REVIEWED_BUDGET', 'counted_in_overlap': False}),
    ]
    for m in matches:
        m.setdefault('secondary_review', None)
    cat_words = {}
    for m in matches:
        if m['category']:
            cat_words[m['category']] = cat_words.get(m['category'], 0) + m['words']
    pct = lambda w: round(100.0 * w / total, 1)
    copied = [m for m in matches if m['category'] in ('verbatim', 'near-verbatim', 'paraphrase')]
    by_ref = {}
    for m in copied:
        by_ref[m['reference']] = by_ref.get(m['reference'], 0) + m['words']
    chunk_words = matches[3]['words'] + matches[4]['words']
    flagged = [
        {'location': 'para 22 to para 23', 'words': 118, 'score': 0.9634, 'threshold': 0.91,
         'detector': 'desklib/ai-text-detector-v1.01', 'score_name': 'AI probability', 'rule': '>=',
         'stock_phrases': [],
         'text': 'The prototype supports all seven functional requirements. In our manual tests, the duplicate registration and full-event checks worked as expected. A pilot with two clubs and 37 students registered for four events without any duplicate registrations. Coordinators reported that preparing the attendance list now takes a few minutes instead of an hour.'},
        {'location': 'para 26', 'words': 104, 'score': 0.9281, 'threshold': 0.91,
         'detector': 'desklib/ai-text-detector-v1.01', 'score_name': 'AI probability', 'rule': '>=',
         'stock_phrases': [],
         'text': 'CampusConnect shows that a small PHP and MySQL application can replace the spreadsheet workflow for club events. The database constraints and transaction-based capacity check address the main data-consistency problems that clubs reported. Future work includes fixing the date validation, adding automated tests, adding email notifications and measuring performance under load.'},
    ]
    scored = 1402
    flagged_words = 222
    ai_pct = round(100 * flagged_words / scored, 1)
    ai = {'status': 'COMPLETED', 'estimated_percent': ai_pct,
          'interpretation': f"Some AI-like writing: {ai_pct}% of the student's own words ({flagged_words} of {scored} scored, in 2 of 11 passages) read as machine-generated. Advisory, not proof.",
          'confidence': 'medium', 'flagged_passages': flagged,
          'limitations': [
              'Advisory only: an AI estimate is a reason to talk with the student, never proof or grounds for a penalty.',
              "Copied sentences are excluded; the estimate covers the student's own writing only.",
              'Formal, formulaic or non-native writing can read as machine-like, and AI text edited by hand can read as human.',
              'Passages under the minimum length are not scored.',
              'Detector: Desklib AI-text classifier (DeBERTa-v3-large). Threshold AI probability >= 0.91 is PROVISIONAL until checked on real student text (scripts/check_ai_thresholds.py).',
              '96 words in short passages were not scored.']}
    return {
        'document': report.name,
        'references': ['kumar_singh_2018.pdf', 'owasp_top10_2021.pdf', 'patel_joshi_2022.pdf'],
        'sentences_checked': 92, 'words_checked': total,
        'overall_percent': pct(sum(m['words'] for m in copied)),
        'percent_by_category': {'verbatim': pct(cat_words.get('verbatim', 0)), 'near-verbatim': pct(cat_words.get('near-verbatim', 0)),
                                'paraphrase': pct(cat_words.get('paraphrase', 0)), 'similar meaning (not confirmed)': pct(cat_words.get('similar meaning (not confirmed)', 0))},
        'percent_by_reference': {k: pct(v) for k, v in sorted(by_ref.items(), key=lambda kv: -kv[1])},
        'percent_in_chunks': pct(chunk_words),
        'chunks': [{'reference': 'kumar_singh_2018.pdf', 'first': 48, 'last': 49, 'sentences': 2, 'words': chunk_words,
                    'document_location': 'para 14 to para 14', 'reference_location': 'p.6 to p.6',
                    'document_text': matches[3]['text'] + ' ' + matches[4]['text'],
                    'reference_text': matches[3]['reference_text'] + ' ' + matches[4]['reference_text'], 'cited': False}],
        'matches': matches,
        'backends': {'embeddings': 'sentence-transformers', 'nli': 'cross-encoder/nli-deberta-v3-base', 'secondary_nli': 'cross-encoder/nli-deberta-v3-base'},
        'notes': ['Secondary semantic candidates are advisory only and excluded from headline overlap, categories and copied chunks. Calibration is required.', 'Percentages are shares of the document\'s words (quoted text and the reference list excluded). They measure overlap for review, not misconduct.'],
        'ai_findings': [], 'ai_notes': [ai['interpretation']], 'ai_detection': ai,
    }


def integrity_no_corpus(report: Path, ai):
    text = report.read_text(encoding='utf-8')
    return {'document': report.name, 'references': [], 'sentences_checked': 0, 'words_checked': 0,
            'overall_percent': 0.0, 'percent_by_category': {'verbatim': 0.0, 'near-verbatim': 0.0, 'paraphrase': 0.0, 'similar meaning (not confirmed)': 0.0},
            'percent_by_reference': {}, 'percent_in_chunks': 0.0, 'chunks': [], 'matches': [],
            'backends': {'embeddings': 'not run', 'nli': 'not run', 'secondary_nli': 'not run'},
            'notes': ['No readable reference documents in empty_reference_corpus.',
                      'Percentages are shares of the document\'s words (quoted text and the reference list excluded). They measure overlap for review, not misconduct.'],
            'ai_findings': [], 'ai_notes': [ai.get('interpretation') or (ai['limitations'] or [''])[0]], 'ai_detection': ai}


# --------------------------------------------------------------------------- #
# Native outputs: Research (ResearchExtraction.to_dict shape used by the core)
# --------------------------------------------------------------------------- #
CAMPUS_REFS = [
    # title, authors, year, verify, verified_year, relevance, conf, year_source, age, in_lr, linked, eng
    ('Enhancing the explanatory power of usability heuristics', 'J. Nielsen', 1994, 'verified', 1994, 'methodologically_relevant', 'medium', 'verified_metadata', 'foundational_or_older', True, True, False),
    ('Design of a web-based campus event management system using PHP and MySQL', 'A. Kumar and R. Singh', 2018, 'not_found', None, 'directly_relevant', 'medium', 'submitted_unverified', 'within_10_years', True, True, False),
    ('A comparative study of role-based access control models for small web applications', 'M. Rahman, S. Ahmed and T. Islam', 2021, 'verified', 2021, 'directly_relevant', 'high', 'verified_metadata', 'within_5_years', True, True, True),
    ('OWASP Top Ten 2021', 'OWASP Foundation', 2021, 'needs_review', None, 'methodologically_relevant', 'medium', 'submitted_unverified', 'within_5_years', True, True, True),
    ('Reducing duplicate submissions in online registration forms', 'K. Patel and D. Joshi', 2022, 'metadata_mismatch', 2017, 'partially_relevant', 'low', 'disputed', 'within_5_years', True, True, False),
    ('Campus engagement platforms and event attendance: a pilot study', 'H. Lee and J. Park', 2023, 'verified', 2023, 'directly_relevant', 'high', 'verified_metadata', 'within_5_years', True, True, False),
    ('Password Hashing', 'The PHP Group', None, 'not_checked', None, 'insufficient_evidence', 'low', 'unknown', 'unknown', True, True, False),
    ('A complete guide to CSS grid', 'R. Andrew', 2024, 'not_found', None, 'possibly_unrelated', 'low', 'submitted_unverified', 'within_5_years', True, True, False),
]


def research_native(report: Path, refs, *, lr_found=True, verify=True, as_of='2026-11-25'):
    n = len(refs)
    papers, overview, ver, rel, rec, attention = [], [], [], [], [], []
    for i, (title, authors, year, vstat, vyear, relc, conf, ysrc, age, in_lr, linked, eng) in enumerate(refs):
        papers.append({'reference_number': i + 1, 'title': title, 'authors': authors, 'year': year, 'raw': f'[{i+1}] {authors}, "{title}", {year or "n.d."}.'})
        overview.append({'reference_number': i + 1, 'title': title, 'verification_status': vstat, 'relevance_classification': relc,
                         'confidence': conf, 'publication_year': vyear if ysrc == 'verified_metadata' else year, 'year_source': ysrc,
                         'age_band': age, 'cited_in_literature_review': in_lr, 'citation_context_linked': linked,
                         'supporting_engineering_connection': eng})
        ver.append({'submitted_title': title, 'submitted_authors': authors, 'submitted_year': year, 'submitted_doi': None,
                    'status': vstat, 'provider': 'openalex' if vstat not in ('not_checked',) else None,
                    'verified_title': (title if vstat == 'verified' else ('Duplicate form submission behaviour in web surveys' if vstat == 'metadata_mismatch' else None)),
                    'verified_authors': ([a.strip() for a in authors.replace(' and ', ', ').split(',')] if vstat == 'verified' else []),
                    'verified_year': vyear, 'verified_doi': ('10.1145/191666.191729' if i == 0 else '10.1016/j.jisa.2021.102934' if i == 2 else '10.1016/j.caeo.2023.100132' if i == 5 else '10.1016/j.ijhcs.2017.04.002' if i == 4 else None),
                    'verified_url': None, 'title_similarity': (0.98 if vstat == 'verified' else 0.41 if vstat == 'metadata_mismatch' else None),
                    'title_match': vstat == 'verified', 'year_match': (vyear == year) if vyear else None, 'author_match': vstat == 'verified',
                    'notes': (['Submitted DOI resolves to a different title and year.'] if vstat == 'metadata_mismatch' else ['Web resource; academic identity lookup not applicable.'] if vstat == 'needs_review' else ['Verification skipped for documentation URL.'] if vstat == 'not_checked' else [])})
        rel.append({'reference_index': i, 'paper_title': title, 'verification_status': vstat, 'classification': relc, 'confidence': conf,
                    'topical_similarity': round(0.82 - i * 0.06, 2), 'methodological_similarity': round(0.7 - i * 0.04, 2),
                    'citation_context_similarity': round(0.76 - i * 0.05, 2), 'citation_lines': [27 + i] if linked else [],
                    'explanation': ('Verified source and citation-independent report methodology share role-based access control terminology.' if eng and i == 2 else
                                    'Verified source and citation-independent report methodology share prepared-statement and access-control guidance.' if eng else
                                    'Citation context and abstract share the campus event registration topic.' if relc == 'directly_relevant' else
                                    'Source contributes evaluation method rather than topic.' if relc == 'methodologically_relevant' else
                                    'Only part of the cited claim overlaps with the source topic.' if relc == 'partially_relevant' else
                                    'Source topic (CSS layout) does not overlap the cited claim about system design.' if relc == 'possibly_unrelated' else
                                    'No abstract or metadata available to assess relevance.'),
                    'method_evidence_strength': 'strong' if eng else 'moderate' if relc == 'directly_relevant' else 'none',
                    'evidence_reliability': 'verified_reference' if vstat == 'verified' else 'unverified_reference'})
        rec.append({'reference_index': i, 'publication_year': vyear if ysrc == 'verified_metadata' else year, 'submitted_year': year,
                    'year_source': ysrc, 'age_band': age, 'literature_review_cited': in_lr, 'year_conflict': ysrc == 'disputed'})
        reasons = []
        if vstat == 'metadata_mismatch':
            reasons.append('DOI/metadata points to a different paper; verify this citation')
        elif vstat == 'not_found':
            reasons.append('Academic identity could not be confirmed')
        elif vstat == 'needs_review':
            reasons.append('Academic metadata needs manual review')
        if relc == 'possibly_unrelated':
            reasons.append('May not substantively support the report topic or methods')
        elif relc == 'insufficient_evidence':
            reasons.append('Relevance remains uncertain; inspect independent source metadata and citation context')
        if ysrc == 'disputed':
            reasons.append(f'Submitted publication year {year} conflicts with resolved year {vyear}')
        if reasons:
            pr = 'high' if vstat in ('metadata_mismatch', 'not_found') else 'medium' if vstat == 'needs_review' or relc == 'possibly_unrelated' else 'low'
            attention.append({'reference_number': i + 1, 'title': title, 'verification_status': vstat, 'relevance': relc, 'priority': pr, 'reasons': reasons})
    attention.sort(key=lambda x: ({'high': 0, 'medium': 1, 'low': 2}[x['priority']], x['reference_number']))
    from collections import Counter
    vc = Counter(r[3] for r in refs)
    rc = Counter(r[5] for r in refs)
    reliable = [r for r in refs if r[7] == 'verified_metadata']
    recent = [r for r in reliable if r[4] and r[4] >= 2021]
    direct = [r for r in reliable if r[5] == 'directly_relevant']
    direct_recent = [r for r in direct if r[4] >= 2021]
    doc_cites = [{'text': f'[{i+1}]', 'line': 27 + i} for i in range(n)]
    lr_cites = list(doc_cites) if lr_found else []
    matches = [{'reference_number': i + 1, 'citation': f'[{i+1}]'} for i in range(n)]
    links = [{'url': 'https://owasp.org/Top10/', 'line': 64}, {'url': 'https://www.php.net/manual/en/book.password.php', 'line': 67}]
    p4 = {'recency_assessment': 'mixed_recency', 'distinct_references': n, 'duplicate_reference_count': 0, 'duplicate_groups': [],
          'possible_version_groups': [], 'reliable_publication_year_count': len(reliable), 'median_reliable_publication_year': 2021 if reliable else None,
          'recent_reliable_count': len(recent), 'recent_reliable_percent': round(100 * len(recent) / len(reliable), 1) if reliable else None,
          'verified_direct_count': len(direct), 'verified_direct_recent_count': len(direct_recent),
          'verified_direct_recent_percent': round(100 * len(direct_recent) / len(direct), 1) if direct else None,
          'literature_review_reliable_count': len(reliable), 'literature_review_recent_reliable_count': len(recent),
          'literature_review_recent_percent': round(100 * len(recent) / len(reliable), 1) if reliable else None,
          'age_distribution': dict(Counter(r[8] for r in refs))}
    report_obj = {
        'schema_version': '1.1', 'source': report.name, 'as_of_date': as_of,
        'headline': 'Research evidence requires review',
        'overview': (f'The bibliography contains {n} entries ({n} distinct works by conservative deduplication). {vc.get("verified",0)} entries have verified identities. '
                     f'The relevance model identifies {rc.get("directly_relevant",0)} directly relevant and {rc.get("methodologically_relevant",0)} methodologically relevant entries. '
                     f'{len(direct_recent)} of {len(direct)} distinct verified directly relevant papers are from the last five years. These are descriptive indicators, not a pass/fail finding.'),
        'phase_1': {'literature_review_found': lr_found, 'references_found': True, 'reference_entries': n,
                    'document_citation_mentions': len(doc_cites), 'literature_review_citation_mentions': len(lr_cites),
                    'reference_match_records': len(matches), 'matched_reference_entries': n, 'hyperlinks_found': len(links)},
        'phase_2': {'status_counts': dict(vc), 'checked_references': n},
        'phase_3': {'classification_counts': dict(rc), 'assessed_references': n,
                    'interpretation': 'Relevance classes are heuristic; they do not establish factual claim support.'},
        'phase_4': p4,
        'key_findings': [f'{len(direct_recent)} of {len(direct)} distinct, verified directly relevant papers were published within the last five years. This describes the supplied references, not the availability of newer research.',
                         f'The Literature Review cites {len(recent)} recent papers among {len(reliable)} distinct papers with confirmed years ({p4["literature_review_recent_percent"]}%).',
                         f'Confirmed publication-year coverage: {len(reliable)} of {n} distinct references.'],
        'attention_items': attention, 'reference_overview': overview,
        'methodological_notes': ['Recency denominators use distinct papers with confirmed years, not all bibliography entries.',
                                 'Publication years labeled disputed or unverified are visible but excluded from confirmed recency statistics.',
                                 'Older methodological foundations are not automatically obsolete.',
                                 'No search for newer or missing papers, full-text claim verification, or comprehensive literature review was performed.'],
        'recommended_checks': ['Verify unresolved or conflicting bibliographic records against the original publication before treating them as established sources.',
                               'Review potentially unrelated papers in their precise citation contexts and remove or justify unsupported references.'],
    }
    return {'source': report.name, 'research_report': report_obj, 'papers': papers, 'hyperlinks': links,
            'document_citations': doc_cites, 'literature_citations': lr_cites, 'citation_reference_matches': matches,
            'verification_results': ver, 'relevance_results': rel, 'recency_results': rec,
            'recency_summary': {'as_of_date': as_of, 'recency_assessment': 'mixed_recency'},
            'notes': (['Online metadata verification enabled (OpenAlex/Crossref).'] if verify else ['Online metadata verification not enabled; identities are unchecked.'])}


# --------------------------------------------------------------------------- #
# Native outputs: Executioner
# --------------------------------------------------------------------------- #
def tid(n):
    return 'T-' + format(0x3F2A00 + n * 7919, 'X').rjust(8, '0')


def campus_executioner(sha):
    repo = 'campusconnect-team/campusconnect'
    cases = []

    def case(n, cat, title, status, method, path, assertions, oracle=None):
        c = {'id': tid(n), 'category': cat, 'title': title, 'status': status,
             'target': {'method': method, 'path': path}, 'oracle': oracle, 'assertions': assertions}
        cases.append(c)
        return c
    c_ready = case(1, 'HTTP_READINESS', 'Application serves the landing page', 'READY_FOR_PHASE4', 'GET', '/', [{'kind': 'status_code', 'operator': 'equals', 'expected': 200}], 'ADAPTER_READINESS')
    c_events = case(2, 'HTTP_ROUTE', 'Public events list renders', 'READY_FOR_PHASE4', 'GET', '/events', [{'kind': 'status_code', 'operator': 'equals', 'expected': 200}, {'kind': 'body_contains', 'operator': 'contains', 'expected': 1}], 'REVIEWED_CATALOG')
    c_create_guard = case(3, 'HTTP_ROUTE', 'Unauthenticated event creation is redirected', 'READY_FOR_PHASE4', 'GET', '/events/create', [{'kind': 'status_code', 'operator': 'equals', 'expected': 302}], 'REVIEWED_CATALOG')
    c_edit = case(4, 'HTTP_ROUTE', 'Edit event rejects end time before start time', 'READY_FOR_PHASE4', 'POST', '/events/edit', [{'kind': 'status_code', 'operator': 'equals', 'expected': 422}], 'REVIEWED_CATALOG')
    c_search = case(5, 'HTTP_ROUTE', 'Search matches club names case-insensitively', 'READY_FOR_PHASE4', 'GET', '/events/search', [{'kind': 'status_code', 'operator': 'equals', 'expected': 200}, {'kind': 'result_count', 'operator': 'at_least', 'expected': 1}], 'REVIEWED_CATALOG')
    case(6, 'HTTP_ROUTE', 'Register for an event', 'NEEDS_ORACLE', 'POST', '/events/register', [])
    case(7, 'HTTP_ROUTE', 'Full event refuses registration', 'NEEDS_ORACLE', 'POST', '/events/register', [])
    case(8, 'HTTP_ROUTE', 'Administrator dashboard counts per club', 'NEEDS_ORACLE', 'GET', '/admin/dashboard', [])
    case(9, 'HTTP_ROUTE', 'Logout clears the session', 'READY_FOR_PHASE4', 'GET', '/logout', [{'kind': 'status_code', 'operator': 'equals', 'expected': 302}], 'REVIEWED_CATALOG')
    case(10, 'BROWSER_UI', 'Client-side search filters the event grid', 'READY_FOR_PHASE4', 'GET', '/events', [{'kind': 'dom_selector', 'operator': 'exists', 'expected': 1}], 'REVIEWED_CATALOG')
    case(11, 'EMAIL', 'Registration confirmation email', 'UNSUPPORTED_BY_RUNNER', 'POST', '/events/register', [])
    case(12, 'HTTP_ROUTE', 'Coordinator cancels an event', 'NEEDS_ORACLE', 'POST', '/events/cancel', [])
    suites = [
        {'kind': 'readiness', 'state': 'PASS', 'result_status': 'TESTS_PASSED', 'vm_verified': True, 'cleanup': CLEAN, 'reused_from_prior': True,
         'adapter': 'php_mysql_v1', 'run_label': 'READINESS', 'report_path': 'phase4/readiness/run_report.json', 'vm_path': 'phase4/readiness/vm.json',
         'observations': [{'test_id': c_ready['id'], 'result': 'PASS', 'status_code': 200, 'expected_status': 200, 'method': 'GET', 'path': '/'}]},
        {'kind': 'api', 'state': 'FAIL', 'result_status': 'TESTS_FAILED', 'vm_verified': True, 'cleanup': CLEAN, 'reused_from_prior': False,
         'adapter': 'php_mysql_v1', 'run_label': 'API', 'report_path': 'phase4/api/run_report.json', 'vm_path': 'phase4/api/vm.json',
         'observations': [
             {'test_id': c_events['id'], 'result': 'PASS', 'status_code': 200, 'expected_status': 200, 'method': 'GET', 'path': '/events',
              'assertions': [{'kind': 'status_code', 'result': 'PASS', 'expected': 200, 'actual': 200}, {'kind': 'body_contains', 'result': 'PASS', 'expected': 1, 'actual': 1}]},
             {'test_id': c_create_guard['id'], 'result': 'PASS', 'status_code': 302, 'expected_status': 302, 'method': 'GET', 'path': '/events/create',
              'assertions': [{'kind': 'status_code', 'result': 'PASS', 'expected': 302, 'actual': 302}]},
             {'test_id': c_edit['id'], 'result': 'FAIL', 'status_code': 302, 'expected_status': 422, 'method': 'POST', 'path': '/events/edit',
              'assertions': [{'kind': 'status_code', 'result': 'FAIL', 'expected': 422, 'actual': 302}]},
             {'test_id': c_search['id'], 'result': 'FAIL', 'status_code': 200, 'expected_status': 200, 'method': 'GET', 'path': '/events/search',
              'assertions': [{'kind': 'status_code', 'result': 'PASS', 'expected': 200, 'actual': 200}, {'kind': 'result_count', 'result': 'FAIL', 'expected': 1, 'actual': 0}]},
             {'test_id': tid(9), 'result': 'PASS', 'status_code': 302, 'expected_status': 302, 'method': 'GET', 'path': '/logout',
              'assertions': [{'kind': 'status_code', 'result': 'PASS', 'expected': 302, 'actual': 302}]},
         ]},
        {'kind': 'auth', 'state': 'PASS', 'result_status': 'TESTS_PASSED', 'vm_verified': True, 'cleanup': CLEAN, 'reused_from_prior': True,
         'adapter': 'php_mysql_v1', 'run_label': 'AUTH', 'report_path': 'phase4/auth/run_report.json', 'vm_path': 'phase4/auth/vm.json',
         'observations': [{'id': 'UNAUTHENTICATED', 'result': 'PASS', 'status_code': 302, 'expected_status': 302},
                          {'id': 'REGISTRATION', 'result': 'PASS', 'status_code': 302, 'expected_status': 302},
                          {'id': 'DUPLICATE_EMAIL_REJECTED', 'result': 'PASS', 'status_code': 200, 'expected_status': 200},
                          {'id': 'LOGIN', 'result': 'PASS', 'status_code': 302, 'expected_status': 302},
                          {'id': 'WRONG_PASSWORD_REJECTED', 'result': 'PASS', 'status_code': 200, 'expected_status': 200},
                          {'id': 'AUTHENTICATED', 'result': 'PASS', 'status_code': 200, 'expected_status': 200}]},
        {'kind': 'db_postconditions', 'state': 'PASS', 'result_status': 'POSTCONDITIONS_PASSED', 'vm_verified': True, 'cleanup': CLEAN, 'reused_from_prior': True,
         'adapter': 'php_mysql_v1', 'run_label': 'POSTCONDITIONS', 'report_path': 'phase4/db_postconditions/run_report.json', 'vm_path': 'phase4/db_postconditions/vm.json',
         'observed_metrics': {'before_matches': 0, 'after_matches': 1, 'expected_fields_match': True},
         'observations': [{'id': 'PC-REGISTRATION-ROW', 'result': 'PASS', 'before_matches': 0, 'after_matches': 1, 'expected_fields_match': True},
                          {'id': 'PC-DUPLICATE-BLOCKED', 'result': 'PASS', 'before_matches': 1, 'after_matches': 1, 'expected_fields_match': True}]},
        {'kind': 'crud', 'state': 'PASS', 'result_status': 'CRUD_PASSED', 'vm_verified': True, 'cleanup': CLEAN, 'reused_from_prior': False,
         'adapter': 'php_mysql_v1', 'run_label': 'CRUD', 'report_path': 'phase4/crud/run_report.json', 'vm_path': 'phase4/crud/vm.json',
         'observed_metrics': {'create_http': 302, 'create_success': True, 'created_db_count': 1, 'created_db_fields_match': True, 'update_http': 302,
                              'update_success': True, 'updated_db_count': 1, 'updated_db_fields_match': True, 'delete_http': 302, 'delete_success': True, 'deleted_db_count': 0},
         'observations': [{'id': 'CRUD_WORKFLOW', 'result': 'PASS', 'create_http': 302, 'create_success': True, 'created_db_count': 1, 'created_db_fields_match': True,
                           'read_http': 200, 'read_matches': 1, 'update_http': 302, 'update_success': True, 'updated_db_count': 1, 'updated_db_fields_match': True,
                           'delete_http': 302, 'delete_success': True, 'deleted_db_count': 0, 'read_deleted_http': 404}]},
        {'kind': 'browser_ui', 'state': 'BLOCKED', 'result_status': 'BROWSER_VM_NOT_VERIFIED', 'vm_verified': False, 'cleanup': CLEAN, 'reused_from_prior': False,
         'adapter': 'php_mysql_v1', 'run_label': 'BROWSER', 'report_path': 'phase4/browser_ui/run_report.json', 'vm_path': None,
         'reason': 'BROWSER_CDP_UNAVAILABLE', 'observations': []},
    ]
    plan = {'status': 'PLANNED_WITH_GAPS', 'summary': {'total': len(cases), 'ready_for_phase4': 7, 'needs_oracle': 4, 'unsupported_by_runner': 1},
            'issues': ['ORACLE_REQUIRED_FOR_STATE_CHANGING_ROUTES', 'EMAIL_TRANSPORT_UNSUPPORTED'], 'cases': cases,
            'trust': 'TEST_SPECIFICATIONS_NOT_EXECUTION',
            'review_candidates': [{'path': '/events/cancel', 'method': 'POST', 'reason': 'STATE_CHANGING_ROUTE_WITHOUT_ORACLE'}]}
    matrix = build_test_case_matrix(plan, suites, effective_mode='REVIEWED')
    endpoints = [
        ('/', 'GET'), ('/login', 'GET'), ('/login', 'POST'), ('/register', 'GET'), ('/register', 'POST'), ('/logout', 'GET'),
        ('/events', 'GET'), ('/events/search', 'GET'), ('/events/create', 'GET'), ('/events/create', 'POST'), ('/events/edit', 'POST'),
        ('/events/cancel', 'POST'), ('/events/register', 'POST'), ('/clubs', 'GET'), ('/clubs/view', 'GET'), ('/admin/dashboard', 'GET')]
    server_endpoints = [{'path': p, 'method': m, 'kind': 'router_entry', 'confidence': 'HIGH', 'client_methods': [m] if m == 'POST' else [],
                         'method_basis': 'STATIC_SERVER_OBSERVATION', 'client_references': 1 if m == 'POST' else 0, 'intended_method': m,
                         'evidence': [{'path': 'public/index.php', 'line': 18 + i * 2}]} for i, (p, m) in enumerate(endpoints)]
    features = [
        {'name': 'Account registration and login', 'origin': 'source', 'confidence': 'HIGH', 'evidence': [{'path': 'controllers/AuthController.php', 'line': 12}]},
        {'name': 'Club pages', 'origin': 'source', 'confidence': 'HIGH', 'evidence': [{'path': 'controllers/ClubController.php', 'line': 9}]},
        {'name': 'Event creation, editing and cancellation', 'origin': 'source', 'confidence': 'HIGH', 'evidence': [{'path': 'controllers/EventController.php', 'line': 21}]},
        {'name': 'Event registration with seat limit', 'origin': 'source', 'confidence': 'MEDIUM', 'evidence': [{'path': 'controllers/RegistrationController.php', 'line': 30}]},
        {'name': 'Client-side event search', 'origin': 'source', 'confidence': 'MEDIUM', 'evidence': [{'path': 'public/js/search.js', 'line': 4}]},
        {'name': 'Administrator dashboard', 'origin': 'readme', 'confidence': 'MEDIUM', 'evidence': [{'path': 'README.md', 'line': 22}]},
        {'name': 'Email notifications', 'origin': 'readme', 'confidence': 'LOW', 'evidence': [{'path': 'README.md', 'line': 41}]},
    ]
    findings_manifest = [
        {'code': 'DB_SCHEMA_FOUND', 'severity': 'INFO', 'message': 'Database schema file detected; disposable seeding possible.', 'evidence': [{'path': 'database/schema.sql', 'line': 1}]},
        {'code': 'NO_AUTOMATED_TESTS', 'severity': 'LOW', 'message': 'No automated test files were detected in the repository.', 'evidence': []},
        {'code': 'ENV_FILE_EXAMPLE_ONLY', 'severity': 'INFO', 'message': 'Configuration uses config.example.php; no committed secrets detected by name.', 'evidence': [{'path': 'config/config.example.php', 'line': 1}]},
    ]
    qfind = [
        {'code': 'SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN', 'severity': 'HIGH', 'path': 'controllers/AdminController.php', 'line': 14, 'confidence': 'MEDIUM', 'category': 'SECURITY',
         'message': 'No recognized local session or role guard before this handler; a shared guard elsewhere was not verified.', 'recommendation': 'Confirm that the dashboard action checks the administrator role on the server before running.'},
        {'code': 'SEC_PHP_EXCEPTION_DISCLOSURE', 'severity': 'MEDIUM', 'path': 'models/Database.php', 'line': 27, 'confidence': 'MEDIUM', 'category': 'SECURITY',
         'message': 'Exception message may be echoed to the client.', 'recommendation': 'Log database exceptions on the server and show a generic message to users.'},
        {'code': 'LONG_FUNCTION', 'severity': 'MEDIUM', 'path': 'controllers/EventController.php', 'line': 58, 'confidence': 'HIGH', 'category': 'STRUCTURE',
         'message': 'Function edit() spans 84 lines.', 'recommendation': 'Split validation, persistence and redirect handling into smaller functions.'},
        {'code': 'DEEP_NESTING', 'severity': 'MEDIUM', 'path': 'controllers/RegistrationController.php', 'line': 41, 'confidence': 'MEDIUM', 'category': 'STRUCTURE',
         'message': 'Nesting depth of 5 inside register().', 'recommendation': 'Use early returns for the full-event and already-registered cases.'},
        {'code': 'JS_LOOSE_EQUALITY', 'severity': 'LOW', 'path': 'public/js/search.js', 'line': 17, 'confidence': 'HIGH', 'category': 'LINT',
         'message': 'Loose equality (==) can coerce types unexpectedly.', 'recommendation': 'Use strict equality (===).'},
        {'code': 'JS_CONSOLE_LOG', 'severity': 'LOW', 'path': 'public/js/search.js', 'line': 33, 'confidence': 'HIGH', 'category': 'LINT',
         'message': 'console.log left in production script.', 'recommendation': 'Remove debugging output before release.'},
        {'code': 'PHP_ERROR_SUPPRESSION', 'severity': 'LOW', 'path': 'views/events/list.php', 'line': 9, 'confidence': 'HIGH', 'category': 'LINT',
         'message': 'Error suppression operator (@) hides failures.', 'recommendation': 'Handle the missing value explicitly instead of suppressing the warning.'},
        {'code': 'TRAILING_WHITESPACE', 'severity': 'LOW', 'path': 'views/layout.php', 'line': 3, 'confidence': 'HIGH', 'category': 'LINT',
         'message': 'Trailing whitespace in source file.', 'recommendation': 'Remove unnecessary trailing whitespace.'},
    ]
    diags = [
        diagnosis('TEST_ASSERTION_MISMATCH', evidence=['phase4/api/run_report.json#/observations/2'], confidence='HIGH', linked_tests=[c_edit['id']]),
        diagnosis('TEST_ASSERTION_MISMATCH', evidence=['phase4/api/run_report.json#/observations/3'], confidence='MEDIUM', linked_tests=[c_search['id']]),
        diagnosis('VM_EXECUTION_UNVERIFIED', evidence=['phase4/browser_ui/run_report.json'], confidence='HIGH', linked_tests=[tid(10)]),
        diagnosis('ORACLE_NOT_ESTABLISHED', evidence=['phase3/test_plan.json'], confidence='HIGH', linked_tests=[tid(6), tid(7), tid(8), tid(12)]),
        diagnosis('TEST_BLOCKED', evidence=['phase3/test_plan.json'], confidence='HIGH', linked_tests=[tid(11)]),
        diagnosis('PHASE5_QUALITY_ADVISORY', evidence=['phase5/code_quality.json#/findings/2'], confidence='MEDIUM'),
    ]
    # diagnosis() dedupes by code in _recommendations; keep distinct items visible in diagnoses.
    result = {
        'schema_version': '0.1', 'stage': 'executioner_evaluation', 'submission_id': 'CC-RUN-0412', 'mode': 'RESUME', 'effective_mode': 'REVIEWED',
        'status': 'NEEDS_REVIEW', 'source': {'kind': 'github', 'repository': repo, 'commit_sha': sha}, 'security_attestation': ATTEST,
        'phase7': 'NOT_RUN_PER_SUBMISSION',
        'workflow': [{'stage': 'source_pinning', 'status': 'PASS', 'reason': None}, {'stage': 'project_intelligence', 'status': 'PASS', 'reason': None},
                     {'stage': 'runtime_planning', 'status': 'PASS', 'reason': None}, {'stage': 'test_planning', 'status': 'PASS', 'reason': 'ORACLES_PARTIAL'},
                     {'stage': 'static_quality', 'status': 'PASS', 'reason': None}, {'stage': 'resume_validation', 'status': 'PASS', 'reason': 'PRIOR_RECEIPTS_MATCHED'},
                     {'stage': 'functional_testing', 'status': 'PARTIAL', 'reason': 'BROWSER_SUITE_NOT_VERIFIED'}, {'stage': 'failure_diagnosis', 'status': 'PASS', 'reason': None}],
        'project_intelligence': {
            'project_name': 'campusconnect', 'architecture': 'server_rendered_web_application', 'architecture_confidence': 'HIGH',
            'languages': {'PHP': 2210, 'JavaScript': 312, 'CSS': 486, 'SQL': 164, 'HTML': 95},
            'technologies': [{'name': 'PHP 8', 'category': 'language_runtime', 'confidence': 'HIGH'}, {'name': 'MySQL', 'category': 'database', 'confidence': 'HIGH'},
                             {'name': 'PDO', 'category': 'database_driver', 'confidence': 'HIGH'}, {'name': 'Vanilla JavaScript', 'category': 'frontend', 'confidence': 'MEDIUM'},
                             {'name': 'CSS Grid', 'category': 'frontend', 'confidence': 'MEDIUM'}, {'name': 'XAMPP', 'category': 'development_environment', 'confidence': 'LOW'}],
            'services': [{'name': 'web', 'kind': 'http_server', 'technology': 'PHP', 'root': 'public', 'confidence': 'HIGH'},
                         {'name': 'database', 'kind': 'relational_database', 'technology': 'MySQL', 'root': 'database', 'confidence': 'HIGH'}],
            'routes_discovered': 21, 'features_discovered': len(features), 'features': features,
            'existing_tests': 0, 'test_artifacts': [], 'setup_files': ['README.md', 'config/config.example.php'],
            'database_schema_files': ['database/schema.sql', 'database/seed.sql'], 'environment_variable_names': ['DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASS'],
            'inventory': {'indexed_files': 61, 'file_role_counts': {'source': 36, 'template': 14, 'asset': 6, 'config': 2, 'docs': 2, 'schema': 2}},
            'findings': findings_manifest, 'unknowns': ['Email transport configuration not found'],
            'route_records': 21, 'server_endpoint_count': len(server_endpoints), 'client_route_records': 5, 'server_endpoints': server_endpoints,
            'count_scope': 'UNIQUE_STATIC_SERVER_PATH_METHOD_RECORDS_NOT_VERIFIED_URLS',
            'overview': 'Static inspection suggests a server_rendered_web_application project using PHP 8, MySQL, PDO, Vanilla JavaScript, CSS Grid, XAMPP. Identified feature candidates: Account registration and login, Club pages, Event creation, editing and cancellation, Event registration with seat limit, Client-side event search, Administrator dashboard. These are source-derived observations, not verified user-visible behavior.'},
        'runtime_plan': {'adapter': 'php_mysql_v1', 'status': 'CANDIDATE_NEEDS_VM_VERIFICATION', 'capability_status': 'SUPPORTED_SHAPE', 'execution_authorized': 'NOT_BY_PLANNING'},
        'test_plan': plan, 'suites': suites, 'test_case_matrix': matrix,
        'code_quality': {'status': 'ANALYZED',
                         'summary': {'indexed_files': 61, 'source_candidates': 36, 'production_files': 27, 'test_files': 0, 'production_code_lines': 2640, 'test_code_lines': 0,
                                     'analyzed_files': 36, 'source_lines': 3410, 'blank_lines': 402, 'comment_lines': 368, 'code_lines': 2640, 'functions_found': 118, 'findings': len(qfind), 'duplicate_groups': 2},
                         'coverage': {'supported_files': 36, 'unsupported_files': 0, 'skipped_files': 0, 'limited_function_files': 22, 'source_ratio': 1.0,
                                      'function_analysis_scope': 'AST_AND_HEURISTIC', 'function_file_ratio': 0.94, 'function_body_ratio': 0.88, 'ast_complexity_share': 0.0,
                                      'duplicate_production_lines': 46, 'duplicate_production_ratio': 0.017},
                         'quality': {'status': 'PROVISIONAL_LIMITED', 'score': None, 'measured_dimensions_indicator': 78,
                                     'dimensions': {'lint': 84, 'complexity': None, 'duplication': 98, 'structure': 69}, 'basis': 'HEURISTIC_NOT_A_GRADE',
                                     'reason_codes': ['LIMITED_COMPLEXITY_ANALYSIS'], 'scoring_version': '0.3',
                                     'weights_used': {'lint': 0.428571, 'duplication': 0.285714, 'structure': 0.285714}, 'confidence': 'LOW',
                                     'overall_score_status': 'WITHHELD_LIMITED_COVERAGE'},
                         'security': {'status': 'INDICATORS_FOUND', 'findings': [f for f in qfind if f['code'].startswith('SEC_')]},
                         'findings': qfind,
                         'functions': [{'name': 'edit', 'path': 'controllers/EventController.php', 'line': 58, 'lines': 84, 'method': 'BRACE_HEURISTIC', 'cyclomatic': None, 'big_o': 'UNKNOWN'},
                                       {'name': 'register', 'path': 'controllers/RegistrationController.php', 'line': 22, 'lines': 51, 'method': 'BRACE_HEURISTIC', 'cyclomatic': None, 'big_o': 'UNKNOWN'}],
                         'duplications': [{'path_a': 'views/events/create.php', 'line_a': 12, 'path_b': 'views/events/edit.php', 'line_b': 14, 'lines': 23}],
                         'limitations': ['Complexity is measured only for Python AST functions; PHP complexity is not scored.', 'Static findings are indicators for review, not confirmed defects.'],
                         'score_is_grade': False},
        'diagnostics': {'status': 'DIAGNOSED', 'summary': {'runs': 6, 'vm_verified_runs': 5, 'failed_tests': 2, 'blocked_tests': 1},
                        'events': [], 'diagnoses': diags, 'dependency_groups': [], 'rerun_comparisons': [], 'root_cause_hypotheses': [
                            {'test_id': c_edit['id'], 'hypothesis': 'Server-side date validation absent on the edit handler; the form redirects instead of returning a validation error.', 'causal_status': 'UNPROVEN', 'confidence': 'MEDIUM', 'source_locations': [{'path': 'controllers/EventController.php', 'line': 71}]},
                            {'test_id': c_search['id'], 'hypothesis': 'Search query compares club names with a case-sensitive collation or exact match.', 'causal_status': 'UNPROVEN', 'confidence': 'LOW', 'source_locations': [{'path': 'models/Event.php', 'line': 88}]}],
                        'static_context': {}, 'security_context': {'indicators': 2},
                        'limitations': ['Hypotheses are not proven causes; failed assertions do not establish student fault.']},
        'recommendations': [], 'limitations': ['FUNCTIONAL_ORACLES_STILL_REQUIRED', 'TEST_TARGETS_UNSUPPORTED_BY_RUNNER', 'STATIC_QUALITY_COVERAGE_OR_CONFIDENCE_LIMITED'],
        'review_requests': [{'type': 'ORACLE_APPROVAL_REQUIRED', 'stage': 'test_planning', 'review_identity': f'{repo}@{sha}'},
                            {'type': 'BROWSER_RUNTIME_UNAVAILABLE', 'stage': 'functional_testing', 'review_identity': f'{repo}@{sha}'}],
        'artifacts': {}, 'error_codes': [], 'vm_attempt_budget': 3, 'vm_attempts_scheduled': 3,
    }
    result['recommendations'] = _recommendations(result)
    result['summary'] = {'suites': [{'kind': s['kind'], 'state': s['state'], 'reused': s['reused_from_prior']} for s in suites],
                         'test_case_counts': matrix['counts'], 'reviewed_check_counts': matrix['reviewed_check_counts'], 'source_pinned': True,
                         'vm_attempts_scheduled_this_call': 3, 'verified_current_suites': 2}
    return result


def meditrack_executioner(sha):
    repo = 'meditrack-group/meditrack'
    cases = [{'id': 'T-00A1F3C2', 'category': 'HTTP_READINESS', 'title': 'Flask application serves GET /', 'status': 'READY_FOR_PHASE4', 'target': {'method': 'GET', 'path': '/'}, 'oracle': 'ADAPTER_READINESS', 'assertions': [{'kind': 'status_code', 'operator': 'equals', 'expected': 200}]},
             {'id': 'T-00A1F3C9', 'category': 'HTTP_ROUTE', 'title': 'Inventory dashboard lists expiring batches', 'status': 'NEEDS_ORACLE', 'target': {'method': 'GET', 'path': '/inventory/dashboard'}, 'oracle': None, 'assertions': []},
             {'id': 'T-00A1F3D0', 'category': 'HTTP_ROUTE', 'title': 'Add medicine rejects negative quantity', 'status': 'NEEDS_ORACLE', 'target': {'method': 'POST', 'path': '/inventory/add'}, 'oracle': None, 'assertions': []},
             {'id': 'T-00A1F3D7', 'category': 'HTTP_ROUTE', 'title': 'Create schedule for unknown patient is rejected', 'status': 'NEEDS_ORACLE', 'target': {'method': 'POST', 'path': '/patients/schedule'}, 'oracle': None, 'assertions': []},
             {'id': 'T-00A1F3DE', 'category': 'HTTP_ROUTE', 'title': 'Daily reminders page renders', 'status': 'READY_FOR_PHASE4', 'target': {'method': 'GET', 'path': '/reminders/today'}, 'oracle': 'REVIEWED_CATALOG', 'assertions': [{'kind': 'status_code', 'operator': 'equals', 'expected': 200}]},
             {'id': 'T-00A1F3E5', 'category': 'SMS', 'title': 'SMS reminder delivery', 'status': 'UNSUPPORTED_BY_RUNNER', 'target': {'method': 'POST', 'path': '/reminders/send'}, 'oracle': None, 'assertions': []}]
    plan = {'status': 'PLANNED_WITH_GAPS', 'summary': {'total': 6, 'ready_for_phase4': 2, 'needs_oracle': 3, 'unsupported_by_runner': 1}, 'issues': ['ORACLE_REQUIRED_FOR_STATE_CHANGING_ROUTES'],
            'cases': cases, 'trust': 'TEST_SPECIFICATIONS_NOT_EXECUTION', 'review_candidates': []}
    matrix = build_test_case_matrix(plan, [], effective_mode='STATIC')
    eps = [('/', 'GET'), ('/login', 'POST'), ('/inventory/dashboard', 'GET'), ('/inventory/add', 'POST'), ('/inventory/batch/<int:id>', 'GET'),
           ('/patients', 'GET'), ('/patients/schedule', 'POST'), ('/reminders/today', 'GET'), ('/reminders/send', 'POST')]
    qfind = [{'code': 'SEC_DB_LITERAL_CONFIG', 'severity': 'HIGH', 'path': 'config.py', 'line': 6, 'confidence': 'MEDIUM', 'category': 'SECURITY',
              'message': 'A literal credential-like value is assigned in configuration (value not shown).', 'recommendation': 'Move the staff password to an environment variable and rotate it if it was ever real.'},
             {'code': 'PY_BARE_EXCEPT', 'severity': 'MEDIUM', 'path': 'app/inventory/routes.py', 'line': 47, 'confidence': 'HIGH', 'category': 'LINT',
              'message': 'Bare except catches every exception, including programming errors.', 'recommendation': 'Catch the specific database or validation exception.'},
             {'code': 'HIGH_CYCLOMATIC', 'severity': 'MEDIUM', 'path': 'app/reminders/service.py', 'line': 12, 'confidence': 'HIGH', 'category': 'STRUCTURE',
              'message': 'build_due_list() has cyclomatic complexity 14.', 'recommendation': 'Separate date filtering from formatting.'},
             {'code': 'PY_NONE_COMPARISON', 'severity': 'LOW', 'path': 'app/patients/routes.py', 'line': 33, 'confidence': 'HIGH', 'category': 'LINT',
              'message': 'Comparison to None uses == instead of is.', 'recommendation': 'Use "is None".'}]
    result = {
        'schema_version': '0.1', 'stage': 'executioner_evaluation', 'submission_id': 'MT-2026-021', 'mode': 'STATIC', 'effective_mode': 'STATIC',
        'status': 'COMPLETED_WITH_LIMITATIONS', 'source': {'kind': 'github', 'repository': repo, 'commit_sha': sha}, 'security_attestation': ATTEST,
        'phase7': 'NOT_RUN_PER_SUBMISSION',
        'workflow': [{'stage': 'source_pinning', 'status': 'PASS', 'reason': None}, {'stage': 'project_intelligence', 'status': 'PASS', 'reason': None},
                     {'stage': 'runtime_planning', 'status': 'PASS', 'reason': None}, {'stage': 'test_planning', 'status': 'PASS', 'reason': 'ORACLES_PARTIAL'},
                     {'stage': 'static_quality', 'status': 'PASS', 'reason': None}, {'stage': 'functional_testing', 'status': 'SKIPPED', 'reason': 'STATIC_MODE_NO_VM'},
                     {'stage': 'failure_diagnosis', 'status': 'PASS', 'reason': None}],
        'project_intelligence': {
            'project_name': 'meditrack', 'architecture': 'python_web_application', 'architecture_confidence': 'HIGH',
            'languages': {'Python': 1180, 'HTML': 640, 'CSS': 120},
            'technologies': [{'name': 'Flask', 'category': 'web_framework', 'confidence': 'HIGH'}, {'name': 'SQLAlchemy', 'category': 'orm', 'confidence': 'HIGH'},
                             {'name': 'SQLite', 'category': 'database', 'confidence': 'HIGH'}, {'name': 'WTForms', 'category': 'validation', 'confidence': 'HIGH'},
                             {'name': 'Bootstrap 5', 'category': 'frontend', 'confidence': 'MEDIUM'}, {'name': 'Twilio SDK', 'category': 'external_service', 'confidence': 'LOW'}],
            'services': [{'name': 'web', 'kind': 'http_server', 'technology': 'Flask', 'root': '.', 'confidence': 'HIGH'}],
            'routes_discovered': 11, 'features_discovered': 5,
            'features': [{'name': 'Medicine and batch inventory', 'origin': 'source', 'confidence': 'HIGH'}, {'name': 'Expiry dashboard', 'origin': 'source', 'confidence': 'HIGH'},
                         {'name': 'Patient dose schedules', 'origin': 'source', 'confidence': 'HIGH'}, {'name': 'Daily reminder list', 'origin': 'source', 'confidence': 'MEDIUM'},
                         {'name': 'SMS reminders', 'origin': 'readme', 'confidence': 'LOW'}],
            'existing_tests': 0, 'test_artifacts': [], 'setup_files': ['requirements.txt', 'run.py'], 'database_schema_files': [],
            'environment_variable_names': ['FLASK_SECRET_KEY'], 'inventory': {'indexed_files': 34, 'file_role_counts': {'source': 14, 'template': 12, 'asset': 4, 'config': 2, 'docs': 2}},
            'findings': [{'code': 'NO_AUTOMATED_TESTS', 'severity': 'LOW', 'message': 'No automated test files were detected in the repository.', 'evidence': []},
                         {'code': 'EXTERNAL_SERVICE_SDK', 'severity': 'INFO', 'message': 'An SMS provider SDK is referenced; external services are not executed.', 'evidence': [{'path': 'requirements.txt', 'line': 7}]}],
            'unknowns': [], 'route_records': 11, 'server_endpoint_count': len(eps), 'client_route_records': 2,
            'server_endpoints': [{'path': p, 'method': m, 'kind': 'decorator_route', 'confidence': 'HIGH', 'client_methods': [], 'method_basis': 'STATIC_SERVER_OBSERVATION',
                                  'client_references': 0, 'intended_method': m, 'evidence': [{'path': 'app/' + (p.split('/')[1] or 'main') + '/routes.py', 'line': 10 + i * 6}]} for i, (p, m) in enumerate(eps)],
            'count_scope': 'UNIQUE_STATIC_SERVER_PATH_METHOD_RECORDS_NOT_VERIFIED_URLS',
            'overview': 'Static inspection suggests a python_web_application project using Flask, SQLAlchemy, SQLite, WTForms, Bootstrap 5, Twilio SDK. Identified feature candidates: Medicine and batch inventory, Expiry dashboard, Patient dose schedules, Daily reminder list, SMS reminders. These are source-derived observations, not verified user-visible behavior.'},
        'runtime_plan': {'adapter': 'python_web_offline_v1', 'status': 'CANDIDATE_NEEDS_VM_VERIFICATION', 'capability_status': 'SUPPORTED_SHAPE', 'execution_authorized': 'NOT_BY_PLANNING'},
        'test_plan': plan, 'suites': [], 'test_case_matrix': matrix,
        'code_quality': {'status': 'ANALYZED',
                         'summary': {'indexed_files': 34, 'source_candidates': 14, 'production_files': 14, 'test_files': 0, 'production_code_lines': 1180, 'test_code_lines': 0, 'analyzed_files': 14,
                                     'source_lines': 1490, 'blank_lines': 190, 'comment_lines': 120, 'code_lines': 1180, 'functions_found': 52, 'findings': len(qfind), 'duplicate_groups': 0},
                         'coverage': {'supported_files': 14, 'unsupported_files': 0, 'skipped_files': 0, 'limited_function_files': 0, 'source_ratio': 1.0, 'function_analysis_scope': 'AST_AND_HEURISTIC',
                                      'function_file_ratio': 1.0, 'function_body_ratio': 0.91, 'ast_complexity_share': 1.0, 'duplicate_production_lines': 0, 'duplicate_production_ratio': 0.0},
                         'quality': {'status': 'PROVISIONAL', 'score': 81, 'dimensions': {'lint': 86, 'complexity': 74, 'duplication': 100, 'structure': 68}, 'basis': 'HEURISTIC_NOT_A_GRADE',
                                     'reason_codes': [], 'scoring_version': '0.3', 'weights_used': {'lint': 0.3, 'complexity': 0.3, 'duplication': 0.2, 'structure': 0.2}, 'confidence': 'MEDIUM'},
                         'security': {'status': 'INDICATORS_FOUND', 'findings': [qfind[0]]}, 'findings': qfind, 'functions': [], 'duplications': [],
                         'limitations': ['Static findings are indicators for review, not confirmed defects.'], 'score_is_grade': False},
        'diagnostics': {'status': 'PARTIAL', 'summary': {'runs': 0, 'vm_verified_runs': 0}, 'events': [],
                        'diagnoses': [diagnosis('NO_EXECUTION_EVIDENCE', evidence=['phase3/test_plan.json'], confidence='HIGH'),
                                      diagnosis('ORACLE_NOT_ESTABLISHED', evidence=['phase3/test_plan.json'], confidence='HIGH', linked_tests=['T-00A1F3C9', 'T-00A1F3D0', 'T-00A1F3D7']),
                                      diagnosis('PHASE5_SECRET_INDICATOR', evidence=['phase5/code_quality.json#/findings/0'], confidence='MEDIUM')],
                        'dependency_groups': [], 'rerun_comparisons': [], 'root_cause_hypotheses': [], 'static_context': {}, 'security_context': {'indicators': 1},
                        'limitations': ['Only planning or static data was supplied; runtime behavior was not evaluated.']},
        'recommendations': [], 'limitations': ['FUNCTIONAL_BEHAVIOR_NOT_EXECUTED', 'FUNCTIONAL_ORACLES_STILL_REQUIRED', 'TEST_TARGETS_UNSUPPORTED_BY_RUNNER', 'DIAGNOSIS_EVIDENCE_PARTIAL'],
        'review_requests': [], 'artifacts': {}, 'error_codes': [], 'vm_attempt_budget': 0, 'vm_attempts_scheduled': 0,
    }
    result['recommendations'] = _recommendations(result)
    result['summary'] = {'suites': [], 'test_case_counts': matrix['counts'], 'reviewed_check_counts': matrix['reviewed_check_counts'], 'source_pinned': True,
                         'vm_attempts_scheduled_this_call': 0, 'verified_current_suites': 0}
    return result


# --------------------------------------------------------------------------- #
# Deterministic stand-in for the local rubric model (real wire schema)
# --------------------------------------------------------------------------- #
FEEDBACK = {
    'problem_scope': ('L4', 'The report defines clubs, students and advisors as users and lists five numbered objectives with an explicit scope exclusion.',
                      'The response-time objective is stated but no measurement method or acceptance threshold for the pilot is given.',
                      'State how each objective will be measured, for example the load-test setup for the two-second target.'),
    'report_quality': ('L4', 'Sections follow a clear problem, design, implementation and testing order with concrete numbers from the pilot.',
                       'Results mix pilot observations with team claims, and the testing table referred to in the text is not included in the extracted report.',
                       'Include the executed test table and separate measured pilot results from qualitative coordinator feedback.'),
    'technical_design': ('L4', 'The report describes a three-layer design with five tables, foreign keys and a unique key on registrations that prevents duplicates.',
                         'The role check is described for every controller, but the static analysis flags one administrator handler without a recognized guard.',
                         'Show the shared authorization check and how it is applied to the administrator routes.'),
    'implementation_quality': ('L3', 'Source analysis covers 36 files and finds prepared statements through a shared Database class and a transaction-based capacity check.',
                               'Static findings include a long edit handler, deep nesting in registration and a possible exception disclosure in the database class.',
                               'Refactor the edit and register handlers with early returns and log database exceptions instead of echoing them.'),
    'functional_validation': ('L3', 'Verified reviewed runs pass authentication, database postconditions and the CRUD workflow, and the events list and guard routes pass.',
                              'Two exact route tests failed: the edit form accepted an end time before the start time and search missed capitalised club names.',
                              'Add server-side date validation on the edit handler and make the club search case-insensitive, then rerun the failed tests.'),
    'testing_strategy': ('L3', 'The test plan lists 14 cases with steps and expected results, including duplicate email, wrong password and full-event rejection checks.',
                         'Tests were executed manually by the team only and the planned load test for 50 concurrent users was not performed.',
                         'Automate the documented cases and run the load test so the response-time objective can be checked.'),
    'research': ('L3', 'The literature review links all eight references to the design, and the role-based access control study [3] is verified and directly relevant.',
                 'Two sources could not be verified and one has a metadata conflict, and the CSS grid article does not support the cited design claim.',
                 'Verify reference [5] and the unconfirmed sources, and replace the CSS article with a source that supports the design decision.'),
}

FEEDBACK_IRRIGATION = {
    'problem_scope': ('L4', 'The study quantifies current use at about 6,000 litres per day and states four objectives for measurement, sensors, design and savings.',
                      'Constraints such as gardener workload and maintenance are mentioned only briefly.', 'Add the maintenance and staffing constraints that a pilot must respect.'),
    'report_quality': ('L4', 'The report is concise and moves from measurement to design and estimated savings with specific figures.',
                       'The comparison of the three sensor types is summarised without the underlying table.', 'Include the sensor comparison table with cost, accuracy and expected life.'),
    'technical_design': ('L3', 'The design specifies one capacitive sensor and valve per bed, a threshold rule, a fifteen-minute maximum and a rain check.',
                         'Threshold values and calibration steps are not defined, and failure behaviour of the valve is not described.', 'Define calibration and a fail-safe state for each valve.'),
    'testing_strategy': ('L3', 'Four pilot scenarios define expected outcomes, including a negative rain check and handling of an invalid sensor reading.',
                         'None of the scenarios has been executed, and pass criteria such as the ten-minute response are not tied to a measurement method.', 'Run the scenarios on a two-bed pilot and record measured response times and water volumes.'),
}


def make_llm(table, defer=()):
    llm = Mock()

    def wire(system, prompt, schema, **kw):
        req = json.loads(prompt)
        out = {}
        for c in req['criteria']:
            cid = c['criterion_id']
            level, strength, limitation, improvement = table[cid]
            if cid in defer:
                level = 'D'
            ev = {}
            for k, v in c['evidence_slots'].items():
                ev[k] = v[0]
            extra = [a for a in c['candidate_evidence_ids'] if a not in ev.values()][:2]
            row = {'choice': level if level in c['levels'] or level == 'D' else sorted(c['levels'])[-2],
                   'evidence': ev, 'additional_evidence_ids': extra}
            observed = cid == 'functional_validation'
            if c['feedback_required']:
                row['feedback'] = {'strength': strength, 'limitation': limitation, 'improvement': improvement, 'observed_failure': observed}
            else:
                row['rationale'] = strength + ' ' + limitation
            out[c['criterion_alias']] = row
        return schema.model_validate(out)
    llm.chat_json.side_effect = wire
    llm.check.return_value = None
    llm.http = Mock()
    return llm


# --------------------------------------------------------------------------- #
# Scenario runner
# --------------------------------------------------------------------------- #
def run_scenario(name, *, report_name, title, sid, repo, sha, natives, rubric, llm, mode='LIVE',
                 verify_research=False, references=None, meta):
    tmp = Path(tempfile.mkdtemp(prefix='piq_'))
    report = tmp / report_name
    shutil.copyfile(INPUTS / report_name, report)
    calls = {}
    def native(agent):
        def fn(*a, **k):
            calls[agent] = calls.get(agent, 0) + 1
            raw = natives[agent](report)
            if isinstance(raw, Exception):
                raise raw
            return raw
        return fn
    integrity = Mock(side_effect=lambda r, c: NS(to_dict=lambda: native('integrity')()))
    research = Mock(side_effect=lambda r, verify=False: NS(to_dict=lambda: native('research')()))
    executioner = Mock(side_effect=lambda req, output_dir: native('executioner')())
    modules = {'projectiq_integrity.document_check': NS(check_document=integrity),
               'projectiq_research.agent': NS(analyze_research_document=research),
               'projectiq_executioner.orchestration': NS(evaluate=executioner),
               'projectiq_summarizer.config': NS(Settings=lambda **kw: NS(num_ctx=8192, seed=42, ollama_url='http://localhost:11434', model='qwen2.5:7b')),
               'projectiq_summarizer.llm': NS(OllamaClient=lambda s: llm)}
    out = tmp / 'run'
    with patch.dict(sys.modules, modules), patch('projectiq_core.resources.release_ollama_model', Mock(return_value='UNLOAD_VERIFIED')):
        if mode == 'IMPORTED':
            sub, data = make_submission(report, title, sid, repo, sha)
            saved = tmp / 'saved'
            raws = {a: natives[a](report) for a in natives}
            collect(submission=sub, report_bytes=data, out=saved, mode='IMPORTED',
                    sources={a: (lambda p, o, a=a: raws[a]) for a in raws}, progress=lambda _: None)
            code = evaluate(out=out, rubric=rubric, evaluation=saved / 'evaluation.json', acknowledge_imported=True)
        else:
            refs_dir = None
            if references:
                refs_dir = tmp / 'refs'
                refs_dir.mkdir()
            code = evaluate(out=out, rubric=rubric, report=report, title=title, submission_id=sid,
                            repository_url=repo, ref=sha, references=refs_dir, verify_research=verify_research)
    result = load_json(out / 'result.json')
    evidence = load_json(out / 'rubric' / 'evidence.json')
    decisions = load_json(out / 'rubric' / 'decisions.json')
    diag = out / 'rubric' / 'proposal_diagnostics.json'
    fixture = {'fixture_kind': 'GENERATED_BY_REAL_BACKEND_WITH_SYNTHETIC_NATIVE_OUTPUTS', 'scenario': name, 'exit_code': code,
               'meta': meta, 'result': result, 'evidence': evidence, 'decisions': decisions,
               'report_text': report.read_text(encoding='utf-8'),
               'proposal_diagnostics': load_json(diag) if diag.exists() else None}
    fixture['reweight_check'] = reweight_check(fixture)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{name}.json').write_text(json.dumps(fixture, indent=1, ensure_ascii=False), encoding='utf-8')
    a = result['assessment']
    print(f"{name}: exit={code} workflow={result['workflow_status']} status={a['status']} proposed={a['proposed_points']} "
          f"assessed={a['assessed_weight_percent']}% unresolved={a['unresolved_criterion_ids']}")
    for c in a['criteria']:
        print(f"   {c['id']:24} {c['status']:22} level={c['level_id']} pts={c['earned_points']}/{c['max_points']} :: {c['reason'][:90]}")
    if KEEP:
        # Keep the complete real output directory so the local bridge can serve it.
        dest = KEEP / f'demo-{name}'
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(out, dest)
        (dest / 'faculty_meta.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
    shutil.rmtree(tmp, ignore_errors=True)
    return fixture


def export_rubrics():
    # Copy the backend's own rubric files verbatim; the UI offers them as templates.
    dest = ROOT / 'frontend' / 'src' / 'mock' / 'rubrics'
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted((ROOT / 'projectiq_core' / 'phase2').glob('*.rubric.json')):
        Rubric.model_validate(load_json(f))
        shutil.copyfile(f, dest / f.name)


def reweight_check(fixture):
    """Expected values from the REAL engine for a weight/total change, used by vitest."""
    from projectiq_core.phase2.contracts import EvidenceBundle, DecisionSet
    from projectiq_core.phase2.scoring import assess, reweight
    from projectiq_core.phase2.evidence import digest
    bundle = EvidenceBundle.model_validate(fixture['evidence'])
    old = Rubric.model_validate(fixture['result']['rubric'])
    decisions = DecisionSet.model_validate(fixture['decisions'])
    data = old.model_dump(mode='json')
    data['total_points'] = 80
    if data['group_weights']:
        data['group_weights'] = {k: (v * 2 if k in ('design', 'implementation') else v) for k, v in data['group_weights'].items()}
    new = Rubric.model_validate(data)
    rebound = reweight(bundle, old, new, decisions)
    a = assess(bundle, new, rebound, include_details=False)
    return {'rubric': new.model_dump(mode='json'), 'assessment': a.model_dump(mode='json')}


def main():
    export_rubrics()
    base = Rubric.model_validate(load_json(DEFAULT_RUBRIC))
    sha_a = '4c1e9b7a2f3d58e0b6a9c2d4e7f1035a8b2c6d90'
    sha_b = '9a7d3e21c4b5f6a8091b2c3d4e5f60718293a4b5'

    # A. CampusConnect: imported reviewed evidence (RESUME reuse + fresh suites), research required.
    rubric_a = configure_rubric(base, research_required=True)
    run_scenario('campusconnect', report_name='campusconnect_report.txt', title='CampusConnect: Club and Event Management Portal',
                 sid='CC-2026-014', repo='https://github.com/campusconnect-team/campusconnect', sha=sha_a, mode='IMPORTED',
                 natives={'summarizer': summarizer_native, 'integrity': integrity_campus,
                          'research': lambda r: research_native(r, CAMPUS_REFS),
                          'executioner': lambda r: campus_executioner(sha_a)},
                 rubric=rubric_a, llm=make_llm(FEEDBACK),
                 meta={'students': ['Ananya Kulkarni', 'Rohan Deshpande', 'Sneha Patil'], 'course': 'Full Stack Development Lab (CE-3102)',
                       'cohort': 'TY B.Tech Computer Engineering, Div A', 'submitted_utc': '2026-11-18T10:42:00Z', 'evaluated_utc': '2026-11-24T15:08:31Z',
                       'executioner_mode_requested': 'RESUME', 'references_uploaded': 3, 'verify_research': True,
                       'elapsed_seconds': 412.6})

    # B. MediTrack: fresh live STATIC run; no corpus; AI model missing; research agent failure.
    rubric_b = configure_rubric(base, research_required=True)
    ai_missing = {'status': 'NOT_AVAILABLE', 'estimated_percent': None, 'interpretation': None, 'confidence': None, 'flagged_passages': [],
                  'limitations': ['AI-percentage model not found: models/ai-text-detector-v1.01. Run: python scripts/setup_offline.py']}
    run_scenario('meditrack', report_name='meditrack_report.txt', title='MediTrack: Medicine Reminder and Inventory Tracker',
                 sid='MT-2026-021', repo='https://github.com/meditrack-group/meditrack', sha=sha_b,
                 natives={'integrity': lambda r: integrity_no_corpus(r, ai_missing),
                          'research': lambda r: RuntimeError('OpenAlex lookup timed out'),
                          'executioner': lambda r: meditrack_executioner(sha_b)},
                 rubric=rubric_b, llm=make_llm({**FEEDBACK,
                     'problem_scope': ('L3', 'The report states the clinic problem and four objectives covering stock, expiry, schedules and reminders.', 'Intended users and constraints such as staff accounts and data privacy are only briefly covered.', 'Describe the clinic staff roles and the constraints a real deployment must respect.'),
                     'report_quality': ('L3', 'Sections are short and in a logical order from problem to design and testing.', 'Design and testing sections give few specific values or results.', 'Add the concrete test inputs and observed results for each manual check.'),
                     'technical_design': ('L3', 'The report describes three blueprints and four related tables, including how a batch belongs to a medicine.', 'Data flow for reminders and access control are not described in detail.', 'Add a diagram of the tables and explain how reminders are generated.'),
                     'implementation_quality': ('L3', 'Static analysis covers all 14 source files and finds SQLAlchemy models and WTForms validation.', 'A credential-like literal in config.py and a bare except in the inventory routes were flagged.', 'Move the staff password to an environment variable and catch specific exceptions.'),
                     'testing_strategy': ('L2', 'The report documents three manual checks with expected outcomes, including a negative-quantity rejection.', 'Only three scenarios are listed and none cover reminders or authentication.', 'Add test cases for reminders, login and expiry boundaries with expected results.')}),
                 meta={'students': ['Kabir Shah', 'Isha Joshi'], 'course': 'Full Stack Development Lab (CE-3102)', 'cohort': 'TY B.Tech Computer Engineering, Div B',
                       'submitted_utc': '2026-11-20T08:15:00Z', 'evaluated_utc': '2026-11-25T06:31:12Z', 'executioner_mode_requested': 'STATIC',
                       'references_uploaded': 0, 'verify_research': True, 'elapsed_seconds': 188.3})

    # C. Irrigation feasibility study: report-only, research excluded, custom template.
    data = base.model_dump(mode='json')
    for c in data['criteria']:
        if c['id'] in ('implementation_quality', 'functional_validation'):
            c['applicability'] = 'EXCLUDED'
        if c['id'] == 'technical_design':
            c['allowed_evidence'] = ['REPORT_EXCERPT']
    data['id'] = 'report-only-study'
    data['title'] = 'Report-only feasibility study'
    data['total_points'] = 50
    data['group_weights'] = {'problem': 20, 'report': 25, 'design': 25, 'implementation': 10, 'validation': 20, 'research': 10}
    rubric_c = configure_rubric(Rubric.model_validate(data), research_required=False)
    ai_low = {'status': 'COMPLETED', 'estimated_percent': 0.0,
              'interpretation': "Little or no AI-like writing: 0.0% of the student's own words (0 of 742 scored, in 0 of 6 passages) read as machine-generated. Advisory, not proof.",
              'confidence': 'medium', 'flagged_passages': [],
              'limitations': ['Advisory only: an AI estimate is a reason to talk with the student, never proof or grounds for a penalty.',
                              "Copied sentences are excluded; the estimate covers the student's own writing only.",
                              'Formal, formulaic or non-native writing can read as machine-like, and AI text edited by hand can read as human.',
                              'Detector: Desklib AI-text classifier (DeBERTa-v3-large). Threshold AI probability >= 0.91 is PROVISIONAL until checked on real student text (scripts/check_ai_thresholds.py).']}
    run_scenario('irrigation', report_name='irrigation_report.txt', title='Feasibility Study: Soil-Moisture Smart Irrigation',
                 sid='IR-2026-030', repo=None, sha=None,
                 natives={'integrity': lambda r: integrity_no_corpus(r, ai_low)},
                 rubric=rubric_c, llm=make_llm(FEEDBACK_IRRIGATION),
                 meta={'students': ['Meera Iyer'], 'course': 'Engineering Design Project (ME-3207)', 'cohort': 'TY B.Tech Mechanical Engineering',
                       'submitted_utc': '2026-11-21T12:00:00Z', 'evaluated_utc': '2026-11-25T07:02:44Z', 'executioner_mode_requested': None,
                       'references_uploaded': 0, 'verify_research': False, 'elapsed_seconds': 64.9})


KEEP = None

if __name__ == '__main__':
    if '--keep' in sys.argv:
        KEEP = Path(sys.argv[sys.argv.index('--keep') + 1]).absolute()
        KEEP.mkdir(parents=True, exist_ok=True)
    main()
