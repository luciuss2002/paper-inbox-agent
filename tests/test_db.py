from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from paper_inbox.db import (
    insert_artifact,
    insert_brief,
    insert_feedback,
    insert_triage_score,
    list_briefs_for_date,
    list_scores_for_date,
    metadata_from_row,
    score_from_row,
    session,
    upsert_paper,
)
from paper_inbox.models import (
    PaperBucket,
    PaperMetadata,
    PaperSource,
    TriageScore,
    UserFeedback,
)


def _paper() -> PaperMetadata:
    return PaperMetadata(
        canonical_id="arxiv:2604.00001",
        source=PaperSource.ARXIV,
        source_id="2604.00001",
        title="Sample paper",
        abstract="Hello world",
        authors=["Alice", "Bob"],
        categories=["cs.CL"],
    )


def _score() -> TriageScore:
    return TriageScore(
        relevance_to_user=5,
        novelty=4,
        practicality=4,
        experiment_strength=3,
        reproducibility_signal=4,
        trend_signal=5,
        final_priority=82,
        bucket=PaperBucket.SKIM,
        reasons=["topical"],
        recommended_action="skim",
    )


def test_upsert_paper_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "test.sqlite"
    with session(db) as conn:
        a = upsert_paper(conn, _paper())
        b = upsert_paper(conn, _paper())
        assert a == b


def test_score_and_brief_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "test.sqlite"
    with session(db) as conn:
        paper_id = upsert_paper(conn, _paper())
        insert_triage_score(conn, paper_id, "2026-04-27", _score())
        insert_brief(conn, paper_id, "2026-04-27", "# brief", model="mock")
        insert_artifact(conn, paper_id, "pdf", str(tmp_path / "x.pdf"))
        insert_feedback(
            conn,
            paper_id,
            UserFeedback(
                paper_id="arxiv:2604.00001",
                feedback="useful",
                created_at=datetime.now(UTC),
            ),
        )

        scores = list_scores_for_date(conn, "2026-04-27")
        briefs = list_briefs_for_date(conn, "2026-04-27")
    assert len(scores) == 1
    assert scores[0]["bucket"] == "Skim"
    assert len(briefs) == 1
    assert briefs[0]["brief_markdown"] == "# brief"


def test_metadata_and_score_from_row(tmp_path: Path) -> None:
    db = tmp_path / "test.sqlite"
    with session(db) as conn:
        paper_id = upsert_paper(conn, _paper())
        insert_triage_score(conn, paper_id, "2026-04-27", _score())
        rows = list_scores_for_date(conn, "2026-04-27")
    meta = metadata_from_row(rows[0])
    score = score_from_row(rows[0])
    assert meta.canonical_id == "arxiv:2604.00001"
    assert score.bucket == PaperBucket.SKIM
