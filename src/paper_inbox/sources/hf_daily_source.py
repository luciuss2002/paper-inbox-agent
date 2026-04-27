"""Hugging Face Daily Papers source.

The endpoint at https://huggingface.co/api/daily_papers returns a JSON list of
papers curated by the community each day. Each entry usually carries an arXiv
id, a title, an abstract/summary, authors, an upvote count, and an optional
machine-generated tl;dr. The upvote count is a strong trend signal that we
preserve as a tag (e.g. ``hf:upvotes=42``) so downstream scoring can use it.

Because HF Daily papers are arXiv papers, we set ``canonical_id`` to the same
``arxiv:<id>`` form used by ``ArxivSource`` so that dedupe collapses duplicates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from paper_inbox.models import PaperMetadata, PaperSource
from paper_inbox.sources.base import PaperSourceBase
from paper_inbox.utils.text import collapse_whitespace

logger = logging.getLogger(__name__)

HF_DAILY_API = "https://huggingface.co/api/daily_papers"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _coerce_authors(raw: Any) -> list[str]:
    """HF returns author objects with ``name`` keys; sometimes nested arrays."""
    out: list[str] = []
    for a in raw or []:
        if isinstance(a, dict):
            n = a.get("name") or a.get("fullname")
            if n:
                out.append(str(n))
        elif isinstance(a, str):
            out.append(a)
    return out


def _entry_to_metadata(entry: dict[str, Any]) -> PaperMetadata | None:
    """Convert one HF daily-papers JSON object into PaperMetadata.

    HF wraps the actual paper either as ``entry['paper']`` (newer schema) or
    flat (older schema). We accept both.
    """
    paper = entry.get("paper") if isinstance(entry.get("paper"), dict) else entry
    arxiv_id = str(paper.get("id") or entry.get("id") or "").strip()
    if not arxiv_id:
        return None

    title = collapse_whitespace(paper.get("title") or entry.get("title") or "")
    abstract = collapse_whitespace(paper.get("summary") or paper.get("abstract") or "")
    authors = _coerce_authors(paper.get("authors") or entry.get("authors"))

    published = _parse_datetime(
        paper.get("publishedAt") or entry.get("publishedAt") or paper.get("submittedOn")
    )

    upvotes = paper.get("upvotes") or entry.get("upvotes") or 0
    num_comments = entry.get("numComments") or paper.get("numComments") or 0
    tldr_obj = paper.get("tldr") or entry.get("tldr")
    tldr_text = ""
    if isinstance(tldr_obj, dict):
        tldr_text = str(tldr_obj.get("text") or "")
    elif isinstance(tldr_obj, str):
        tldr_text = tldr_obj

    canonical_id = f"arxiv:{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    landing_url = f"https://huggingface.co/papers/{arxiv_id}"

    tags: list[str] = ["source:hf_daily"]
    if upvotes:
        tags.append(f"hf:upvotes={int(upvotes)}")
    if num_comments:
        tags.append(f"hf:comments={int(num_comments)}")
    if tldr_text:
        tags.append("hf:has_tldr")

    return PaperMetadata(
        canonical_id=canonical_id,
        source=PaperSource.HF_DAILY,
        source_id=arxiv_id,
        title=title,
        abstract=abstract or tldr_text,
        authors=authors,
        published_at=published,
        updated_at=None,
        pdf_url=pdf_url,
        landing_url=landing_url,
        categories=[],
        tags=tags,
    )


def parse_daily_papers_json(text: str) -> list[PaperMetadata]:
    """Parse a HF Daily Papers JSON payload into a list of PaperMetadata."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("[hf_daily] JSON decode failed: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("[hf_daily] expected list, got %s", type(data).__name__)
        return []
    out: list[PaperMetadata] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        meta = _entry_to_metadata(entry)
        if meta is not None:
            out.append(meta)
    return out


class HfDailySource(PaperSourceBase):
    name = "hf_daily"

    def __init__(
        self,
        *,
        offline_fixture: str | Path | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.offline_fixture = Path(offline_fixture) if offline_fixture else None
        self.timeout_seconds = timeout_seconds

    def _read_offline_fixture(self) -> str:
        assert self.offline_fixture is not None
        return self.offline_fixture.read_text(encoding="utf-8")

    def _fetch_remote(self, max_results: int) -> str:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.get(HF_DAILY_API, params={"limit": max_results})
            resp.raise_for_status()
            return resp.text

    def fetch(self, config: dict[str, Any]) -> list[PaperMetadata]:
        if self.offline_fixture is not None:
            text = self._read_offline_fixture()
            papers = parse_daily_papers_json(text)
            logger.info("[hf_daily] offline fixture yielded %d papers", len(papers))
            return papers

        cfg = config.get("hf_daily", config)
        if not cfg.get("enabled", False):
            return []

        max_results = int(cfg.get("max_results", 30))
        try:
            text = self._fetch_remote(max_results)
        except Exception as exc:
            logger.warning("[hf_daily] fetch failed: %s", exc)
            return []
        papers = parse_daily_papers_json(text)
        logger.info("[hf_daily] %d papers from API", len(papers))
        return papers
