"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from paper_inbox.db import (
    list_briefs_for_date,
    list_scores_for_date,
    session,
    upsert_paper,
)
from paper_inbox.llm.base import LLMClient
from paper_inbox.llm.mock_client import MockLLMClient
from paper_inbox.logging_config import configure_logging
from paper_inbox.pipeline.collect import collect_papers
from paper_inbox.pipeline.dedupe import dedupe_papers
from paper_inbox.pipeline.fast_read import fast_read_paper
from paper_inbox.pipeline.feedback import record_feedback
from paper_inbox.pipeline.pdf_fetch import fetch_pdf
from paper_inbox.pipeline.pdf_parse import parse_pdf
from paper_inbox.pipeline.report import generate_daily_report
from paper_inbox.pipeline.run_daily import run_daily_pipeline
from paper_inbox.pipeline.triage import run_triage
from paper_inbox.scoring import load_profile
from paper_inbox.settings import (
    DEFAULT_PROFILE_PATH,
    DEFAULT_RUNTIME_PATH,
    DEFAULT_SOURCES_PATH,
    init_default_configs,
    load_settings,
)
from paper_inbox.storage.paths import RuntimePaths, ensure_paths
from paper_inbox.utils.dates import today_iso

app = typer.Typer(
    add_completion=False,
    help="Paper Inbox Agent — daily research paper triage and fast-read.",
)

logger = logging.getLogger("paper_inbox")


def _build_llm(*, mock_llm: bool, runtime_cfg: dict) -> LLMClient:
    if mock_llm:
        return MockLLMClient()
    provider = (runtime_cfg.get("llm") or {}).get("provider", "openai").lower()
    if provider == "openai":
        from paper_inbox.llm.openai_client import OpenAIClient

        max_retries = int((runtime_cfg.get("llm") or {}).get("max_retries", 3))
        return OpenAIClient(max_retries=max_retries)
    raise typer.BadParameter(f"Unsupported llm.provider: {provider}")


def _load_all(
    profile: Path | None,
    sources: Path | None,
    runtime: Path | None,
):
    settings = load_settings(
        runtime_path=runtime,
        sources_path=sources,
        profile_path=profile,
    )
    research_profile = load_profile(data=settings.profile)
    return settings, research_profile


@app.command("init")
def cmd_init() -> None:
    """Initialise configs (copy *.example.yaml) and create the SQLite DB."""
    configure_logging()
    created = init_default_configs()
    if created:
        for p in created:
            typer.echo(f"created {p}")
    else:
        typer.echo("configs already present — nothing to copy.")

    try:
        settings = load_settings()
    except FileNotFoundError as exc:
        typer.echo(f"⚠️  {exc}")
        raise typer.Exit(code=1) from exc
    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)
    with session(paths.db_path):
        pass
    typer.echo(f"data dir: {paths.data_dir}")
    typer.echo(f"db path : {paths.db_path}")
    typer.echo("\nNext steps:")
    typer.echo(
        "  paper-inbox run-daily --mock-llm "
        "--offline-fixture tests/fixtures/sample_arxiv_feed.xml"
    )


@app.command("run-daily")
def cmd_run_daily(
    date: str = typer.Option(None, "--date", help="ISO date YYYY-MM-DD (default: today)"),
    profile: Path = typer.Option(DEFAULT_PROFILE_PATH, "--profile"),
    sources: Path = typer.Option(DEFAULT_SOURCES_PATH, "--sources"),
    runtime: Path = typer.Option(DEFAULT_RUNTIME_PATH, "--runtime"),
    mock_llm: bool = typer.Option(False, "--mock-llm"),
    offline_fixture: Path = typer.Option(None, "--offline-fixture"),
) -> None:
    """Run the full daily pipeline."""
    configure_logging()
    run_date = date or today_iso()
    settings, research_profile = _load_all(profile, sources, runtime)
    llm = _build_llm(mock_llm=mock_llm, runtime_cfg=settings.runtime)

    result = asyncio.run(
        run_daily_pipeline(
            run_date=run_date,
            settings=settings,
            llm=llm,
            profile=research_profile,
            offline_fixture=offline_fixture,
        )
    )

    typer.echo(f"\nDaily Inbox — {result.run_date}")
    typer.echo(f"  collected : {result.total_collected}")
    typer.echo(f"  deduped   : {result.after_dedupe}")
    typer.echo(f"  triaged   : {result.triaged}")
    typer.echo(f"  briefs    : {result.briefs_generated}")
    typer.echo(f"  report    : {result.report_path}")


@app.command("collect")
def cmd_collect(
    date: str = typer.Option(None, "--date"),
    profile: Path = typer.Option(DEFAULT_PROFILE_PATH, "--profile"),
    sources: Path = typer.Option(DEFAULT_SOURCES_PATH, "--sources"),
    runtime: Path = typer.Option(DEFAULT_RUNTIME_PATH, "--runtime"),
    offline_fixture: Path = typer.Option(None, "--offline-fixture"),
) -> None:
    """Only collect + dedupe + upsert papers."""
    configure_logging()
    run_date = date or today_iso()
    settings, _ = _load_all(profile, sources, runtime)
    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)

    raw = collect_papers(
        settings.sources,
        runtime_cfg=settings.runtime,
        offline_fixture=offline_fixture,
    )
    deduped = dedupe_papers(raw)

    with session(paths.db_path) as conn:
        for p in deduped:
            upsert_paper(conn, p)
        conn.commit()

    typer.echo(f"[collect] {run_date}: raw={len(raw)} unique={len(deduped)}")


@app.command("triage")
def cmd_triage(
    date: str = typer.Option(None, "--date"),
    profile: Path = typer.Option(DEFAULT_PROFILE_PATH, "--profile"),
    sources: Path = typer.Option(DEFAULT_SOURCES_PATH, "--sources"),
    runtime: Path = typer.Option(DEFAULT_RUNTIME_PATH, "--runtime"),
    mock_llm: bool = typer.Option(False, "--mock-llm"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """Triage already-stored papers (those with no score for the run_date)."""
    configure_logging()
    run_date = date or today_iso()
    settings, research_profile = _load_all(profile, sources, runtime)
    llm = _build_llm(mock_llm=mock_llm, runtime_cfg=settings.runtime)

    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)

    from paper_inbox.db import metadata_from_row

    with session(paths.db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.* FROM papers p
            WHERE NOT EXISTS (
                SELECT 1 FROM triage_scores s
                WHERE s.paper_id = p.id AND s.run_date = ?
            )
            ORDER BY p.id DESC LIMIT ?
            """,
            (run_date, limit),
        ).fetchall()
        papers = [metadata_from_row(dict(r)) for r in rows]

        llm_cfg = settings.runtime.get("llm", {})
        scored = asyncio.run(
            run_triage(
                conn,
                llm,
                papers,
                research_profile,
                run_date=run_date,
                model=str(llm_cfg.get("model_triage", "gpt-4o-mini")),
                temperature=float(llm_cfg.get("temperature", 0.2)),
            )
        )

    typer.echo(f"[triage] scored {len(scored)} papers for {run_date}")


@app.command("brief")
def cmd_brief(
    date: str = typer.Option(None, "--date"),
    profile: Path = typer.Option(DEFAULT_PROFILE_PATH, "--profile"),
    sources: Path = typer.Option(DEFAULT_SOURCES_PATH, "--sources"),
    runtime: Path = typer.Option(DEFAULT_RUNTIME_PATH, "--runtime"),
    mock_llm: bool = typer.Option(False, "--mock-llm"),
    limit: int = typer.Option(5, "--limit"),
) -> None:
    """Generate briefs for the top-N high-priority scored papers without one yet."""
    configure_logging()
    run_date = date or today_iso()
    settings, research_profile = _load_all(profile, sources, runtime)
    llm = _build_llm(mock_llm=mock_llm, runtime_cfg=settings.runtime)
    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)

    pipeline_cfg = settings.runtime.get("pipeline", {})
    download_buckets = set(pipeline_cfg.get("download_pdf_for_buckets", ["Must Read", "Skim"]))
    min_priority = int(pipeline_cfg.get("min_priority_for_pdf_read", 70))
    timeout_seconds = float(settings.runtime.get("network", {}).get("timeout_seconds", 30))
    llm_cfg = settings.runtime.get("llm", {})

    from paper_inbox.db import metadata_from_row, score_from_row

    async def _run() -> int:
        count = 0
        with session(paths.db_path) as conn:
            already_briefed = {
                b["paper_id"] for b in list_briefs_for_date(conn, run_date)
            }
            scores = list_scores_for_date(
                conn, run_date, min_priority=min_priority
            )
            scores = [
                s for s in scores
                if s["bucket"] in download_buckets and s["paper_id"] not in already_briefed
            ]
            scores = scores[:limit]

            for row in scores:
                paper = metadata_from_row(row)
                score = score_from_row(row)
                pdf_file = fetch_pdf(
                    conn,
                    int(row["paper_id"]),
                    paper,
                    pdf_dir=paths.pdf_dir,
                    timeout_seconds=timeout_seconds,
                )
                text: str | None = None
                if pdf_file is not None:
                    parsed = parse_pdf(
                        conn,
                        int(row["paper_id"]),
                        paper.canonical_id,
                        pdf_file,
                        parsed_dir=paths.parsed_dir,
                    )
                    if parsed is not None:
                        text = parsed.read_text(encoding="utf-8", errors="ignore")
                await fast_read_paper(
                    conn,
                    llm,
                    int(row["paper_id"]),
                    paper,
                    score,
                    research_profile,
                    run_date=run_date,
                    model=str(llm_cfg.get("model_reader", "gpt-4o-mini")),
                    temperature=float(llm_cfg.get("temperature", 0.2)),
                    paper_text=text,
                    briefs_dir=paths.briefs_dir,
                    reports_dir=paths.reports_dir,
                )
                count += 1
        return count

    n = asyncio.run(_run())
    typer.echo(f"[brief] generated {n} briefs for {run_date}")


@app.command("report")
def cmd_report(
    date: str = typer.Option(None, "--date"),
    runtime: Path = typer.Option(DEFAULT_RUNTIME_PATH, "--runtime"),
) -> None:
    """Render and print the path to the daily report."""
    configure_logging()
    run_date = date or today_iso()
    settings = load_settings(runtime_path=runtime, sources_path=DEFAULT_SOURCES_PATH, profile_path=DEFAULT_PROFILE_PATH)
    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)
    with session(paths.db_path) as conn:
        out_path = generate_daily_report(
            conn, run_date=run_date, reports_dir=paths.reports_dir
        )
    typer.echo(str(out_path))


@app.command("feedback")
def cmd_feedback(
    paper_id: str = typer.Option(..., "--paper-id", help="canonical id, e.g. arxiv:2501.12345"),
    feedback: str = typer.Option(
        ..., "--feedback",
        help="useful | not_relevant | must_read | too_shallow | archive",
    ),
    note: str = typer.Option(None, "--note"),
    runtime: Path = typer.Option(DEFAULT_RUNTIME_PATH, "--runtime"),
) -> None:
    """Record user feedback for a paper."""
    configure_logging()
    if feedback not in {"useful", "not_relevant", "must_read", "too_shallow", "archive"}:
        raise typer.BadParameter(f"unknown feedback kind: {feedback}")
    settings = load_settings(runtime_path=runtime, sources_path=DEFAULT_SOURCES_PATH, profile_path=DEFAULT_PROFILE_PATH)
    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)

    with session(paths.db_path) as conn:
        fid = record_feedback(
            conn,
            canonical_id=paper_id,
            feedback=feedback,  # type: ignore[arg-type]
            note=note,
        )
    if fid is None:
        typer.echo(f"⚠️  paper {paper_id} not found in database — feedback not stored.")
        raise typer.Exit(code=1)
    typer.echo(f"recorded feedback id={fid} for {paper_id}")


@app.command("list")
def cmd_list(
    date: str = typer.Option(None, "--date"),
    bucket: str = typer.Option(None, "--bucket", help="Must Read | Skim | Archive | Ignore"),
    runtime: Path = typer.Option(DEFAULT_RUNTIME_PATH, "--runtime"),
) -> None:
    """List scored papers for a date / bucket."""
    configure_logging()
    run_date = date or today_iso()
    settings = load_settings(runtime_path=runtime, sources_path=DEFAULT_SOURCES_PATH, profile_path=DEFAULT_PROFILE_PATH)
    paths = RuntimePaths.from_config(settings.runtime)
    ensure_paths(paths)
    with session(paths.db_path) as conn:
        rows = list_scores_for_date(conn, run_date, bucket=bucket)
    if not rows:
        typer.echo(f"(no papers for {run_date}{f' bucket={bucket}' if bucket else ''})")
        return
    for row in rows:
        typer.echo(
            f"{row['final_priority']:>3}  [{row['bucket']:<9}]  "
            f"{row['canonical_id']}  {row['title']}"
        )


if __name__ == "__main__":  # pragma: no cover
    app()
