"""DeepSeekClient construction + thinking/reasoning_effort smoke tests.

These never hit the real DeepSeek API — we substitute the underlying
``AsyncOpenAI`` stub object with a fake recorder so we can assert the
exact kwargs that would have been sent over the wire.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from paper_inbox.llm.deepseek_client import DEEPSEEK_DEFAULT_BASE_URL, DeepSeekClient


def test_constructor_uses_explicit_key_and_base_url() -> None:
    c = DeepSeekClient(api_key="sk-test", base_url="https://example.com/v1")
    # The wrapper inherits OpenAIClient — we only need to confirm it doesn't blow up
    # and that it kept our base_url
    assert c is not None
    # AsyncOpenAI exposes the resolved base_url via str()
    assert "example.com" in str(c._client.base_url)


def test_constructor_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    c = DeepSeekClient()
    assert c is not None
    # default base url
    assert DEEPSEEK_DEFAULT_BASE_URL in str(c._client.base_url)


def test_constructor_raises_without_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        DeepSeekClient()


def test_cli_build_llm_picks_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from paper_inbox.cli import _build_llm

    runtime_cfg = {
        "llm": {
            "provider": "deepseek",
            "model_triage": "deepseek-chat",
            "model_reader": "deepseek-chat",
            "max_retries": 1,
        }
    }
    client = _build_llm(mock_llm=False, runtime_cfg=runtime_cfg)
    assert isinstance(client, DeepSeekClient)


def test_cli_build_llm_passes_reasoning_and_thinking(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from paper_inbox.cli import _build_llm

    runtime_cfg = {
        "llm": {
            "provider": "deepseek",
            "model_triage": "deepseek-v4-pro",
            "model_reader": "deepseek-v4-pro",
            "max_retries": 1,
            "reasoning_effort": "max",
            "thinking_enabled": True,
        }
    }
    client = _build_llm(mock_llm=False, runtime_cfg=runtime_cfg)
    assert isinstance(client, DeepSeekClient)
    assert client.reasoning_effort == "max"
    assert client.thinking_enabled is True


class _RecordingCompletions:
    """Stand-in for AsyncOpenAI.chat.completions; records args + returns a stub."""

    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        choice = SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))
        return SimpleNamespace(choices=[choice], usage=None)


def _patch_underlying_client(client: DeepSeekClient) -> _RecordingCompletions:
    rec = _RecordingCompletions()
    client._client = SimpleNamespace(  # type: ignore[attr-defined]
        chat=SimpleNamespace(completions=rec)
    )
    return rec


def test_complete_forwards_thinking_and_effort() -> None:
    client = DeepSeekClient(
        api_key="sk-test",
        reasoning_effort="max",
        thinking_enabled=True,
    )
    rec = _patch_underlying_client(client)
    asyncio.run(client.complete("hello", model="deepseek-v4-pro", temperature=0.0))

    assert rec.last_kwargs is not None
    assert rec.last_kwargs["model"] == "deepseek-v4-pro"
    assert rec.last_kwargs["temperature"] == 0.0
    assert rec.last_kwargs["reasoning_effort"] == "max"
    assert rec.last_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert rec.last_kwargs["messages"] == [{"role": "user", "content": "hello"}]


def test_complete_omits_extras_when_disabled() -> None:
    client = DeepSeekClient(
        api_key="sk-test",
        reasoning_effort=None,
        thinking_enabled=False,
    )
    rec = _patch_underlying_client(client)
    asyncio.run(client.complete("x", model="deepseek-chat"))

    assert rec.last_kwargs is not None
    assert "reasoning_effort" not in rec.last_kwargs
    assert "extra_body" not in rec.last_kwargs
