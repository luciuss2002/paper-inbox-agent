"""Abstract LLM client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class LLMResponse(BaseModel):
    text: str
    model: str
    usage: dict | None = None


class LLMClient(ABC):
    """All callers depend on this interface, not on specific providers."""

    @abstractmethod
    async def complete(
        self, prompt: str, *, model: str, temperature: float = 0.2
    ) -> LLMResponse:
        raise NotImplementedError
