"""Measurable text signals, computed with plain code and no language model.

These act as an independent second opinion. The LLM judge never sees them,
so when both reach the same conclusion that agreement means something, and
it feeds into the confidence score.

The AI-style signal is a heuristic. It is shown to the reviewer as advisory
evidence and is never used to deduct marks on its own."""
import re
import statistics
from dataclasses import asdict, dataclass
from typing import Dict, List

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
SENT_RE = re.compile(r"(?<=[.!?])\s+|\n\s*\n")

AI_PHRASES = [
    r"\bdelv(?:e|es|ing)\b", r"\bin today'?s\b", r"\bfast[- ]paced\b",
    r"\brapidly evolving\b", r"\bever[- ]evolving\b", r"\bdigital landscape\b",
    r"\bit is (?:important|worth|crucial) to note\b",
    r"\bplays? an? (?:crucial|pivotal|vital|key|significant) role\b",
    r"\ba testament to\b", r"\bseamless(?:ly)?\b", r"\bleverag(?:e|es|ing)\b",
    r"\bharness(?:es|ing)? the power\b", r"\bunlock(?:s|ing)?\b",
    r"\bfurthermore\b", r"\bmoreover\b", r"\bin conclusion\b", r"\bin summary\b",
    r"\bnot only\b.{0,60}?\bbut also\b", r"\bcutting[- ]edge\b",
    r"\bpaving the way\b", r"\bgame[- ]changer\b", r"\brevolutioni[sz](?:e|es|ing)\b",
    r"\bstreamlin(?:e|es|ing)\b", r"\bfoster(?:s|ing)?\b", r"\bholistic\b",
    r"\bmyriad\b", r"\bplethora\b", r"\btapestry\b", r"\bnavigat\w* the complexities\b",
    r"\bin the realm of\b", r"\bstands as\b", r"\bempower(?:s|ing)?\b",
    r"\binnovative\b", r"\btransformative\b", r"\bvaluable insights\b",
    r"\bhighest standards\b", r"\bensur(?:e|es|ing) that\b", r"\ba wide range of\b",
]
_AI_RES = [re.compile(p, re.I | re.S) for p in AI_PHRASES]

TEXTSPEAK = {"u", "ur", "pls", "plz", "coz", "cuz", "thx", "gonna", "wanna",
             "dont", "doesnt", "cant", "wont", "im", "ive", "isnt", "didnt",
             "thats", "wasnt", "arent"}
_SUFFIXES = ("isation", "ization", "ations", "ation", "ised", "ized", "ises",
             "izes", "ise", "ize", "ings", "ing", "ers", "er", "ed", "es", "ly",
             "s", "d")


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _syllables(word: str) -> int:
    w = word.lower().strip("'")
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(1, n)


_spell = None


def _spellchecker():
    global _spell
    if _spell is None:
        try:
            from spellchecker import SpellChecker
            _spell = SpellChecker()
        except Exception:
            _spell = False
    return _spell or None


def _typo_rate(words: List[str]) -> float:
    """Share of ordinary lowercase words that look like misspellings: unknown
    to the dictionary, one edit away from a real word, and not just an
    inflected form (plural, -ing, -ised) of a known word."""
    sp = _spellchecker()
    if sp is None:
        return 0.0
    cands = [w.lower() for w in words
             if w.islower() and len(w) > 3 and "'" not in w and "-" not in w]
    if not cands:
        return 0.0
    unknown = sp.unknown(cands)
    typos = 0
    for w in cands:
        if w not in unknown:
            continue
        inflected = any(
            w.endswith(suf) and len(w) - len(suf) >= 3 and
            (sp.known([w[:-len(suf)]]) or sp.known([w[:-len(suf)] + "e"]))
            for suf in _SUFFIXES)
        if inflected:
            continue
        if sp.known(sp.edit_distance_1(w)):
            typos += 1
    return typos / len(cands)


@dataclass
class TextSignals:
    words: int
    sentences: int
    avg_sentence_words: float
    sentence_length_cv: float        # low = very even rhythm
    longest_sentence_words: int
    flesch_reading_ease: float
    typo_rate: float                 # share of lowercase words
    mechanics_per_100w: float        # lowercase starts, " i ", missing spaces...
    ai_phrases_per_100w: float
    ai_phrases_found: List[str]
    specifics_per_100w: float        # numbers, acronyms, code-like tokens
    technical_density: float
    profile_scores: Dict[str, float]
    profile: str
    profile_margin: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute_signals(text: str) -> TextSignals:
    words = WORD_RE.findall(text)
    n = max(1, len(words))
    raw_sents = [s.strip() for s in SENT_RE.split(text) if s and s.strip()]
    sents = [s for s in raw_sents if len(WORD_RE.findall(s)) >= 3]
    lens = [len(WORD_RE.findall(s)) for s in sents] or [len(words)]
    avg = statistics.mean(lens)
    cv = (statistics.pstdev(lens) / avg) if avg and len(lens) > 1 else 0.0
    syl = sum(_syllables(w) for w in words)
    flesch = 206.835 - 1.015 * (n / max(1, len(sents))) - 84.6 * (syl / n)

    # --- writing mechanics ------------------------------------------------
    lower_starts = sum(1 for s in sents
                       if (m := re.search(r"[A-Za-z]", s)) and m.group().islower())
    lone_i = len(re.findall(r"(?<![A-Za-z.])i(?![A-Za-z.'])", text))
    no_space = len(re.findall(r"[,;](?=[A-Za-z])", text))
    repeats = len(re.findall(r"\b(\w{2,})\s+\1\b", text, re.I))
    speak = sum(1 for w in words if w.lower() in TEXTSPEAK)
    shouting = len(re.findall(r"[!?]{2,}|\.{4,}", text))
    mechanics = lower_starts + lone_i + no_space + repeats + speak + shouting
    typo = _typo_rate(words)

    # --- AI-style phrasing ------------------------------------------------
    found = []
    for rx in _AI_RES:
        for m in rx.finditer(text):
            found.append(re.sub(r"\s+", " ", m.group(0).lower()))
    ai_rate = len(found) / n * 100

    # --- specificity / technical density ----------------------------------
    tokens = text.split()
    numbers = len(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?(?:%|x|ms|gb|mb|kb)?\b",
                             text, re.I))
    acronyms = len(re.findall(r"\b[A-Z]{2,}[0-9]*s?\b", text))
    code_like = sum(1 for t in tokens
                    if re.search(r"[a-z][A-Z]|_[a-z]|\w\.\w+\(|[=<>]{1,2}\d|@\d", t))
    long_words = sum(1 for w in words if len(w) >= 13)
    specifics = numbers + acronyms + code_like
    spec_rate = specifics / n * 100
    tech_density = (specifics + long_words) / n

    # --- profile scores (0-1) ---------------------------------------------
    runon = _clip((max(lens) - 45) / 40)
    poor = (0.45 * _clip(typo / 0.05) + 0.35 * _clip(mechanics / n * 100 / 3)
            + 0.20 * runon)
    ai = (0.55 * _clip(ai_rate / 2.0)
          + 0.25 * _clip((0.50 - cv) / 0.25)
          + 0.20 * (1 - _clip(spec_rate / 4)))
    ai *= 1 - 0.6 * _clip(poor / 0.6)          # sloppy text is rarely machine-written
    tech = (0.5 * _clip(tech_density / 0.10) + 0.3 * _clip(numbers / n * 100 / 5)
            + 0.2 * _clip((55 - flesch) / 40))
    tech *= 1 - 0.5 * _clip(ai_rate / 2.0)     # buzzwords are not technical depth
    scores = {"poorly_written": round(poor, 3),
              "likely_ai_generated": round(ai, 3),
              "highly_technical": round(tech, 3)}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, second = ranked[0], ranked[1]
    profile = top[0] if top[1] >= 0.45 else "standard"
    margin = top[1] - second[1] if profile != "standard" else 0.45 - top[1]

    return TextSignals(
        words=len(words), sentences=len(sents),
        avg_sentence_words=round(avg, 1), sentence_length_cv=round(cv, 3),
        longest_sentence_words=max(lens), flesch_reading_ease=round(flesch, 1),
        typo_rate=round(typo, 4), mechanics_per_100w=round(mechanics / n * 100, 2),
        ai_phrases_per_100w=round(ai_rate, 2), ai_phrases_found=sorted(set(found)),
        specifics_per_100w=round(spec_rate, 2), technical_density=round(tech_density, 3),
        profile_scores=scores, profile=profile, profile_margin=round(margin, 3))
