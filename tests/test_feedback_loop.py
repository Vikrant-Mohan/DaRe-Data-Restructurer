"""The core acceptance test: validation failures are fed back into the prompt.

A scripted LLM returns schema-invalid output first. The test proves that:
  1. the run recovers on attempt 2,
  2. the validation error text (including custom cross-field validator
     messages) appears inside attempt 2's user message,
  3. the previous raw output is echoed into that message too,
  4. every attempt is captured in the audit trail.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from extractor.errors import ExtractionFailed
from extractor.llm.base import BackendCall
from extractor.pipeline import ExtractionAgent, ExtractionOptions
from extractor.schemas.examples import Invoice, InvoiceItem
from tests.fixtures.responses import invalid_invoice_json, invalid_product_json


@pytest.fixture
def invoice_page() -> str:
    from tests.fixtures.pages import INVOICE_HTML

    return INVOICE_HTML


async def test_validation_error_is_fed_back_into_prompt(
    settings: Any, scripted_backend: Any, invoice_page: str
) -> None:
    bad = invalid_invoice_json()  # totals do not reconcile -> validator fires
    good = Invoice(
        invoice_number="INV-001",
        invoice_date="2026-02-10",
        currency="USD",
        subtotal=Decimal("110.00"),
        tax=Decimal("11.00"),
        total=Decimal("121.00"),
        items=[
            InvoiceItem(
                description="Widget", quantity=Decimal("10"), unit_price=Decimal("11"), total=Decimal("110")
            )
        ],
    )
    backend = scripted_backend([bad, good])
    agent = ExtractionAgent(settings, backend=backend)

    result = await agent.extract_html_text(
        invoice_page, schema=Invoice, options=ExtractionOptions(use_cache=False, audit=False)
    )

    # 1. recovered on attempt 2
    assert result.provenance.attempt_count == 2
    assert result.data.total == Decimal("121.00")

    # 2. attempt-2 messages contain the validation error text and previous output
    assert len(backend.calls) == 2
    retry_text = backend.last_user_text()
    assert "subtotal" in retry_text.lower()
    assert "sum of item totals" in retry_text.lower()  # custom validator message
    assert invalid_invoice_json() in retry_text  # previous output echoed back

    # 3. first call had NO feedback message; second call DID
    first_user_msgs = [
        m for m in backend.calls[0]["messages"] if m.get("role") == "user"
    ]
    assert len(first_user_msgs) == 1  # just the document
    second_user_msgs = [
        m for m in backend.calls[1]["messages"] if m.get("role") == "user"
    ]
    assert len(second_user_msgs) == 2  # document + feedback

    # 4. audit trail records both attempts with the errors
    trace = result.run_trace
    assert len(trace.attempts) == 2
    assert not trace.attempts[0].ok
    assert "sum of item totals" in " ".join(trace.attempts[0].validation_errors)
    assert trace.attempts[1].ok
    assert trace.outcome == "success"


async def test_pydantic_field_errors_are_formatted_into_feedback(
    settings: Any, scripted_backend: Any, invoice_page: str
) -> None:
    """Malformed JSON on attempt 1 -> error echoed; attempt 2 succeeds."""
    bad = "{not json"
    good = invalid_product_json()
    backend = scripted_backend([bad, good])
    agent = ExtractionAgent(settings, backend=backend)

    from extractor.schemas.examples import Product

    result = await agent.extract_html_text(
        "<html><body>nothing</body></html>",
        schema=Product,
        options=ExtractionOptions(use_cache=False, audit=False),
    )

    assert result.provenance.attempt_count == 2
    retry_text = backend.last_user_text()
    assert "not valid JSON" in retry_text
    assert "{not json" in retry_text
    assert result.data.name == "Blue Widget"


async def test_exhausted_retries_raise_with_audit_trail(
    settings: Any, scripted_backend: Any, invoice_page: str
) -> None:
    backend = scripted_backend(["{bad json}"] * 5)
    agent = ExtractionAgent(settings, backend=backend)

    with pytest.raises(ExtractionFailed) as excinfo:
        await agent.extract_html_text(
            invoice_page,
            schema=Invoice,
            options=ExtractionOptions(use_cache=False, audit=True, max_attempts=3),
        )

    trace = excinfo.value.run_trace
    assert trace is not None
    assert trace.attempt_count == 3
    assert trace.outcome == "failed"
    assert len(backend.calls) == 3
    # audit file was written for replay
    assert (settings.audit_dir / f"{trace.run_id}.jsonl").exists()


async def test_escalation_context_is_reinjected(
    settings: Any, scripted_backend: Any
) -> None:
    """A schema with css_hints gets the matching DOM region re-injected."""
    from decimal import Decimal

    from extractor.schemas.examples import Product

    small_product = Product(name="Widget", price=Decimal("5.00"), currency="USD")
    backend = scripted_backend(["{bad json}", "{bad json}", small_product])
    agent = ExtractionAgent(settings, backend=backend)

    html = (
        "<html><body><main><div class='product'><h1>Widget</h1>"
        "<span class='price'>$5.00</span></div></main></body></html>"
    )
    result = await agent.extract_html_text(
        html,
        schema=Product,
        options=ExtractionOptions(
            use_cache=False, audit=False, max_attempts=3, max_escalations=2
        ),
    )

    assert result.data.name == "Widget"
    # attempt 3's feedback message contains the re-injected DOM region
    retry_text = backend.last_user_text()
    assert "re-injected" in retry_text.lower()
    assert "class=\"product\"" in retry_text  # the matched DOM region
    assert "product" in retry_text
    kinds = [a.kind for a in result.run_trace.attempts]
    assert kinds[0] == "base"
    assert kinds[1] == "escalation:dom"


async def test_backend_exception_becomes_feedback_not_crash(
    settings: Any, scripted_backend: Any
) -> None:
    from extractor.schemas.examples import Product
    from tests.fixtures.responses import valid_product

    backend = scripted_backend(
        [RuntimeError("provider down"), valid_product()]
    )
    agent = ExtractionAgent(settings, backend=backend)
    result = await agent.extract_html_text(
        "<p>x</p>", schema=Product, options=ExtractionOptions(use_cache=False, audit=False)
    )
    assert result.provenance.attempt_count == 2
    assert "provider down" in backend.last_user_text()


_ = BackendCall  # re-exported for typing clarity in failure paths
