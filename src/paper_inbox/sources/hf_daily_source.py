"""Hugging Face Daily Papers source — placeholder for v0.2."""

from __future__ import annotations

import logging
from typing import Any

from paper_inbox.models import PaperMetadata
from paper_inbox.sources.base import PaperSourceBase

logger = logging.getLogger(__name__)


class HfDailySource(PaperSourceBase):
    name = "hf_daily"

    def fetch(self, config: dict[str, Any]) -> list[PaperMetadata]:
        cfg = config.get("hf_daily", {})
        if not cfg.get("enabled", False):
            return []
        logger.info("[hf_daily] not implemented in MVP — skipping")
        return []
