"""Triage stage: render prompt, call LLM, parse JSON, score, persist."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from jinja2 import Template

from paper_inbox.db import insert_triage_score, upsert_paper
from paper_inbox.llm.base import LLMClient
from paper_inbox.models import PaperMetadata, TriageScore
from paper_inbox.scoring import ResearchProfile, compute_triage_score

logger = logging.getLogger(__name__)


PROMPT_PATH = Path("prompts/triage_prompt.md")


def _render_prompt(template_text: str, *, paper: PaperMetadata, profile: ResearchProfile) -> str:
    tpl = Template(template_text)
    return tpl.render(
        research_profile=json.dumps(profile.raw, ensure_ascii=False, indent=2),
        title=paper.title,
        authors=", ".join(paper.authors),
        abstract=paper.abstract,
        categories=", ".join(paper.categories),
        source=paper.source.value,
        published_at=paper.published_at.isoformat() if paper.published_at else "",
    )


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from LLM output.

    Strips fenced ```json blocks and falls back to the largest {...} substring.
    """
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


async def triage_paper(
    llm: LLMClient,
    paper: PaperMetadata,
    profile: ResearchProfile,
    *,
    model: str,
    temperature: float = 0.2,
    prompt_template: str | None = None,
) -> TriageScore | None:
    template_text = prompt_template if prompt_template is not None else PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _render_prompt(template_text, paper=paper, profile=profile)

    try:
        raw = await _call_llm_json(llm, prompt=prompt, model=model, temperature=temperature)
    except Exception as exc:
        logger.warning(
            "[triage] LLM JSON parse failed for %s — %s", paper.canonical_id, exc
        )
        return None

    try:
        return compute_triage_score(raw, paper=paper, profile=profile)
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "[triage] invalid score schema for %s — %s", paper.canonical_id, exc
        )
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
) -> list[tuple[int, PaperMetadata, TriageScore]]:
    """Run triage on all papers; persist scores; return (paper_id, paper, score) triples."""
    if prompt_template is None and PROMPT_PATH.exists():
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    out: list[tuple[int, PaperMetadata, TriageScore]] = []
    for paper in papers:
        paper_id = upsert_paper(conn, paper)
        score = await triage_paper(
            llm,
            paper,
            profile,
            model=model,
            temperature=temperature,
            prompt_template=prompt_template,
        )
        if score is None:
            continue
        insert_triage_score(conn, paper_id, run_date, score)
        out.append((paper_id, paper, score))
    conn.commit()
    logger.info("[triage] scored %d/%d papers", len(out), len(papers))
    return out
