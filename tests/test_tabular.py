"""Tests for the deterministic TablePlan engine (extractor.tabular)."""

from __future__ import annotations

from typing import Any

import pytest

from extractor.errors import RestructureError
from extractor.tabular import (
    ColumnOp,
    ColumnOpKind,
    FilterOp,
    FilterRule,
    SortRule,
    TablePlan,
    apply_plan,
    normalize_cell,
)

HEADERS = ["Order ID", "Customer", "Country", "Total", "Notes"]
ROWS: list[list[Any]] = [
    [1001, "  Ada Lovelace ", "in", "1,250.50", ""],
    [1002, "Grace Hopper", "US", "$990", None],
    [1003, "Alan Turing", "IN", 450, "priority"],
    [1001, "  Ada Lovelace ", "in", "1,250.50", ""],
    [None, None, None, None, None],  # fully empty row
]


def plan(**kwargs: Any) -> TablePlan:
    return TablePlan(**kwargs)


def test_normalize_cell_types() -> None:
    import datetime as dt

    assert normalize_cell(None) is None
    assert normalize_cell(True) is True
    assert normalize_cell(dt.date(2026, 3, 5)) == "2026-03-05"
    assert normalize_cell(dt.datetime(2026, 3, 5, 7, 8)) == "2026-03-05T07:08:00"
    from decimal import Decimal

    assert normalize_cell(Decimal("1.5")) == 1.5


def test_empty_rows_dropped_and_stats() -> None:
    result = apply_plan(HEADERS, ROWS, plan())
    assert result.stats.rows_in == 5
    assert result.stats.dropped_rows == 1
    assert result.stats.rows_out == 4
    assert result.columns == HEADERS  # untouched plan keeps original layout


def test_rename_trim_coerce_chain() -> None:
    p = plan(
        columns=[
            ColumnOp(op=ColumnOpKind.rename, column="Order ID", target="order_id"),
            ColumnOp(op=ColumnOpKind.trim, column="Customer"),
            ColumnOp(op=ColumnOpKind.coerce_number, column="Total"),
            ColumnOp(op=ColumnOpKind.upper_case, column="Country"),
        ]
    )
    result = apply_plan(HEADERS, ROWS, p)
    assert result.columns[0] == "order_id"
    assert result.rows[0] == [1001, "Ada Lovelace", "IN", 1250.5, ""]


def test_coerce_int_counts_failures() -> None:
    p = plan(columns=[ColumnOp(op=ColumnOpKind.coerce_int, column="Customer")])
    result = apply_plan(HEADERS, ROWS, p)
    assert result.stats.coerce_failures["Customer"] == 4  # every row has a name


def test_dedupe_on_raw_columns() -> None:
    result = apply_plan(HEADERS, ROWS, plan(dedupe=True))
    assert result.stats.deduped_rows == 1
    assert result.stats.rows_out == 3


def test_filter_contains_and_numeric_gt() -> None:
    p = plan(filter=FilterRule(column="Country", op=FilterOp.contains, value="n"))
    result = apply_plan(HEADERS, ROWS, p)
    assert result.stats.rows_out == 3  # 'in', 'IN', dup 'in'; 'us' has no 'n'
    p2 = plan(filter=FilterRule(column="Total", op=FilterOp.gte, value=900))
    result2 = apply_plan(HEADERS, ROWS, p2)
    assert result2.stats.rows_out == 3  # 1250.50, $990, dup 1250.50


def test_map_values_and_fill_default() -> None:
    p = plan(
        columns=[
            ColumnOp(op=ColumnOpKind.upper_case, column="Country"),
            ColumnOp(op=ColumnOpKind.map_values, column="Country", mapping={"IN": "India", "US": "United States"}),
            ColumnOp(op=ColumnOpKind.fill_default, column="Notes", default="standard"),
        ]
    )
    result = apply_plan(HEADERS, ROWS, p)
    countries = {r[2] for r in result.rows}
    assert countries == {"India", "United States"}
    notes = [r[4] for r in result.rows]
    assert notes.count("standard") == 3  # the two blanks + the empty string
    assert "priority" in notes  # non-blank values are preserved


def test_concat_and_split() -> None:
    p = plan(
        columns=[
            ColumnOp(op=ColumnOpKind.concat, column="Customer", sources=["Customer", "Country"], target="label", separator="-"),
        ]
    )
    result = apply_plan(HEADERS, ROWS, p)
    assert "label" in result.columns
    assert result.rows[0][result.columns.index("label")] == "  Ada Lovelace -in"

    p2 = plan(columns=[ColumnOp(op=ColumnOpKind.split_column, column="Customer", target="name", separator=" ")])
    result2 = apply_plan(HEADERS, ROWS, p2)
    assert "name_1" in result2.columns and "name_2" in result2.columns
    assert "Customer" not in result2.columns


def test_sort_numeric_aware() -> None:
    p = plan(
        columns=[ColumnOp(op=ColumnOpKind.coerce_number, column="Total")],
        sort=[SortRule(column="Total", descending=True)],
    )
    result = apply_plan(HEADERS, ROWS, p)
    totals = [r[3] for r in result.rows]
    assert totals == [1250.5, 1250.5, 990.0, 450.0]


def test_limit_rows() -> None:
    result = apply_plan(HEADERS, ROWS, plan(limit_rows=2))
    assert result.stats.rows_out == 2


def test_unknown_column_raises() -> None:
    with pytest.raises(RestructureError):
        apply_plan(HEADERS, ROWS, plan(columns=[ColumnOp(op=ColumnOpKind.trim, column="Nope")]))
    with pytest.raises(RestructureError):
        apply_plan(HEADERS, ROWS, plan(filter=FilterRule(column="Nope", op=FilterOp.empty)))
    with pytest.raises(RestructureError):
        apply_plan(HEADERS, ROWS, plan(sort=[SortRule(column="Nope")]))


def test_blank_headers_get_names() -> None:
    result = apply_plan(["A", None, ""], [[1, 2, 3]], plan())
    assert result.columns == ["A", "column_2", "column_3"]
    assert result.rows == [[1, 2, 3]]


def test_to_dicts_roundtrip() -> None:
    result = apply_plan(HEADERS, ROWS, plan())
    dicts = result.to_dicts()
    assert dicts[0]["Order ID"] == 1001
