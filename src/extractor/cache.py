"""Deterministic hashing and a file-backed result cache.

Cache keys combine source hash, schema hash, model, backend, prompt version
and an options fingerprint, so identical inputs always replay the identical
cached result and any input change forces a fresh extraction.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def new_id(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class CacheKey:
    """Everything that determines the extraction output for a request."""

    source_sha256: str
    schema_sha256: str
    model: str
    backend: str
    prompt_version: str
    options_fingerprint: str  # canonical JSON of extraction options

    def as_string(self) -> str:
        joined = "|".join(
            [
                self.source_sha256,
                self.schema_sha256,
                self.model,
                self.backend,
                self.prompt_version,
                self.options_fingerprint,
            ]
        )
        return sha256_text(joined)


def canonical_fingerprint(obj: Any) -> str:
    """Stable JSON fingerprint (sorted keys, no whitespace)."""
    return sha256_text(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    )


class ResultCache:
    """File-backed cache storing validated extraction results as JSON."""

    def __init__(self, directory: Path, ttl_hours: float) -> None:
        self.directory = directory
        self.ttl_seconds = max(0.0, ttl_hours * 3600.0)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        # ttl_seconds == 0 disables reuse entirely (immune to clock skew).
        if self.ttl_seconds == 0 or (time.time() - path.stat().st_mtime) > self.ttl_seconds:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def put(self, key: str, result: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
