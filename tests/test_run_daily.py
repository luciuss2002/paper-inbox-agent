"""End-to-end pipeline test using mock LLM and the offline arXiv fixture."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import yaml

from paper_inbox.llm.mock_client import MockLLMClient
from paper_inbox.pipeline.run_daily import run_daily_pipeline
from paper_inbox.scoring import load_profile
from paper_inbox.settings import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "sample_arxiv_feed.xml"
PROFILE_FIXTURE = Path(__file__).parent / "fixtures" / "sample_profile.yaml"


def _make_settings(tmp_path: Path) -> Settings:
    runtime = {
        "paths": {
            "data_dir": str(tmp_path / "data"),
            "pdf_dir": str(tmp_path / "data" / "pdfs"),
            "parsed_dir": str(tmp_path / "data" / "parsed"),
            "briefs_dir": str(tmp_path / "data" / "briefs"),
            "reports_dir": str(tmp_path / "data" / "reports"),
            "db_path": str(tmp_path / "data" / "paper_inbox.sqlite"),
        },
        "llm": {
            "provider": "mock",
            "model_triage": "mock",
            "model_reader": "mock",
            "temperature": 0.0,
            "max_retries": 1,
        },
        "pipeline": {
            "daily_candidate_limit": 50,
            "download_pdf_for_buckets": ["Must Read", "Skim"],
            "max_pdf_to_read_per_day": 0,  # avoid network for PDFs in tests
            "min_priority_for_pdf_read": 70,
        },
        "network": {"timeout_seconds": 5, "max_retries": 1},
    }
    sources = {"arxiv": {"enabled": True}, "hf_daily": {"enabled": False}}
    profile_data = yaml.safe_load(PROFILE_FIXTURE.read_text(encoding="utf-8"))

    return Settings(
        runtime=runtime,
        sources=sources,
        profile=profile_data,
        runtime_path=tmp_path / "runtime.yaml",
        sources_path=tmp_path / "sources.yaml",
        profile_path=tmp_path / "profile.yaml",
    )


def test_run_daily_with_mock_llm_and_offline_fixture(tmp_path: Path) -> None:
    cwd = os.getcwd()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        os.chdir(repo_root)  # so prompts/ paths resolve
        settings = _make_settings(tmp_path)
        profile = load_profile(data=settings.profile)
        llm = MockLLMClient()

        result = asyncio.run(
            run_daily_pipeline(
                run_date="2026-04-27",
                settings=settings,
                llm=llm,
                profile=profile,
                offline_fixture=str(FIXTURE),
            )
        )
    finally:
        os.chdir(cwd)

    assert result.total_collected == 4
    assert result.after_dedupe == 4
    assert result.triaged >= 1
    assert result.report_path.exists()
    text = result.report_path.read_text(encoding="utf-8")
    assert "Daily Paper Inbox - 2026-04-27" in text
    # SQLite was populated
    db = Path(settings.runtime["paths"]["db_path"])
    assert db.exists()
