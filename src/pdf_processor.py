"""
pdf_processor.py — Processes PDF files dropped into PDF_INBOX_PATH.

Pipeline:
  1. Extract text (pymupdf preferred, pypdf fallback)
  2. Ask Gemini to summarise via the pdf_analysis prompt
  3. Write a processed note to INBOX_PATH (Clippings/) — same format as web clips
  4. Move PDF to PDF_ARCHIVE_PATH/YYYY-MM/
  5. On any failure, leave the PDF untouched so it retries next scan
"""

import shutil
from datetime import datetime
from pathlib import Path

from config import PDF_ARCHIVE_PATH, INBOX_PATH, CLIP_CONTENT_LIMIT, load_prompt
from notes import write_note, safe_filename, today
from llm import llm_simple, parse_json_response
from indexer import index_note


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract full text from a PDF. Returns empty string if extraction fails."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(pdf_path))
        text = "\n\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except ImportError:
        pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        pages = [p.extract_text() for p in reader.pages if p.extract_text()]
        return "\n\n".join(pages)
    except Exception:
        return ""


def _archive_path_for(pdf_path: Path) -> Path:
    """Return the destination archive path for a PDF, avoiding overwrites."""
    folder = PDF_ARCHIVE_PATH / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / pdf_path.name
    if dest.exists():
        dest = folder / f"{pdf_path.stem}_{today()}{pdf_path.suffix}"
    return dest


def find_unprocessed_pdfs(pdf_inbox_path: Path) -> list[Path]:
    if not pdf_inbox_path.exists():
        return []
    return list(pdf_inbox_path.glob("*.pdf"))


def process_pdf(path: Path):
    """
    Full processing pipeline for a dropped PDF.
    Leaves the file untouched on any failure.
    """
    print(f"[pdf] Processing: {path.name}")

    text = extract_pdf_text(path)
    if not text.strip():
        print(f"[pdf] No text extracted from {path.name}, leaving for retry.")
        return

    content = text[:CLIP_CONTENT_LIMIT]

    system_prompt = load_prompt("clip_system")
    user_prompt = load_prompt("clip_analysis", source_url=path.name, content=content)

    result_text = llm_simple(prompt=user_prompt, system=system_prompt, task="clip")

    data = parse_json_response(result_text)
    if not data:
        print(f"[pdf] JSON parse failed for {path.name}, leaving for retry.")
        return

    title = data.get("title", path.stem)
    clean_title = safe_filename(title)
    tags = data.get("tags", [])
    analysis = data.get("content") or data.get("summary") or ""
    moc_summary = data.get("moc_summary") or ""

    archive_dest = _archive_path_for(path)
    try:
        shutil.copy2(str(path), str(archive_dest))
        path.unlink()
    except Exception as e:
        print(f"[pdf] Could not archive {path.name}: {e}, leaving for retry.")
        archive_dest.unlink(missing_ok=True)
        return

    note_path = INBOX_PATH / f"{clean_title}.md"
    fm = {
        "pdf_source": path.name,
        "pdf_archive": str(archive_dest),
        "processed": True,
        "processed_date": today(),
        "tags": tags,
    }
    note_body = f"{analysis}\n\n---\n\n## Original Content\n\n{text}"
    write_note(note_path, fm, note_body)

    index_note(
        note_title=clean_title,
        note_path=note_path,
        summary=moc_summary,
        tags=tags,
        analysis=analysis,
    )

    print(f"[pdf] Done: {clean_title} → {archive_dest.parent.name}/")
