"""Shared fixtures: isolated Settings and scripted fake backends."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from extractor.config import Settings
from extractor.schemas.base import BaseExtraction


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    """Settings isolated from the user's environment for tests."""
    return Settings(
        model="test-model",
        backend="instructor",
        temperature=0.0,
        seed=7,
        max_attempts=4,
        max_escalations=2,
        cache_dir=tmp_path / "cache",
        cache_ttl_hours=1.0,
        audit_dir=tmp_path / "audit",
        user_schema_dir=tmp_path / "schemas_user",
    )


class ScriptedBackend:
    """Fake backend returning a queue of BackendCall-like responses.

    Records every call's messages so tests can assert the feedback loop:
    the second call's user messages must contain the validation errors and
    the previous raw output.
    """

    name = "scripted"

    def __init__(self, outputs: list[BaseExtraction | str | Exception]) -> None:
        self._outputs = outputs
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: type[BaseExtraction],
        model: str,
        temperature: float,
        seed: int | None,
    ) -> Any:
        from extractor.llm.base import BackendCall

        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "schema": schema,
                "model": model,
                "temperature": temperature,
                "seed": seed,
            }
        )
        out = self._outputs.pop(0) if self._outputs else Exception("script exhausted")
        if isinstance(out, Exception):
            return BackendCall(error=str(out), model_used=model, backend_name=self.name)
        if isinstance(out, str):
            # Validate like the real instructor backend would, so scripted
            # invalid outputs exercise the schema-validation feedback path.
            import json as _json

            from extractor.llm.instructor_backend import _format_pydantic_errors

            try:
                obj = _json.loads(out)
            except _json.JSONDecodeError as exc:
                return BackendCall(
                    raw_text=out,
                    parsed=None,
                    validation_errors=[f"output was not valid JSON: {exc}"],
                    model_used=model,
                    backend_name=self.name,
                )
            try:
                parsed = schema.model_validate(obj)
            except Exception as exc:
                from pydantic import ValidationError

                errors = (
                    _format_pydantic_errors(exc)
                    if isinstance(exc, ValidationError)
                    else [str(exc)]
                )
                return BackendCall(
                    raw_text=out,
                    parsed=None,
                    validation_errors=errors,
                    model_used=model,
                    backend_name=self.name,
                )
            return BackendCall(
                raw_text=out,
                parsed=parsed,
                model_used=model,
                backend_name=self.name,
            )
        return BackendCall(
            raw_text=out.model_dump_json(),
            parsed=out,
            model_used=model,
            backend_name=self.name,
        )

    def last_user_text(self) -> str:
        """Concatenated text of all user-role messages in the last call."""
        texts: list[str] = []
        for m in self.calls[-1]["messages"]:
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    texts.append(content)
                else:
                    texts.extend(
                        str(p.get("text", "")) for p in content if isinstance(p, dict)
                    )
        return "\n".join(texts)


@pytest.fixture
def scripted_backend() -> type[ScriptedBackend]:
    return ScriptedBackend
