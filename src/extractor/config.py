"""Central configuration via environment / .env (pydantic-settings)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _runtime_base() -> Path:
    """Base dir for runtime artifacts (cache, audit, user schemas).

    Deployment filesystems like Vercel Functions are read-only except for
    ``/tmp``; probe writability once and fall back so cache/audit writes
    never crash the app in production.
    """
    cwd = Path.cwd()
    try:
        with tempfile.TemporaryFile(dir=cwd):
            return cwd
    except OSError:
        return Path(tempfile.gettempdir())


_RUNTIME_BASE = _runtime_base()


class Settings(BaseSettings):
    """Runtime configuration, all env-driven (prefix ``EXTRACT_``)."""

    model_config = SettingsConfigDict(
        env_prefix="EXTRACT_",
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Model routing (any LiteLLM provider string) ─────────────────────────
    model: str = "litellm/gpt-4o-mini"
    vision_model: str = ""  # empty -> fall back to `model`

    # ── Extraction backend ──────────────────────────────────────────────────
    backend: Literal["instructor", "outlines"] = "instructor"

    # ── Determinism ─────────────────────────────────────────────────────────
    temperature: float = 0.0
    seed: int | None = 7

    # ── Retry / feedback loop ───────────────────────────────────────────────
    max_attempts: int = Field(default=4, ge=1)
    max_escalations: int = Field(default=2, ge=0)

    # ── Playwright ──────────────────────────────────────────────────────────
    page_timeout_ms: int = 45_000
    nav_wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = (
        "networkidle"
    )
    user_agent: str | None = None

    # ── PDF ─────────────────────────────────────────────────────────────────
    # Chars per square inch; pages below this density are treated as scanned
    # (no usable text layer) and routed through the vision model.
    pdf_text_density: float = 5.0
    pdf_dpi: int = 170

    # ── Cache ───────────────────────────────────────────────────────────────
    cache_dir: Path = Field(default_factory=lambda: _RUNTIME_BASE / ".cache")
    cache_ttl_hours: float = 24.0

    # ── Misc ────────────────────────────────────────────────────────────────
    user_schema_dir: Path = Field(default_factory=lambda: _RUNTIME_BASE / "schemas_user")
    audit_dir: Path = Field(default_factory=lambda: _RUNTIME_BASE / "audit")
    log_level: str = "INFO"

    @property
    def effective_vision_model(self) -> str:
        """Model used for scanned-PDF page images (vision-capable)."""
        return self.vision_model or self.model
