"""Tests for the enrich pipeline stage + paper_enrichment table."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from paper_inbox.db import (
    get_enrichment,
    insert_feedback,
    session,
    upsert_paper,
)
from paper_inbox.models import (
    PaperMetadata,
    PaperSource,
    UserFeedback,
)
from paper_inbox.pipeline.enrich import (
    _hf_upvotes_from_tags,
    enrich_papers,
    enrichment_summary,
)
from paper_inbox.sources.semantic_scholar import S2Enrichment


class FakeS2Client:
    def __init__(self, results: dict[str, S2Enrichment]):
        self.results = results

    def fetch_many(self, canonical_ids: list[str]) -> dict[str, S2Enrichment]:
        return {cid: self.results[cid] for cid in canonical_ids if cid in self.results}


def _paper(canonical: str, title: str, *, tags: list[str] | None = None) -> PaperMetadata:
    return PaperMetadata(
        canonical_id=canonical,
        source=PaperSource.ARXIV,
        source_id=canonical.split(":", 1)[1],
        title=title,
        tags=tags or [],
    )


def test_hf_upvotes_extraction() -> None:
    assert _hf_upvotes_from_tags(["hf:upvotes=87", "source:hf_daily"]) == 87
    assert _hf_upvotes_from_tags(["source:arxiv"]) is None
    assert _hf_upvotes_from_tags([]) is None


def test_enrichment_summary_pretty() -> None:
    assert enrichment_summary(None) == ""
    text = enrichment_summary(
        {"citation_count": 12, "influential_citation_count": 3, "tldr": "x", "year": 2025}
    )
    assert "citations=12" in text
    assert "influential=3" in text


def test_enrich_papers_persists_s2_and_hf(tmp_path: Path) -> None:
    db = tmp_path / "x.sqlite"
    paper1 = _paper("arxiv:2604.00001", "Search-R1+", tags=["hf:upvotes=87"])
    paper2 = _paper("arxiv:2604.00002", "AnomalyVLM", tags=["source:arxiv"])

    fake = FakeS2Client(
        results={
            "arxiv:2604.00001": S2Enrichment(
                canonical_id="arxiv:2604.00001",
                citation_count=10,
                influential_citation_count=2,
                tldr="A search-augmented agent",
                year=2026,
                venue="NeurIPS",
            ),
        }
    )

    with session(db) as conn:
        pk1 = upsert_paper(conn, paper1)
        pk2 = upsert_paper(conn, paper2)
        enrich_papers(conn, [(pk1, paper1), (pk2, paper2)], s2_client=fake)

        e1 = get_enrichment(conn, pk1)
        e2 = get_enrichment(conn, pk2)

    assert e1 is not None
    assert e1["citation_count"] == 10
    assert e1["hf_upvotes"] == 87
    assert e1["tldr"] == "A search-augmented agent"

    assert e2 is not None
    # paper2 wasn't in fake results, but row still created with HF upvotes None
    assert e2["citation_count"] is None
    assert e2["hf_upvotes"] is None


def test_enrich_papers_skip_s2(tmp_path: Path) -> None:
    db = tmp_path / "y.sqlite"
    paper = _paper("arxiv:2604.00001", "x", tags=["hf:upvotes=42"])
    fake = FakeS2Client(results={})  # would be ignored anyway

    with session(db) as conn:
        pk = upsert_paper(conn, paper)
        enrich_papers(conn, [(pk, paper)], s2_client=fake, skip_semantic_scholar=True)
        e = get_enrichment(conn, pk)

    assert e is not None
    assert e["citation_count"] is None
    assert e["hf_upvotes"] == 42  # HF upvotes still persisted even with S2 off


def test_feedback_table_query_helper(tmp_path: Path) -> None:
    """Sanity: list_feedback returns rows joined to papers (used by signals)."""
    from paper_inbox.db import list_feedback

    db = tmp_path / "z.sqlite"
    paper = _paper("arxiv:1", "Tool Use Reinforcement Learning")
    with session(db) as conn:
        pk = upsert_paper(conn, paper)
        insert_feedback(
            conn, pk,
            UserFeedback(
                paper_id="arxiv:1",
                feedback="useful",
                created_at=datetime.now(UTC),
            ),
        )
        rows = list_feedback(conn)
    assert len(rows) == 1
    assert rows[0]["feedback"] == "useful"
    assert rows[0]["title"] == "Tool Use Reinforcement Learning"
