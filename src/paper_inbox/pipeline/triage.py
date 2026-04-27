"""Triage stage: render prompt, call LLM, parse JSON, score, persist."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from jinja2 import Template

from paper_inbox.db import (
    get_enrichment,
    insert_triage_score,
    upsert_paper,
)
from paper_inbox.llm.base import LLMClient
from paper_inbox.models import PaperMetadata, TriageScore
from paper_inbox.pipeline.enrich import enrichment_summary
from paper_inbox.scoring import (
    ResearchProfile,
    apply_bucket_overrides,
    bucket_for_priority,
    compute_final_priority,
)
from paper_inbox.scoring.feedback_signals import (
    FeedbackSignals,
    adjust_relevance,
    derive_signals,
)

logger = logging.getLogger(__name__)


PROMPT_PATH = Path("prompts/triage_prompt.md")


def _render_prompt(
    template_text: str,
    *,
    paper: PaperMetadata,
    profile: ResearchProfile,
    enrichment_text: str = "",
    feedback_block: str = "",
) -> str:
    tpl = Template(template_text)
    return tpl.render(
        research_profile=json.dumps(profile.raw, ensure_ascii=False, indent=2),
        title=paper.title,
        authors=", ".join(paper.authors),
        abstract=paper.abstract,
        categories=", ".join(paper.categories),
        source=paper.source.value,
        published_at=paper.published_at.isoformat() if paper.published_at else "",
        enrichment=enrichment_text,
        feedback_signals=feedback_block,
    )


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from LLM output."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


async def _call_llm_json(
    llm: LLMClient,
    *,
    prompt: str,
    model: str,
    temperature: float,
) -> dict[str, Any]:
    """Call the LLM, parse JSON, retry once with a stricter system note on failure."""
    resp = await llm.complete(prompt, model=model, temperature=temperature)
    try:
        return _extract_json(resp.text)
    except (json.JSONDecodeError, ValueError):
        retry_prompt = (
            prompt
            + "\n\n严格要求：只输出 JSON，不要 Markdown，不要任何额外解释。再试一次。"
        )
        resp2 = await llm.complete(retry_prompt, model=model, temperature=temperature)
        return _extract_json(resp2.text)


def _build_triage_score(
    raw: dict[str, Any],
    *,
    paper: PaperMetadata,
    profile: ResearchProfile,
    feedback_signals: FeedbackSignals | None,
) -> TriageScore:
    """Convert raw LLM output → TriageScore, applying feedback adjustments first.

    Order of operations:
      1. Take per-axis 1–5 scores from LLM
      2. Tweak ``relevance_to_user`` based on user feedback signals (±1)
      3. Compute weighted final priority
      4. Map to bucket, then apply rule overrides (low_interest etc.)
    """
    axes = {
        "relevance_to_user": int(raw["relevance_to_user"]),
        "novelty": int(raw["novelty"]),
        "practicality": int(raw["practicality"]),
        "experiment_strength": int(raw["experiment_strength"]),
        "reproducibility_signal": int(raw["reproducibility_signal"]),
        "trend_signal": int(raw["trend_signal"]),
    }

    extra_reasons: list[str] = []
    if feedback_signals is not None and not feedback_signals.is_empty():
        new_relevance, fb_reasons = adjust_relevance(
            axes["relevance_to_user"],
            paper.authors,
            f"{paper.title}\n{paper.abstract}",
            feedback_signals,
        )
        axes["relevance_to_user"] = new_relevance
        extra_reasons.extend(fb_reasons)

    final = compute_final_priority(axes)
    bucket = bucket_for_priority(final)
    bucket = apply_bucket_overrides(bucket, axes, paper=paper, profile=profile)

    reasons = [str(r) for r in raw.get("reasons", []) if r]
    reasons.extend(extra_reasons)
    action = str(raw.get("recommended_action", "")).strip()

    return TriageScore(
        relevance_to_user=axes["relevance_to_user"],
        novelty=axes["novelty"],
        practicality=axes["practicality"],
        experiment_strength=axes["experiment_strength"],
        reproducibility_signal=axes["reproducibility_signal"],
        trend_signal=axes["trend_signal"],
        final_priority=final,
        bucket=bucket,
        reasons=reasons,
        recommended_action=action,
    )


async def triage_paper(
    llm: LLMClient,
    paper: PaperMetadata,
    profile: ResearchProfile,
    *,
    model: str,
    temperature: float = 0.2,
    prompt_template: str | None = None,
    enrichment_text: str = "",
    feedback_signals: FeedbackSignals | None = None,
) -> TriageScore | None:
    template_text = (
        prompt_template
        if prompt_template is not None
        else PROMPT_PATH.read_text(encoding="utf-8")
    )
    feedback_block = (
        feedback_signals.to_prompt_block() if feedback_signals is not None else ""
    )
    prompt = _render_prompt(
        template_text,
        paper=paper,
        profile=profile,
        enrichment_text=enrichment_text,
        feedback_block=feedback_block,
    )

    try:
        raw = await _call_llm_json(llm, prompt=prompt, model=model, temperature=temperature)
    except Exception as exc:
        logger.warning("[triage] LLM JSON parse failed for %s — %s", paper.canonical_id, exc)
        return None

    try:
        return _build_triage_score(
            raw, paper=paper, profile=profile, feedback_signals=feedback_signals
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("[triage] invalid score schema for %s — %s", paper.canonical_id, exc)
        return None


async def run_triage(
    conn: sqlite3.Connection,
    llm: LLMClient,
    papers: list[PaperMetadata],
    profile: ResearchProfile,
    *,
    run_date: str,
    model: str,
    temperature: float = 0.2,
    prompt_template: str | None = None,
    use_feedback_signals: bool = True,
) -> list[tuple[int, PaperMetadata, TriageScore]]:
    """Run triage on all papers; persist scores; return (paper_id, paper, score) triples."""
    if prompt_template is None and PROMPT_PATH.exists():
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    feedback_signals = derive_signals(conn) if use_feedback_signals else None
    if feedback_signals is not None and not feedback_signals.is_empty():
        logger.info(
            "[triage] feedback signals: +%d / -%d (authors %d/%d, kw %d/%d)",
            feedback_signals.positive_count,
            feedback_signals.negative_count,
            len(feedback_signals.positive_authors),
            len(feedback_signals.negative_authors),
            len(feedback_signals.positive_keywords),
            len(feedback_signals.negative_keywords),
        )

    out: list[tuple[int, PaperMetadata, TriageScore]] = []
    for paper in papers:
        paper_id = upsert_paper(conn, paper)
        enrichment = get_enrichment(conn, paper_id)
        enrichment_text = enrichment_summary(enrichment) if enrichment else ""
        score = await triage_paper(
            llm,
            paper,
            profile,
            model=model,
            temperature=temperature,
            prompt_template=prompt_template,
            enrichment_text=enrichment_text,
            feedback_signals=feedback_signals,
        )
        if score is None:
            continue
        insert_triage_score(conn, paper_id, run_date, score)
        out.append((paper_id, paper, score))
    conn.commit()
    logger.info("[triage] scored %d/%d papers", len(out), len(papers))
    return out
