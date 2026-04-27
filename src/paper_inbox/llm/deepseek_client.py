"""DeepSeek LLM client.

DeepSeek's chat API is OpenAI-compatible (``/v1/chat/completions`` shape), so
we just point the existing OpenAI SDK at ``https://api.deepseek.com`` and
read credentials from ``DEEPSEEK_API_KEY``.

In addition to the OpenAI surface, DeepSeek's V-series accepts:

* ``reasoning_effort`` — like the OpenAI o1/o3 family. Values seen in the wild:
  ``"low"`` / ``"medium"`` / ``"high"`` / ``"max"``. Pass through whatever the
  user configures; the API rejects invalid values, which is the right
  feedback loop.
* ``extra_body={"thinking": {"type": "enabled"}}`` — opts the model into its
  hidden chain-of-thought (a.k.a. "thinking mode"). Without this flag the
  model answers directly.

Both are exposed as constructor knobs and forwarded into every
``chat.completions.create`` call.

Model ids are NOT hardcoded. Set them in ``runtime.yaml`` (e.g.
``deepseek-chat``, ``deepseek-reasoner``, ``deepseek-v4-pro``, ...). See
https://api-docs.deepseek.com/ for the current list.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from paper_inbox.llm.base import LLMResponse
from paper_inbox.llm.openai_client import OpenAIClient

logger = logging.getLogger(__name__)

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekClient(OpenAIClient):
    """OpenAI-compatible client preconfigured for DeepSeek's endpoint and
    augmented with their ``thinking`` + ``reasoning_effort`` knobs."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
        reasoning_effort: str | None = "high",
        thinking_enabled: bool = True,
    ) -> None:
        resolved_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        resolved_base = (
            base_url
            or os.environ.get("DEEPSEEK_BASE_URL")
            or DEEPSEEK_DEFAULT_BASE_URL
        )
        if not resolved_key:
            raise RuntimeError(
                "DeepSeek API key not found. Set DEEPSEEK_API_KEY in .env "
                "or pass api_key= explicitly."
            )
        super().__init__(
            api_key=resolved_key,
            base_url=resolved_base,
            max_retries=max_retries,
        )
        self.reasoning_effort = reasoning_effort
        self.thinking_enabled = thinking_enabled

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.2,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.thinking_enabled:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        resp: Any = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = None
        if getattr(resp, "usage", None) is not None:
            try:
                usage = resp.usage.model_dump()
            except AttributeError:
                usage = dict(resp.usage)
        return LLMResponse(text=text, model=model, usage=usage)
