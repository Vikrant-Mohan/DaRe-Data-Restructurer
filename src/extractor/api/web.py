"""Web UI router: schema introspection + plan template + static SPA mount."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


def _ui_dir() -> Path:
    """Locate the ui/ assets in dev and in the PyInstaller onedir bundle."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        for candidate in (base / "_internal" / "ui", base / "ui"):
            if candidate.is_dir():
                return candidate
    return Path(__file__).resolve().parents[3] / "ui"


_UI_DIST = _ui_dir()


def _type_name(prop: dict[str, Any]) -> str:
    any_of = prop.get("anyOf")
    optional = ""
    if isinstance(any_of, list) and any(p.get("type") == "null" for p in any_of):
        optional = " | null"
    if "enum" in prop:
        return "enum: " + " | ".join(str(v) for v in prop["enum"]) + optional
    kind = prop.get("format") or prop.get("type") or "any"
    return f"{kind}{optional}"


def _field_info(cls: Any) -> list[dict[str, Any]]:
    schema = cls.model_json_schema()
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    out: list[dict[str, Any]] = []
    for name, prop in props.items():
        out.append(
            {
                "name": name,
                "type": _type_name(prop),
                "required": name in required,
                "description": prop.get("description", ""),
            }
        )
    return out


def _describe_schema(cls: Any) -> dict[str, Any]:
    return {
        "name": cls.schema_name,
        "description": cls.schema_description,
        "css_hints": list(cls.css_hints),
        "fields": _field_info(cls),
    }


@router.get("/api/health")
async def api_health() -> dict[str, Any]:
    """UI-facing health: registered schemas + configured model."""
    from extractor.api.main import pool

    agent, settings = pool.get()
    return {
        "status": "ok",
        "schemas": agent.registry.names(),
        "model": settings.model,
    }


@router.get("/api/schemas")
async def schemas_detail() -> dict[str, list[dict[str, Any]]]:
    """Full schema details (fields, types) for the UI dropdown."""
    from extractor.api.main import pool

    agent, _ = pool.get()
    return {
        "schemas": [
            _describe_schema(agent.registry.get(name)) for name in agent.registry.names()
        ]
    }


@router.get("/api/plan-template")
async def plan_template() -> dict[str, Any]:
    """Column ops + filter reference for the plan editor."""
    return {
        "ops": [
            "keep",
            "drop",
            "rename",
            "trim",
            "upper",
            "lower",
            "titlecase",
            "coerce_number",
            "coerce_int",
            "coerce_text",
            "fill_default",
            "map_values",
            "concat",
            "split",
        ],
        "filters": [
            "eq",
            "neq",
            "contains",
            "not_contains",
            "empty",
            "not_empty",
            "gt",
            "gte",
            "lt",
            "lte",
            "in",
        ],
    }


@router.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str) -> FileResponse:
    """Serve the static UI; unknown paths fall back to the SPA index."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    root = _UI_DIST.resolve()
    candidate = (root / full_path).resolve()
    if candidate.is_file() and str(candidate).startswith(str(root)):
        return FileResponse(candidate)
    index = root / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="UI not built")
    return FileResponse(index)
