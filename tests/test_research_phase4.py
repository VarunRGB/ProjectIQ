"""Phase 4A tests are fully offline and do not require neural downloads."""
from dataclasses import asdict
from datetime import date
from pathlib import Path
import json
import pytest
from projectiq_research.models import ResearchExtraction, Section, PaperReference, CitationMention
from projectiq_research.verification_models import PaperVerification
from projectiq_research.relevance_models import PaperRelevance
from projectiq_research.recency_analyzer import analyze_recency
from projectiq_research.research_report import build_research_report, render_research_report_markdown


def make_result(entries, *, literature=True):
    """(title, submitted, official, status, label, doi) tuples."""
    papers, verifications, relevance = [], [], []
    for i, (title, submitted, official, status, label, doi) in enumerate(entries):
        papers.append(PaperReference(f'[{i+1}] A, "{title}", {submitted}.', title, 'A', submitted, doi, [], 50+i, 50+i))
        verifications.append(PaperVerification(title, 'A', submitted, doi, status,
                verified_title=title if status not in {'not_found','lookup_error'} else None,
                verified_year=official if status not in {'not_found','lookup_error'} else None,
                verified_doi=doi))
        relevance.append(PaperRelevance(i,title,status,label,'medium'))
    sec=Section('literature_review', literature, 'Literature Review' if literature else None,
                'We cite references.' if literature else '', 2 if literature else None, 8 if literature else None)
    matches=[{'line_number':4,'status':'matched','matched_paper_indices':[i], 'kind':'numeric',
              'citation_text':f'[{i+1}]', 'reference_numbers':[i+1]} for i in range(len(entries))]
    return ResearchExtraction(source='sample.pdf',literature_review=sec,
        references=Section('references',True,'References'),literature_citations=[],
        papers=papers,literature_links=[],reference_links=[],hyperlinks=[],
        verification_results=verifications,relevance_results=relevance,
        citation_reference_matches=matches,document_citations=[CitationMention(f'[{i+1}]','numeric',4) for i in range(len(entries))])


def e(title, submitted, official, status='verified', label='directly_relevant', doi=None):
    return (title, submitted, official, status, label, doi)


def test_verified_year_overrides_wrong_submitted_year_without_forgiving_discrepancy():
    x=make_result([e('Scene OCR',2025,2016)])
    rows,summary=analyze_recency(x,as_of_date='2026-11-18')
    assert rows[0].publication_year==2016 and rows[0].age_years==10
    assert rows[0].year_conflict and rows[0].year_source=='verified_metadata'
    assert summary.recent_reliable_percent==0


def test_uncertain_year_not_counted_and_no_false_recency():
    x=make_result([e('A',2025,2025),e('B',2026,2024,'needs_review'),e('C',2026,None,'not_found')])
    rows,s=analyze_recency(x,as_of_date='2026-11-18')
    assert s.reliable_year_count==1 and s.recent_reliable_percent==100
    assert s.uncertain_year_count==2
    assert rows[1].year_source=='disputed' and rows[2].year_source=='submitted_unverified'


def test_future_dates_excluded_and_flagged():
    x=make_result([e('A',2031,2031),e('B',2021,2021)])
    rows,s=analyze_recency(x,as_of_date='2026-11-18')
    assert rows[0].age_band=='future_dated' and s.future_dated_count==1
    assert s.reliable_year_count==1 and s.recent_reliable_percent==100


def test_dedup_doi_and_stable_research_denominator():
    x=make_result([e('A',2024,2024,doi='10.5/abc'),e('A',2024,2024,doi='10.5/abc'), e('B',2014,2014,doi='10.5/def')])
    rows,s=analyze_recency(x,as_of_date='2026-11-18')
    assert s.distinct_references==2 and s.duplicate_reference_count==1
    assert s.recent_reliable_count==1 and s.recent_reliable_percent==50


def test_irrelevant_recent_does_not_inflate_direct_coverage():
    x=make_result([e('Direct',2018,2018),e('Unrelated',2026,2026,'verified','possibly_unrelated')])
    _,s=analyze_recency(x,as_of_date='2026-11-18')
    assert s.recent_reliable_percent==50
    assert s.verified_direct_recent_percent==0
    assert s.recency_assessment=='older_direct_literature'


def test_old_foundations_not_labeled_obsolete():
    x=make_result([e('Architecture',2010,2010,'verified','methodologically_relevant')])
    rows,s=analyze_recency(x,as_of_date='2026-11-18')
    assert rows[0].role_assessment=='foundational_method_candidate'
    assert 'obsolescence' in ' '.join(rows[0].notes)
    assert s.recency_assessment=='limited_topic_evidence'


def test_missing_literature_review_is_not_fabricated():
    x=make_result([e('A',2024,2024)],literature=False)
    _,s=analyze_recency(x,as_of_date='2026-11-18')
    assert s.literature_review_reference_count==0
    assert s.literature_review_recent_percent is None
    assert any('No Literature Review' in note for note in s.findings)


def test_citations_and_invalid_links():
    x=make_result([e('A',2024,2024),e('B',2019,2019)])
    x.citation_reference_matches=[{'status':'matched','line_number':4,'matched_paper_indices':[0]},
                                  {'status':'unresolved','line_number':4,'matched_paper_indices':[]},
                                  {'status':'ambiguous','line_number':4,'matched_paper_indices':[]}]
    _,s=analyze_recency(x,as_of_date='2026-11-18')
    assert s.document_cited_reference_count==1 and s.uncited_reference_count==1
    assert s.unresolved_citation_mention_count==1 and s.ambiguous_citation_mention_count==1


def test_no_references_and_no_verified_metadata():
    x=make_result([])
    rows,s=analyze_recency(x,as_of_date='2026-11-18')
    assert rows==[] and s.recency_assessment=='insufficient_evidence'
    assert s.recent_reliable_percent is None and s.median_reliable_year is None
    y=make_result([e('Fictitious paper',2026,None,'not_found')])
    _,s=analyze_recency(y,as_of_date='2026-11-18')
    assert s.reliable_year_count==0 and s.recent_reliable_percent is None


def test_invalid_date_rejected():
    with pytest.raises(ValueError):
        analyze_recency(make_result([]),as_of_date='2026-02-30')


def test_final_output_is_serializable_and_markdown_is_clean():
    x=make_result([e('A',2024,2024),e('Suspicious',2026,None,'not_found','possibly_unrelated')])
    x.recency_results,x.recency_summary=analyze_recency(x,as_of_date=date(2026,11,18))
    x.research_report=build_research_report(x,x.recency_summary)
    data=x.to_dict()
    assert data['research_report']['phase_1']['reference_entries']==2
    assert data['research_report']['phase_4']['reliable_publication_year_count']==1
    assert data['research_report']['attention_items'][0]['reference_number']==2
    text=render_research_report_markdown(x.research_report)
    assert 'Phase 1' in text and 'Phase 2' in text and 'Phase 3' in text and 'Phase 4' in text
    assert 'newer-paper search' in text.lower() or 'No newer-paper search' in text
    assert json.loads(json.dumps(data))['recency_summary']['as_of_date']=='2026-11-18'


def test_named_identity_conflict_is_not_deduplicated_by_false_doi():
    x=make_result([e('Real',2024,2024,doi='10.5/abc'),e('Fake',2024,2024,'metadata_mismatch',doi='10.5/abc')])
    _,s=analyze_recency(x,as_of_date='2026-11-18')
    assert s.distinct_references==2 and s.reliable_year_count==1
