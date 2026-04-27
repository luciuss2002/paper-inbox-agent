"""Tiny retry helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    description: str = "operation",
) -> T:
    """Retry an async callable with exponential backoff.

    Raises the last exception if all attempts fail.
    """
    last_exc: BaseException | None = None
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as exc:  # pragma: no cover - logged path
            last_exc = exc
            if attempt >= attempts:
                logger.warning("%s failed after %d attempts: %s", description, attempt, exc)
                raise
            logger.info(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                description,
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay *= backoff
    assert last_exc is not None  # pragma: no cover
    raise last_exc
