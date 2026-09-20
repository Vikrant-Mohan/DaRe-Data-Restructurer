"""Shared test helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from extractor.audit import AttemptRecord, RunTrace, new_run_id
from extractor.pipeline import ExtractionResult
from extractor.schemas.base import Provenance


def fake_result(data: Any) -> ExtractionResult:
    """Build a plausible ExtractionResult around a validated instance."""
    run_id = new_run_id("extract")
    prov = Provenance(
        source_kind="html",
        source_origin="inline://html",
        source_sha256="0" * 64,
        schema_name=data.schema_name,
        schema_sha256=data.schema_sha256(),
        model="test-model",
        backend="instructor",
        prompt_version="1.0.0",
        extracted_at=datetime.now(UTC).isoformat(),
        attempt_count=1,
        cache_hit=False,
        run_id=run_id,
    )
    trace = RunTrace(
        run_id=run_id,
        source_kind="html",
        source_origin="inline://html",
        source_sha256="0" * 64,
        schema_name=data.schema_name,
        schema_sha256=data.schema_sha256(),
        model="test-model",
        backend="instructor",
        outcome="success",
        attempts=[AttemptRecord(index=1, model="test-model", backend="instructor", ok=True)],
    )
    return ExtractionResult(data=data, provenance=prov, run_trace=trace)
