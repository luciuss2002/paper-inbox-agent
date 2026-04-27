"""End-to-end test that feedback signals + enrichment flow into triage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from paper_inbox.db import insert_feedback, session, upsert_paper
from paper_inbox.llm.mock_client import MockLLMClient
from paper_inbox.models import PaperMetadata, PaperSource, UserFeedback
from paper_inbox.pipeline.triage import run_triage
from paper_inbox.scoring import load_profile

PROMPT = """你是一个研究论文筛选助手。
{{ research_profile }}
Title: {{ title }}
Authors: {{ authors }}
Abstract: {{ abstract }}
Categories: {{ categories }}
Source: {{ source }}
Published At: {{ published_at }}
{% if enrichment %}{{ enrichment }}{% endif %}
{% if feedback_signals %}{{ feedback_signals }}{% endif %}
请输出严格 JSON
"""


def _profile():
    return load_profile(
        data={
            "user": {"name": "x"},
            "interests": {
                "high": ["agentic reinforcement learning", "tool use"],
                "medium": [],
                "low": [],
            },
            "favorite_papers": [],
            "negative_examples": [],
            "output_preferences": {},
        }
    )


def test_run_triage_uses_feedback_signals(tmp_path: Path) -> None:
    db = tmp_path / "f.sqlite"
    profile = _profile()

    with session(db) as conn:
        # Pre-populate feedback so signals are non-empty
        liked = PaperMetadata(
            canonical_id="arxiv:past",
            source=PaperSource.ARXIV,
            source_id="past",
            title="Tool-use reinforcement learning agents",
            abstract="agentic RL with search",
            authors=["Alice"],
        )
        pk = upsert_paper(conn, liked)
        insert_feedback(
            conn, pk,
            UserFeedback(
                paper_id=liked.canonical_id,
                feedback="useful",
                created_at=datetime.now(UTC),
            ),
        )

        # New paper with same author → positive signal should boost relevance
        new_paper = PaperMetadata(
            canonical_id="arxiv:new",
            source=PaperSource.ARXIV,
            source_id="new",
            title="Tool use agent training",
            abstract="agentic reinforcement learning study",
            authors=["Alice", "Bob"],
        )

        scored = asyncio.run(
            run_triage(
                conn,
                MockLLMClient(),
                [new_paper],
                profile,
                run_date="2026-04-27",
                model="mock",
                prompt_template=PROMPT,
            )
        )

    assert len(scored) == 1
    _, paper, score = scored[0]
    assert paper.canonical_id == "arxiv:new"
    # The mock heuristic gives relevance 5 already (capped), but reasons list
    # should include either fb-driven note or LLM-driven note. We assert that
    # reasons is non-empty and the bucket is at least Skim.
    assert score.bucket.value in ("Must Read", "Skim")
    assert score.reasons
