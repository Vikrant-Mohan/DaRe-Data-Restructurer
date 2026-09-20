"""Pipeline tests: caching, provenance, merging, PDF vision routing."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, ClassVar

import pytest

from extractor.audit import RunTrace
from extractor.pipeline import ExtractionAgent, ExtractionOptions
from extractor.schemas.base import BaseExtraction
from extractor.schemas.examples import Invoice, Product
from tests.fixtures.pages import (
    PRODUCT_HTML,
    PRODUCT_HTML_DRIFTED,
    scanned_pdf_bytes,
    text_pdf_bytes,
)
from tests.fixtures.responses import valid_invoice, valid_product


async def test_success_result_carries_provenance(settings: Any, scripted_backend: Any) -> None:
    backend = scripted_backend([valid_product()])
    agent = ExtractionAgent(settings, backend=backend)
    result = await agent.extract_html_text(
        PRODUCT_HTML, schema=Product, options=ExtractionOptions(use_cache=False, audit=False)
    )
    prov = result.provenance
    assert prov.source_kind == "html"
    assert prov.source_origin == "inline://html"
    assert len(prov.source_sha256) == 64
    assert prov.schema_name == "product"
    assert prov.attempt_count == 1
    assert prov.cache_hit is False
    assert prov.run_id == result.run_trace.run_id
    assert result.data.name == "Blue Widget"


async def test_cache_hit_replays_result_without_llm_call(
    settings: Any, scripted_backend: Any
) -> None:
    backend = scripted_backend([valid_product()])
    agent = ExtractionAgent(settings, backend=backend)
    opts = ExtractionOptions(use_cache=True, audit=False)

    r1 = await agent.extract_html_text(PRODUCT_HTML, schema=Product, options=opts)
    assert r1.provenance.cache_hit is False
    r2 = await agent.extract_html_text(PRODUCT_HTML, schema=Product, options=opts)
    assert r2.provenance.cache_hit is True
    assert r2.data.model_dump() == r1.data.model_dump()
    assert len(backend.calls) == 1  # second run never touched the backend


async def test_cache_key_changes_with_schema(settings: Any, scripted_backend: Any) -> None:
    backend = scripted_backend([valid_product(), valid_product()])
    agent = ExtractionAgent(settings, backend=backend)

    await agent.extract_html_text(PRODUCT_HTML, schema=Product, options=ExtractionOptions(use_cache=True, audit=False))

    class ProductV2(Product):
        schema_name = "product_v2"

    await agent.extract_html_text(PRODUCT_HTML, schema=ProductV2, options=ExtractionOptions(use_cache=True, audit=False))
    assert len(backend.calls) == 2  # different schema -> fresh extraction


async def test_drifted_dom_still_feeds_model_the_values(
    settings: Any, scripted_backend: Any
) -> None:
    """DOM drift resilience: renamed classes/moved nodes still surface values."""
    backend = scripted_backend([valid_product()])
    agent = ExtractionAgent(settings, backend=backend)
    await agent.extract_html_text(
        PRODUCT_HTML_DRIFTED, schema=Product, options=ExtractionOptions(use_cache=False, audit=False)
    )
    prompt_text = backend.last_user_text()
    for value in ("Blue Widget", "24.99", "BW-001", "USD", "4.5", "128"):
        assert value in prompt_text, f"{value!r} missing from pruned DOM prompt"


async def test_scanned_pdf_routes_to_vision(settings: Any, scripted_backend: Any) -> None:
    """Pages without a text layer are rendered to images and attached."""
    backend = scripted_backend([valid_invoice()])
    agent = ExtractionAgent(settings, backend=backend)
    result = await agent.extract_pdf_bytes(
        scanned_pdf_bytes(),
        origin="scan.pdf",
        schema=Invoice,
        options=ExtractionOptions(use_cache=False, audit=False),
    )
    assert result.data.invoice_number == "INV-001"
    assert result.provenance.source_kind == "pdf"
    assert result.provenance.pages == [1]

    # the LLM call must contain an image part
    user_content = [
        m["content"] for m in backend.calls[0]["messages"] if m.get("role") == "user"
    ]
    assert isinstance(user_content[0], list)
    kinds = {p["type"] for p in user_content[0]}
    assert "image_url" in kinds


async def test_text_pdf_uses_text_layer_not_vision(settings: Any, scripted_backend: Any) -> None:
    settings.pdf_text_density = 1.0  # permissive so the short fixture stays text
    backend = scripted_backend([valid_invoice()])
    agent = ExtractionAgent(settings, backend=backend)
    await agent.extract_pdf_bytes(
        text_pdf_bytes(),
        origin="text.pdf",
        schema=Invoice,
        options=ExtractionOptions(use_cache=False, audit=False),
    )
    user_content = [
        m["content"] for m in backend.calls[0]["messages"] if m.get("role") == "user"
    ]
    assert isinstance(user_content[0], str)  # plain text, no image parts
    assert "INV-2026-042" in user_content[0]


async def test_merge_concatenate_strategy(settings: Any, scripted_backend: Any) -> None:
    from pydantic import Field

    class CartItem(BaseExtraction):
        schema_name = "cart_item"
        label: str
        amount: Decimal = Field(ge=0)

    class Cart(BaseExtraction):
        schema_name = "cart"
        merge_strategy: ClassVar[str] = "concatenate_items"
        items: list[CartItem] = Field(default_factory=list)
        total: Decimal = Field(default=Decimal("0"), ge=0)

    agent = ExtractionAgent(settings, backend=scripted_backend([]))
    a = Cart(items=[CartItem(label="x", amount=Decimal("1"))], total=Decimal("1"))
    b = Cart(items=[CartItem(label="y", amount=Decimal("2"))], total=Decimal("2"))
    merged = agent.merge_instances([a, b], schema=Cart)
    assert [i.label for i in merged.items] == ["x", "y"]  # type: ignore[union-attr]


async def test_run_trace_jsonl_roundtrip(settings: Any, scripted_backend: Any) -> None:
    backend = scripted_backend([valid_product()])
    agent = ExtractionAgent(settings, backend=backend)
    result = await agent.extract_html_text(
        PRODUCT_HTML, schema=Product, options=ExtractionOptions(use_cache=False, audit=True)
    )
    path = settings.audit_dir / f"{result.run_trace.run_id}.jsonl"
    assert path.exists()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["type"] == "run"
    assert rows[-1]["type"] == "outcome"
    assert rows[-1]["outcome"] == "success"

    restored = RunTrace.from_jsonl(path)
    assert restored.run_id == result.run_trace.run_id
    assert restored.attempt_count == 1
    assert "extractor" in restored.summary() or "attempt" in restored.summary()


async def test_missing_content_raises_source_error(settings: Any, scripted_backend: Any) -> None:
    from extractor.errors import SourceError

    agent = ExtractionAgent(settings, backend=scripted_backend([]))
    doc: Any = type(
        "Doc",
        (),
        {
            "kind": "html",
            "origin": "empty",
            "raw_sha256": "0" * 64,
            "pages": [],
            "metadata": {},
            "full_text": "",
        },
    )()
    with pytest.raises(SourceError):
        await agent.extract_document(doc, schema=Product, options=ExtractionOptions(use_cache=False))
