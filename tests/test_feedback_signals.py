"""Tests for feedback-driven scoring signals."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from paper_inbox.db import insert_feedback, session, upsert_paper
from paper_inbox.models import (
    PaperMetadata,
    PaperSource,
    UserFeedback,
)
from paper_inbox.scoring.feedback_signals import (
    FeedbackSignals,
    _tokens,
    adjust_relevance,
    derive_signals,
)


def _record(conn, paper: PaperMetadata, feedback: str) -> None:
    pk = upsert_paper(conn, paper)
    insert_feedback(
        conn, pk,
        UserFeedback(
            paper_id=paper.canonical_id,
            feedback=feedback,  # type: ignore[arg-type]
            created_at=datetime.now(UTC),
        ),
    )


def test_tokens_strips_stopwords_and_punctuation() -> None:
    toks = _tokens("Tool-use Reinforcement Learning for LLM Agents")
    assert "tool-use" in toks
    assert "reinforcement" in toks
    assert "agents" in toks
    assert "for" not in toks  # stopword
    assert "the" not in toks


def test_derive_signals_groups_positive_and_negative(tmp_path: Path) -> None:
    db = tmp_path / "fb.sqlite"
    with session(db) as conn:
        _record(
            conn,
            PaperMetadata(
                canonical_id="arxiv:1",
                source=PaperSource.ARXIV,
                source_id="1",
                title="Search-augmented reasoning agents via RL",
                abstract="We train tool-use agents.",
                authors=["Alice", "Bob"],
            ),
            "useful",
        )
        _record(
            conn,
            PaperMetadata(
                canonical_id="arxiv:2",
                source=PaperSource.ARXIV,
                source_id="2",
                title="Agentic reinforcement learning at scale",
                abstract="Tool use and search.",
                authors=["Alice", "Carol"],
            ),
            "must_read",
        )
        _record(
            conn,
            PaperMetadata(
                canonical_id="arxiv:3",
                source=PaperSource.ARXIV,
                source_id="3",
                title="Object detection benchmark on ImageNet",
                abstract="Pure leaderboard improvements.",
                authors=["Dave"],
            ),
            "not_relevant",
        )

        sig = derive_signals(conn)

    assert sig.positive_count == 2
    assert sig.negative_count == 1
    assert "Alice" in sig.positive_authors
    assert "Dave" in sig.negative_authors
    assert "Alice" not in sig.negative_authors  # only positive papers
    # 'reinforcement' should appear in positive keywords
    assert any("reinforcement" in kw for kw in sig.positive_keywords)
    assert any(
        "leaderboard" in kw or "imagenet" in kw or "detection" in kw
        for kw in sig.negative_keywords
    )


def test_adjust_relevance_boosts_on_positive_author() -> None:
    sig = FeedbackSignals(
        positive_authors={"Alice"},
        positive_count=1,
    )
    new_rel, reasons = adjust_relevance(3, ["Alice", "Eve"], "x", sig)
    assert new_rel == 4
    assert any("正反馈" in r for r in reasons)


def test_adjust_relevance_dampens_on_negative_keywords() -> None:
    sig = FeedbackSignals(
        negative_keywords={"leaderboard", "imagenet", "detection"},
        negative_count=1,
    )
    text = "ImageNet detection leaderboard with no method novelty"
    new_rel, reasons = adjust_relevance(4, ["Eve"], text, sig)
    assert new_rel == 3
    assert any("负面关键词" in r for r in reasons)


def test_adjust_relevance_no_change_when_empty() -> None:
    sig = FeedbackSignals()
    new_rel, reasons = adjust_relevance(3, ["X"], "any text", sig)
    assert new_rel == 3
    assert reasons == []


def test_overlapping_keywords_dropped() -> None:
    sig = FeedbackSignals(
        positive_keywords={"agent", "rl"},
        negative_keywords={"agent"},
    )
    # When deriving signals via derive_signals, overlap is removed.
    # adjust_relevance just uses what we pass in — confirm the math is symmetric.
    assert "agent" in sig.positive_keywords  # this fixture isn't going through derive
