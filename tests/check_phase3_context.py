"""Inspect Phase 3 heading and methodology extraction without APIs or models.

Usage: python tools/check_phase3_context.py path/to/report.pdf
"""
import argparse
from pathlib import Path
import sys

# Allow `python tools/check_phase3_context.py ...` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectiq_research.agent import _analyse_parsed_document
from projectiq_research.document_parser import load_document
from projectiq_research.relevance_context import _parse_heading, prepare_relevance_context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('document', type=Path)
    args = parser.parse_args()
    doc = load_document(args.document)
    extraction = _analyse_parsed_document(doc, verify=False)
    evidence = prepare_relevance_context(doc, extraction)
    print(f'Document: {doc.source}')
    print(f'References: {len(extraction.papers)}')
    print(f'Report title anchor: {evidence.title}')
    print(f'Methodology passages: {len(evidence.methodology_passages)}')
    print(f'Citing-independent method passages: {len(evidence.independent_methodology_passages)}')
    print('Recognized heading candidates:')
    ref_line = extraction.references.start_line if extraction.references.found else len(doc.text.splitlines()) + 1
    for number, text in enumerate(doc.text.splitlines(), 1):
        if number >= ref_line:
            break
        candidate = _parse_heading(text)
        if candidate and candidate.role != 'other':
            print(f'  L{number}: [{candidate.role}] {candidate.title}')
    if evidence.methodology_passages:
        print('Methodology preview:')
        print(evidence.methodology_passages[0][:460])
    else:
        print('No methodology was found: inspect the document structure and PDF text extraction.')


if __name__ == '__main__':
    main()
