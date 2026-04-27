"""Runtime paths handling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    data_dir: Path
    pdf_dir: Path
    parsed_dir: Path
    briefs_dir: Path
    reports_dir: Path
    db_path: Path

    @classmethod
    def from_config(cls, cfg: dict) -> RuntimePaths:
        paths = cfg.get("paths", {})
        return cls(
            data_dir=Path(paths.get("data_dir", "./data")),
            pdf_dir=Path(paths.get("pdf_dir", "./data/pdfs")),
            parsed_dir=Path(paths.get("parsed_dir", "./data/parsed")),
            briefs_dir=Path(paths.get("briefs_dir", "./data/briefs")),
            reports_dir=Path(paths.get("reports_dir", "./data/reports")),
            db_path=Path(paths.get("db_path", "./data/paper_inbox.sqlite")),
        )

    def reports_for(self, run_date: str) -> Path:
        return self.reports_dir / run_date


def ensure_paths(paths: RuntimePaths) -> None:
    """Create all runtime directories if they do not exist."""
    for d in (
        paths.data_dir,
        paths.pdf_dir,
        paths.parsed_dir,
        paths.briefs_dir,
        paths.reports_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    paths.db_path.parent.mkdir(parents=True, exist_ok=True)
