"""Stand-ins for MiniLM and DeBERTa so the document check can be tested offline.

They are NOT models. The "embedder" maps a few synonyms to one word and compares
bags of words; the "NLI" calls two sentences equivalent when they share most of
their meaning words. Enough to exercise every code path; real runs use the real
models from models/.
"""
import re

import numpy as np

SYNONYMS = {
    "recognising": "recognition", "recognition": "recognition", "automatically": "automatic",
    "automatic": "automatic", "log": "track", "track": "track", "meals": "eat", "eat": "eat",
    "photo": "photo", "photograph": "photo", "handwritten": "manual", "manual": "manual",
    "records": "diary", "diaries": "diary", "diary": "diary", "datasets": "dataset", "dataset": "dataset",
    "dominated": "mostly", "mostly": "mostly", "western": "western", "food": "food", "dishes": "food",
    "systems": "model", "models": "model", "badly": "poorly", "poorly": "poorly", "perform": "do",
    "do": "do", "indian": "indian", "trained": "trained", "users": "people", "people": "people",
    "keeping": "writing", "writing": "writing", "widely": "public", "public": "public",
}
STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "by", "is", "are", "be", "it",
        "that", "this", "with", "as", "so", "them", "they", "our", "we", "can", "help", "instead",
        "simply", "taking", "lets", "like", "because", "used", "hard", "existing", "contain", "such",
        "without", "what", "on", "from", "at", "than"}


def canon(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [SYNONYMS.get(w, w) for w in words if w not in STOP]


class FakeEmbedder:
    backend = "sentence-transformers"   # pretend, so the code treats results as semantic
    is_semantic = True                   # a property on the real Embedder, so a plain attribute here

    def similarity(self, a, b):
        vocab = {}
        bags_a, bags_b = [canon(t) for t in a], [canon(t) for t in b]
        for bag in bags_a + bags_b:
            for w in bag:
                vocab.setdefault(w, len(vocab))

        def vec(bag):
            v = np.zeros(max(1, len(vocab)))
            for w in bag:
                v[vocab[w]] += 1
            n = np.linalg.norm(v)
            return v / n if n else v

        A = np.array([vec(x) for x in bags_a])
        B = np.array([vec(x) for x in bags_b])
        return A @ B.T

    def save_cache(self):
        pass


class FakeNLI:
    backend = "transformers-nli"
    is_neural = True                     # a property on the real NLIModel, so a plain attribute here

    def predict(self, premises, hypotheses):
        out = []
        for p, h in zip(premises, hypotheses):
            sp, sh = set(canon(p)), set(canon(h))
            share = len(sp & sh) / max(1, len(sh))          # how much of h is covered by p
            ent = 0.9 if share >= 0.45 else 0.15
            out.append({"entailment": ent, "neutral": 1 - ent - 0.05, "contradiction": 0.05})
        return out


def install(monkeypatch):
    """Make the analyzer use the stand-ins for the rest of one test."""
    from projectiq_integrity.ria import models

    monkeypatch.setattr(models, "get_embedder", lambda settings=None: FakeEmbedder())
    monkeypatch.setattr(models, "get_nli", lambda settings=None: FakeNLI())
