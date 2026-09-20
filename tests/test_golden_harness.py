"""Golden harness tests: fixture extraction + DOM-mutant replay (fake LLM)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from extractor.harness.golden import discover, run_golden_suite
from extractor.schemas.examples import Product
from tests.fixtures.pages import PRODUCT_HTML, PRODUCT_HTML_DRIFTED
from tests.fixtures.responses import valid_product


def _make_golden_dir(
    tmp_path: Path, html: str, mutants: list[dict[str, str]] | None = None
) -> Path:
    (tmp_path / "product_page.html").write_text(html, encoding="utf-8")
    (tmp_path / "product_page.golden.json").write_text(
        json.dumps(valid_product().model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if mutants is not None:
        (tmp_path / "product_page.mutants.json").write_text(
            json.dumps(mutants), encoding="utf-8"
        )
    return tmp_path


async def test_golden_suite_passes_on_fixture_and_drifted_mutant(
    tmp_path: Path, settings: Any, scripted_backend: Any
) -> None:
    """The mutant simulates the drifted DOM; same golden must hold."""
    mutants = [
        {"regex": r"class=\"product\"", "replace": "class=\"ProductHero\""},
        {"regex": r"<h1 class=\"name\">", "replace": "<h2 data-qa=\"product-title\">"},
    ]
    golden_dir = _make_golden_dir(tmp_path, PRODUCT_HTML, mutants)
    backend = scripted_backend([valid_product()] * 3)  # one per variant
    failures = await run_golden_suite(
        golden_dir,
        Product,
        settings=settings,
        replay_only=True,
        fake_backend=backend,
    )
    assert failures == []
    assert len(backend.calls) == 3  # original + 2 mutants, one success each


async def test_golden_suite_detects_mismatch(tmp_path: Path, settings: Any, scripted_backend: Any) -> None:
    golden_dir = _make_golden_dir(tmp_path, PRODUCT_HTML)
    golden_file = golden_dir / "product_page.golden.json"
    expected = json.loads(golden_file.read_text())
    expected["name"] = "WRONG NAME"
    golden_file.write_text(json.dumps(expected))

    backend = scripted_backend([valid_product()])
    failures = await run_golden_suite(
        golden_dir, Product, settings=settings, replay_only=True, fake_backend=backend
    )
    assert failures
    assert "mismatch" in failures[0]


async def test_golden_update_mode_rewrites_golden(tmp_path: Path, settings: Any, scripted_backend: Any) -> None:
    golden_dir = _make_golden_dir(tmp_path, PRODUCT_HTML)
    (golden_dir / "product_page.golden.json").write_text('{"stale": true}')
    backend = scripted_backend([valid_product()])
    failures = await run_golden_suite(
        golden_dir, Product, settings=settings, update=True, replay_only=True, fake_backend=backend
    )
    assert failures == []
    new_golden = json.loads((golden_dir / "product_page.golden.json").read_text())
    assert new_golden["name"] == "Blue Widget"


async def test_drifted_fixture_extracts_same_values(
    tmp_path: Path, settings: Any, scripted_backend: Any
) -> None:
    """End-to-end: the drifted product page passes the identical golden file."""
    golden_dir = tmp_path / "drift"
    golden_dir.mkdir()
    (golden_dir / "product_page.html").write_text(PRODUCT_HTML_DRIFTED, encoding="utf-8")
    (golden_dir / "product_page.golden.json").write_text(
        json.dumps(valid_product().model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    backend = scripted_backend([valid_product()])
    failures = await run_golden_suite(
        golden_dir, Product, settings=settings, replay_only=True, fake_backend=backend
    )
    assert failures == []


async def test_discover_finds_cases(tmp_path: Path) -> None:
    _make_golden_dir(tmp_path, PRODUCT_HTML)
    cases = discover(tmp_path)
    assert len(cases) == 1
    assert cases[0].name == "product_page"
    assert cases[0].golden is not None
