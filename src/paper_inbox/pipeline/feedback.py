"""User feedback persistence."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from paper_inbox.db import get_paper_by_canonical_id, insert_feedback
from paper_inbox.models import UserFeedback

logger = logging.getLogger(__name__)

FeedbackKind = Literal["useful", "not_relevant", "must_read", "too_shallow", "archive"]


def record_feedback(
    conn: sqlite3.Connection,
    *,
    canonical_id: str,
    feedback: FeedbackKind,
    note: str | None = None,
) -> int | None:
    """Insert a feedback row keyed by canonical_id (e.g. 'arxiv:2501.12345')."""
    row = get_paper_by_canonical_id(conn, canonical_id)
    if row is None:
        logger.warning("[feedback] unknown paper_id %s — not recording", canonical_id)
        return None
    fb = UserFeedback(
        paper_id=canonical_id,
        feedback=feedback,
        note=note,
        created_at=datetime.now(UTC),
    )
    fid = insert_feedback(conn, int(row["id"]), fb)
    conn.commit()
    return fid
