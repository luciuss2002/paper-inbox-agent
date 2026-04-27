"""Fast-read stage: render fast-reader prompt, call LLM, persist Markdown brief."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Template

from paper_inbox.db import insert_brief
from paper_inbox.llm.base import LLMClient
from paper_inbox.models import PaperBrief, PaperMetadata, TriageScore
from paper_inbox.scoring import ResearchProfile
from paper_inbox.storage.artifacts import brief_path, report_brief_path
from paper_inbox.utils.text import truncate_for_prompt

logger = logging.getLogger(__name__)


PROMPT_PATH = Path("prompts/fast_reader_prompt.md")


def _render_prompt(
    template_text: str,
    *,
    paper: PaperMetadata,
    score: TriageScore,
    profile: ResearchProfile,
    paper_text: str,
) -> str:
    tpl = Template(template_text)
    return tpl.render(
        research_profile=json.dumps(profile.raw, ensure_ascii=False, indent=2),
        title=paper.title,
        authors=", ".join(paper.authors),
        abstract=paper.abstract,
        triage_score=json.dumps(score.model_dump(mode="json"), ensure_ascii=False, indent=2),
        paper_text=truncate_for_prompt(paper_text),
    )


def _shallow_brief_markdown(
    paper: PaperMetadata, score: TriageScore, *, reason: str
) -> str:
    return f"""# {paper.title}

> 注意：{reason}，本 brief 仅基于 title/abstract。

## 0. Verdict

优先级：{score.bucket.value}

## 1. 摘要

{paper.abstract}

## 2. Triage 评分

- relevance_to_user: {score.relevance_to_user}
- novelty: {score.novelty}
- practicality: {score.practicality}
- experiment_strength: {score.experiment_strength}
- reproducibility_signal: {score.reproducibility_signal}
- trend_signal: {score.trend_signal}
- final_priority: {score.final_priority}/100

## 3. Triage 理由

{chr(10).join(f"- {r}" for r in score.reasons) or "- (none)"}

## 4. 建议动作

{score.recommended_action or "(none)"}
"""


async def fast_read_paper(
    conn: sqlite3.Connection,
    llm: LLMClient,
    paper_id: int,
    paper: PaperMetadata,
    score: TriageScore,
    profile: ResearchProfile,
    *,
    run_date: str,
    model: str,
    temperature: float = 0.2,
    paper_text: str | None,
    briefs_dir: Path,
    reports_dir: Path,
    prompt_template: str | None = None,
) -> Path | None:
    """Generate a Markdown brief for a single paper. Returns the report-side path."""
    if paper_text and paper_text.strip():
        if prompt_template is None:
            prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        prompt = _render_prompt(
            prompt_template,
            paper=paper,
            score=score,
            profile=profile,
            paper_text=paper_text,
        )
        try:
            resp = await llm.complete(prompt, model=model, temperature=temperature)
            markdown = resp.text.strip()
            llm_model = resp.model
        except Exception as exc:
            logger.warning("[fast_read] LLM call failed for %s: %s", paper.canonical_id, exc)
            markdown = _shallow_brief_markdown(paper, score, reason="LLM 调用失败")
            llm_model = None
    else:
        markdown = _shallow_brief_markdown(paper, score, reason="PDF 解析失败")
        llm_model = None

    brief = PaperBrief(
        paper_id=paper.canonical_id,
        verdict="",
        priority=score.final_priority,
        bucket=score.bucket,
        thirty_second_summary="",
        three_minute_summary="",
        problem="",
        key_idea="",
        method="",
        experiments="",
        main_takeaways=[],
        relation_to_user_interests={},
        key_claims=[],
        experiment_credibility="",
        reusable_ideas=[],
        open_questions=[],
        related_work=[],
        generated_at=datetime.now(UTC),
    )

    # Always write to briefs_dir/<run_date>/<paper>.md
    bp = brief_path(briefs_dir, paper.canonical_id, run_date)
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(markdown, encoding="utf-8")

    # And mirror into reports_dir/<run_date>/<bucket>/<paper>.md
    rp = report_brief_path(reports_dir, run_date, score.bucket.value, paper.canonical_id)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(markdown, encoding="utf-8")

    insert_brief(conn, paper_id, run_date, markdown, brief=brief, model=llm_model)
    conn.commit()
    return rp
