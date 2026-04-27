"""Pydantic models shared across the pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PaperSource(StrEnum):
    ARXIV = "arxiv"
    HF_DAILY = "hf_daily"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    MANUAL = "manual"


class PaperBucket(StrEnum):
    MUST_READ = "Must Read"
    SKIM = "Skim"
    ARCHIVE = "Archive"
    IGNORE = "Ignore"


class PaperMetadata(BaseModel):
    canonical_id: str
    source: PaperSource
    source_id: str
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    pdf_url: str | None = None
    landing_url: str | None = None
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TriageScore(BaseModel):
    relevance_to_user: int = Field(ge=1, le=5)
    novelty: int = Field(ge=1, le=5)
    practicality: int = Field(ge=1, le=5)
    experiment_strength: int = Field(ge=1, le=5)
    reproducibility_signal: int = Field(ge=1, le=5)
    trend_signal: int = Field(ge=1, le=5)
    final_priority: int = Field(ge=0, le=100)
    bucket: PaperBucket
    reasons: list[str] = Field(default_factory=list)
    recommended_action: str = ""


class PaperBrief(BaseModel):
    paper_id: str
    verdict: str = ""
    priority: int = 0
    bucket: PaperBucket = PaperBucket.IGNORE
    thirty_second_summary: str = ""
    three_minute_summary: str = ""
    problem: str = ""
    key_idea: str = ""
    method: str = ""
    experiments: str = ""
    main_takeaways: list[str] = Field(default_factory=list)
    relation_to_user_interests: dict[str, str] = Field(default_factory=dict)
    key_claims: list[dict[str, Any]] = Field(default_factory=list)
    experiment_credibility: str = ""
    reusable_ideas: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    related_work: list[str] = Field(default_factory=list)
    generated_at: datetime


class UserFeedback(BaseModel):
    paper_id: str
    feedback: Literal["useful", "not_relevant", "must_read", "too_shallow", "archive"]
    note: str | None = None
    created_at: datetime
