from __future__ import annotations

import asyncio
import json

from paper_inbox.llm.mock_client import MockLLMClient


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_mock_triage_returns_valid_json() -> None:
    llm = MockLLMClient()
    prompt = (
        "你是一个研究论文筛选助手。\n"
        "请输出严格 JSON\n"
        "Title: agentic reinforcement learning for tool use\n"
        "Abstract: We release code for our search-augmented reasoning agent.\n"
        "relevance_to_user, novelty"
    )
    resp = asyncio.run(llm.complete(prompt, model="mock"))
    data = json.loads(resp.text)
    for k in (
        "relevance_to_user",
        "novelty",
        "practicality",
        "experiment_strength",
        "reproducibility_signal",
        "trend_signal",
    ):
        assert 1 <= int(data[k]) <= 5
    assert isinstance(data["reasons"], list)


def test_mock_fast_reader_returns_markdown() -> None:
    llm = MockLLMClient()
    prompt = "你是一个研究员速读助手\nTitle: A Test Paper\n"
    resp = asyncio.run(llm.complete(prompt, model="mock"))
    assert resp.text.startswith("# A Test Paper")
    assert "## 0. Verdict" in resp.text


def test_mock_daily_report_returns_markdown() -> None:
    llm = MockLLMClient()
    prompt = "你是一个研究秘书。请生成每日论文简报。"
    resp = asyncio.run(llm.complete(prompt, model="mock"))
    assert "Daily Paper Inbox" in resp.text
