from __future__ import annotations

from pathlib import Path

from paper_inbox.db import insert_brief, insert_triage_score, session, upsert_paper
from paper_inbox.models import PaperBucket, PaperMetadata, PaperSource, TriageScore
from paper_inbox.pipeline.report import generate_daily_report


def test_generate_daily_report_renders_markdown(tmp_path: Path) -> None:
    db = tmp_path / "x.sqlite"
    reports_dir = tmp_path / "reports"

    with session(db) as conn:
        for i in range(2):
            paper = PaperMetadata(
                canonical_id=f"arxiv:2604.0000{i}",
                source=PaperSource.ARXIV,
                source_id=f"2604.0000{i}",
                title=f"Test paper {i}",
                abstract="abs",
                landing_url=f"http://arxiv.org/abs/2604.0000{i}",
            )
            paper_id = upsert_paper(conn, paper)
            score = TriageScore(
                relevance_to_user=5,
                novelty=5,
                practicality=5,
                experiment_strength=5,
                reproducibility_signal=5,
                trend_signal=5,
                final_priority=95,
                bucket=PaperBucket.MUST_READ,
                reasons=["very relevant"],
                recommended_action="read carefully",
            )
            insert_triage_score(conn, paper_id, "2026-04-27", score)
            insert_brief(conn, paper_id, "2026-04-27", f"# Brief {i}", model="mock")

        out = generate_daily_report(
            conn, run_date="2026-04-27", reports_dir=reports_dir
        )

    md = out.read_text(encoding="utf-8")
    assert "Daily Paper Inbox - 2026-04-27" in md
    assert "Must Read: 2" in md
    assert "Test paper 0" in md
    assert "Test paper 1" in md
