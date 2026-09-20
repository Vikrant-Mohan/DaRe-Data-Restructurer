"""RunTrace: a complete, replayable audit record for one extraction run.

Every LLM attempt — prompt size, raw output, validation errors, token usage
and cost — is recorded, so any failed run can be replayed and inspected.
"""

from __future__ import annotations

import json
import platform
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from extractor.version import PROMPT_VERSION


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a dict or an object (LiteLLM usage objects vary)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def extract_usage(raw: Any) -> tuple[dict[str, int], float | None]:
    """Pull token usage and response cost out of a LiteLLM completion object."""
    usage: dict[str, int] = {}
    cost: float | None = None
    try:
        u = _get(raw, "usage")
        if u is not None:
            usage = {
                "prompt_tokens": int(_get(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(_get(u, "completion_tokens", 0) or 0),
                "total_tokens": int(_get(u, "total_tokens", 0) or 0),
            }
        hidden = _get(raw, "_hidden_params") or {}
        if isinstance(hidden, dict):
            rc = hidden.get("response_cost")
            if isinstance(rc, int | float):
                cost = float(rc)
    except Exception:  # noqa: BLE001 - audit must never break the pipeline
        pass
    return usage, cost


class AttemptRecord(BaseModel):
    """One LLM call attempt, including the validation errors it produced."""

    index: int  # 1-based attempt number within the run
    kind: str = "base"  # base | escalation:dom | escalation:image
    model: str
    backend: str
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    ok: bool = False
    prompt_chars: int = 0
    prompt_excerpt: str = ""  # head+tail of the final user message
    raw_output: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost_usd: float | None = None
    error: str | None = None  # backend-level exception text


class RunTrace(BaseModel):
    """Full audit of one run: inputs, every attempt, final outcome."""

    run_id: str
    source_kind: str  # html | pdf
    source_origin: str  # url or file path
    source_sha256: str
    schema_name: str
    schema_sha256: str
    model: str
    backend: str
    prompt_version: str = PROMPT_VERSION
    cache_key: str = ""
    cache_hit: bool = False
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    outcome: str = "pending"  # success | failed | cache_hit | source_error
    result_json: dict[str, Any] | None = None
    error: str | None = None
    total_cost_usd: float = 0.0
    environment: dict[str, str] = Field(default_factory=lambda: _env_info())

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def summary(self) -> str:
        """Human-readable one-page summary of the run."""
        lines = [
            f"run        {self.run_id}",
            f"outcome    {self.outcome}  (attempts: {self.attempt_count})",
            f"source     {self.source_kind} {self.source_origin}",
            f"           sha256={self.source_sha256[:16]}…",
            f"schema     {self.schema_name} sha256={self.schema_sha256[:16]}…",
            f"model      {self.model}  backend={self.backend}  "
            f"prompt_v={self.prompt_version}",
            f"cost       ${self.total_cost_usd:.6f}",
        ]
        for a in self.attempts:
            status = "ok" if a.ok else "error"
            errs = "; ".join(a.validation_errors) or a.error or "-"
            lines.append(
                f"attempt {a.index} [{a.kind}] {status}  {errs[:160]}"
            )
        if self.error:
            lines.append(f"error      {self.error}")
        return "\n".join(lines)

    def to_jsonl(self, directory: Path) -> Path:
        """Write a replayable JSONL record: header, one line per attempt, outcome."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.jsonl"
        rows: list[dict[str, Any]] = []
        header = self.model_dump(mode="json")
        header["type"] = "run"
        rows.append(header)
        for a in self.attempts:
            row = a.model_dump(mode="json")
            row["type"] = "attempt"
            rows.append(row)
        outcome = {"type": "outcome", "outcome": self.outcome, "error": self.error}
        rows.append(outcome)
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")
        return path

    @classmethod
    def from_jsonl(cls, path: Path) -> RunTrace:
        """Reconstruct a RunTrace from its JSONL record."""
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        header = next(r for r in rows if r.get("type") == "run")
        attempts = [r for r in rows if r.get("type") == "attempt"]
        header.pop("type", None)
        trace = cls(**header)
        trace.attempts = [AttemptRecord(**a) for a in attempts]
        return trace


def _env_info() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "prompt_version": PROMPT_VERSION,
    }
