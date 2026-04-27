"""Collect papers from all enabled sources."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from paper_inbox.models import PaperMetadata
from paper_inbox.sources.arxiv_source import ArxivSource
from paper_inbox.sources.hf_daily_source import HfDailySource

logger = logging.getLogger(__name__)


def collect_papers(
    sources_cfg: dict[str, Any],
    *,
    runtime_cfg: dict[str, Any] | None = None,
    offline_fixture: str | Path | None = None,
    offline_hf_fixture: str | Path | None = None,
) -> list[PaperMetadata]:
    """Run all enabled sources and return a flat list of metadata.

    A failure in one source must NOT abort the whole run.

    ``offline_fixture`` feeds the arXiv source; ``offline_hf_fixture`` feeds
    the HF Daily source. Either being set forces that source to bypass the
    network and read from disk, regardless of ``enabled`` in config.
    """
    timeout = float((runtime_cfg or {}).get("network", {}).get("timeout_seconds", 30))
    out: list[PaperMetadata] = []

    arxiv_cfg = sources_cfg.get("arxiv", {})
    if arxiv_cfg.get("enabled", True) or offline_fixture:
        try:
            src = ArxivSource(offline_fixture=offline_fixture, timeout_seconds=timeout)
            out.extend(src.fetch(sources_cfg))
        except Exception as exc:
            logger.warning("[collect] arxiv source failed: %s", exc)

    hf_cfg = sources_cfg.get("hf_daily", {})
    if hf_cfg.get("enabled", False) or offline_hf_fixture:
        try:
            hf = HfDailySource(
                offline_fixture=offline_hf_fixture, timeout_seconds=timeout
            )
            out.extend(hf.fetch(sources_cfg))
        except Exception as exc:
            logger.warning("[collect] hf_daily source failed: %s", exc)

    logger.info("[collect] total papers from all sources: %d", len(out))
    return out
