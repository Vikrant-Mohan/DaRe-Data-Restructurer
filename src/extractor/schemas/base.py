"""Base classes for user-defined extraction schemas.

Every user schema must subclass ``BaseExtraction``, which provides
deterministic hashing of the schema itself and optional DOM css hints.
Provenance is carried by the pipeline's ``ExtractionResult`` envelope rather
than the model, so the LLM never sees (or fabricates) provenance fields.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    """Where an extraction came from and how it was produced."""

    model_config = ConfigDict(extra="forbid")

    source_kind: str  # html | pdf
    source_origin: str  # url or file path
    source_sha256: str
    schema_name: str
    schema_sha256: str
    model: str
    backend: str
    prompt_version: str
    extracted_at: str  # ISO 8601 UTC, filled by the pipeline
    attempt_count: int = 0
    cache_hit: bool = False
    run_id: str = ""
    pages: list[int] = Field(default_factory=list)  # PDF page numbers (1-based)


class BaseExtraction(BaseModel):
    """Base for strict extraction schemas.

    Subclasses must declare ``schema_name``. Add domain validators freely
    (``@model_validator`` / ``@field_validator``): their error messages are fed
    back into the LLM prompt context on retry — that is the point.
    """

    model_config = ConfigDict(extra="forbid")

    schema_name: ClassVar[str] = ""
    css_hints: ClassVar[list[str]] = []  # optional: focus the DOM region
    schema_description: ClassVar[str] = "Extract structured entities from the document."

    @classmethod
    def json_schema_compact(cls) -> str:
        """JSON Schema (compact JSON text) used for hashing and prompts."""
        return json.dumps(cls.model_json_schema(), separators=(",", ":"), default=str)

    @classmethod
    def schema_sha256(cls) -> str:
        """Deterministic hash of this schema definition."""
        return hashlib.sha256(cls.json_schema_compact().encode("utf-8")).hexdigest()

    @classmethod
    def validator_contract(cls) -> str:
        """Human-readable summary of custom validators, shown to the model.

        Uses Pydantic's public ``__pydantic_decorators__`` registry so only
        real validators are listed, with their first docstring line as the
        rule description.
        """
        try:
            decos = cls.__pydantic_decorators__
            names = sorted(set(decos.field_validators) | set(decos.model_validators))
        except AttributeError:  # pragma: no cover - very old pydantic
            names = []
        rules: list[str] = []
        for name in names:
            func = getattr(cls, name, None)
            doc = inspect.getdoc(func) if func is not None else None
            first_line = doc.splitlines()[0] if doc else "must satisfy the declared rule"
            rules.append(f"- {name}: {first_line}")
        return "\n".join(rules) if rules else "(no additional cross-field rules)"

    @classmethod
    def generation_hints(cls) -> dict[str, Any]:
        """Extra context for the prompt builder (css hints, description)."""
        return {
            "description": cls.schema_description,
            "css_hints": list(cls.css_hints),
        }
