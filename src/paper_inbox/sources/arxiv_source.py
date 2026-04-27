"""arXiv Atom feed source."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser
import httpx

from paper_inbox.models import PaperMetadata, PaperSource
from paper_inbox.sources.base import PaperSourceBase
from paper_inbox.utils.text import collapse_whitespace

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"


def _normalize_arxiv_id(raw: str) -> str:
    """Strip arXiv URL or version suffix and return the bare paper id like '2501.12345'."""
    if not raw:
        return ""
    m = re.search(r"abs/([^/?#]+)", raw)
    base = m.group(1) if m else raw
    base = re.sub(r"v\d+$", "", base)
    return base


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # arXiv timestamps look like 2025-01-15T17:25:43Z
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _entry_to_metadata(entry: Any) -> PaperMetadata | None:
    raw_id = getattr(entry, "id", None) or entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
    if not raw_id:
        return None
    arxiv_id = _normalize_arxiv_id(raw_id)
    if not arxiv_id:
        return None

    title = collapse_whitespace(getattr(entry, "title", "") or "")
    abstract = collapse_whitespace(getattr(entry, "summary", "") or "")
    authors = []
    for a in getattr(entry, "authors", []) or []:
        name = getattr(a, "name", None) or (a.get("name") if isinstance(a, dict) else None)
        if name:
            authors.append(name)

    published_at = _parse_datetime(getattr(entry, "published", None))
    updated_at = _parse_datetime(getattr(entry, "updated", None))

    pdf_url: str | None = None
    landing_url: str | None = None
    for link in getattr(entry, "links", []) or []:
        href = getattr(link, "href", None) or (link.get("href") if isinstance(link, dict) else None)
        rel = getattr(link, "rel", None) or (link.get("rel") if isinstance(link, dict) else None)
        ltype = getattr(link, "type", None) or (link.get("type") if isinstance(link, dict) else None)
        title_attr = getattr(link, "title", None) or (link.get("title") if isinstance(link, dict) else None)
        if not href:
            continue
        if title_attr == "pdf" or (ltype and "pdf" in ltype):
            pdf_url = href
        elif rel == "alternate":
            landing_url = href

    if pdf_url is None and landing_url:
        pdf_url = landing_url.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in landing_url else None

    categories: list[str] = []
    primary = getattr(entry, "arxiv_primary_category", None)
    if primary:
        term = getattr(primary, "term", None) or (
            primary.get("term") if isinstance(primary, dict) else None
        )
        if term:
            categories.append(term)
    for tag in getattr(entry, "tags", []) or []:
        term = getattr(tag, "term", None) or (tag.get("term") if isinstance(tag, dict) else None)
        if term and term not in categories:
            categories.append(term)

    canonical_id = f"arxiv:{arxiv_id}"
    return PaperMetadata(
        canonical_id=canonical_id,
        source=PaperSource.ARXIV,
        source_id=arxiv_id,
        title=title,
        abstract=abstract,
        authors=authors,
        published_at=published_at,
        updated_at=updated_at,
        pdf_url=pdf_url,
        landing_url=landing_url,
        categories=categories,
    )


def parse_atom_feed(text: str) -> list[PaperMetadata]:
    """Parse an arXiv Atom feed string into PaperMetadata."""
    feed = feedparser.parse(text)
    out: list[PaperMetadata] = []
    for entry in feed.entries:
        meta = _entry_to_metadata(entry)
        if meta is not None:
            out.append(meta)
    return out


def build_search_query(categories: list[str], queries: list[str]) -> list[str]:
    """Return one or more API `search_query` strings — keywords + a category-only query."""
    out: list[str] = []
    for q in queries or []:
        kw = q.strip().replace('"', "")
        if kw:
            out.append(f'all:"{kw}"')
    if categories:
        cat_clause = " OR ".join(f"cat:{c}" for c in categories)
        out.append(cat_clause)
    return out


class ArxivSource(PaperSourceBase):
    name = "arxiv"

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

    def _fetch_remote(self, params: dict[str, Any]) -> str:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.get(ARXIV_API, params=params)
            resp.raise_for_status()
            return resp.text

    def fetch(self, config: dict[str, Any]) -> list[PaperMetadata]:
        if self.offline_fixture is not None:
            text = self._read_offline_fixture()
            papers = parse_atom_feed(text)
            logger.info("[arxiv] offline fixture yielded %d papers", len(papers))
            return papers

        cfg = config.get("arxiv", config)
        if not cfg.get("enabled", True):
            return []

        categories = list(cfg.get("categories", []))
        queries = list(cfg.get("queries", []))
        max_results = int(cfg.get("max_results_per_query", 25))
        sort_by = cfg.get("sort_by", "submittedDate")
        sort_order = cfg.get("sort_order", "descending")

        search_queries = build_search_query(categories, queries)
        seen: dict[str, PaperMetadata] = {}
        for sq in search_queries:
            params = {
                "search_query": sq,
                "max_results": max_results,
                "sortBy": sort_by,
                "sortOrder": sort_order,
            }
            try:
                text = self._fetch_remote(params)
            except Exception as exc:  # network failures must not break the run
                logger.warning("[arxiv] query failed: %s — %s", sq, exc)
                continue
            for paper in parse_atom_feed(text):
                seen.setdefault(paper.canonical_id, paper)
        papers = list(seen.values())
        logger.info("[arxiv] %d unique papers across %d queries", len(papers), len(search_queries))
        return papers
