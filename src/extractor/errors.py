"""Exception hierarchy for the extraction pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from extractor.audit import RunTrace


class ExtractorError(Exception):
    """Base class for all extractor errors."""


class ExtractionFailed(ExtractorError):
    """All attempts exhausted; carries the full audit trail for replay."""

    def __init__(self, message: str, run_trace: RunTrace | None = None) -> None:
        super().__init__(message)
        self.run_trace = run_trace

    def audit_summary(self) -> str:
        """Human-readable one-page summary of the run trace."""
        if self.run_trace is None:
            return ""
        return self.run_trace.summary()


class SchemaNotFound(ExtractorError):
    """Requested schema name is not registered."""


class SchemaInvalid(ExtractorError):
    """A user-supplied schema module could not be loaded or is not valid."""


class SourceError(ExtractorError):
    """Fetching the source document failed (navigation error, bad PDF, ...)."""


class BackendError(ExtractorError):
    """The LLM backend itself failed (network, provider error) after retries."""


class RestructureError(ExtractorError):
    """A table-restructuring plan referenced unknown columns or invalid ops."""
