"""Extract text from PDFs."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from paper_inbox.db import insert_artifact
from paper_inbox.storage.artifacts import parsed_path

logger = logging.getLogger(__name__)

MIN_TEXT_LEN = 2000


def _extract_with_pypdf(pdf_path_: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return ""
    try:
        reader = PdfReader(str(pdf_path_))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.info("[pdf_parse] pypdf failed for %s: %s", pdf_path_, exc)
        return ""


def _extract_with_pymupdf(pdf_path_: Path) -> str:
    try:
        import fitz  # type: ignore
    except ImportError:
        return ""
    try:
        doc = fitz.open(str(pdf_path_))
    except Exception as exc:
        logger.info("[pdf_parse] pymupdf failed for %s: %s", pdf_path_, exc)
        return ""
    try:
        return "\n".join(page.get_text() or "" for page in doc)
    finally:
        doc.close()


def parse_pdf(
    conn: sqlite3.Connection,
    paper_id: int,
    canonical_id: str,
    pdf_file: Path,
    *,
    parsed_dir: Path,
) -> Path | None:
    """Extract text from a PDF; persist as a .txt artifact. Returns the parsed path or None."""
    text = _extract_with_pypdf(pdf_file)
    if len(text) < MIN_TEXT_LEN:
        logger.info(
            "[pdf_parse] pypdf only produced %d chars for %s — trying pymupdf",
            len(text),
            canonical_id,
        )
        alt = _extract_with_pymupdf(pdf_file)
        if len(alt) > len(text):
            text = alt

    if not text.strip():
        logger.warning("[pdf_parse] failed to extract text for %s", canonical_id)
        return None

    target = parsed_path(parsed_dir, canonical_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    insert_artifact(conn, paper_id, "parsed_text", str(target))
    return target
