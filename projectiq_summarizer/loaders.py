"""Read a submission from .txt, .md, .pdf or .docx into plain text."""
from pathlib import Path


def load_text(path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".txt", ".md", ""):
        return p.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ImportError("Reading PDFs needs pypdf:  pip install pypdf") from e
        reader = PdfReader(str(p))
        return "\n\n".join((pg.extract_text() or "") for pg in reader.pages)
    if ext == ".docx":
        try:
            import docx
        except ImportError as e:
            raise ImportError("Reading .docx needs python-docx:  pip install python-docx") from e
        d = docx.Document(str(p))
        return "\n\n".join(par.text for par in d.paragraphs if par.text.strip())
    raise ValueError(f"Unsupported file type '{ext}'. Use .txt, .md, .pdf or .docx")
