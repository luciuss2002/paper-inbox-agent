from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from paper_inbox.models import PaperSource
from paper_inbox.sources import arxiv_source
from paper_inbox.sources.arxiv_source import ArxivSource, parse_atom_feed

FIXTURE = Path(__file__).parent / "fixtures" / "sample_arxiv_feed.xml"


def test_parse_atom_feed_yields_metadata() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    papers = parse_atom_feed(text)
    assert len(papers) == 4
    p = next(p for p in papers if p.source_id == "2604.00001")
    assert p.canonical_id == "arxiv:2604.00001"
    assert p.source == PaperSource.ARXIV
    assert "Search-R1+" in p.title
    assert p.pdf_url and p.pdf_url.endswith(".pdf")
    assert "cs.CL" in p.categories
    assert p.authors[0] == "Alice Liu"


def test_arxiv_source_offline_fixture_returns_papers() -> None:
    src = ArxivSource(offline_fixture=str(FIXTURE))
    out = src.fetch({"arxiv": {"enabled": True}})
    assert len(out) == 4


def test_arxiv_api_url_uses_https() -> None:
    """Regression: arXiv 301-redirects http→https; we must hit https directly."""
    assert arxiv_source.ARXIV_API.startswith("https://")


@dataclass
class _FakeArxivClient:
    """Records GETs and returns scripted responses; supports a flaky-then-ok script."""

    responses: list[Any] = field(default_factory=list)  # exception or httpx.Response
    gets: list[dict[str, Any]] = field(default_factory=list)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, *, params=None):
        self.gets.append({"url": url, "params": params})
        if not self.responses:
            return httpx.Response(
                500, request=httpx.Request("GET", url)
            )
        next_resp = self.responses.pop(0)
        if isinstance(next_resp, Exception):
            raise next_resp
        return next_resp


def _ok_atom(text: str | None = None) -> httpx.Response:
    body = text or FIXTURE.read_text(encoding="utf-8")
    return httpx.Response(
        200,
        text=body,
        request=httpx.Request("GET", arxiv_source.ARXIV_API),
        headers={"content-type": "application/atom+xml"},
    )


def test_fetch_remote_retries_on_transport_error(monkeypatch) -> None:
    """A transient TransportError on the first try should be retried, not surfaced."""
    fake = _FakeArxivClient(
        responses=[
            httpx.RemoteProtocolError("Server disconnected"),
            _ok_atom(),
        ]
    )
    monkeypatch.setattr(arxiv_source.httpx, "Client", lambda **kw: fake)
    monkeypatch.setattr(arxiv_source.time, "sleep", lambda _s: None)

    src = ArxivSource(query_interval=0, retries=2)
    out = src.fetch({"arxiv": {"enabled": True, "queries": ["foo"], "categories": []}})

    assert len(fake.gets) == 2  # initial + 1 retry
    assert len(out) == 4  # parsed the eventual successful response


def test_fetch_continues_when_one_query_fails(monkeypatch) -> None:
    """Per-query failures must not abort the whole collection."""
    fake = _FakeArxivClient(
        responses=[
            httpx.RemoteProtocolError("disconnected"),
            httpx.RemoteProtocolError("disconnected"),
            httpx.RemoteProtocolError("disconnected"),  # query A: gives up after retries
            _ok_atom(),  # query B: ok
        ]
    )
    monkeypatch.setattr(arxiv_source.httpx, "Client", lambda **kw: fake)
    monkeypatch.setattr(arxiv_source.time, "sleep", lambda _s: None)

    src = ArxivSource(query_interval=0, retries=2)
    out = src.fetch(
        {"arxiv": {"enabled": True, "queries": ["a", "b"], "categories": []}}
    )
    # query A: 1 + 2 retries = 3 attempts; query B: 1 attempt = 4 total
    assert len(fake.gets) == 4
    assert len(out) == 4  # only query B contributed


def test_query_interval_sleeps_between_queries(monkeypatch) -> None:
    """A 3s sleep between queries honors arXiv's ToS rate limit."""
    fake = _FakeArxivClient(responses=[_ok_atom(), _ok_atom()])
    monkeypatch.setattr(arxiv_source.httpx, "Client", lambda **kw: fake)

    sleeps: list[float] = []
    monkeypatch.setattr(arxiv_source.time, "sleep", lambda s: sleeps.append(s))

    src = ArxivSource(query_interval=3.0, retries=0)
    src.fetch(
        {"arxiv": {"enabled": True, "queries": ["a", "b"], "categories": []}}
    )
    # Two queries → exactly one inter-query sleep of 3s
    assert 3.0 in sleeps
