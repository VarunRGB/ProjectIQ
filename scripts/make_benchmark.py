"""Builds the Integrity benchmark: a student research paper with planted copying, its
reference papers, and an answer key saying what every planted sentence is.

    python scripts/make_benchmark.py        # writes tests/fixtures/benchmark/

All papers are invented for testing. Edit the text below and run again to change a
case; the PDFs and answer_key.csv are always regenerated together.

Each student sentence is a tuple (case_id, expected, source_ref, note, text):
- case_id   "own" for the student's own writing, otherwise a short id (V1, N2, P3, H1...)
- expected  what a correct tool should say; alternatives separated by "|":
            verbatim, near-verbatim, paraphrase, similar (shown, not counted), none
- source_ref the reference id the sentence was taken from ("" if none)
"""
import csv
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "benchmark"

# --------------------------------------------------------------------------- #
# Reference papers (id -> file name, title, sections of sentences)
# --------------------------------------------------------------------------- #
REFS = {
    "A": ("transformer_text_classification", "Transformer Models for Short Text Classification", [
        ("Abstract", [
            "Transformer models have become the standard approach for classifying short texts such as tweets, product reviews and search queries.",
            "Unlike recurrent networks, they process every word of a sentence in parallel and learn which words matter through self-attention.",
        ]),
        ("1 Introduction", [
            "Pre-trained models such as BERT are first trained on large unlabelled corpora and then fine-tuned on a small labelled dataset for a specific task.",
            "This two-stage process allows strong performance even when only a few thousand labelled examples are available.",
            "However, the largest models require substantial memory and are slow to run on ordinary laptops.",
            "Distilled variants such as DistilBERT keep most of the accuracy while using roughly half the parameters.",
        ]),
        ("2 Method", [
            "Accuracy is the proportion of predictions that match the true labels.",
            "Because accuracy can be misleading on imbalanced data, we also report the F1 score of the minority class.",
            "Each model was fine-tuned for three epochs with a learning rate of 2e-5 and a batch size of 32.",
            "Early stopping on the validation loss prevented the models from overfitting the training data.",
        ]),
        ("3 Results", [
            "DistilBERT reached an F1 score within two points of BERT-base while running 60 percent faster at inference.",
            "Most errors occurred on sarcastic texts, where the literal words contradict the intended meaning.",
        ]),
        ("4 Conclusion", [
            "Lightweight transformers are a practical choice when computing resources are limited.",
        ]),
        ("References", ["[1] Devlin J. et al. BERT: pre-training of deep bidirectional transformers. NAACL 2019."]),
    ]),
    "B": ("fake_review_detection", "Detecting Deceptive Online Reviews", [
        ("Abstract", [
            "Online reviews strongly influence purchasing decisions, which creates an incentive for sellers to post fake ones.",
        ]),
        ("1 Introduction", [
            "Fake reviews are often written in bulk by paid workers who follow a template supplied by the seller.",
            "As a result, deceptive reviews tend to be shorter, more emotional and less specific than genuine ones.",
            "They frequently mention the product name repeatedly but rarely describe concrete experiences such as delivery time or packaging.",
            "Early detection systems relied on hand-crafted features such as review length, rating deviation and the proportion of capital letters.",
            "These features are easy to compute but are also easy for spammers to evade once they become known.",
        ]),
        ("2 Related Work", [
            "Behavioural signals, such as many reviews posted from one account in a short time, are harder to fake than textual cues.",
            "Combining textual and behavioural features consistently outperforms either source of evidence alone.",
            "A major obstacle is the lack of labelled data, since even human readers identify fake reviews only slightly better than chance.",
            "Most public datasets were created by asking crowd workers to write fake hotel reviews, which may not reflect real spam.",
        ]),
        ("3 Results", [
            "Fine-tuning on reviews from the target platform improved accuracy by seven points compared with training on hotel reviews alone.",
            "Real-time screening of new reviews is needed so that fake ones are removed before customers read them.",
        ]),
        ("References", ["[1] Ott M. et al. Finding deceptive opinion spam by any stretch of the imagination. ACL 2011."]),
    ]),
    "C": ("code_mixed_text", "Processing Hinglish: Challenges of Code-Mixed Text", [
        ("Abstract", [
            "Code-mixing, the use of two languages within a single sentence, is common on Indian social media and shopping sites.",
        ]),
        ("1 Introduction", [
            "Hinglish text mixes Hindi words written in the Latin script with English words and grammar.",
            "Spelling is not standardised, so the same Hindi word can appear in many different romanised forms.",
            "Models pre-trained only on English text therefore treat many Hindi words as unknown tokens.",
            "Multilingual models such as mBERT and XLM-R handle code-mixed text better because their vocabularies include Hindi subwords.",
        ]),
        ("2 Discussion", [
            "Normalising spellings before classification further reduces errors caused by inconsistent romanisation.",
            "Annotated Hinglish datasets remain small, which limits how much can be learned from supervised training.",
        ]),
        ("References", ["[1] Conneau A. et al. Unsupervised cross-lingual representation learning at scale. ACL 2020."]),
    ]),
    # Unrelated paper: nothing should match it.
    "D": ("crop_yield_prediction", "Predicting Crop Yield from Satellite Images", [
        ("Abstract", [
            "Satellite images taken during the growing season can predict wheat yield weeks before harvest.",
        ]),
        ("1 Introduction", [
            "Vegetation indices computed from red and near-infrared bands track how green and dense a crop is.",
            "Cloud cover during the monsoon removes many images, so models must cope with missing observations.",
            "We trained a gradient boosting model on five seasons of data from districts in Punjab.",
        ]),
        ("2 Conclusion", [
            "Yield forecasts at district level can help agencies plan procurement and storage in advance.",
        ]),
    ]),
}

# --------------------------------------------------------------------------- #
# The student's paper: (case_id, expected, source_ref, note, text)
# --------------------------------------------------------------------------- #
OWN = ("own", "none", "", "student's own writing")
STUDENT_TITLE = "Detecting Fake Product Reviews in Hinglish on Indian E-commerce Platforms"
STUDENT = [
    ("Abstract", [
        (*OWN, "Fake reviews mislead shoppers on Indian e-commerce sites, and many of them are written in Hinglish rather than English."),
        (*OWN, "We collected 4,200 reviews from two shopping platforms, labelled them with the help of three annotators, and fine-tuned a multilingual transformer to flag likely fake reviews."),
        (*OWN, "Our best model reached an F1 score of 0.83 on fake reviews."),
    ]),
    ("1 Introduction", [
        ("P1", "paraphrase", "B", "reworded, same meaning",
         "Because shoppers rely heavily on reviews when deciding what to buy, sellers have a reason to publish fabricated ones."),
        ("P2", "paraphrase", "C", "reworded, same meaning",
         "Indian users of social media and online stores often switch between two languages in the middle of a sentence."),
        ("V1", "verbatim", "B", "hyphen removed and capital changed only (Real-time -> Real time)",
         "Real time screening of new reviews is needed so that fake ones are removed before customers read them."),
        (*OWN, "On the two platforms we studied, more than a third of reviews contained at least one Hindi word."),
        ("V2", "verbatim", "B", "copied word for word, with a citation but no quotation marks",
         "A major obstacle is the lack of labelled data, since even human readers identify fake reviews only slightly better than chance [2]."),
        ("H1", "none", "C", "copied but inside quotation marks: must NOT be counted",
         'As noted in earlier work, "Spelling is not standardised, so the same Hindi word can appear in many different romanised forms."'),
    ]),
    ("2 Related Work", [
        ("V3", "verbatim", "B", "copied chunk (1 of 4), no citation",
         "Fake reviews are often written in bulk by paid workers who follow a template supplied by the seller."),
        ("V4", "verbatim", "B", "copied chunk (2 of 4)",
         "As a result, deceptive reviews tend to be shorter, more emotional and less specific than genuine ones."),
        ("V5", "verbatim", "B", "copied chunk (3 of 4)",
         "They frequently mention the product name repeatedly but rarely describe concrete experiences such as delivery time or packaging."),
        ("V6", "verbatim", "B", "copied chunk (4 of 4)",
         "Early detection systems relied on hand-crafted features such as review length, rating deviation and the proportion of capital letters."),
        (*OWN, "None of these studies looked at reviews that mix Hindi and English."),
        (*OWN, "Our work fills that gap for two of the largest shopping sites in India."),
        ("N1", "near-verbatim", "A", "copied chunk (1 of 3), two words swapped",
         "Pre-trained models such as BERT are first trained on huge unlabelled corpora and then fine-tuned on a small labelled dataset for a particular task."),
        ("N2", "near-verbatim", "A", "copied chunk (2 of 3), one word swapped",
         "This two-stage process allows strong performance even when only a few thousand labelled samples are available."),
        ("N3", "near-verbatim", "A", "copied chunk (3 of 3), one phrase swapped",
         "However, the largest models require a lot of memory and are slow to run on ordinary laptops."),
        ("V7", "verbatim", "C", "copied, then the next source sentence comes BEFORE it (order reversed)",
         "Multilingual models such as mBERT and XLM-R handle code-mixed text better because their vocabularies include Hindi subwords."),
        ("V8", "verbatim", "C", "copied, order reversed, cited",
         "Models pre-trained only on English text therefore treat many Hindi words as unknown tokens [3]."),
        ("M1", "paraphrase|near-verbatim|similar", "A", "two source sentences merged into one",
         "Transformer models, which read all the words of a sentence in parallel and use self-attention to decide which words matter, are now the standard way to classify short texts such as reviews."),
    ]),
    ("3 Methodology", [
        (*OWN, "We scraped reviews for 150 products in electronics and personal care, and kept only reviews with at least ten words."),
        (*OWN, "Three annotators labelled each review as genuine or fake using a guideline we wrote, and disagreements were settled by majority vote."),
        ("P3", "paraphrase", "C", "reworded, same meaning",
         "Since there is no fixed way to spell Hindi in English letters, a single word can be written in several ways."),
        ("H2", "none|similar", "C", "the student's own method, related to a source claim but not the same statement",
         "To reduce mistakes caused by these varied spellings, we mapped each word to one standard spelling before training."),
        ("N4", "near-verbatim", "A", "end of the source sentence removed",
         "Each model was fine-tuned for three epochs with a learning rate of 2e-5."),
        ("V9", "verbatim", "A", "standard definition copied (a reviewer would likely accept this)",
         "Accuracy is the proportion of predictions that match the true labels."),
        ("P4", "paraphrase", "A", "reworded, same meaning",
         "Since our classes are imbalanced and accuracy alone can give a false picture, we additionally report the F1 score for the fake class."),
    ]),
    ("4 Results", [
        (*OWN, "The fine-tuned XLM-R model reached an F1 score of 0.83 on fake reviews, compared with 0.71 for an English-only BERT model."),
        ("H3", "none|similar", "B", "OPPOSITE meaning of a source sentence: must NOT count as paraphrase",
         "Training on reviews from the target platform did not help at all, giving the same accuracy as training on hotel reviews alone."),
        ("P5", "paraphrase", "A", "reworded, same meaning",
         "The model made most of its mistakes on sarcastic reviews, in which the words literally say the opposite of what the writer means."),
        (*OWN, "Most other errors were very short reviews such as nice product, which could honestly be either genuine or fake."),
        ("H4", "none|similar", "A", "same topic as a source sentence, different statement",
         "Lightweight models also mattered to us, because our college lab has no GPU."),
    ]),
    ("5 Conclusion", [
        (*OWN, "Hinglish reviews need multilingual models, and our labelled dataset will be shared with other researchers."),
        ("V10", "verbatim", "A", "copied with one spelling mistake (acuracy)",
         "Distilled variants such as DistilBERT keep most of the acuracy while using roughly half the parameters."),
        ("V11", "verbatim", "B", "British to American spelling only (Behavioural -> Behavioral)",
         "Behavioral signals, such as many reviews posted from one account in a short time, are harder to fake than textual cues."),
        (*OWN, "In future we will add these account-level signals to our text model."),
    ]),
    ("References", [
        (None, None, None, None, "[1] Devlin J. et al. BERT: pre-training of deep bidirectional transformers. NAACL 2019."),
        (None, None, None, None, "[2] Ott M. et al. Finding deceptive opinion spam. ACL 2011."),
        (None, None, None, None, "[3] Conneau A. et al. Unsupervised cross-lingual representation learning at scale. ACL 2020."),
    ]),
]

# Chunks a correct tool should report: (reference id, first case, last case)
EXPECTED_CHUNKS = [("B", "V3", "V6"), ("A", "N1", "N3")]


def write_pdf(path: Path, title: str, sections) -> None:
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    story = [Paragraph(title, styles["Title"]), Spacer(1, 8)]
    for heading, sentences in sections:
        story.append(Paragraph(heading, styles["Heading2"]))
        texts = [s if isinstance(s, str) else s[-1] for s in sentences]
        if heading == "References":
            story += [Paragraph(t, body) for t in texts]
        else:
            story.append(Paragraph(" ".join(texts).replace("&", "&amp;"), body))
        story.append(Spacer(1, 6))
    SimpleDocTemplate(str(path), pagesize=A4, title=title).build(story)


def main() -> int:
    refs_dir = OUT / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    for old in refs_dir.glob("*.pdf"):
        old.unlink()
    for ref_id, (fname, title, sections) in REFS.items():
        write_pdf(refs_dir / f"{fname}.pdf", title, sections)
    write_pdf(OUT / "student_paper.pdf", STUDENT_TITLE, STUDENT)

    ref_names = {k: v[0] for k, v in REFS.items()}
    rows = []
    for _, sentences in STUDENT:
        for case_id, expected, ref, note, text in sentences:
            if case_id is None:
                continue
            rows.append({"case": case_id, "expected": expected,
                         "source": ref_names.get(ref, ""), "note": note, "sentence": text})
    with open(OUT / "answer_key.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["case", "expected", "source", "note", "sentence"])
        writer.writeheader()
        writer.writerows(rows)
    with open(OUT / "expected_chunks.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "first_case", "last_case"])
        writer.writerows((ref_names[r], a, b) for r, a, b in EXPECTED_CHUNKS)
    planted = sum(r["case"] != "own" for r in rows)
    print(f"Wrote {OUT}: student_paper.pdf, {len(REFS)} references, {planted} planted cases, "
          f"{len(rows) - planted} own sentences.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
