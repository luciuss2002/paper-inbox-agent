"""Base interface for paper sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from paper_inbox.models import PaperMetadata


class PaperSourceBase(ABC):
    """A source produces a list of `PaperMetadata` for a given run."""

    name: str = "base"

    @abstractmethod
    def fetch(self, config: dict[str, Any]) -> list[PaperMetadata]:
        """Return a list of papers; must be safe to call without network if `offline_fixture` is set."""
        raise NotImplementedError
