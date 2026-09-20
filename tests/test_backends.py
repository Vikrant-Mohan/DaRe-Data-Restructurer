"""Backend unit tests (no network): normalization, error mapping, factory."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from extractor.config import Settings
from extractor.llm.instructor_backend import (
    InstructorBackend,
    _format_pydantic_errors,
    _map_exception,
    normalize_model_string,
)
from extractor.llm.registry import make_backend
from extractor.schemas.examples import Invoice


def test_normalize_bare_model_gets_litellm_prefix() -> None:
    assert normalize_model_string("gpt-4o-mini") == "litellm/gpt-4o-mini"


def test_normalize_passthrough_provider_strings() -> None:
    assert normalize_model_string("openai/gpt-4o") == "openai/gpt-4o"
    assert normalize_model_string("litellm/anthropic/claude-3-5-sonnet") == (
        "litellm/anthropic/claude-3-5-sonnet"
    )
    assert normalize_model_string("ollama/llama3.1") == "ollama/llama3.1"


def test_format_pydantic_errors_is_stable_and_annotated() -> None:
    try:
        Invoice(invoice_number=None, total="x")  # type: ignore[arg-type]
    except ValidationError as exc:
        lines = _format_pydantic_errors(exc)
    assert any(line.startswith("invoice_number") for line in lines)
    assert any("input:" in line for line in lines)
    assert all(
        "Field required" in line or "Input should" in line or "->" not in line
        for line in lines
    )


def _wrapped_validation_error() -> Exception:
    """Build an exception chain containing a real ValidationError."""
    try:
        try:
            Invoice(
                invoice_number="A",
                total=Decimal("1"),
                subtotal=Decimal("2"),
                tax=Decimal("0"),
            )  # fails _totals_math
        except ValidationError as ve:
            raise RuntimeError("instructor wrapper") from ve
    except RuntimeError as outer:
        return outer
    raise AssertionError("unreachable")


def test_map_exception_extracts_validator_message() -> None:
    errors, raw, usage, cost = _map_exception(_wrapped_validation_error())
    assert any("does not equal" in e for e in errors)
    assert raw is None
    assert usage == {}
    assert cost is None


def test_map_exception_tolerates_plain_exceptions() -> None:
    errors, raw, usage, cost = _map_exception(ValueError("network down"))
    assert errors == []  # not a validation failure; surfaced as backend error
    assert raw is None


def test_make_backend_unknown_raises() -> None:
    with pytest.raises(ValueError):
        make_backend(Settings(), backend="quantum")


def test_instructor_backend_failure_path_returns_structured_call(settings: Any) -> None:
    backend = InstructorBackend(settings)
    call = backend._failure(Invoice, _wrapped_validation_error())
    assert call.parsed is None
    assert any("does not equal" in e for e in call.validation_errors)
    assert call.error is None  # it was a validation issue, not a backend fault
