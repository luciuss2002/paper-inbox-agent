"""Download PDFs for selected papers."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import httpx

from paper_inbox.db import get_artifact, insert_artifact
from paper_inbox.models import PaperMetadata
from paper_inbox.storage.artifacts import pdf_path
from paper_inbox.utils.hashing import sha256_file

logger = logging.getLogger(__name__)


def fetch_pdf(
    conn: sqlite3.Connection,
    paper_id: int,
    paper: PaperMetadata,
    *,
    pdf_dir: Path,
    timeout_seconds: float = 30.0,
) -> Path | None:
    """Download the PDF for a paper, skipping if already on disk. Returns local path on success."""
    if not paper.pdf_url:
        logger.info("[pdf_fetch] no pdf_url for %s; skipping", paper.canonical_id)
        return None

    target = pdf_path(pdf_dir, paper.canonical_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and target.stat().st_size > 0:
        existing = get_artifact(conn, paper_id, "pdf")
        if not existing:
            insert_artifact(
                conn, paper_id, "pdf", str(target), sha256=sha256_file(str(target))
            )
        return target

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            resp = client.get(paper.pdf_url)
            resp.raise_for_status()
            target.write_bytes(resp.content)
    except Exception as exc:
        logger.warning("[pdf_fetch] failed for %s: %s", paper.canonical_id, exc)
        return None

    insert_artifact(conn, paper_id, "pdf", str(target), sha256=sha256_file(str(target)))
    return target
