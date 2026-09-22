"""Restructure endpoints: Excel upload, LLM plan, deterministic apply, export.

Stateless design: the frontend uploads the workbook with each call and either
(1) asks for a generated plan + preview, (2) applies a chosen plan and gets the
full result, or (3) applies a plan and downloads the restructured .xlsx.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from extractor.errors import ExtractorError, RestructureError
from extractor.excel import (
    WorkbookError,
    generate_plan,
    load_workbook_tables,
    restructure_workbook,
    results_to_xlsx,
    sheet_digest,
)
from extractor.tabular import ColumnOpKind, TablePlan, apply_plan

MAX_UPLOAD_BYTES = 20_000_000

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter()


def _pool() -> Any:
    """Lazy pool import (avoids a circular import with extractor.api.main)."""
    from extractor.api.main import pool

    return pool


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file too large (max {MAX_UPLOAD_BYTES // 1_000_000} MB)",
        )
    if len(data) < 4:
        raise HTTPException(status_code=400, detail="empty file")
    return data


def _parse_plan(plan_json: str) -> TablePlan | None:
    if not plan_json.strip():
        return None
    try:
        raw = json.loads(plan_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"plan is not valid JSON: {exc}") from exc
    try:
        return TablePlan.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid plan: {exc}") from exc


def _pick_table(
    tables: list[dict[str, Any]], sheet: str
) -> dict[str, Any]:
    if not sheet.strip():
        return tables[0]
    wanted = sheet.strip().lower()
    for t in tables:
        if str(t["name"]).strip().lower() == wanted:
            return t
    available = ", ".join(str(t["name"]) for t in tables)
    raise HTTPException(status_code=404, detail=f"sheet '{sheet}' not found (available: {available})")


def _plain_english(plan: TablePlan) -> list[str]:
    """Human-readable step list for the UI."""
    steps: list[str] = []
    for op in plan.columns:
        col = op.column
        if op.op is ColumnOpKind.keep:
            steps.append(f"Keep '{col}'")
        elif op.op is ColumnOpKind.drop:
            steps.append(f"Drop '{col}'")
        elif op.op is ColumnOpKind.rename:
            steps.append(f"Rename '{col}' to '{op.target or col}'")
        elif op.op in (ColumnOpKind.trim, ColumnOpKind.upper, ColumnOpKind.lower, ColumnOpKind.titlecase):
            steps.append(f"{op.op.value.replace('titlecase', 'Title-case')} '{col}'")
        elif op.op in (ColumnOpKind.coerce_number, ColumnOpKind.coerce_int, ColumnOpKind.coerce_text):
            steps.append(f"Convert '{col}' to {op.op.value.split('_')[1]}")
        elif op.op is ColumnOpKind.fill_default:
            steps.append(f"Fill blanks in '{col}' with {op.default!r}")
        elif op.op is ColumnOpKind.map_values:
            steps.append(f"Map values in '{col}' ({len(op.mapping)} mappings)")
        elif op.op is ColumnOpKind.concat:
            steps.append(f"Concat {op.sources} into '{op.target or col + '_concat'}' with separator {op.separator!r}")
        elif op.op is ColumnOpKind.split:
            steps.append(f"Split '{col}' on {op.separator!r} into '{op.target or col}_1..n'")
    if plan.filter is not None:
        steps.append(f"Filter: {plan.filter.column} {plan.filter.op.value} {plan.filter.value!r}")
    if plan.dedupe:
        steps.append("Remove duplicate rows")
    if plan.sort:
        steps.append("Sort by " + ", ".join(f"{r.column} ({'desc' if r.descending else 'asc'})" for r in plan.sort))
    if plan.limit_rows is not None:
        steps.append(f"Limit to first {plan.limit_rows} rows")
    return steps


@router.post("/api/restructure/plan")
async def restructure_plan(
    file: UploadFile = File(...),
    target: str = Form(...),
    sheet: str = Form(""),
    model: str = Form(""),
) -> dict[str, Any]:
    """Upload + target → LLM-generated plan, plain-English steps, and preview."""
    _, settings = _pool().get()
    data = await _read_upload(file)
    try:
        tables = load_workbook_tables(data, origin=file.filename or "workbook.xlsx")
        table = _pick_table(tables, sheet)
        headers = [str(h) if h is not None and str(h).strip() else f"column_{i + 1}" for i, h in enumerate(table["headers"])]
        plan = await generate_plan(
            sheet_digest(table),
            target,
            settings=settings,
            model=model or None,
            known_columns=headers,
        )
        result = apply_plan(headers, table["rows"], plan, sheet_name=str(table["name"]))
    except WorkbookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "sheet_name": result.sheet_name,
        "sheets": [str(t["name"]) for t in tables],
        "plan": plan.model_dump(),
        "steps": _plain_english(plan),
        "preview": {
            "columns": result.columns,
            "rows": result.rows[:25],
            "stats": result.stats.model_dump(),
        },
    }


@router.post("/api/restructure/apply")
async def restructure_apply(
    file: UploadFile = File(...),
    plan: str = Form(...),
    sheet: str = Form(""),
) -> dict[str, Any]:
    """Apply an explicit plan to a sheet → full rows + stats (no download)."""
    data = await _read_upload(file)
    table_plan = _parse_plan(plan)
    if table_plan is None:
        raise HTTPException(status_code=400, detail="plan is required")
    try:
        tables = load_workbook_tables(data, origin=file.filename or "workbook.xlsx")
        table = _pick_table(tables, sheet)
        headers = [str(h) if h is not None and str(h).strip() else f"column_{i + 1}" for i, h in enumerate(table["headers"])]
        result = apply_plan(headers, table["rows"], table_plan, sheet_name=str(table["name"]))
    except WorkbookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RestructureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "sheet_name": result.sheet_name,
        "columns": result.columns,
        "rows": result.rows,
        "stats": result.stats.model_dump(),
        "steps": _plain_english(table_plan),
    }


@router.post("/api/restructure/download")
async def restructure_download(
    file: UploadFile = File(...),
    plan: str = Form(...),
    sheet: str = Form(""),
) -> Response:
    """Apply an explicit plan and download the restructured .xlsx."""
    data = await _read_upload(file)
    table_plan = _parse_plan(plan)
    if table_plan is None:
        raise HTTPException(status_code=400, detail="plan is required")
    try:
        tables = load_workbook_tables(data, origin=file.filename or "workbook.xlsx")
        table = _pick_table(tables, sheet)
        headers = [str(h) if h is not None and str(h).strip() else f"column_{i + 1}" for i, h in enumerate(table["headers"])]
        result = apply_plan(headers, table["rows"], table_plan, sheet_name=str(table["name"]))
        xlsx = results_to_xlsx([result])
    except WorkbookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RestructureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        content=xlsx,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="restructured.xlsx"'},
    )


@router.post("/api/restructure/quick")
async def restructure_quick(
    file: UploadFile = File(...),
    target: str = Form(...),
    sheet: str = Form(""),
    model: str = Form(""),
) -> Response:
    """One-shot: upload + target → LLM plan applied to every sheet → .xlsx."""
    _, settings = _pool().get()
    data = await _read_upload(file)
    try:
        results = await restructure_workbook(
            data,
            target,
            settings=settings,
            model=model or None,
            sheet=sheet or None,
        )
        xlsx = results_to_xlsx(results)
    except WorkbookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(
        content=xlsx,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="restructured.xlsx"'},
    )
