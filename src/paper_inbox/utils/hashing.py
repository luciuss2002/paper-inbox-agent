"""Hashing helpers used for canonical IDs and dedup keys."""

from __future__ import annotations

import hashlib
import re

_PUNCT_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not title:
        return ""
    text = title.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def title_hash(title: str) -> str:
    """Stable short hash of a normalized title."""
    norm = normalize_title(title)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: str) -> str:
    """Return the sha256 hex digest of a file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
