"""Regression checks for final report presentation and conservative entity resolution."""
import json
from pathlib import Path

from projectiq_research.models import PaperReference, ResearchExtraction, Section
from projectiq_research.relevance_models import PaperRelevance
from projectiq_research.verification_models import PaperVerification
from projectiq_research.recency_analyzer import analyze_recency
from projectiq_research.research_report import build_research_report, render_research_report_markdown


def fixture(rows):
    """Entries: (title, submitted_year, verified_year, status, verified_doi, submitted_doi, title_match, label)."""
    papers, verifications, relevant = [], [], []
    for i, (title, submitted, official, status, resolved_doi, claimed_doi, title_match, label) in enumerate(rows):
        papers.append(PaperReference(f'[{i+1}] {title}', title, 'Someone', submitted, claimed_doi, [], 30+i, 30+i))
        verifications.append(PaperVerification(
            submitted_title=title, submitted_authors='Someone', submitted_year=submitted, submitted_doi=claimed_doi,
            status=status, verified_title=title if status != 'not_found' else None,
            verified_year=official, verified_doi=resolved_doi, title_match=title_match,
        ))
        relevant.append(PaperRelevance(reference_index=i, paper_title=title, verification_status=status,
                                       classification=label, confidence='medium'))
    return ResearchExtraction(source=r'C:\temp\test_document.pdf',
        literature_review=Section('literature_review',False),references=Section('references',True),
        literature_citations=[],papers=papers,literature_links=[],reference_links=[],hyperlinks=[],
        relevance_results=relevant,verification_results=verifications)


def row(title, submitted, official, status='verified', resolved_doi=None, claimed_doi=None, title_match=True, label='directly_relevant'):
    return title, submitted, official, status, resolved_doi, claimed_doi, title_match, label


def finish(x):
    x.recency_results,x.recency_summary=analyze_recency(x,as_of_date='2026-11-18')
    x.research_report=build_research_report(x,x.recency_summary)
    return x


def test_wrong_year_duplicate_collapses_with_verified_doi():
    x=finish(fixture([
        row('Scene recognition',2016,2016,resolved_doi='10.1000/abc',claimed_doi='10.1000/abc'),
        row('Scene recognition',2024,2016,status='needs_review',resolved_doi='https://doi.org/10.1000/ABC',claimed_doi='10.1000/abc'),
    ]))
    assert x.recency_summary.distinct_references==1
    assert x.recency_summary.duplicate_reference_count==1
    assert x.recency_summary.duplicate_groups[0]['references']==[1,2]
    assert x.recency_summary.reliable_year_count==1
    assert x.recency_summary.verified_direct_count==1
    assert x.recency_results[1].year_conflict
    assert x.recency_results[1].year_source=='disputed'


def test_invented_title_doi_mismatch_is_not_collapsed():
    x=finish(fixture([
        row('Coral conservation',2024,2024,resolved_doi='10.1000/abc'),
        row('Fake OCR paper',2024,2024,status='metadata_mismatch',resolved_doi='10.1000/abc',claimed_doi='10.1000/abc',title_match=False),
    ]))
    assert x.recency_summary.distinct_references==2
    assert x.recency_summary.duplicate_reference_count==0
    assert x.recency_summary.reliable_year_count==1


def test_needs_review_without_title_confirmation_not_merged():
    x=finish(fixture([
        row('Paper A',2024,2024,resolved_doi='10.1000/a'),
        row('Paper B',2024,2024,status='needs_review',resolved_doi='10.1000/a',title_match=False),
    ]))
    assert x.recency_summary.distinct_references==2


def test_same_title_different_versions_flagged_not_conflated():
    x=finish(fixture([
        row('ImageNet CNN',2012,2012),
        row('Imagenet cnn',2017,2017,resolved_doi='10.1145/abc'),
    ]))
    assert x.recency_summary.distinct_references==2
    assert x.recency_summary.possible_version_groups==[{
        'references':[1,2], 'note':'Same resolved title but distinct publication records; inspect editions/versions.'
    }]
    assert x.recency_summary.duplicate_reference_count==0


def test_missing_doi_matches_confirmed_title_year_of_doi_paper():
    x=finish(fixture([
        row('Same verified article',2020,2020),
        row('Same Verified Article',2020,2020,resolved_doi='10.1000/a'),
    ]))
    assert x.recency_summary.distinct_references==1


def test_verified_same_title_different_dois_stay_separate_even_same_year():
    x=finish(fixture([
        row('A title',2020,2020,resolved_doi='10.1000/a'),
        row('A title',2020,2020,resolved_doi='10.1000/b'),
    ]))
    assert x.recency_summary.distinct_references==2
    assert x.recency_summary.duplicate_reference_count==0


def test_report_shows_precise_numerators_denominators_and_duplicates():
    x=finish(fixture([
        row('OCR review',2017,2017,resolved_doi='10.1000/a'),
        row('OCR review',2024,2017,status='needs_review',resolved_doi='10.1000/a'),
        row('Unrelated <paper> | *markup*',2024,2024,label='possibly_unrelated'),
    ]))
    report=render_research_report_markdown(x.research_report)
    assert report.startswith('# ProjectIQ — Research Quality Report\n')
    assert '\n## Executive summary' in report
    assert '\n### Phase 1' in report
    assert '\n### Phase 2' in report
    assert '\n### Phase 3' in report
    assert '\n### Phase 4A' in report
    assert '0/1 (0.0%)' in report
    assert '2 distinct works' in report
    assert '[1], [2]' in report
    assert 'Unrelated &lt;paper&gt; \\| \\*markup\\*' in report
    assert '| Ref. | Paper (short title) | Identity |' in report
    assert '\\|---:' not in report
    assert x.research_report.schema_version=='1.1'
    assert x.research_report.phase_4['duplicate_groups'][0]['references']==[1,2]
    json.dumps(x.to_dict())


def test_empty_denominators_are_na_not_zero_percent():
    x=finish(fixture([]))
    report=render_research_report_markdown(x.research_report)
    assert '0/0 (N/A (no verified sample))' in report
    assert '0.0%' not in report


def test_unverified_publication_age_is_visibly_qualified():
    x=finish(fixture([row('Unknown',2026,None,status='not_found',title_match=None)]))
    report=render_research_report_markdown(x.research_report)
    assert '2026 (unconfirmed)' in report
    assert 'Very recent (unconfirmed)' in report
    assert 'No newer-paper search was conducted' in report


def test_all_high_priority_warnings_display_by_default():
    entries=[row('Unresolved paper '+str(i),2020,None,status='not_found',title_match=None) for i in range(18)]
    x=finish(fixture(entries))
    report=render_research_report_markdown(x.research_report)
    assert '**[18]**' in report
    assert report.count('Academic identity could not be confirmed')==18
    truncated=render_research_report_markdown(x.research_report,max_attention=5)
    assert '**13 additional flagged references**' in truncated


def test_optional_no_verification_does_not_claim_verified_identity():
    x=fixture([row('Paper A',2024,None,status='not_checked',title_match=None)])
    x.verification_results=[]
    x=finish(x)
    assert x.recency_summary.reliable_year_count==0
    assert '0 entries have verified identities' in x.research_report.overview
    assert 'N/A' in render_research_report_markdown(x.research_report)


def test_json_rebuild_keeps_prior_phases_unchanged():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    from rebuild_research_report import rebuild
    x=finish(fixture([
        row('A paper',2018,2018,resolved_doi='10.1000/a'),
        row('A paper',2025,2018,status='needs_review',resolved_doi='10.1000/a'),
    ]))
    old=x.to_dict()
    output, markdown, summary=rebuild(old)
    for name in ['papers','verification_results','relevance_results','citation_reference_matches','literature_review']:
        assert output[name] == old[name]
    assert summary.distinct_references==1
    assert '[1], [2]' in markdown
    assert output['research_report']['schema_version']=='1.1'
    assert output['recency_summary']['as_of_date']=='2026-11-18'
