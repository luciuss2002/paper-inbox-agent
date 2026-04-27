"""End-to-end daily pipeline orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from paper_inbox.db import session, upsert_paper
from paper_inbox.llm.base import LLMClient
from paper_inbox.models import PaperBucket, PaperMetadata
from paper_inbox.pipeline.collect import collect_papers
from paper_inbox.pipeline.dedupe import dedupe_papers
from paper_inbox.pipeline.enrich import enrich_papers
from paper_inbox.pipeline.fast_read import fast_read_paper
from paper_inbox.pipeline.pdf_fetch import fetch_pdf
from paper_inbox.pipeline.pdf_parse import parse_pdf
from paper_inbox.pipeline.report import generate_daily_report
from paper_inbox.pipeline.triage import run_triage
from paper_inbox.scoring import ResearchProfile
from paper_inbox.settings import Settings
from paper_inbox.storage.paths import RuntimePaths, ensure_paths

logger = logging.getLogger(__name__)


@dataclass
class DailyResult:
    run_date: str
    report_path: Path
    total_collected: int
    after_dedupe: int
    triaged: int
    briefs_generated: int
    enriched: int


async def run_daily_pipeline(
    *,
    run_date: str,
    settings: Settings,
    llm: LLMClient,
    profile: ResearchProfile,
    offline_fixture: str | Path | None = None,
    offline_hf_fixture: str | Path | None = None,
    skip_enrichment: bool = False,
) -> DailyResult:
    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)

    pipeline_cfg = settings.runtime.get("pipeline", {})
    llm_cfg = settings.runtime.get("llm", {})
    enrich_cfg = settings.runtime.get("enrichment", {})

    daily_cap = int(pipeline_cfg.get("daily_candidate_limit", 150))
    download_buckets = set(pipeline_cfg.get("download_pdf_for_buckets", ["Must Read", "Skim"]))
    max_pdf_to_read = int(pipeline_cfg.get("max_pdf_to_read_per_day", 8))
    min_priority_for_pdf = int(pipeline_cfg.get("min_priority_for_pdf_read", 70))
    timeout_seconds = float(settings.runtime.get("network", {}).get("timeout_seconds", 30))

    model_triage = str(llm_cfg.get("model_triage", "gpt-4o-mini"))
    model_reader = str(llm_cfg.get("model_reader", "gpt-4o-mini"))
    temperature = float(llm_cfg.get("temperature", 0.2))
    use_feedback_signals = bool(pipeline_cfg.get("use_feedback_signals", True))

    enrichment_enabled = (
        bool(enrich_cfg.get("semantic_scholar_enabled", True)) and not skip_enrichment
    )

    logger.info("[run_daily] %s — start", run_date)

    raw_papers = collect_papers(
        settings.sources,
        runtime_cfg=settings.runtime,
        offline_fixture=offline_fixture,
        offline_hf_fixture=offline_hf_fixture,
    )
    deduped = dedupe_papers(raw_papers)[:daily_cap]
    logger.info(
        "[run_daily] collected=%d deduped=%d", len(raw_papers), len(deduped)
    )

    enriched_count = 0

    with session(paths.db_path) as conn:
        upserts: list[tuple[int, PaperMetadata]] = []
        for p in deduped:
            paper_id = upsert_paper(conn, p)
            upserts.append((paper_id, p))
        conn.commit()

        if enrichment_enabled and upserts:
            try:
                results = enrich_papers(
                    conn,
                    upserts,
                    skip_semantic_scholar=False,
                )
                enriched_count = len(results)
            except Exception as exc:
                logger.warning("[run_daily] enrichment failed: %s", exc)
        elif upserts:
            # Still persist HF upvotes even when S2 is off
            try:
                enrich_papers(conn, upserts, skip_semantic_scholar=True)
            except Exception as exc:
                logger.warning("[run_daily] hf-only enrichment failed: %s", exc)

        scored = await run_triage(
            conn,
            llm,
            deduped,
            profile,
            run_date=run_date,
            model=model_triage,
            temperature=temperature,
            use_feedback_signals=use_feedback_signals,
        )

        candidates = [
            (paper_id, paper, score)
            for (paper_id, paper, score) in scored
            if score.bucket.value in download_buckets
            and score.final_priority >= min_priority_for_pdf
        ]
        candidates.sort(
            key=lambda t: (t[2].bucket != PaperBucket.MUST_READ, -t[2].final_priority)
        )
        candidates = candidates[:max_pdf_to_read]
        logger.info("[run_daily] selected %d papers for PDF reading", len(candidates))

        briefs = 0
        for paper_id, paper, score in candidates:
            pdf_file = fetch_pdf(
                conn,
                paper_id,
                paper,
                pdf_dir=paths.pdf_dir,
                timeout_seconds=timeout_seconds,
            )
            text: str | None = None
            if pdf_file is not None:
                parsed = parse_pdf(
                    conn,
                    paper_id,
                    paper.canonical_id,
                    pdf_file,
                    parsed_dir=paths.parsed_dir,
                )
                if parsed is not None:
                    text = parsed.read_text(encoding="utf-8", errors="ignore")
            await fast_read_paper(
                conn,
                llm,
                paper_id,
                paper,
                score,
                profile,
                run_date=run_date,
                model=model_reader,
                temperature=temperature,
                paper_text=text,
                briefs_dir=paths.briefs_dir,
                reports_dir=paths.reports_dir,
            )
            briefs += 1

        report_path = generate_daily_report(
            conn, run_date=run_date, reports_dir=paths.reports_dir
        )

    return DailyResult(
        run_date=run_date,
        report_path=report_path,
        total_collected=len(raw_papers),
        after_dedupe=len(deduped),
        triaged=len(scored),
        briefs_generated=briefs,
        enriched=enriched_count,
    )
