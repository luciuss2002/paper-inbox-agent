"""arXiv Atom feed source."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser
import httpx

from paper_inbox.models import PaperMetadata, PaperSource
from paper_inbox.sources.base import PaperSourceBase
from paper_inbox.utils.text import collapse_whitespace

logger = logging.getLogger(__name__)

# Use https directly: arXiv 301-redirects all http traffic, and httpx doesn't
# follow redirects by default.
ARXIV_API = "https://export.arxiv.org/api/query"

# arXiv API ToS: "Make no more than one request every three seconds." We honor
# this between queries so a 16-keyword config doesn't get the connection
# slammed shut. https://info.arxiv.org/help/api/tou.html
DEFAULT_QUERY_INTERVAL = 3.0


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
        query_interval: float = DEFAULT_QUERY_INTERVAL,
        retries: int = 2,
    ) -> None:
        self.offline_fixture = Path(offline_fixture) if offline_fixture else None
        self.timeout_seconds = timeout_seconds
        self.query_interval = query_interval
        self.retries = retries

    def _read_offline_fixture(self) -> str:
        assert self.offline_fixture is not None
        return self.offline_fixture.read_text(encoding="utf-8")

    def _fetch_remote(self, client: httpx.Client, params: dict[str, Any]) -> str:
        """One query with up to ``self.retries`` extra attempts on transport errors.

        arXiv occasionally drops the connection (`Server disconnected without
        sending a response`) when we're at the edge of their rate limit. A
        short backoff usually makes the next attempt succeed.
        """
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = client.get(ARXIV_API, params=params)
                resp.raise_for_status()
                return resp.text
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    backoff = self.query_interval * (attempt + 1)
                    logger.info(
                        "[arxiv] retry %d/%d for %s in %.1fs — %s",
                        attempt + 1,
                        self.retries,
                        params.get("search_query"),
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
        assert last_exc is not None
        raise last_exc

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
        with httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=True,  # arXiv may still 301 in some edge cases
            headers={"User-Agent": "paper-inbox-agent/0.2"},
        ) as client:
            for idx, sq in enumerate(search_queries):
                params = {
                    "search_query": sq,
                    "max_results": max_results,
                    "sortBy": sort_by,
                    "sortOrder": sort_order,
                }
                try:
                    text = self._fetch_remote(client, params)
                except Exception as exc:
                    logger.warning("[arxiv] query failed: %s — %s", sq, exc)
                    continue
                hits = parse_atom_feed(text)
                for paper in hits:
                    seen.setdefault(paper.canonical_id, paper)
                logger.info("[arxiv] %s → %d hits (running unique=%d)", sq, len(hits), len(seen))

                # Honor arXiv's 1-req-per-3s policy between queries.
                if idx < len(search_queries) - 1 and self.query_interval:
                    time.sleep(self.query_interval)

        papers = list(seen.values())
        logger.info("[arxiv] %d unique papers across %d queries", len(papers), len(search_queries))
        return papers
