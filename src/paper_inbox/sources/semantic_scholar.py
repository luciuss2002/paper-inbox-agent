"""Semantic Scholar metadata enrichment.

Fetches citation counts, an influential-citation count, and a TLDR for each
paper. Without an API key the public endpoint shares a global rate-limit
pool, so we use the ``/paper/batch`` endpoint to fold up to 500 ids into a
single POST — dramatically lowering the chance of being throttled.

We keep this module tiny and resilient: any HTTP / rate-limit / parse
failure must NOT block the rest of the daily run.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1/paper"
S2_BATCH_API = f"{S2_API}/batch"
DEFAULT_FIELDS = "citationCount,influentialCitationCount,tldr,year,venue"

BATCH_MAX_IDS = 500


@dataclass(frozen=True)
class S2Enrichment:
    canonical_id: str
    citation_count: int | None = None
    influential_citation_count: int | None = None
    tldr: str | None = None
    year: int | None = None
    venue: str | None = None


def _arxiv_id_from_canonical(canonical_id: str) -> str | None:
    if canonical_id.startswith("arxiv:"):
        return canonical_id.split(":", 1)[1]
    return None


def _parse_response(canonical_id: str, payload: dict[str, Any]) -> S2Enrichment:
    tldr = payload.get("tldr")
    tldr_text = None
    if isinstance(tldr, dict):
        tldr_text = tldr.get("text") or None

    return S2Enrichment(
        canonical_id=canonical_id,
        citation_count=payload.get("citationCount"),
        influential_citation_count=payload.get("influentialCitationCount"),
        tldr=tldr_text,
        year=payload.get("year"),
        venue=payload.get("venue") or None,
    )


class SemanticScholarClient:
    """Wrapper around the Semantic Scholar public Graph API.

    Uses ``/paper/batch`` for ``fetch_many`` (1 POST per ≤500 papers) and
    falls back to ``/paper/{id}`` for ``fetch_one``. On 429, respects the
    ``Retry-After`` header for one retry, then bails out and lets the rest
    of the pipeline run without enrichment for the remaining papers.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        fields: str = DEFAULT_FIELDS,
        sleep_between_batches: float = 1.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        self.timeout_seconds = timeout_seconds
        self.fields = fields
        self.sleep_between_batches = 0.0 if self.api_key else sleep_between_batches

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"x-api-key": self.api_key}
        return {}

    # ─── per-paper GET (kept for parity / single lookups) ─────────────────

    def fetch_one(
        self, canonical_id: str, *, client: httpx.Client | None = None
    ) -> S2Enrichment | None:
        arxiv_id = _arxiv_id_from_canonical(canonical_id)
        if not arxiv_id:
            return None
        url = f"{S2_API}/arXiv:{arxiv_id}"
        params = {"fields": self.fields}

        owns_client = client is None
        c = client or httpx.Client(timeout=self.timeout_seconds, headers=self._headers())
        try:
            resp = c.get(url, params=params)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                logger.info("[s2] 429 on single fetch %s — skipping", canonical_id)
                return None
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.info("[s2] %s — %s", canonical_id, exc)
            return None
        finally:
            if owns_client:
                c.close()

        return _parse_response(canonical_id, data)

    # ─── batch POST (the cheap path) ──────────────────────────────────────

    def _post_batch(
        self, client: httpx.Client, ids: list[str]
    ) -> list[dict[str, Any] | None] | None:
        """One batch POST with at most one 429 retry. Returns None on hard failure."""
        params = {"fields": self.fields}
        body = {"ids": ids}

        for attempt in (0, 1):
            try:
                resp = client.post(S2_BATCH_API, params=params, json=body)
            except Exception as exc:
                logger.warning("[s2] batch POST network error: %s", exc)
                return None

            if resp.status_code == 429:
                if attempt == 0:
                    wait = _parse_retry_after(resp.headers.get("Retry-After"), default=5.0)
                    logger.info(
                        "[s2] rate limited (429); sleeping %.1fs and retrying once",
                        wait,
                    )
                    time.sleep(min(wait, 30.0))
                    continue
                logger.warning("[s2] still 429 after retry — giving up on this batch")
                return None

            if resp.status_code in (400, 404):
                logger.warning(
                    "[s2] batch returned HTTP %d — body: %s",
                    resp.status_code,
                    resp.text[:300],
                )
                return None

            try:
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("[s2] batch HTTP %d: %s", resp.status_code, exc)
                return None

            try:
                data = resp.json()
            except Exception as exc:
                logger.warning("[s2] batch invalid JSON: %s", exc)
                return None

            if not isinstance(data, list):
                logger.warning("[s2] batch returned non-list payload")
                return None

            return data

        return None

    def fetch_many(self, canonical_ids: list[str]) -> dict[str, S2Enrichment]:
        """Enrich up to N papers via the /paper/batch endpoint.

        Folds the input into chunks of ≤500 ids per request. If any chunk
        gets a hard 429 we stop early and return whatever was already
        enriched — partial enrichment is better than no enrichment.
        """
        pairs: list[tuple[str, str]] = []
        for cid in canonical_ids:
            arxiv_id = _arxiv_id_from_canonical(cid)
            if arxiv_id:
                pairs.append((cid, f"arXiv:{arxiv_id}"))

        if not pairs:
            return {}

        out: dict[str, S2Enrichment] = {}
        with httpx.Client(timeout=self.timeout_seconds, headers=self._headers()) as client:
            for chunk_start in range(0, len(pairs), BATCH_MAX_IDS):
                chunk = pairs[chunk_start : chunk_start + BATCH_MAX_IDS]
                ids = [s2_id for _, s2_id in chunk]
                payload = self._post_batch(client, ids)
                if payload is None:
                    logger.warning(
                        "[s2] batch failed; skipping remaining %d paper(s) for this run",
                        len(pairs) - chunk_start - len(chunk),
                    )
                    break

                for (cid, _), entry in zip(chunk, payload, strict=False):
                    if entry:
                        out[cid] = _parse_response(cid, entry)

                if chunk_start + BATCH_MAX_IDS < len(pairs) and self.sleep_between_batches:
                    time.sleep(self.sleep_between_batches)

        logger.info(
            "[s2] enriched %d/%d papers via /paper/batch", len(out), len(canonical_ids)
        )
        return out


def _parse_retry_after(value: str | None, *, default: float = 5.0) -> float:
    """Parse a ``Retry-After`` header value (seconds) safely."""
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default
