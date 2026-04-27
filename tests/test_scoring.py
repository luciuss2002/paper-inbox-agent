from __future__ import annotations

from paper_inbox.models import PaperBucket, PaperMetadata, PaperSource
from paper_inbox.scoring import (
    apply_bucket_overrides,
    bucket_for_priority,
    compute_final_priority,
    compute_triage_score,
    load_profile,
)
from paper_inbox.scoring.profile_match import detect_low_interest


def _profile():
    return load_profile(
        data={
            "user": {"name": "x"},
            "interests": {
                "high": ["agentic reinforcement learning", "tool use"],
                "medium": ["long context"],
                "low": ["pure computer vision"],
            },
            "favorite_papers": [],
            "negative_examples": [],
            "output_preferences": {},
        }
    )


def test_compute_final_priority_min_and_max() -> None:
    assert compute_final_priority(
        {
            "relevance_to_user": 1,
            "novelty": 1,
            "practicality": 1,
            "experiment_strength": 1,
            "reproducibility_signal": 1,
            "trend_signal": 1,
        }
    ) == 0
    assert compute_final_priority(
        {
            "relevance_to_user": 5,
            "novelty": 5,
            "practicality": 5,
            "experiment_strength": 5,
            "reproducibility_signal": 5,
            "trend_signal": 5,
        }
    ) == 100


def test_bucket_for_priority_thresholds() -> None:
    assert bucket_for_priority(95) == PaperBucket.MUST_READ
    assert bucket_for_priority(90) == PaperBucket.MUST_READ
    assert bucket_for_priority(89) == PaperBucket.SKIM
    assert bucket_for_priority(70) == PaperBucket.SKIM
    assert bucket_for_priority(69) == PaperBucket.ARCHIVE
    assert bucket_for_priority(50) == PaperBucket.ARCHIVE
    assert bucket_for_priority(49) == PaperBucket.IGNORE
    assert bucket_for_priority(0) == PaperBucket.IGNORE


def test_override_high_relevance_high_novelty_floors_skim() -> None:
    scores = {
        "relevance_to_user": 5,
        "novelty": 4,
        "practicality": 1,
        "experiment_strength": 1,
        "reproducibility_signal": 1,
        "trend_signal": 1,
    }
    result = apply_bucket_overrides(PaperBucket.IGNORE, scores)
    assert result == PaperBucket.SKIM


def test_override_low_novelty_caps_archive() -> None:
    scores = {
        "relevance_to_user": 5,
        "novelty": 2,
        "practicality": 2,
        "experiment_strength": 5,
        "reproducibility_signal": 5,
        "trend_signal": 5,
    }
    result = apply_bucket_overrides(PaperBucket.MUST_READ, scores)
    assert result == PaperBucket.ARCHIVE


def test_override_low_interest_downgrades() -> None:
    profile = _profile()
    paper = PaperMetadata(
        canonical_id="arxiv:1",
        source=PaperSource.ARXIV,
        source_id="1",
        title="Pure computer vision benchmark study",
        abstract="Object detection benchmark with no method novelty.",
    )
    scores = {
        "relevance_to_user": 4,
        "novelty": 4,
        "practicality": 4,
        "experiment_strength": 4,
        "reproducibility_signal": 4,
        "trend_signal": 4,
    }
    result = apply_bucket_overrides(
        PaperBucket.MUST_READ, scores, paper=paper, profile=profile
    )
    assert result == PaperBucket.SKIM


def test_detect_low_interest_skips_when_high_match() -> None:
    profile = _profile()
    text = "Pure computer vision via tool use"
    assert detect_low_interest(text, profile) is False


def test_compute_triage_score_full_path() -> None:
    profile = _profile()
    paper = PaperMetadata(
        canonical_id="arxiv:42",
        source=PaperSource.ARXIV,
        source_id="42",
        title="Tool use RL agents",
        abstract="...",
    )
    raw = {
        "relevance_to_user": 5,
        "novelty": 4,
        "practicality": 4,
        "experiment_strength": 4,
        "reproducibility_signal": 4,
        "trend_signal": 5,
        "reasons": ["matches user's tool-use focus"],
        "recommended_action": "read carefully",
    }
    score = compute_triage_score(raw, paper=paper, profile=profile)
    assert score.final_priority > 70
    assert score.bucket in (PaperBucket.SKIM, PaperBucket.MUST_READ)
    assert score.reasons == ["matches user's tool-use focus"]
