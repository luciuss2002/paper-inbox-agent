"""Artifact (PDF, parsed text, brief) filename helpers."""

from __future__ import annotations

from pathlib import Path

from paper_inbox.utils.text import safe_filename


def file_id_from_canonical(canonical_id: str) -> str:
    """Convert e.g. 'arxiv:2501.12345' -> 'arxiv_2501_12345'."""
    return safe_filename(canonical_id.replace(":", "_").replace(".", "_"))


def pdf_path(pdf_dir: Path, canonical_id: str) -> Path:
    return pdf_dir / f"{file_id_from_canonical(canonical_id)}.pdf"


def parsed_path(parsed_dir: Path, canonical_id: str) -> Path:
    return parsed_dir / f"{file_id_from_canonical(canonical_id)}.txt"


def brief_path(briefs_dir: Path, canonical_id: str, run_date: str) -> Path:
    return briefs_dir / run_date / f"{file_id_from_canonical(canonical_id)}.md"


def report_brief_path(reports_dir: Path, run_date: str, bucket: str, canonical_id: str) -> Path:
    bucket_slug = "must_read" if bucket == "Must Read" else (
        "skim" if bucket == "Skim" else "archive"
    )
    return (
        reports_dir
        / run_date
        / bucket_slug
        / f"{file_id_from_canonical(canonical_id)}.md"
    )
