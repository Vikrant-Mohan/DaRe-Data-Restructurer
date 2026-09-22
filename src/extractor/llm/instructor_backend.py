"""Instructor backend: provider-agnostic structured output via LiteLLM.

Instructor validates the model output against the schema; on failure the
pipeline (not the library) owns the retry loop so every attempt is audited.
Validation errors are extracted from the exception chain and fed back by the
pipeline as prompt context.
"""

from __future__ import annotations

import json
from typing import Any

import litellm
from pydantic import BaseModel, ValidationError

from extractor.audit import extract_usage
from extractor.config import Settings
from extractor.llm.base import BackendCall
from extractor.schemas.base import BaseExtraction

_KNOWN_INSTRUCTOR_PROVIDERS = (
    "litellm/",
    "openai/",
    "anthropic/",
    "google/",
    "gemini/",
    "mistral/",
    "cohere/",
    "groq/",
    "fireworks/",
    "cerebras/",
    "databricks/",
    "bedrock/",
    "sambanova/",
    "vertex_ai/",
    "watsonx/",
    "perplexity/",
    "deepseek/",
    "xai/",
)


def normalize_model_string(model: str) -> str:
    """Route bare model names through LiteLLM (provider-agnostic default)."""
    if model.startswith(_KNOWN_INSTRUCTOR_PROVIDERS):
        return model
    if "/" in model:  # provider-prefixed, pass through (instructor may know it)
        return model
    return f"litellm/{model}"


class InstructorBackend:
    """One validated LLM call via instructor.from_provider (async)."""

    name = "instructor"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clients: dict[str, Any] = {}

    def _client(self, model: str) -> Any:
        """Build (and cache) an instructor async client for a model string."""
        import instructor

        normalized = normalize_model_string(model)
        if normalized not in self._clients:
            self._clients[normalized] = instructor.from_provider(
                normalized, async_client=True
            )
        return self._clients[normalized]

    async def call(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: type[BaseExtraction],
        model: str,
        temperature: float,
        seed: int | None,
    ) -> BackendCall:
        client = self._client(model)
        kwargs: dict[str, Any] = {
            "response_model": schema,
            "messages": messages,
            "temperature": temperature,
            # The pipeline drives retries; instructor does exactly one attempt
            # so its internal re-prompts never bypass our audit trail.
            "max_retries": 1,
        }
        if seed is not None:
            kwargs["seed"] = seed
        if normalize_model_string(model).startswith("litellm/"):
            # instructor v2's litellm provider ignores the model at build time;
            # it must be passed on every call (litellm expects the bare id).
            kwargs["model"] = normalize_model_string(model)[len("litellm/") :]

        try:
            result, completion = await client.chat.completions.create_with_completion(
                **kwargs
            )
        except Exception as exc:  # noqa: BLE001 - converted to BackendCall
            return self._failure(schema, exc)

        usage, cost = extract_usage(completion)
        raw_text = _completion_text(completion)
        return BackendCall(
            raw_text=raw_text,
            parsed=result if isinstance(result, BaseModel) else None,
            validation_errors=[],
            error=None,
            token_usage=usage,
            cost_usd=cost,
            model_used=model,
            backend_name=self.name,
        )

    # -- failure mapping ---------------------------------------------------
    def _failure(self, schema: type[BaseExtraction], exc: Exception) -> BackendCall:
        errors, raw_text, usage, cost = _map_exception(exc)
        return BackendCall(
            raw_text=raw_text,
            parsed=None,
            validation_errors=errors,
            error=None if errors else str(exc),
            token_usage=usage,
            cost_usd=cost,
            model_used="",
            backend_name=self.name,
        )


def _completion_text(completion: Any) -> str | None:
    try:
        return completion.choices[0].message.content
    except Exception:
        return None


def _map_exception(
    exc: Exception,
) -> tuple[list[str], str | None, dict[str, int], float | None]:
    """Pull validation errors + raw output out of instructor's exception chain."""
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and len(chain) < 8:
        chain.append(cur)
        nxt = cur.__cause__ or cur.__context__
        cur = nxt if nxt is not cur else None

    errors: list[str] = []
    for e in chain:
        if isinstance(e, ValidationError):
            errors.extend(_format_pydantic_errors(e))
        elif isinstance(e, json.JSONDecodeError):
            errors.append(f"output was not valid JSON: {e}")
        elif type(e).__name__ == "InstructorRetryException" and not errors:
            msg = str(e)
            if msg:
                errors.append(msg[:500])

    raw_text: str | None = None
    for e in chain:
        last = getattr(e, "last_completion", None)
        if last is not None:
            raw_text = _completion_text(last)
            if raw_text:
                break

    usage: dict[str, int] = {}
    cost: float | None = None
    for e in chain:
        last = getattr(e, "last_completion", None)
        if last is not None:
            usage, cost = extract_usage(last)
            break

    return errors, raw_text, usage, cost


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    """Stable, model-friendly rendering of pydantic validation errors."""
    out: list[str] = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err.get("loc", []))
        msg = err.get("msg", "invalid value")
        got = err.get("input")
        got_s = repr(got)
        if len(got_s) > 120:
            got_s = got_s[:117] + "..."
        out.append(f"{loc or '<root>'}: {msg} (input: {got_s})")
    return out


# litellm import is needed for its logging side effects in some deployments;
# referencing it silences unused-import linters while keeping the behavior.
_ = litellm
