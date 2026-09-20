"""Golden-file regression harness.

Layout inside the golden directory:
    <name>.html / <name>.pdf   raw source fixture
    <name>.golden.json         expected extraction (the "golden" output)
    <name>.mutants.json        optional: list of DOM mutations to replay
                               (regex, replacement) that simulate drift

The harness runs extraction against each fixture (replaying recorded LLM
outputs when --no-llm) and asserts the result matches the golden file. After
each mutation replay the extraction must still match — that is the regression
net for DOM drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extractor.config import Settings
from extractor.pipeline import ExtractionAgent, ExtractionOptions
from extractor.schemas.base import BaseExtraction


@dataclass
class GoldenCase:
    source: Path
    golden: Path | None
    mutants: list[tuple[str, str]]

    @property
    def name(self) -> str:
        return self.source.stem


def discover(directory: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for src in sorted(directory.glob("*.html")) + sorted(directory.glob("*.pdf")):
        golden = src.with_suffix(".golden.json")
        mutants_path = src.with_suffix(".mutants.json")
        mutants: list[tuple[str, str]] = []
        if mutants_path.exists():
            raw = json.loads(mutants_path.read_text(encoding="utf-8"))
            mutants = [(m["regex"], m["replace"]) for m in raw]
        cases.append(GoldenCase(source=src, golden=golden if golden.exists() else None, mutants=mutants))
    return cases


def _mutate(html: str, mutants: list[tuple[str, str]]) -> str:
    import re

    for pattern, replacement in mutants:
        html = re.sub(pattern, replacement, html)
    return html


async def run_golden_suite(
    directory: Path,
    schema: type[BaseExtraction],
    *,
    settings: Settings | None = None,
    update: bool = False,
    replay_only: bool = True,
    fake_backend: Any | None = None,
) -> list[str]:
    """Run all golden cases; return a list of failure descriptions (empty = pass)."""
    settings = settings or Settings()
    agent = ExtractionAgent(settings, backend=fake_backend) if fake_backend else ExtractionAgent(settings)
    failures: list[str] = []

    for case in discover(directory):
        try:
            html = case.source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            failures.append(f"{case.name}: only .html fixtures supported in replay mode")
            continue

        variants: list[tuple[str, str]] = [("original", html)]
        for i, (pattern, replacement) in enumerate(case.mutants):
            variants.append((f"mutant{i}", _mutate(html, [(pattern, replacement)])))

        for variant_name, variant_html in variants:
            try:
                opts = ExtractionOptions(use_cache=False, audit=False)
                result = await agent.extract_html_text(
                    variant_html,
                    origin=f"{case.name}:{variant_name}",
                    schema=schema,
                    options=opts,
                )
                got = result.data.model_dump(mode="json", exclude_none=False)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{case.name}[{variant_name}]: extraction error: {exc}")
                continue

            if update and case.golden is not None:
                case.golden.write_text(
                    json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                continue

            if case.golden is None:
                failures.append(f"{case.name}: missing golden file")
                continue

            expected = json.loads(case.golden.read_text(encoding="utf-8"))
            if _normalize(expected) != _normalize(got):
                failures.append(
                    f"{case.name}[{variant_name}]: output mismatch\n"
                    f"  expected: {json.dumps(expected, sort_keys=True)}\n"
                    f"  got:      {json.dumps(got, sort_keys=True)}"
                )
    return failures


def _normalize(obj: Any) -> Any:
    """Normalize for comparison: None vs missing keys, list order kept."""
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in sorted(obj.items()) if v is not None}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    return obj
