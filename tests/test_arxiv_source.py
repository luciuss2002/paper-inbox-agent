from __future__ import annotations

from pathlib import Path

from paper_inbox.models import PaperSource
from paper_inbox.sources.arxiv_source import ArxivSource, parse_atom_feed

FIXTURE = Path(__file__).parent / "fixtures" / "sample_arxiv_feed.xml"


def test_parse_atom_feed_yields_metadata() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    papers = parse_atom_feed(text)
    assert len(papers) == 4
    p = next(p for p in papers if p.source_id == "2604.00001")
    assert p.canonical_id == "arxiv:2604.00001"
    assert p.source == PaperSource.ARXIV
    assert "Search-R1+" in p.title
    assert p.pdf_url and p.pdf_url.endswith(".pdf")
    assert "cs.CL" in p.categories
    assert p.authors[0] == "Alice Liu"


def test_arxiv_source_offline_fixture_returns_papers() -> None:
    src = ArxivSource(offline_fixture=str(FIXTURE))
    out = src.fetch({"arxiv": {"enabled": True}})
    assert len(out) == 4
