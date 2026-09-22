"""Excel restructuring: read workbooks, generate a TablePlan via LLM, apply it.

Two-phase design (mirrors the HTML/PDF agent):
  1. **Understand** — the LLM sees sheet headers + sample rows and proposes a
     :class:`TablePlan` (validated against the actual sheet columns).
  2. **Apply** — :func:`extractor.tabular.apply_plan` runs the plan
     deterministically over the full dataset, no LLM in the hot path.
"""

from __future__ import annotations

import io
from typing import Any

from extractor.config import Settings
from extractor.errors import ExtractorError
from extractor.llm.instructor_backend import normalize_model_string
from extractor.tabular import TablePlan, TableResult, apply_plan, normalize_cell

# ── workbook loading ─────────────────────────────────────────────────────────

MAX_SHEET_BYTES = 20_000_000  # 20 MB guard
MAX_ROWS_PROMPTED = 40  # rows shown to the LLM for plan generation
PLAN_TARGET_MAX_CHARS = 4000


class WorkbookError(ExtractorError):
    """A workbook could not be parsed or had no usable sheet."""


def load_workbook_tables(
    data: bytes,
    *,
    origin: str = "upload.xlsx",
) -> list[dict[str, Any]]:
    """Parse an xlsx/xls workbook into ``[{name, headers, rows}, ...]``.

    Cells are normalized to JSON-safe scalars via
    :func:`extractor.tabular.normalize_cell`. Raises :class:`WorkbookError` on
    parse failure or when no sheet has at least one header and one row.
    """
    try:
        from openpyxl import load_workbook

        wb = load_workbook(
            io.BytesIO(data),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except Exception as exc:
        raise WorkbookError(f"cannot parse workbook {origin}: {exc}") from exc

    tables: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        rows: list[list[Any]] = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))
        if not rows:
            continue
        headers = [normalize_cell(c) for c in rows[0]]
        body = rows[1:]
        # Skip sheets that are effectively empty
        if not any(str(h or "").strip() for h in headers) and not any(
            any(c is not None for c in r) for r in body
        ):
            continue
        tables.append({"name": ws.title, "headers": headers, "rows": body})
    if not tables:
        raise WorkbookError(
            f"no sheets with data found in {origin}; file may be empty or not a workbook"
        )
    return tables


def sheet_digest(table: dict[str, Any]) -> str:
    """Compact, deterministic text a model can base a TablePlan on."""
    headers = table["headers"]
    rows: list[list[Any]] = table["rows"]
    lines = [
        f"Sheet '{table['name']}' — {len(rows)} data rows, columns: "
        + ", ".join(str(h) for h in headers)
    ]
    for i, row in enumerate(rows[:MAX_ROWS_PROMPTED], start=1):
        cells = ", ".join(f"{h}={_short(row[j]) if j < len(row) else ''}" for j, h in enumerate(headers))
        lines.append(f"{i}. {cells}")
    if len(rows) > MAX_ROWS_PROMPTED:
        lines.append(f"… ({len(rows) - MAX_ROWS_PROMPTED} more rows not shown)")
    return "\n".join(lines)


def _short(value: Any, limit: int = 60) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── plan generation ──────────────────────────────────────────────────────────

PLAN_INSTRUCTIONS = """\
You are a spreadsheet-restructuring planner. Given a sheet digest and a target \
description, output ONE TablePlan JSON object that reshapes the raw sheet into \
the user's target layout.

Column references must use the EXACT header names listed. Choose from these ops:
- keep / drop / rename(target=) — shape the output columns
- trim / upper / lower / titlecase — normalize text
- coerce_number / coerce_int / coerce_text — fix cell types
- fill_default(default=) — replace blanks with a value
- map_values(mapping={"IN": "India", ...}) — canonicalize labels
- concat(sources=[...], target=, separator=) — merge columns into one
- split(target=, separator=) — split one column into target_1, target_2, ...
Add filter (one rule), dedupe, sort, and limit_rows when the target implies it. \
Only include transformations the target actually needs; leave sound columns \
alone. If the target description conflicts with the data, follow the data.
"""


async def generate_plan(
    digest: str,
    target: str,
    *,
    settings: Settings,
    model: str | None = None,
    known_columns: list[str] | None = None,
) -> TablePlan:
    """Ask the model for a TablePlan reshaping the sheet toward ``target``."""

    import instructor
    from pydantic import ValidationError

    from extractor.llm.instructor_backend import _format_pydantic_errors

    normalized = normalize_model_string(model or settings.model)
    client = instructor.from_provider(normalized, async_client=True)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": PLAN_INSTRUCTIONS,
        },
        {
            "role": "user",
            "content": (
                f"## Sheet digest\n{digest}\n\n"
                f"## Target layout\n{target}\n\n"
                "Output the TablePlan JSON object now."
            ),
        },
    ]
    call_kwargs: dict[str, Any] = {}
    if normalized.startswith("litellm/"):
        # instructor v2's litellm provider ignores the model at build time;
        # it must be passed on every call (litellm expects the bare model id).
        call_kwargs["model"] = normalized[len("litellm/") :]
    try:
        plan = await client.chat.completions.create(
            response_model=TablePlan,
            messages=messages,  # type: ignore[arg-type]
            temperature=settings.temperature,
            seed=settings.seed,
            max_retries=2,
            **call_kwargs,
        )
    except ValidationError as exc:
        raise ExtractorError(f"model produced an invalid plan: {_format_pydantic_errors(exc)}") from exc
    except Exception as exc:
        raise ExtractorError(f"plan generation failed: {type(exc).__name__}: {exc}") from exc

    if known_columns:
        _constrain_plan(plan, known_columns)
    return plan


def _constrain_plan(plan: TablePlan, known_columns: list[str]) -> None:
    """Drop plan pieces that reference columns absent from this sheet."""
    known = set(known_columns)

    def ok(col: str) -> bool:
        return col in known

    plan.columns = [
        op
        for op in plan.columns
        if ok(op.column) and (op.op != "concat" or all(s in known for s in op.sources))
    ]
    if plan.filter is not None and not ok(plan.filter.column):
        plan.filter = None
    plan.sort = [s for s in plan.sort if ok(s.column)]


# ── restructure orchestration ────────────────────────────────────────────────


async def restructure_workbook(
    data: bytes,
    target: str,
    *,
    settings: Settings,
    model: str | None = None,
    sheet: str | None = None,
    plan: TablePlan | None = None,
) -> list[TableResult]:
    """Full flow: parse → (LLM plan per sheet | use given plan) → apply → results."""
    tables = load_workbook_tables(data, origin="workbook")
    if sheet:
        wanted = sheet.strip().lower()
        tables = [t for t in tables if str(t["name"]).strip().lower() == wanted]
        if not tables:
            raise WorkbookError(f"sheet '{sheet}' not found in workbook")

    results: list[TableResult] = []
    for table in tables:
        headers = [str(h) if h is not None and str(h).strip() else f"column_{i + 1}" for i, h in enumerate(table["headers"])]
        table_plan = plan
        if table_plan is None:
            table_plan = await generate_plan(
                sheet_digest(table),
                target,
                settings=settings,
                model=model,
                known_columns=headers,
            )
        results.append(
            apply_plan(
                headers,
                table["rows"],
                table_plan,
                sheet_name=str(table["name"]),
            )
        )
    return results


def results_to_xlsx(results: list[TableResult]) -> bytes:
    """Serialize restructured tables to an .xlsx workbook (one sheet per table)."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for result in results:
        title = result.sheet_name or "Sheet1"
        base, n = title, 2
        while title in used or len(title) > 31:
            title = f"{base[:28]}_{n}"
            n += 1
        used.add(title)
        ws = wb.create_sheet(title=title)
        ws.append(list(result.columns))
        for row in result.rows:
            ws.append(list(row))
        ws.freeze_panes = "A2"
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def results_to_csv(results: list[TableResult]) -> str:
    """Single-table CSV (first result)."""
    import csv as _csv

    if not results:
        return ""
    result = results[0]
    out = io.StringIO()
    writer = _csv.writer(out)
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow([_display_csv(v) for v in row])
    return out.getvalue()


def _display_csv(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

