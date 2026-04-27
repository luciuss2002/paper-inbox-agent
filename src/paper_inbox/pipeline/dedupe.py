"""Dedupe candidate papers."""

from __future__ import annotations

from paper_inbox.models import PaperMetadata, PaperSource
from paper_inbox.utils.hashing import title_hash


def dedupe_papers(papers: list[PaperMetadata]) -> list[PaperMetadata]:
    """Dedupe by canonical_id first, then by normalized title hash.

    Preserves first-seen order.
    """
    seen_ids: set[str] = set()
    seen_title_hashes: set[str] = set()
    out: list[PaperMetadata] = []
    for p in papers:
        if p.source == PaperSource.ARXIV and p.canonical_id:
            if p.canonical_id in seen_ids:
                continue
            seen_ids.add(p.canonical_id)
            out.append(p)
            seen_title_hashes.add(title_hash(p.title))
            continue

        h = title_hash(p.title)
        if p.canonical_id in seen_ids or h in seen_title_hashes:
            continue
        if p.canonical_id:
            seen_ids.add(p.canonical_id)
        seen_title_hashes.add(h)
        out.append(p)
    return out
