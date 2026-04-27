"""Enrich papers with external metadata (Semantic Scholar + HF upvotes)."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from paper_inbox.db import upsert_enrichment
from paper_inbox.models import PaperMetadata
from paper_inbox.sources.semantic_scholar import S2Enrichment, SemanticScholarClient

logger = logging.getLogger(__name__)


_HF_UPVOTES_RE = re.compile(r"^hf:upvotes=(\d+)$")


def _hf_upvotes_from_tags(tags: list[str]) -> int | None:
    for t in tags or []:
        m = _HF_UPVOTES_RE.match(t)
        if m:
            return int(m.group(1))
    return None


def enrich_papers(
    conn: sqlite3.Connection,
    papers_with_ids: list[tuple[int, PaperMetadata]],
    *,
    s2_client: SemanticScholarClient | None = None,
    skip_semantic_scholar: bool = False,
) -> dict[str, S2Enrichment]:
    """Enrich a list of (paper_pk, PaperMetadata) tuples.

    For every paper we always persist the HF upvote count (extracted from the
    metadata tags). If ``skip_semantic_scholar`` is False and the paper has an
    ``arxiv:*`` canonical id, we also fetch S2 fields. Failures per paper are
    logged but never bubble up — enrichment is best-effort.
    """
    s2_results: dict[str, S2Enrichment] = {}

    s2_targets = [
        p.canonical_id
        for _pk, p in papers_with_ids
        if p.canonical_id.startswith("arxiv:")
    ]

    if not skip_semantic_scholar and s2_targets:
        client = s2_client or SemanticScholarClient()
        try:
            s2_results = client.fetch_many(s2_targets)
        except Exception as exc:
            logger.warning("[enrich] semantic scholar batch failed: %s", exc)
            s2_results = {}

    for paper_id, paper in papers_with_ids:
        s2 = s2_results.get(paper.canonical_id)
        hf_upvotes = _hf_upvotes_from_tags(paper.tags)
        upsert_enrichment(
            conn,
            paper_id,
            citation_count=s2.citation_count if s2 else None,
            influential_citation_count=s2.influential_citation_count if s2 else None,
            tldr=s2.tldr if s2 else None,
            year=s2.year if s2 else None,
            venue=s2.venue if s2 else None,
            hf_upvotes=hf_upvotes,
        )
    conn.commit()
    return s2_results


def enrichment_summary(enrichment: dict[str, Any] | None) -> str:
    """Render an enrichment row as a short prompt-friendly string."""
    if not enrichment:
        return ""
    bits: list[str] = []
    if enrichment.get("citation_count") is not None:
        bits.append(f"citations={enrichment['citation_count']}")
    if enrichment.get("influential_citation_count") is not None:
        bits.append(f"influential={enrichment['influential_citation_count']}")
    if enrichment.get("year"):
        bits.append(f"year={enrichment['year']}")
    if enrichment.get("venue"):
        bits.append(f"venue={enrichment['venue']}")
    if enrichment.get("hf_upvotes"):
        bits.append(f"hf_upvotes={enrichment['hf_upvotes']}")
    if enrichment.get("tldr"):
        bits.append(f"tldr={enrichment['tldr'][:200]}")
    return " · ".join(bits)
