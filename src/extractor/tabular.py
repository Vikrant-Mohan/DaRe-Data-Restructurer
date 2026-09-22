"""Deterministic table restructuring: apply a declarative plan to tabular data.

This module is deliberately LLM-free: a ``TablePlan`` is a small, serializable
spec (per-column ops, filter, sort, dedupe) that maps any workbook/CSV into a
normalized target layout. The same plan always yields identical output for the
same input, so it is auditable and safe to run in bulk.
"""

from __future__ import annotations

import datetime as _dt
import math
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from extractor.errors import RestructureError

# ── cell normalization ───────────────────────────────────────────────────────


def normalize_cell(value: Any) -> Any:
    """Map an openpyxl/CSV cell onto a JSON-safe scalar (None, str, number)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.UTC).replace(tzinfo=None)
        return value.isoformat(sep="T")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, _dt.time):
        return value.isoformat(timespec="seconds")
    if isinstance(value, _dt.timedelta):
        total = value.total_seconds()
        hours, rem = divmod(int(total), 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return value if isinstance(value, str) else str(value)


def _display(value: Any) -> str:
    """Render a cell for display contexts (concat, text filters)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "").replace("%", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


# ── plan model ───────────────────────────────────────────────────────────────


class ColumnOpKind(StrEnum):
    keep = "keep"
    drop = "drop"
    rename = "rename"
    trim = "trim"
    upper_case = "upper"  # member names avoid shadowing str methods (upper/lower/split)
    lower_case = "lower"
    titlecase = "titlecase"
    coerce_number = "coerce_number"
    coerce_int = "coerce_int"
    coerce_text = "coerce_text"
    fill_default = "fill_default"
    map_values = "map_values"
    concat = "concat"
    split_column = "split"


class ColumnOp(BaseModel):
    """One transformation on one column (or into a new column)."""

    op: ColumnOpKind
    column: str
    target: str = ""  # rename target / concat & split output base name
    sources: list[str] = Field(default_factory=list)  # concat inputs
    separator: str = " "
    mapping: dict[str, Any] = Field(default_factory=dict)  # map_values
    default: Any = None  # fill_default value

    @field_validator("column")
    @classmethod
    def _column_required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("column must be a non-empty header name")
        return v.strip()


class FilterOp(StrEnum):
    eq = "eq"
    neq = "neq"
    contains = "contains"
    not_contains = "not_contains"
    empty = "empty"
    not_empty = "not_empty"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    in_list = "in"


class FilterRule(BaseModel):
    column: str
    op: FilterOp
    value: Any = None


class SortRule(BaseModel):
    column: str
    descending: bool = False


class TablePlan(BaseModel):
    """A serializable, deterministic restructuring spec."""

    name: str = ""
    columns: list[ColumnOp] = Field(default_factory=list)
    filter: FilterRule | None = None
    drop_empty_rows: bool = True
    dedupe: bool = False
    sort: list[SortRule] = Field(default_factory=list)
    limit_rows: int | None = Field(default=None, ge=0)


class TableStats(BaseModel):
    rows_in: int = 0
    rows_out: int = 0
    columns_in: int = 0
    columns_out: int = 0
    dropped_rows: int = 0
    deduped_rows: int = 0
    filtered_rows: int = 0
    coerce_failures: dict[str, int] = Field(default_factory=dict)


class TableResult(BaseModel):
    """Output of applying a plan: normalized columns + rows + stats."""

    columns: list[str]
    rows: list[list[Any]]
    stats: TableStats
    plan: TablePlan
    sheet_name: str = ""

    def to_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]


# ── engine ───────────────────────────────────────────────────────────────────


def apply_plan(
    headers: list[str],
    rows: list[list[Any]],
    plan: TablePlan,
    *,
    sheet_name: str = "",
) -> TableResult:
    """Apply ``plan`` to raw table data. Pure function; raises RestructureError."""
    stats = TableStats(rows_in=len(rows), columns_in=len(headers))
    known = [
        str(h).strip() if h is not None and str(h).strip() != "" else f"column_{i + 1}"
        for i, h in enumerate(headers)
    ]
    _validate_plan(plan, known)

    records: list[dict[str, Any]] = []
    for row in rows:
        rec: dict[str, Any] = {}
        for i, header in enumerate(known):
            rec[header] = normalize_cell(row[i]) if i < len(row) else None
        records.append(rec)

    if plan.drop_empty_rows:
        before = len(records)
        records = [
            r
            for r in records
            if any(v is not None and str(v).strip() != "" for v in r.values())
        ]
        stats.dropped_rows = before - len(records)

    for op in plan.columns:
        records = [_apply_op(r, op, stats) for r in records]

    if plan.filter is not None:
        before = len(records)
        records = [r for r in records if _matches(r, plan.filter)]
        stats.filtered_rows = before - len(records)

    if plan.dedupe:
        seen: set[tuple[Any, ...]] = set()
        unique: list[dict[str, Any]] = []
        for r in records:
            key = tuple(_display(r.get(c)) for c in known)
            if key not in seen:
                seen.add(key)
                unique.append(r)
        stats.deduped_rows = len(records) - len(unique)
        records = unique

    # stable multi-key sort: apply rules last-to-first so the first rule wins
    for rule in reversed(plan.sort):
        _apply_sort(records, rule)

    if plan.limit_rows is not None:
        records = records[: plan.limit_rows]

    columns = _output_columns(plan, known, records)
    out_rows = [[r.get(c) for c in columns] for r in records]

    stats.rows_out = len(out_rows)
    stats.columns_out = len(columns)
    return TableResult(
        columns=columns,
        rows=out_rows,
        stats=stats,
        plan=plan,
        sheet_name=sheet_name,
    )


def _apply_sort(records: list[dict[str, Any]], rule: SortRule) -> None:
    """Stable sort on one rule; numbers before text before blanks."""

    def key(r: dict[str, Any]) -> tuple[int, Any]:
        return _sort_key(r.get(rule.column))

    records.sort(key=key, reverse=rule.descending)


def _validate_plan(plan: TablePlan, known: list[str]) -> None:
    known_set = set(known)

    def need(col: str, what: str) -> None:
        if col not in known_set:
            raise RestructureError(
                f"{what}: unknown column '{col}' (available: {', '.join(known)})"
            )

    rename_counts: dict[str, int] = {}
    for op in plan.columns:
        if op.op is ColumnOpKind.concat:
            if len(op.sources) < 2:
                raise RestructureError("concat needs at least two 'sources' columns")
            for src in op.sources:
                need(src, f"concat '{op.column}'")
        elif op.op is not ColumnOpKind.split:
            need(op.column, f"op '{op.op.value}'")
        if op.op is ColumnOpKind.rename:
            rename_counts[op.column] = rename_counts.get(op.column, 0) + 1
    for col, count in rename_counts.items():
        if count > 1:
            raise RestructureError(f"column '{col}' renamed more than once")

    if plan.filter is not None:
        need(plan.filter.column, "filter")
    for rule in plan.sort:
        need(rule.column, "sort")


def _apply_op(rec: dict[str, Any], op: ColumnOp, stats: TableStats) -> dict[str, Any]:
    col = op.column
    if op.op is ColumnOpKind.keep:
        return rec
    if op.op is ColumnOpKind.drop:
        rec.pop(col, None)
    elif op.op is ColumnOpKind.rename:
        target = op.target.strip() or col
        if target != col:
            rec[target] = rec.pop(col, None)
    elif op.op is ColumnOpKind.trim:
        v = rec.get(col)
        rec[col] = v.strip() if isinstance(v, str) else v
    elif op.op in (ColumnOpKind.upper_case, ColumnOpKind.lower_case, ColumnOpKind.titlecase):
        v = rec.get(col)
        if isinstance(v, str):
            rec[col] = v.upper() if op.op is ColumnOpKind.upper_case else (
                v.lower() if op.op is ColumnOpKind.lower_case else v.title()
            )
    elif op.op in (ColumnOpKind.coerce_number, ColumnOpKind.coerce_int, ColumnOpKind.coerce_text):
        rec[col] = _coerce(rec.get(col), op.op, stats, col)
    elif op.op is ColumnOpKind.fill_default:
        v = rec.get(col)
        if v is None or (isinstance(v, str) and not v.strip()):
            rec[col] = op.default
    elif op.op is ColumnOpKind.map_values:
        key = _display(rec.get(col))
        if key in op.mapping:
            rec[col] = op.mapping[key]
    elif op.op is ColumnOpKind.concat:
        target = op.target.strip() or f"{col}_concat"
        parts = [_display(rec.get(src)) for src in op.sources]
        rec[target] = op.separator.join(parts)
    elif op.op is ColumnOpKind.split_column:
        target = op.target.strip() or col
        parts = _display(rec.get(col)).split(op.separator) if op.separator else list(_display(rec.get(col)))
        rec.pop(col, None)
        for i, part in enumerate(parts, start=1):
            if i > 12:  # safety bound on runaway splits
                break
            rec[f"{target}_{i}"] = part if part != "" else None
    else:  # pragma: no cover - enum complete
        raise RestructureError(f"unsupported op: {op.op}")
    return rec


def _coerce(value: Any, kind: ColumnOpKind, stats: TableStats, column: str) -> Any:
    if kind is ColumnOpKind.coerce_text:
        return _display(value) if value is not None else None
    num = _as_number(value)
    if num is None:
        if value is not None:
            stats.coerce_failures[column] = stats.coerce_failures.get(column, 0) + 1
        return None
    return int(round(num)) if kind is ColumnOpKind.coerce_int else num


def _matches(rec: dict[str, Any], rule: FilterRule) -> bool:
    v = rec.get(rule.column)
    op = rule.op
    if op is FilterOp.empty:
        return v is None or (isinstance(v, str) and not v.strip())
    if op is FilterOp.not_empty:
        return not (v is None or (isinstance(v, str) and not v.strip()))
    if op is FilterOp.contains:
        return rule.value is not None and str(rule.value).lower() in _display(v).lower()
    if op is FilterOp.not_contains:
        return rule.value is None or str(rule.value).lower() not in _display(v).lower()
    if op is FilterOp.in_list:
        options = rule.value if isinstance(rule.value, list) else [rule.value]
        return _display(v) in {_display(o) for o in options}
    # numeric comparison when both sides parse as numbers, else string
    a_num, b_num = _as_number(v), _as_number(rule.value)
    if a_num is not None and b_num is not None:
        a: Any = a_num
        b: Any = b_num
    else:
        a, b = _display(v), _display(rule.value)
    if op is FilterOp.eq:
        return a == b
    if op is FilterOp.neq:
        return a != b
    if op is FilterOp.gt:
        return a > b
    if op is FilterOp.gte:
        return a >= b
    if op is FilterOp.lt:
        return a < b
    if op is FilterOp.lte:
        return a <= b
    raise RestructureError(f"unsupported filter op: {op}")  # pragma: no cover


def _sort_key(value: Any) -> tuple[int, Any]:
    """Sort key: numbers first, then text, then blanks (per direction)."""
    num = _as_number(value)
    if num is not None:
        return (0, num)
    if value is None or (isinstance(value, str) and not value.strip()):
        return (2, "")
    return (1, _display(value).lower())


def _output_columns(
    plan: TablePlan, known: list[str], records: list[dict[str, Any]]
) -> list[str]:
    """Final column order: planned keeps/renames first, then all other keys."""
    columns: list[str] = []
    dropped = {op.column for op in plan.columns if op.op is ColumnOpKind.drop}
    for op in plan.columns:
        if op.op is ColumnOpKind.rename:
            target = op.target.strip() or op.column
            if target not in columns:
                columns.append(target)
        elif op.op is ColumnOpKind.keep and op.column not in columns:
            columns.append(op.column)
    seen = set(columns)
    for r in records:
        for key in r:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    if not records:  # nothing survived: fall back to the original layout
        for col in known:
            if col not in dropped and col not in columns:
                columns.append(col)
    return [c for c in columns if c not in dropped]
