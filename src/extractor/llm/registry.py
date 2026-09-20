"""Backend factory honoring the configured backend kind."""

from __future__ import annotations

from extractor.config import Settings
from extractor.llm.base import ExtractionBackend
from extractor.llm.instructor_backend import InstructorBackend
from extractor.llm.outlines_backend import OutlinesBackend


def make_backend(settings: Settings, backend: str | None = None) -> ExtractionBackend:
    """Instantiate the configured extraction backend."""
    kind = backend or settings.backend
    if kind == "instructor":
        return InstructorBackend(settings)
    if kind == "outlines":
        return OutlinesBackend(settings)
    raise ValueError(f"unknown backend: {kind!r} (use 'instructor' or 'outlines')")
