"""AI-style text indicators: style measures plus perplexity from a small local model.

Two independent checks must AGREE before anything is flagged:

1. Style measures (plain code, from projectiq_summarizer.signals): stock AI phrases,
   very even sentence rhythm, few checkable specifics.
2. Perplexity from distilgpt2, run locally on the CPU: machine-written text is
   usually very predictable (low perplexity) and very even from sentence to
   sentence (low "burstiness").

Vetoes, because these patterns are human:
- heavy spelling and grammar errors (same rule as the judge);
- too little text to measure.
If the language model is missing, nothing is flagged: one check alone is never enough.

Known bias: detectors like this wrongly flag a large share of writing by
non-native English speakers (Liang et al., 2023, Patterns). The output is a
reason to look closer, never a reason to deduct marks.
"""
import logging
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from projectiq_summarizer.signals import _AI_RES, compute_signals

from .config import DEFAULT, MODELS_DIR, IntegritySettings
from .contracts import Evidence
from .evidence import Signal
from .ria.preprocessing import split_sentences

logger = logging.getLogger(__name__)

AI_LIMITATION = ("AI-text indicators are probabilistic. Fluent, formal or non-native writing can "
                 "look machine-like, and edited AI text can look human. This is a prompt for a "
                 "conversation with the student, never evidence for a penalty.")

# sentence -> (sum of token negative log-likelihoods, number of tokens)
SentenceScorer = Callable[[Sequence[str]], List[tuple]]


class LocalLM:
    """distilgpt2 from models/, CPU only, loaded on first use. Never downloads."""

    def __init__(self, folder: str = DEFAULT.perplexity_model, models_dir: Path = MODELS_DIR):
        self.path = Path(models_dir) / folder
        self._tok = self._model = None
        self.available = self.path.exists()

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._tok = AutoTokenizer.from_pretrained(self.path, local_files_only=True)
        self._model = AutoModelForCausalLM.from_pretrained(self.path, local_files_only=True)
        self._model.eval()

    def __call__(self, sentences: Sequence[str]) -> List[tuple]:
        import torch

        self._load()
        out = []
        with torch.no_grad():
            for sent in sentences:
                ids = self._tok(sent, return_tensors="pt", truncation=True, max_length=128).input_ids
                bos = torch.tensor([[self._tok.bos_token_id]])
                ids = torch.cat([bos, ids], dim=1)
                if ids.shape[1] < 3:
                    continue
                loss = self._model(ids, labels=ids).loss.item()   # mean NLL over predicted tokens
                n = ids.shape[1] - 1
                out.append((sent, loss * n, n))
        return out


@dataclass
class PerplexityProfile:
    perplexity: float          # whole text, token-weighted
    burstiness: float          # std / mean of sentence log-perplexity; low = very even
    sentences: List[tuple]     # (sentence, perplexity), most predictable first


def perplexity_profile(sentences: Sequence[str], scorer: SentenceScorer) -> Optional[PerplexityProfile]:
    scored = scorer(sentences)
    if len(scored) < 3:
        return None
    total_nll = sum(s[1] for s in scored)
    total_tok = sum(s[2] for s in scored)
    per_sent = [(s, s_nll / n) for s, s_nll, n in scored if n]
    logs = [v for _, v in per_sent]
    mean = statistics.mean(logs)
    burst = statistics.pstdev(logs) / mean if mean else 0.0
    ranked = sorted(((s, math.exp(v)) for s, v in per_sent), key=lambda x: x[1])
    return PerplexityProfile(math.exp(total_nll / total_tok), burst, ranked)


def _phrase_sentences(sentences: Sequence[str], limit: int = 3) -> List[tuple]:
    """Sentences that contain stock AI phrases, with the phrases found."""
    hits = []
    for sent in sentences:
        found = sorted({re.sub(r"\s+", " ", m.group(0).lower()) for rx in _AI_RES for m in rx.finditer(sent)})
        if found:
            hits.append((len(found), sent, found))
    hits.sort(key=lambda h: -h[0])
    return [(s, f) for _, s, f in hits[:limit]]


def ai_signals(text: str, location_prefix: str = "report", scorer: Optional[SentenceScorer] = None,
               settings: IntegritySettings = DEFAULT) -> tuple:
    """Returns (signals, notes). Signals is empty unless BOTH checks agree."""
    notes: List[str] = []
    sentences = [" ".join(s.split()) for s in split_sentences(text) if len(s.split()) >= 5]
    style = compute_signals(text)
    if style.words < settings.min_words_for_ai_check:
        notes.append(f"AI-text check skipped: {style.words} words is too short to measure.")
        return [], notes
    poor = style.profile_scores["poorly_written"]
    if poor >= settings.poor_writing_veto:
        notes.append("AI-text check: frequent spelling and grammar errors, a human pattern; not flagged.")
        return [], notes
    style_ai = style.profile_scores["likely_ai_generated"]
    style_agrees = style_ai >= settings.style_support

    if scorer is None:
        lm = LocalLM(settings.perplexity_model)
        if not lm.available:
            notes.append(f"AI-text check: perplexity model not found in {lm.path}; style measures "
                         "alone are never enough, so nothing was flagged. Run scripts/setup_offline.py.")
            return [], notes
        scorer = lm
    profile = perplexity_profile(sentences[:settings.max_sentences_scored], scorer)
    if profile is None:
        notes.append("AI-text check: too few sentences for perplexity.")
        return [], notes
    ppl_agrees = (profile.perplexity <= settings.perplexity_ai_max
                  and profile.burstiness <= settings.burstiness_ai_max)
    notes.append(f"AI-text check: style score {style_ai:.2f} (needs {settings.style_support}), "
                 f"perplexity {profile.perplexity:.1f} (needs <= {settings.perplexity_ai_max}), "
                 f"burstiness {profile.burstiness:.2f} (needs <= {settings.burstiness_ai_max}).")
    if not (style_agrees and ppl_agrees):
        return [], notes

    evidence = [Evidence(source="report", location=f"{location_prefix}, stock phrases: {', '.join(found)}",
                         quote=sent) for sent, found in _phrase_sentences(sentences)]
    evidence += [Evidence(source="report", location=f"{location_prefix}, very predictable sentence "
                                                    f"(perplexity {ppl:.1f})", quote=sent)
                 for sent, ppl in profile.sentences[:3]]
    ppl_margin = min((settings.perplexity_ai_max - profile.perplexity) / settings.perplexity_ai_max,
                     (settings.burstiness_ai_max - profile.burstiness) / settings.burstiness_ai_max)
    signal = Signal(
        kind="ai_style_text",
        summary=(f"Style measures and the local language model agree the text reads as machine-like: "
                 f"style score {style_ai:.2f}, perplexity {profile.perplexity:.1f}, "
                 f"burstiness {profile.burstiness:.2f}. Advisory only."),
        evidence=evidence,
        scores={"style_margin": style_ai - settings.style_support, "perplexity_margin": ppl_margin},
        limitations=AI_LIMITATION,
    )
    return [signal], notes
