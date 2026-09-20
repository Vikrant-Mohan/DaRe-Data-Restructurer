"""Outlines backend: grammar-constrained decoding for local models.

Constrained decoding makes schema-invalid JSON nearly impossible; the JSON
schema is compiled from the Pydantic model. Semantic (cross-field) validators
still run — their errors flow back through the same pipeline retry loop.

Model strings (settings ``model``):
  transformers/<hf-id>   local HuggingFace transformers model
  ollama/<model>         local Ollama server
  vllm/<model>           vLLM engine (requires vllm extras)

This backend is opt-in: ``EXTRACT_BACKEND=outlines`` and install with the
``outlines`` extra.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ValidationError

from extractor.config import Settings
from extractor.errors import BackendError
from extractor.llm.base import BackendCall
from extractor.schemas.base import BaseExtraction


class OutlinesBackend:
    """Constrained-decoding backend using Outlines."""

    name = "outlines"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._models: dict[str, Any] = {}

    async def call(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: type[BaseExtraction],
        model: str,
        temperature: float,
        seed: int | None,
    ) -> BackendCall:
        try:
            return await asyncio.to_thread(
                self._generate_sync,
                messages,
                schema,
                model,
                temperature,
                seed,
            )
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001
            return BackendCall(
                raw_text=None,
                parsed=None,
                validation_errors=[],
                error=f"outlines generation failed: {exc}",
                model_used=model,
                backend_name=self.name,
            )

    # -- sync generation (runs in a worker thread) --------------------------
    def _generate_sync(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseExtraction],
        model: str,
        temperature: float,
        seed: int | None,
    ) -> BackendCall:
        if _messages_have_images(messages):
            raise BackendError(
                "outlines backend does not support vision inputs; use the "
                "instructor backend for scanned PDFs"
            )
        generator = self._generator(model, schema)
        prompt = _flatten_messages(messages)
        if seed is not None:
            _try_seed(seed)
        try:
            result = generator(prompt, temperature=temperature)  # type: ignore[call-arg]
        except TypeError:
            # older/newer outlines signatures without temperature kwarg
            result = generator(prompt)

        raw_text: str | None
        parsed: BaseModel | None
        errors: list[str] = []
        if isinstance(result, BaseModel):
            parsed = result
            raw_text = result.model_dump_json()
        else:
            raw_text = str(result)
            parsed = None
            obj, json_err = _safe_json(raw_text)
            if json_err is not None:
                errors.append(f"output was not valid JSON: {json_err}")
            else:
                try:
                    parsed = schema.model_validate(obj)
                except ValidationError as ve:
                    from extractor.llm.instructor_backend import _format_pydantic_errors

                    errors.extend(_format_pydantic_errors(ve))

        return BackendCall(
            raw_text=raw_text,
            parsed=parsed,
            validation_errors=errors,
            error=None,
            model_used=model,
            backend_name=self.name,
        )

    # -- model/generator construction ---------------------------------------
    def _generator(self, model: str, schema: type[BaseExtraction]) -> Any:
        try:
            import outlines  # noqa: PLC0415
        except ImportError as exc:
            raise BackendError(
                "outlines is not installed; install with: pip install 'extractor[outlines]'"
            ) from exc

        kind, _, name = model.partition("/")
        if not name:
            kind, name = "transformers", model
        cache_key = f"{kind}:{name}"
        if cache_key not in self._models:
            self._models[cache_key] = self._build_model(outlines, kind, name)
        base_model = self._models[cache_key]
        return outlines.Generator(base_model, schema)

    @staticmethod
    def _build_model(outlines: Any, kind: str, name: str) -> Any:
        if kind == "ollama":
            try:
                from ollama import Client  # noqa: PLC0415
            except ImportError as exc:
                raise BackendError(
                    "pip install ollama  (required for ollama/ model strings)"
                ) from exc
            return outlines.from_ollama(Client(host="http://localhost:11434"), name)
        if kind == "vllm":
            return outlines.from_vllm(name)
        if kind == "transformers":
            return outlines.from_transformers(name)
        raise BackendError(
            f"unsupported outlines model string {kind!r}: use transformers/, ollama/ or vllm/"
        )


def _messages_have_images(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            if any(part.get("type") == "image_url" for part in content):
                return True
    return False


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Outlines generators take a plain string prompt."""
    parts: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                str(p.get("text", "")) for p in content if isinstance(p, dict)
            )
    return "\n\n".join(parts)


def _safe_json(text: str) -> tuple[Any, Exception | None]:
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, exc


def _try_seed(seed: int) -> None:
    """Seed torch when available for reproducible local generation."""
    try:
        import torch  # noqa: PLC0415

        torch.manual_seed(seed)
    except ImportError:
        pass
