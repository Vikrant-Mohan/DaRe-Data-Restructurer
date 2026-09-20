"""Backend protocol and shared result types for LLM backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from extractor.schemas.base import BaseExtraction


@dataclass
class BackendCall:
    """Result of one LLM call, successful or not."""

    raw_text: str | None = None  # assistant text, when capturable
    parsed: BaseModel | None = None  # backend-validated instance
    validation_errors: list[str] = field(default_factory=list)
    error: str | None = None  # backend exception text (network, provider...)
    token_usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    model_used: str = ""
    backend_name: str = ""


@runtime_checkable
class ExtractionBackend(Protocol):
    """One LLM call: source text -> validated instance or errors."""

    async def call(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: type[BaseExtraction],
        model: str,
        temperature: float,
        seed: int | None,
    ) -> BackendCall: ...
