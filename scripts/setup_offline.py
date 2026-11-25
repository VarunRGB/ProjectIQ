"""One-time setup for fully offline ProjectIQ runs. Run ONCE while online:

    python scripts/setup_offline.py                     # everything, AI models at 0.5B (about 3 GB)
    python scripts/setup_offline.py --ai-detector classifier   # the Desklib AI classifier (1.75 GB)
    python scripts/setup_offline.py --ai-models 1.5B    # also the 1.5B Binoculars pair (about 6 GB more)
    python scripts/setup_offline.py --ai-models 0.5B,1.5B,3B
    python scripts/setup_offline.py --check             # list what is present

Downloads into the project's models/ folder:
  models/all-MiniLM-L6-v2               embeddings (summariser + finding similar sentences)
  models/nli-deberta-v3-small           NLI (confirms paraphrases)
  models/distilgpt2                     perplexity for the AI-text check (fallback)
  models/ai-text-detector-v1.01         AI percentage: Desklib classifier (--ai-detector classifier)
  models/Qwen2.5-<size>, -Instruct      AI percentage: observer and performer (Binoculars)
  models/en_core_web_sm, models/nltk_data   sentence splitting and stop-words

After this nothing is downloaded at run time (HF_HUB_OFFLINE=1 is set by the code).
To set up a second laptop with no internet, copy the models/ folder.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from projectiq_integrity.config import AI_MODEL_PAIRS, MODELS_DIR  # noqa: E402

MODELS = {
    "all-MiniLM-L6-v2": ("sentence-transformers", "sentence-transformers/all-MiniLM-L6-v2", "90 MB"),
    "nli-deberta-v3-small": ("cross-encoder", "cross-encoder/nli-deberta-v3-small", "280 MB"),
    "distilgpt2": ("causal-lm", "distilbert/distilgpt2", "330 MB"),
    "en_core_web_sm": ("spacy", "en_core_web_sm", "15 MB"),
    "nltk_data": ("nltk", "stopwords", "1 MB"),
}
CLASSIFIER = {"ai-text-detector-v1.01": ("desklib", "desklib/ai-text-detector-v1.01", "1.75 GB")}
AI_PAIR_SIZE = {"0.5B": "1 GB each", "1.5B": "3 GB each", "3B": "6 GB each"}


def ai_pair_models(sizes) -> dict:
    """The Binoculars observer/performer pair for each size asked for."""
    out = {}
    for size in sizes:
        for folder in AI_MODEL_PAIRS[size][:2]:
            out[folder] = ("causal-lm", f"Qwen/{folder}", AI_PAIR_SIZE[size])
    return out


def present(path: Path) -> bool:
    return path.is_file() or (path.is_dir() and any(path.iterdir()))


def fetch_model(folder: str, kind: str, name: str) -> None:
    target = MODELS_DIR / folder
    if kind == "sentence-transformers":
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(name, device="cpu").save(str(target))
    elif kind in ("cross-encoder", "causal-lm"):
        from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer

        cls = AutoModelForSequenceClassification if kind == "cross-encoder" else AutoModelForCausalLM
        AutoTokenizer.from_pretrained(name).save_pretrained(target)
        cls.from_pretrained(name).save_pretrained(target)
    elif kind == "desklib":
        from transformers import AutoTokenizer

        from projectiq_integrity.ai_detection import desklib_model_class

        AutoTokenizer.from_pretrained(name).save_pretrained(target)
        desklib_model_class().from_pretrained(name).save_pretrained(target)
    elif kind == "spacy":
        import spacy
        from spacy.cli import download

        try:
            nlp = spacy.load(name)
        except OSError:
            download(name)
            nlp = spacy.load(name)
        nlp.to_disk(target)
    elif kind == "nltk":
        import nltk

        target.mkdir(parents=True, exist_ok=True)
        if not nltk.download(name, download_dir=str(target), quiet=True):
            raise RuntimeError("NLTK download failed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only list what is present")
    ap.add_argument("--ai-models", default="0.5B",
                    help=f"Binoculars model sizes, comma separated: {', '.join(AI_MODEL_PAIRS)} (default 0.5B); "
                         "'none' to skip them")
    ap.add_argument("--ai-detector", default="",
                    help="also fetch an AI detector: 'classifier' (Desklib, 1.75 GB)")
    args = ap.parse_args()
    sizes = [x.strip() for x in args.ai_models.split(",") if x.strip() and x.strip().lower() != "none"]
    unknown = [x for x in sizes if x not in AI_MODEL_PAIRS]
    if unknown:
        print(f"Unknown AI model size(s): {', '.join(unknown)}. Choose from {', '.join(AI_MODEL_PAIRS)}.")
        return 2
    if not args.check:
        for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
            os.environ.pop(var, None)   # this script is the one place allowed online
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

    missing = 0
    items = [(f, MODELS_DIR / f, f"{n} ({size})", (lambda f=f, k=k, n=n: fetch_model(f, k, n)))
             for f, (k, n, size) in {**MODELS, **ai_pair_models(sizes),
                                     **(CLASSIFIER if args.ai_detector == "classifier" else {})}.items()]
    for label, path, desc, fetch in items:
        if present(path):
            print(f"[ok]      {label}")
        elif args.check:
            print(f"[missing] {label}: {desc}")
            missing += 1
        else:
            print(f"[fetch]   {label}: {desc} ...", flush=True)
            try:
                fetch()
                print(f"[ok]      {label}")
            except Exception as exc:  # noqa: BLE001
                print(f"[failed]  {label}: {exc}")
                missing += 1
    if missing:
        print(f"\n{missing} item(s) missing.")
        return 1
    print("\nEverything is local. ProjectIQ now runs without internet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
