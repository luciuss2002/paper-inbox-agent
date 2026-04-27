from __future__ import annotations

from pathlib import Path

from paper_inbox.models import PaperSource
from paper_inbox.sources.hf_daily_source import HfDailySource, parse_daily_papers_json

FIXTURE = Path(__file__).parent / "fixtures" / "sample_hf_daily.json"


def test_parse_daily_papers_json_extracts_metadata() -> None:
    papers = parse_daily_papers_json(FIXTURE.read_text(encoding="utf-8"))
    assert len(papers) == 3
    p = next(p for p in papers if p.source_id == "2604.00001")
    assert p.canonical_id == "arxiv:2604.00001"
    assert p.source == PaperSource.HF_DAILY
    assert "Search-R1+" in p.title
    assert p.authors == ["Alice Liu", "Bob Chen"]
    assert "hf:upvotes=87" in p.tags
    assert "hf:has_tldr" in p.tags
    assert "hf:comments=12" in p.tags
    assert p.pdf_url == "https://arxiv.org/pdf/2604.00001"


def test_offline_fixture_round_trip() -> None:
    src = HfDailySource(offline_fixture=str(FIXTURE))
    out = src.fetch({"hf_daily": {"enabled": True}})
    assert len(out) == 3
    titles = {p.title for p in out}
    assert any("AnomalyVLM" in t for t in titles)
    assert any("AgentBench-Pro" in t for t in titles)


def test_disabled_returns_empty() -> None:
    src = HfDailySource()  # no fixture, no offline mode
    assert src.fetch({"hf_daily": {"enabled": False}}) == []


def test_malformed_json_returns_empty() -> None:
    assert parse_daily_papers_json("{not json") == []
    assert parse_daily_papers_json('{"not": "a list"}') == []


def test_dedupe_merges_arxiv_and_hf_signals() -> None:
    """A paper appearing in both arXiv and HF Daily must merge tags/upvotes."""
    from paper_inbox.models import PaperMetadata
    from paper_inbox.pipeline.dedupe import dedupe_papers

    arxiv_one = PaperMetadata(
        canonical_id="arxiv:2604.00001",
        source=PaperSource.ARXIV,
        source_id="2604.00001",
        title="Search-R1+: Tool-Use RL",
        categories=["cs.CL"],
        tags=["source:arxiv"],
    )
    hf_one = PaperMetadata(
        canonical_id="arxiv:2604.00001",
        source=PaperSource.HF_DAILY,
        source_id="2604.00001",
        title="Search-R1+: Tool-Use RL",
        tags=["source:hf_daily", "hf:upvotes=87", "hf:has_tldr"],
    )
    out = dedupe_papers([arxiv_one, hf_one])
    assert len(out) == 1
    merged = out[0]
    assert "source:arxiv" in merged.tags
    assert "hf:upvotes=87" in merged.tags
    assert "hf:has_tldr" in merged.tags
    assert "cs.CL" in merged.categories
