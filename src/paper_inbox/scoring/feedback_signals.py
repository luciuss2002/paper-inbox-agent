"""Aggregate ``user_feedback`` rows into signals that bias future scoring.

The contract is intentionally simple so the user can reason about it:

* "useful" / "must_read" feedback → the paper's authors and salient title
  keywords go into a positive set.
* "not_relevant" / "archive" feedback → the same fields go into a negative
  set.
* "too_shallow" is informational only — it doesn't change scoring (the user
  liked the topic, just wanted more depth).

At triage time these sets are injected into the prompt as extra context, and a
small post-LLM rule bumps / dampens the ``relevance_to_user`` axis when a new
paper's authors or title clearly overlap with these sets.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from paper_inbox.db import list_feedback

POSITIVE_KINDS = {"useful", "must_read"}
NEGATIVE_KINDS = {"not_relevant", "archive"}

_STOPWORDS = {
    "the", "a", "an", "for", "of", "to", "and", "with", "from", "via",
    "on", "in", "by", "is", "are", "be", "we", "our", "this", "that",
    "using", "based", "study", "paper", "model", "models", "method",
    "methods", "approach", "approaches", "novel", "new", "towards",
    "learning", "deep", "neural", "network", "networks",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def _tokens(text: str) -> list[str]:
    if not text:
        return []
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if t.lower() not in _STOPWORDS and not t.isdigit()
    ]


@dataclass(frozen=True)
class FeedbackSignals:
    positive_authors: set[str] = field(default_factory=set)
    negative_authors: set[str] = field(default_factory=set)
    positive_keywords: set[str] = field(default_factory=set)
    negative_keywords: set[str] = field(default_factory=set)
    positive_count: int = 0
    negative_count: int = 0

    def is_empty(self) -> bool:
        return self.positive_count == 0 and self.negative_count == 0

    def to_prompt_block(self) -> str:
        """Render as a short prompt-friendly Markdown block, or empty string if no signals."""
        if self.is_empty():
            return ""
        lines = ["用户历史反馈（已标注论文中归纳出的偏好）："]
        if self.positive_authors:
            top = ", ".join(sorted(self.positive_authors)[:10])
            lines.append(f"  喜欢的作者：{top}")
        if self.positive_keywords:
            top = ", ".join(sorted(self.positive_keywords)[:15])
            lines.append(f"  喜欢的关键词：{top}")
        if self.negative_authors:
            top = ", ".join(sorted(self.negative_authors)[:10])
            lines.append(f"  不喜欢的作者：{top}")
        if self.negative_keywords:
            top = ", ".join(sorted(self.negative_keywords)[:15])
            lines.append(f"  不喜欢的关键词：{top}")
        return "\n".join(lines)


def derive_signals(
    conn: sqlite3.Connection,
    *,
    keyword_top_k: int = 30,
) -> FeedbackSignals:
    """Walk the feedback table and produce a FeedbackSignals snapshot."""
    rows = list_feedback(conn)
    pos_authors: set[str] = set()
    neg_authors: set[str] = set()
    pos_kw_counter: Counter[str] = Counter()
    neg_kw_counter: Counter[str] = Counter()
    pos_count = 0
    neg_count = 0

    for r in rows:
        kind = r.get("feedback")
        if kind not in POSITIVE_KINDS and kind not in NEGATIVE_KINDS:
            continue
        is_positive = kind in POSITIVE_KINDS
        if is_positive:
            pos_count += 1
        else:
            neg_count += 1

        authors = json.loads(r.get("authors_json") or "[]")
        title = r.get("title") or ""
        abstract = r.get("abstract") or ""
        kws = _tokens(title) + _tokens(abstract)[:20]

        if is_positive:
            pos_authors.update(a for a in authors if a)
            pos_kw_counter.update(kws)
        else:
            neg_authors.update(a for a in authors if a)
            neg_kw_counter.update(kws)

    pos_keywords = {kw for kw, _ in pos_kw_counter.most_common(keyword_top_k)}
    neg_keywords = {kw for kw, _ in neg_kw_counter.most_common(keyword_top_k)}

    # If a keyword shows up in both, drop it from both — it's not discriminative.
    overlap = pos_keywords & neg_keywords
    pos_keywords -= overlap
    neg_keywords -= overlap

    return FeedbackSignals(
        positive_authors=pos_authors,
        negative_authors=neg_authors,
        positive_keywords=pos_keywords,
        negative_keywords=neg_keywords,
        positive_count=pos_count,
        negative_count=neg_count,
    )


def adjust_relevance(
    relevance: int, paper_authors: list[str], paper_text: str, signals: FeedbackSignals
) -> tuple[int, list[str]]:
    """Tweak the ``relevance_to_user`` 1–5 score given accumulated feedback.

    Returns ``(new_relevance, reasons)`` where reasons is a short list of
    human-readable strings describing why the score moved (or didn't).
    """
    if signals.is_empty():
        return relevance, []

    reasons: list[str] = []
    score = int(relevance)
    text_lower = (paper_text or "").lower()

    pos_author_hit = any(a in signals.positive_authors for a in paper_authors)
    neg_author_hit = any(a in signals.negative_authors for a in paper_authors)
    pos_kw_hits = sum(1 for kw in signals.positive_keywords if kw in text_lower)
    neg_kw_hits = sum(1 for kw in signals.negative_keywords if kw in text_lower)

    if pos_author_hit:
        score = min(5, score + 1)
        reasons.append("作者出现在用户历史正反馈中（+1）")
    if neg_author_hit and not pos_author_hit:
        score = max(1, score - 1)
        reasons.append("作者出现在用户历史负反馈中（-1）")

    if pos_kw_hits >= 3:
        score = min(5, score + 1)
        reasons.append(f"标题/摘要命中 {pos_kw_hits} 个用户偏好关键词（+1）")
    if neg_kw_hits >= 3 and pos_kw_hits < neg_kw_hits:
        score = max(1, score - 1)
        reasons.append(f"标题/摘要命中 {neg_kw_hits} 个用户负面关键词（-1）")

    return score, reasons
