"""Deterministic structured-data extraction agent.

Crawls changing DOM structures (Playwright) and unstructured PDFs (PyMuPDF +
vision fallback) and extracts entities into strict Pydantic schemas, feeding
schema-validation failures back into the prompt context until the output
conforms — or a fully audited failure is raised.
"""

from extractor.audit import AttemptRecord, RunTrace
from extractor.cache import CacheKey, ResultCache
from extractor.config import Settings
from extractor.errors import (
    BackendError,
    ExtractionFailed,
    ExtractorError,
    SchemaInvalid,
    SchemaNotFound,
    SourceError,
)
from extractor.pipeline import ExtractionAgent

__version__ = "0.1.0"

__all__ = [
    "AttemptRecord",
    "BackendError",
    "CacheKey",
    "ExtractionAgent",
    "ExtractionFailed",
    "ExtractorError",
    "ResultCache",
    "RunTrace",
    "SchemaInvalid",
    "SchemaNotFound",
    "Settings",
    "SourceError",
]
