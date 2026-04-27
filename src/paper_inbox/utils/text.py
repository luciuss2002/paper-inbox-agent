"""Text helpers."""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")


def collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def truncate_for_prompt(text: str, head: int = 50_000, tail: int = 20_000) -> str:
    """Truncate long text by keeping head + tail with a marker in between."""
    if not text:
        return ""
    if len(text) <= head + tail:
        return text
    return (
        text[:head]
        + "\n\n... [truncated middle for length] ...\n\n"
        + text[-tail:]
    )


def safe_filename(name: str) -> str:
    """Convert an arbitrary identifier into a filesystem-safe slug."""
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", name or "")
    return s.strip("_") or "untitled"
