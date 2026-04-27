"""Research profile loading and simple keyword matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Interests:
    high: list[str] = field(default_factory=list)
    medium: list[str] = field(default_factory=list)
    low: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchProfile:
    user_name: str
    interests: Interests
    favorite_papers: list[str]
    negative_examples: list[str]
    output_preferences: dict[str, Any]
    raw: dict[str, Any]


def load_profile(path: str | Path | None = None, *, data: dict[str, Any] | None = None) -> ResearchProfile:
    """Load a research profile from a yaml file or in-memory dict."""
    if data is None:
        if path is None:
            raise ValueError("Must provide either path or data")
        with Path(path).open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    user_name = (data.get("user") or {}).get("name", "Researcher")
    interests_raw = data.get("interests") or {}
    interests = Interests(
        high=list(interests_raw.get("high", [])),
        medium=list(interests_raw.get("medium", [])),
        low=list(interests_raw.get("low", [])),
    )
    return ResearchProfile(
        user_name=user_name,
        interests=interests,
        favorite_papers=list(data.get("favorite_papers", [])),
        negative_examples=list(data.get("negative_examples", [])),
        output_preferences=dict(data.get("output_preferences", {})),
        raw=data,
    )


def _matches_any(text: str, keywords: list[str]) -> bool:
    if not text or not keywords:
        return False
    haystack = text.lower()
    return any(kw.lower() in haystack for kw in keywords if kw)


def detect_low_interest(text: str, profile: ResearchProfile) -> bool:
    """Heuristic: is the paper title/abstract obviously aligned with the user's low interests
    while not matching any high interest?"""
    if _matches_any(text, profile.interests.low):
        return not _matches_any(text, profile.interests.high)
    return False
