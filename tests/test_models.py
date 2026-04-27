from __future__ import annotations

import pytest
from pydantic import ValidationError

from paper_inbox.models import (
    PaperBucket,
    PaperMetadata,
    PaperSource,
    TriageScore,
)


def test_paper_metadata_defaults() -> None:
    m = PaperMetadata(
        canonical_id="arxiv:2501.12345",
        source=PaperSource.ARXIV,
        source_id="2501.12345",
        title="A test paper",
    )
    assert m.authors == []
    assert m.categories == []
    assert m.tags == []
    assert m.abstract == ""


def test_triage_score_validates_range() -> None:
    with pytest.raises(ValidationError):
        TriageScore(
            relevance_to_user=6,
            novelty=3,
            practicality=3,
            experiment_strength=3,
            reproducibility_signal=3,
            trend_signal=3,
            final_priority=80,
            bucket=PaperBucket.SKIM,
            reasons=["x"],
            recommended_action="read",
        )


def test_triage_score_bucket_enum() -> None:
    s = TriageScore(
        relevance_to_user=5,
        novelty=4,
        practicality=4,
        experiment_strength=4,
        reproducibility_signal=4,
        trend_signal=4,
        final_priority=82,
        bucket=PaperBucket.SKIM,
        reasons=["a", "b"],
        recommended_action="skim quickly",
    )
    assert s.bucket == PaperBucket.SKIM
