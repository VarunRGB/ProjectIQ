# ProjectIQ Research Agent - Phase 1 fixture corpus

This folder contains 20 synthetic reports for testing Phase 1 document parsing, section detection, citation extraction, reference extraction and hyperlink collection.

## Contents

- TXT: numbered headings, chapter headings, uppercase headings, missing/empty sections, wrapped references, mixed citation styles and malformed bibliography entries.
- Markdown: heading aliases, APA-like references, numeric citations, author-year citations, Markdown links, plain URLs and `www.` links.
- DOCX: real Word heading styles and embedded clickable hyperlink relationships.
- PDF: selectable text across two pages with link annotations, plus an image-only scanned-style PDF that intentionally has no extractable text layer.

Most papers, authors and venues are synthetic. `example.org` links and the placeholder DOI identifiers are not intended to resolve. The Dijkstra citation in `04_markdown_heading_aliases.md` is a real historical foundational reference, included to represent an older source.

## Copy into ProjectIQ

Copy this entire `research` folder to:

```text
ProjectIQ/test_cases/research/
```

Copy `tests/test_research_fixture_corpus.py` from this bundle into your existing `ProjectIQ/tests/` directory.

## Run the corpus tests

From the ProjectIQ root with your virtual environment activated:

```powershell
python -m pytest tests/test_research_fixture_corpus.py -v
```

To run both the original Phase 1 tests and these corpus tests:

```powershell
python -m pytest tests/test_research_phase1.py tests/test_research_fixture_corpus.py -v
```

## Important test-design note

These reports are synthetic, controlled fixtures. They test the parser against known document shapes and expected outcomes; they do not establish general extraction accuracy for all student reports. The image-only PDF is expected to return no sections because Phase 1 does not include OCR. PDF annotations are captured at page level by the current parser, so section-specific PDF link assignment is less precise than TXT/Markdown/DOCX assignment.

See `manifest.json` for the expected section presence, paper counts and scenario coverage for each file.
