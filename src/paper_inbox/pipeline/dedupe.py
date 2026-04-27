"""Dedupe candidate papers, merging cross-source signals when possible."""

from __future__ import annotations

from paper_inbox.models import PaperMetadata
from paper_inbox.utils.hashing import title_hash


def _merge_into(primary: PaperMetadata, extra: PaperMetadata) -> PaperMetadata:
    """Merge tags / authors / categories from ``extra`` into ``primary``.

    Used when the same canonical_id is seen from multiple sources (e.g. the
    same arXiv paper surfaced via both ArxivSource and HfDailySource). The
    primary entry wins on the core fields; only enriching collections are
    unioned so downstream signals (HF upvotes, extra categories) survive.
    """
    merged_tags = list(primary.tags)
    for t in extra.tags:
        if t not in merged_tags:
            merged_tags.append(t)
    merged_categories = list(primary.categories)
    for c in extra.categories:
        if c not in merged_categories:
            merged_categories.append(c)
    merged_authors = primary.authors or extra.authors

    return primary.model_copy(
        update={
            "tags": merged_tags,
            "categories": merged_categories,
            "authors": merged_authors,
            "abstract": primary.abstract or extra.abstract,
            "pdf_url": primary.pdf_url or extra.pdf_url,
            "landing_url": primary.landing_url or extra.landing_url,
        }
    )


def dedupe_papers(papers: list[PaperMetadata]) -> list[PaperMetadata]:
    """Collapse duplicates by canonical_id, then by normalized title hash.

    For ``arxiv:*`` canonical ids we always trust the id (they are the
    authoritative key across sources). When a duplicate is seen we *merge*
    tags / categories / authors so signals from multiple sources accumulate
    rather than getting dropped on first-seen.
    """
    by_id: dict[str, int] = {}
    by_hash: dict[str, int] = {}
    out: list[PaperMetadata] = []

    for p in papers:
        cid = p.canonical_id
        h = title_hash(p.title)

        existing_idx: int | None = None
        if cid and cid in by_id:
            existing_idx = by_id[cid]
        elif not cid.startswith("arxiv:") and h in by_hash:
            existing_idx = by_hash[h]

        if existing_idx is not None:
            out[existing_idx] = _merge_into(out[existing_idx], p)
            continue

        out.append(p)
        idx = len(out) - 1
        if cid:
            by_id[cid] = idx
        by_hash[h] = idx

    return out
