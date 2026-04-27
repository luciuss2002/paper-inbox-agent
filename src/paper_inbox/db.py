"""SQLite repository helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from paper_inbox.migrations import init_database
from paper_inbox.models import (
    PaperBrief,
    PaperBucket,
    PaperMetadata,
    PaperSource,
    TriageScore,
    UserFeedback,
)
from paper_inbox.utils.dates import now_utc_iso


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def session(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_database(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _isoformat(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def upsert_paper(conn: sqlite3.Connection, paper: PaperMetadata) -> int:
    """Insert or update a paper by canonical_id. Returns the paper row id."""
    now = now_utc_iso()
    row = conn.execute(
        "SELECT id FROM papers WHERE canonical_id = ?",
        (paper.canonical_id,),
    ).fetchone()

    payload = {
        "canonical_id": paper.canonical_id,
        "source": paper.source.value,
        "source_id": paper.source_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors_json": json.dumps(paper.authors, ensure_ascii=False),
        "published_at": _isoformat(paper.published_at),
        "updated_at": _isoformat(paper.updated_at),
        "pdf_url": paper.pdf_url,
        "landing_url": paper.landing_url,
        "categories_json": json.dumps(paper.categories, ensure_ascii=False),
        "tags_json": json.dumps(paper.tags, ensure_ascii=False),
    }

    if row is None:
        cols = list(payload.keys()) + ["created_at", "last_seen_at"]
        placeholders = ", ".join(["?"] * len(cols))
        values = list(payload.values()) + [now, now]
        cur = conn.execute(
            f"INSERT INTO papers ({', '.join(cols)}) VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)

    paper_id = int(row["id"])
    set_clause = ", ".join(f"{k} = ?" for k in payload) + ", last_seen_at = ?"
    values = list(payload.values()) + [now, paper_id]
    conn.execute(f"UPDATE papers SET {set_clause} WHERE id = ?", values)
    return paper_id


def get_paper_by_canonical_id(
    conn: sqlite3.Connection, canonical_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM papers WHERE canonical_id = ?",
        (canonical_id,),
    ).fetchone()
    return dict(row) if row else None


def insert_triage_score(
    conn: sqlite3.Connection,
    paper_id: int,
    run_date: str,
    score: TriageScore,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO triage_scores (
            paper_id, run_date,
            relevance_to_user, novelty, practicality,
            experiment_strength, reproducibility_signal, trend_signal,
            final_priority, bucket, reasons_json, recommended_action,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            paper_id,
            run_date,
            score.relevance_to_user,
            score.novelty,
            score.practicality,
            score.experiment_strength,
            score.reproducibility_signal,
            score.trend_signal,
            score.final_priority,
            score.bucket.value,
            json.dumps(score.reasons, ensure_ascii=False),
            score.recommended_action,
            now_utc_iso(),
        ),
    )
    return int(cur.lastrowid)


def insert_brief(
    conn: sqlite3.Connection,
    paper_id: int,
    run_date: str,
    brief_markdown: str,
    brief: PaperBrief | None = None,
    model: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO paper_briefs (
            paper_id, run_date, brief_markdown, brief_json, model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            paper_id,
            run_date,
            brief_markdown,
            brief.model_dump_json() if brief else None,
            model,
            now_utc_iso(),
        ),
    )
    return int(cur.lastrowid)


def insert_artifact(
    conn: sqlite3.Connection,
    paper_id: int,
    artifact_type: str,
    path: str,
    sha256: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO artifacts (paper_id, artifact_type, path, sha256, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (paper_id, artifact_type, path, sha256, now_utc_iso()),
    )
    return int(cur.lastrowid)


def get_artifact(
    conn: sqlite3.Connection, paper_id: int, artifact_type: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM artifacts
        WHERE paper_id = ? AND artifact_type = ?
        ORDER BY id DESC LIMIT 1
        """,
        (paper_id, artifact_type),
    ).fetchone()
    return dict(row) if row else None


def insert_feedback(conn: sqlite3.Connection, paper_id: int, feedback: UserFeedback) -> int:
    cur = conn.execute(
        """
        INSERT INTO user_feedback (paper_id, feedback, note, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            paper_id,
            feedback.feedback,
            feedback.note,
            feedback.created_at.isoformat(),
        ),
    )
    return int(cur.lastrowid)


def list_scores_for_date(
    conn: sqlite3.Connection,
    run_date: str,
    bucket: str | None = None,
    min_priority: int | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT s.*, p.canonical_id, p.title, p.abstract, p.authors_json,
               p.pdf_url, p.landing_url, p.categories_json, p.source, p.source_id,
               p.id AS paper_pk
        FROM triage_scores s
        JOIN papers p ON p.id = s.paper_id
        WHERE s.run_date = ?
    """
    params: list[Any] = [run_date]
    if bucket is not None:
        query += " AND s.bucket = ?"
        params.append(bucket)
    if min_priority is not None:
        query += " AND s.final_priority >= ?"
        params.append(min_priority)
    query += " ORDER BY s.final_priority DESC, s.id DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def list_briefs_for_date(conn: sqlite3.Connection, run_date: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT b.*, p.canonical_id, p.title
        FROM paper_briefs b
        JOIN papers p ON p.id = b.paper_id
        WHERE b.run_date = ?
        ORDER BY b.id DESC
        """,
        (run_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def metadata_from_row(row: dict[str, Any]) -> PaperMetadata:
    """Reconstruct a PaperMetadata from a `papers` table row."""
    authors = json.loads(row.get("authors_json") or "[]")
    categories = json.loads(row.get("categories_json") or "[]")
    tags = json.loads(row.get("tags_json") or "[]")
    published_at = row.get("published_at")
    updated_at = row.get("updated_at")
    return PaperMetadata(
        canonical_id=row["canonical_id"],
        source=PaperSource(row["source"]),
        source_id=row["source_id"],
        title=row["title"],
        abstract=row.get("abstract") or "",
        authors=authors,
        published_at=datetime.fromisoformat(published_at) if published_at else None,
        updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
        pdf_url=row.get("pdf_url"),
        landing_url=row.get("landing_url"),
        categories=categories,
        tags=tags,
    )


def score_from_row(row: dict[str, Any]) -> TriageScore:
    return TriageScore(
        relevance_to_user=row["relevance_to_user"],
        novelty=row["novelty"],
        practicality=row["practicality"],
        experiment_strength=row["experiment_strength"],
        reproducibility_signal=row["reproducibility_signal"],
        trend_signal=row["trend_signal"],
        final_priority=row["final_priority"],
        bucket=PaperBucket(row["bucket"]),
        reasons=json.loads(row.get("reasons_json") or "[]"),
        recommended_action=row.get("recommended_action") or "",
    )


def upsert_papers(
    conn: sqlite3.Connection, papers: Iterable[PaperMetadata]
) -> dict[str, int]:
    """Bulk upsert; returns a map of canonical_id -> paper id."""
    out: dict[str, int] = {}
    for p in papers:
        out[p.canonical_id] = upsert_paper(conn, p)
    return out


# ─── v0.2: enrichment + feedback aggregation ───────────────────────────────


def upsert_enrichment(
    conn: sqlite3.Connection,
    paper_id: int,
    *,
    citation_count: int | None = None,
    influential_citation_count: int | None = None,
    tldr: str | None = None,
    year: int | None = None,
    venue: str | None = None,
    hf_upvotes: int | None = None,
) -> None:
    """Insert or replace enrichment for a paper. Latest call wins."""
    conn.execute(
        """
        INSERT INTO paper_enrichment (
            paper_id, citation_count, influential_citation_count,
            tldr, year, venue, hf_upvotes, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
            citation_count = excluded.citation_count,
            influential_citation_count = excluded.influential_citation_count,
            tldr = excluded.tldr,
            year = excluded.year,
            venue = excluded.venue,
            hf_upvotes = COALESCE(excluded.hf_upvotes, paper_enrichment.hf_upvotes),
            fetched_at = excluded.fetched_at
        """,
        (
            paper_id,
            citation_count,
            influential_citation_count,
            tldr,
            year,
            venue,
            hf_upvotes,
            now_utc_iso(),
        ),
    )


def get_enrichment(
    conn: sqlite3.Connection, paper_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM paper_enrichment WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    return dict(row) if row else None


def list_feedback(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every feedback row joined to the paper for downstream aggregation."""
    rows = conn.execute(
        """
        SELECT f.*, p.canonical_id, p.title, p.abstract,
               p.authors_json, p.categories_json
        FROM user_feedback f
        JOIN papers p ON p.id = f.paper_id
        ORDER BY f.id DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]
