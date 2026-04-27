"""Date utilities."""

from __future__ import annotations

from datetime import UTC, date, datetime


def today_iso() -> str:
    """Return today's date as ISO string (YYYY-MM-DD) in local time."""
    return date.today().isoformat()


def now_utc_iso() -> str:
    """Return the current UTC timestamp as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def parse_iso_date(value: str) -> date:
    """Parse a YYYY-MM-DD string into a date."""
    return date.fromisoformat(value)
