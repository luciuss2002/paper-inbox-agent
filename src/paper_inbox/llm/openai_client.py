"""OpenAI-compatible LLM client."""

from __future__ import annotations

import logging
import os
from typing import Any

from paper_inbox.llm.base import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    """Thin wrapper around the official OpenAI SDK.

    Reads `OPENAI_API_KEY` (and optional `OPENAI_BASE_URL`) from environment.
    Importing the SDK is deferred so the project remains usable without it.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
    ) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "OpenAI SDK not installed. Install with `pip install paper-inbox-agent[openai]`."
            ) from exc

        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
            max_retries=max_retries,
        )

    async def complete(
        self, prompt: str, *, model: str, temperature: float = 0.2
    ) -> LLMResponse:
        resp: Any = await self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = None
        if getattr(resp, "usage", None) is not None:
            try:
                usage = resp.usage.model_dump()
            except AttributeError:
                usage = dict(resp.usage)
        return LLMResponse(text=text, model=model, usage=usage)
