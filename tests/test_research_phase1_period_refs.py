"""Cross-domain unquoted author-period-title bibliography regressions.

These are author/title format tests, not hardcoded paper lookup results.
"""
import pytest

from projectiq_research.reference_extractor import extract_references
from projectiq_research.models import ParsedDocument
from projectiq_research.section_detector import detect_sections


@pytest.mark.parametrize("raw,authors,title,year", [
    (
        "[1] Shih-Wei Li, John S Koh, and Jason Nieh. Protecting cloud virtual\n"
        "machines from hypervisor and host operating system exploits. In 28th\n"
        "USENIX Security Symposium, pages 1357–1374, 2019.",
        "Shih-Wei Li, John S Koh, and Jason Nieh",
        "Protecting cloud virtual machines from hypervisor and host operating system exploits", 2019,
    ),
    (
        "[4] Yisroel Mirsky, Tomer Doitshman, Yuval Elovici, and Asaf Shabtai.\n"
        "Kitsune: an ensemble of autoencoders for online network intrusion\n"
        "detection. arXiv preprint arXiv:1802.09089, 2018.",
        "Yisroel Mirsky, Tomer Doitshman, Yuval Elovici, and Asaf Shabtai",
        "Kitsune: an ensemble of autoencoders for online network intrusion detection", 2018,
    ),
    (
        "[5] Steve TK Jan, Qingying Hao, Tianrui Hu, Jiameng Pu, Sonal Oswal,\n"
        "Gang Wang, and Bimal Viswanath. Throwing darts in the dark? detecting\n"
        "bots with limited data using neural data augmentation. In 2020 IEEE\n"
        "symposium on security and privacy, 2020.",
        "Steve TK Jan, Qingying Hao, Tianrui Hu, Jiameng Pu, Sonal Oswal, Gang Wang, and Bimal Viswanath",
        "Throwing darts in the dark? detecting bots with limited data using neural data augmentation", 2020,
    ),
    (
        "[28] Y ang Zhou, Zezhou Wang, Sowmya Dharanipragada, and Minlan Y u.\n"
        "Electrode: Accelerating distributed protocols with ebpf. In 20th USENIX\n"
        "Symposium, 2023.",
        "Y ang Zhou, Zezhou Wang, Sowmya Dharanipragada, and Minlan Y u",
        "Electrode: Accelerating distributed protocols with ebpf", 2023,
    ),
    (
        "[32] John H Ring IV, Colin M V an Oort, Samson Durst, Vanessa White,\n"
        "Joseph P Near, and Christian Skalka. Methods for host-based intrusion\n"
        "detection with deep learning. Digital Threats: Research and Practice, 2021.",
        "John H Ring IV, Colin M V an Oort, Samson Durst, Vanessa White, Joseph P Near, and Christian Skalka",
        "Methods for host-based intrusion detection with deep learning", 2021,
    ),
    (
        "[41] Brian W Matthews. Comparison of the predicted and observed\n"
        "secondary structure of t4 phage lysozyme. Biochimica et Biophysica\n"
        "Acta, 405(2):442–451, 1975.",
        "Brian W Matthews",
        "Comparison of the predicted and observed secondary structure of t4 phage lysozyme", 1975,
    ),
    (
        "[43] Thomas H Cormen, Charles E Leiserson, Ronald L Rivest, and Clifford\n"
        "Stein. Introduction to algorithms. MIT press, 2022.",
        "Thomas H Cormen, Charles E Leiserson, Ronald L Rivest, and Clifford Stein",
        "Introduction to algorithms", 2022,
    ),
    (
        "[44] Hao Wu, Patrick Judd, Xiaojie Zhang, Mikhail Isaev, and Paulius\n"
        "Micikevicius. Integer quantization for deep learning inference: Principles\n"
        "and empirical evaluation, 2020.",
        "Hao Wu, Patrick Judd, Xiaojie Zhang, Mikhail Isaev, and Paulius Micikevicius",
        "Integer quantization for deep learning inference: Principles and empirical evaluation", 2020,
    ),
    (
        "[3] A. L. Spitz. Determination of the script and language content of\n"
        "document images. IEEE Transactions on PAMI, 1997.",
        "A. L. Spitz",
        "Determination of the script and language content of document images", 1997,
    ),
])
def test_unquoted_author_period_title_venue(raw, authors, title, year):
    paper, = extract_references(raw)
    assert (paper.authors, paper.title, paper.year) == (authors, title, year)
    assert paper.raw == raw
    assert paper.start_line <= paper.end_line


@pytest.mark.parametrize("raw", [
    "[1] Amazon. Cloud security software. https://aws.amazon.com/marketplace/solutions/security.",
    "[2] Leptonica image processing and analysis library. http://www.leptonica.com.",
    "[3] Jonathan Corbet. Concurrency management in bpf. https://lwn.net/Articles/779120/.",
    "[4] Tesseract open source OCR engine. http://code.google.com/p/tesseract-ocr.",
    "[5] Research topic: A survey of evidence, 2021.",
])
def test_software_web_or_prose_not_assumed_to_be_academic_paper(raw):
    paper, = extract_references(raw)
    assert paper.title is None


def test_unnamed_literature_review_is_not_hallucinated():
    doc = ParsedDocument("new-domain.txt", """I. INTRODUCTION
    A substantial discussion of previous papers [1], [2].
II. BACKGROUND & MOTIVATION
    Technical background [1], [3].
III. METHOD
    Our proposed method.
IV. REFERENCES
[1] Shih-Wei Li, John S Koh, and Jason Nieh. Protecting cloud virtual machines. In USENIX, 2019.
[2] Example, J. (2022). A study on detection. Research Journal.
[3] Another group. Some tooling. https://example.com/.
""")
    literature, refs, notes = detect_sections(doc)
    assert not literature.found
    assert refs.found
    assert len(extract_references(refs.content)) == 3
    assert any("Literature Review" in note for note in notes)
