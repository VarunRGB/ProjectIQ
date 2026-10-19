
from pathlib import Path
import sys

# Locate the ProjectIQ repository root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Support both a src/projectiq_summarizer/ and
# projectiq_summarizer/ package layout.
SRC_DIR = PROJECT_ROOT / "src"
IMPORT_ROOT = SRC_DIR if SRC_DIR.is_dir() else PROJECT_ROOT
sys.path.insert(0, str(IMPORT_ROOT))

from projectiq_summarizer.chunking import (
    chunk_text,
    chunk_text_structure_aware,
)
from projectiq_summarizer.config import Settings


# CHANGE THIS if your document is stored somewhere else.
DOCUMENT_PATH = PROJECT_ROOT / "ProjectIQ" / "test_cases" / "scalability" / "large_7000_tokens.txt"

# Load the document as a string.
if not DOCUMENT_PATH.is_file():
    raise FileNotFoundError(
        f"Document not found: {DOCUMENT_PATH}\n"
        "Update DOCUMENT_PATH to the actual location of the .txt file."
    )

text = DOCUMENT_PATH.read_text(encoding="utf-8")

# Use the same configuration for both methods.
settings = Settings()

baseline = chunk_text(
    text,
    settings.chunk_chars,
    settings.chunk_overlap_chars,
)

aware = chunk_text_structure_aware(
    text,
    settings.chunk_chars,
    settings.chunk_overlap_chars,
)

print(f"Document: {DOCUMENT_PATH.name}")
print(f"Input characters: {len(text):,}")
print(
    f"Chunk settings: max_chars={settings.chunk_chars}, "
    f"overlap={settings.chunk_overlap_chars}"
)

for name, chunks in [
    ("BASELINE", baseline),
    ("STRUCTURE-AWARE", aware),
]:
    print(f"\n{'=' * 70}")
    print(f"{name}: {len(chunks)} chunks")
    print("=" * 70)

    for i, chunk in enumerate(chunks, 1):
        lines = [
            line.strip()
            for line in chunk.splitlines()
            if line.strip()
        ]

        start = lines[0][:120] if lines else ""
        end = lines[-1][-120:] if lines else ""

        print(f"\nChunk {i}: {len(chunk):,} characters")
        print(f"  START: {start!r}")
        print(f"  END:   {end!r}")
