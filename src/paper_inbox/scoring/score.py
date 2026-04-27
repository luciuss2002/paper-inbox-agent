"""Priority computation, bucket assignment, and rule overrides."""

from __future__ import annotations

from typing import Any

from paper_inbox.models import PaperBucket, PaperMetadata, TriageScore
from paper_inbox.scoring.profile_match import ResearchProfile, detect_low_interest

DEFAULT_WEIGHTS: dict[str, float] = {
    "relevance_to_user": 0.30,
    "novelty": 0.20,
    "practicality": 0.15,
    "experiment_strength": 0.15,
    "reproducibility_signal": 0.10,
    "trend_signal": 0.10,
}


def _normalize(x: int) -> float:
    """Map a 1-5 score onto [0, 1]."""
    return (x - 1) / 4


def compute_final_priority(scores: dict[str, int], weights: dict[str, float] | None = None) -> int:
    """Compute the final 0-100 priority from per-axis 1-5 scores."""
    w = weights or DEFAULT_WEIGHTS
    total = 0.0
    for k, weight in w.items():
        total += weight * _normalize(int(scores[k]))
    return round(100 * total)


def bucket_for_priority(priority: int) -> PaperBucket:
    if priority >= 90:
        return PaperBucket.MUST_READ
    if priority >= 70:
        return PaperBucket.SKIM
    if priority >= 50:
        return PaperBucket.ARCHIVE
    return PaperBucket.IGNORE


_BUCKET_LEVEL = {
    PaperBucket.IGNORE: 0,
    PaperBucket.ARCHIVE: 1,
    PaperBucket.SKIM: 2,
    PaperBucket.MUST_READ: 3,
}
_LEVEL_BUCKET = {v: k for k, v in _BUCKET_LEVEL.items()}


def _at_least(current: PaperBucket, floor: PaperBucket) -> PaperBucket:
    return _LEVEL_BUCKET[max(_BUCKET_LEVEL[current], _BUCKET_LEVEL[floor])]


def _at_most(current: PaperBucket, ceiling: PaperBucket) -> PaperBucket:
    return _LEVEL_BUCKET[min(_BUCKET_LEVEL[current], _BUCKET_LEVEL[ceiling])]


def _downgrade(bucket: PaperBucket) -> PaperBucket:
    return _LEVEL_BUCKET[max(0, _BUCKET_LEVEL[bucket] - 1)]


def apply_bucket_overrides(
    bucket: PaperBucket,
    scores: dict[str, int],
    *,
    paper: PaperMetadata | None = None,
    profile: ResearchProfile | None = None,
    has_code_signal: bool | None = None,
) -> PaperBucket:
    """Apply rule overrides described in the design spec section 7.3."""
    relevance = scores.get("relevance_to_user", 0)
    novelty = scores.get("novelty", 0)
    practicality = scores.get("practicality", 0)
    repro = scores.get("reproducibility_signal", 0)

    if relevance == 5 and novelty >= 4:
        bucket = _at_least(bucket, PaperBucket.SKIM)

    code_signal = has_code_signal if has_code_signal is not None else (repro >= 4)
    if code_signal and relevance >= 4:
        bucket = _at_least(bucket, PaperBucket.SKIM)

    if novelty <= 2 and practicality <= 2:
        bucket = _at_most(bucket, PaperBucket.ARCHIVE)

    if profile is not None and paper is not None:
        text = f"{paper.title}\n{paper.abstract}"
        if detect_low_interest(text, profile):
            bucket = _downgrade(bucket)

    return bucket


def compute_triage_score(
    raw: dict[str, Any],
    *,
    paper: PaperMetadata | None = None,
    profile: ResearchProfile | None = None,
) -> TriageScore:
    """Convert raw LLM output (per-axis 1-5 scores + reasons) into a full TriageScore."""
    axes = {
        "relevance_to_user": int(raw["relevance_to_user"]),
        "novelty": int(raw["novelty"]),
        "practicality": int(raw["practicality"]),
        "experiment_strength": int(raw["experiment_strength"]),
        "reproducibility_signal": int(raw["reproducibility_signal"]),
        "trend_signal": int(raw["trend_signal"]),
    }
    final = compute_final_priority(axes)
    bucket = bucket_for_priority(final)
    bucket = apply_bucket_overrides(bucket, axes, paper=paper, profile=profile)

    reasons = [str(r) for r in raw.get("reasons", []) if r]
    action = str(raw.get("recommended_action", "")).strip()

    return TriageScore(
        relevance_to_user=axes["relevance_to_user"],
        novelty=axes["novelty"],
        practicality=axes["practicality"],
        experiment_strength=axes["experiment_strength"],
        reproducibility_signal=axes["reproducibility_signal"],
        trend_signal=axes["trend_signal"],
        final_priority=final,
        bucket=bucket,
        reasons=reasons,
        recommended_action=action,
    )
