"""Tests for Semantic Scholar enrichment — uses a fake httpx client.

We never hit the real S2 API; all requests are intercepted by a stub
``FakeClient`` that records the call args and returns scripted responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from paper_inbox.sources import semantic_scholar
from paper_inbox.sources.semantic_scholar import (
    SemanticScholarClient,
    _arxiv_id_from_canonical,
    _parse_response,
    _parse_retry_after,
)

# ─── pure-helper tests ────────────────────────────────────────────────────


def test_arxiv_id_extraction() -> None:
    assert _arxiv_id_from_canonical("arxiv:2501.12345") == "2501.12345"
    assert _arxiv_id_from_canonical("manual:foo") is None
    assert _arxiv_id_from_canonical("") is None


def test_parse_response_handles_missing_fields() -> None:
    out = _parse_response(
        "arxiv:1",
        {
            "citationCount": 12,
            "influentialCitationCount": 3,
            "tldr": {"text": "Short summary"},
            "year": 2025,
        },
    )
    assert out.citation_count == 12
    assert out.influential_citation_count == 3
    assert out.tldr == "Short summary"
    assert out.year == 2025
    assert out.venue is None


def test_parse_response_when_no_tldr() -> None:
    out = _parse_response("arxiv:1", {"citationCount": 0})
    assert out.tldr is None
    assert out.citation_count == 0


def test_parse_retry_after() -> None:
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after("0.5") == 0.5
    assert _parse_retry_after(None) == 5.0
    assert _parse_retry_after("garbage") == 5.0


# ─── fake httpx client ────────────────────────────────────────────────────


@dataclass
class FakeClient:
    """Records every POST/GET; returns scripted responses in order."""

    post_responses: list[httpx.Response] = field(default_factory=list)
    get_responses: list[httpx.Response] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)
    gets: list[dict[str, Any]] = field(default_factory=list)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, *, params=None, json=None):  # noqa: A002
        self.posts.append({"url": url, "params": params, "json": json})
        if not self.post_responses:
            return _resp(500, url=url, method="POST")
        return self.post_responses.pop(0)

    def get(self, url, *, params=None):
        self.gets.append({"url": url, "params": params})
        if not self.get_responses:
            return _resp(500, url=url, method="GET")
        return self.get_responses.pop(0)

    def close(self) -> None:
        pass


def _patch_httpx(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr(semantic_scholar.httpx, "Client", lambda **kw: fake)


def _resp(
    status: int,
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
    method: str = "POST",
    url: str = "https://api.semanticscholar.org/graph/v1/paper/batch",
) -> httpx.Response:
    """Build an httpx.Response with a request attached so raise_for_status works."""
    return httpx.Response(
        status,
        json=json,
        headers=headers,
        request=httpx.Request(method, url),
    )


# ─── batch endpoint tests ─────────────────────────────────────────────────


def test_fetch_many_uses_batch_endpoint(monkeypatch) -> None:
    fake = FakeClient(
        post_responses=[
            _resp(
                200,
                json=[
                    {
                        "citationCount": 42,
                        "influentialCitationCount": 7,
                        "tldr": {"text": "Mocked TLDR"},
                        "year": 2026,
                        "venue": "ICLR",
                    },
                    None,  # miss for the second id
                ],
            )
        ]
    )
    _patch_httpx(monkeypatch, fake)

    client = SemanticScholarClient(api_key="dummy", sleep_between_batches=0)
    out = client.fetch_many(["arxiv:2604.00001", "arxiv:2604.99999"])

    assert len(fake.posts) == 1
    assert fake.posts[0]["url"].endswith("/paper/batch")
    assert fake.posts[0]["json"] == {
        "ids": ["arXiv:2604.00001", "arXiv:2604.99999"]
    }
    assert "fields" in fake.posts[0]["params"]

    assert "arxiv:2604.00001" in out
    assert out["arxiv:2604.00001"].citation_count == 42
    assert out["arxiv:2604.00001"].venue == "ICLR"
    assert "arxiv:2604.99999" not in out  # was None in the response


def test_fetch_many_filters_non_arxiv_ids(monkeypatch) -> None:
    fake = FakeClient(post_responses=[_resp(200, json=[])])
    _patch_httpx(monkeypatch, fake)
    client = SemanticScholarClient(api_key="dummy", sleep_between_batches=0)
    out = client.fetch_many(["manual:abc", "hf:123"])
    # No arxiv ids → no POST at all
    assert fake.posts == []
    assert out == {}


def test_fetch_many_handles_429_with_retry(monkeypatch) -> None:
    fake = FakeClient(
        post_responses=[
            _resp(429, headers={"Retry-After": "0"}),
            _resp(200, json=[{"citationCount": 1, "influentialCitationCount": 0}]),
        ]
    )
    _patch_httpx(monkeypatch, fake)

    client = SemanticScholarClient(api_key="dummy", sleep_between_batches=0)
    out = client.fetch_many(["arxiv:1"])

    assert len(fake.posts) == 2  # initial + 1 retry
    assert "arxiv:1" in out
    assert out["arxiv:1"].citation_count == 1


def test_fetch_many_gives_up_after_two_429s(monkeypatch) -> None:
    fake = FakeClient(
        post_responses=[
            _resp(429, headers={"Retry-After": "0"}),
            _resp(429, headers={"Retry-After": "0"}),
        ]
    )
    _patch_httpx(monkeypatch, fake)

    client = SemanticScholarClient(api_key="dummy", sleep_between_batches=0)
    out = client.fetch_many(["arxiv:1"])

    assert len(fake.posts) == 2
    assert out == {}  # no enrichment but didn't crash


def test_fetch_many_chunks_at_500(monkeypatch) -> None:
    """501 ids → 2 batch POSTs, the second with the leftover id."""
    fake = FakeClient(
        post_responses=[
            _resp(200, json=[None] * 500),
            _resp(200, json=[None]),
        ]
    )
    _patch_httpx(monkeypatch, fake)

    client = SemanticScholarClient(api_key="dummy", sleep_between_batches=0)
    ids = [f"arxiv:2604.{i:05d}" for i in range(501)]
    client.fetch_many(ids)

    assert len(fake.posts) == 2
    assert len(fake.posts[0]["json"]["ids"]) == 500
    assert len(fake.posts[1]["json"]["ids"]) == 1


# ─── per-paper GET fallback ───────────────────────────────────────────────


def test_fetch_one_uses_arxiv_get(monkeypatch) -> None:
    fake = FakeClient(
        get_responses=[
            _resp(
                200,
                json={"citationCount": 5, "influentialCitationCount": 1},
                method="GET",
                url="https://api.semanticscholar.org/graph/v1/paper/arXiv:2604.00001",
            )
        ]
    )
    _patch_httpx(monkeypatch, fake)
    client = SemanticScholarClient(api_key="dummy")
    out = client.fetch_one("arxiv:2604.00001")
    assert out is not None
    assert out.citation_count == 5
    assert fake.gets[0]["url"].endswith("/paper/arXiv:2604.00001")


def test_fetch_one_404_returns_none(monkeypatch) -> None:
    fake = FakeClient(get_responses=[_resp(404, method="GET")])
    _patch_httpx(monkeypatch, fake)
    client = SemanticScholarClient(api_key="dummy")
    assert client.fetch_one("arxiv:9999") is None


def test_fetch_one_429_returns_none_no_crash(monkeypatch) -> None:
    fake = FakeClient(get_responses=[_resp(429, headers={"Retry-After": "1"}, method="GET")])
    _patch_httpx(monkeypatch, fake)
    client = SemanticScholarClient(api_key="dummy")
    assert client.fetch_one("arxiv:1") is None


def test_fetch_one_non_arxiv_returns_none() -> None:
    client = SemanticScholarClient(api_key="dummy")
    assert client.fetch_one("manual:foo") is None
