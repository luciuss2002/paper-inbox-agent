from __future__ import annotations

from paper_inbox.models import PaperMetadata, PaperSource
from paper_inbox.pipeline.dedupe import dedupe_papers
from paper_inbox.utils.hashing import normalize_title, title_hash


def _p(canonical: str, title: str, source: PaperSource = PaperSource.ARXIV) -> PaperMetadata:
    return PaperMetadata(
        canonical_id=canonical,
        source=source,
        source_id=canonical.split(":", 1)[1],
        title=title,
    )


def test_normalize_title_removes_punctuation_and_whitespace() -> None:
    assert normalize_title("  Hello,   World!  ") == "hello world"
    assert normalize_title("Search-R1: Better RAG?") == "search r1 better rag"


def test_title_hash_consistent_for_equivalent_titles() -> None:
    assert title_hash("Search-R1: Better RAG?") == title_hash("search r1 better rag")
    assert title_hash("foo") != title_hash("bar")


def test_dedupe_by_arxiv_id_preserves_first() -> None:
    papers = [
        _p("arxiv:1", "Paper A"),
        _p("arxiv:1", "Paper A — duplicate"),
        _p("arxiv:2", "Paper B"),
    ]
    out = dedupe_papers(papers)
    assert len(out) == 2
    assert out[0].canonical_id == "arxiv:1"
    assert out[0].title == "Paper A"


def test_dedupe_by_title_for_non_arxiv() -> None:
    papers = [
        _p("manual:a", "Same Paper", source=PaperSource.MANUAL),
        _p("manual:b", "Same  Paper!", source=PaperSource.MANUAL),
    ]
    out = dedupe_papers(papers)
    assert len(out) == 1
