"""Tests for schema registry, hashing, and the strict example schemas."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from extractor.errors import SchemaInvalid, SchemaNotFound
from extractor.schemas.examples import Invoice, InvoiceItem, Product
from extractor.schemas.registry import SchemaRegistry


def test_builtin_examples_are_registered() -> None:
    registry = SchemaRegistry()
    assert "product" in registry.names()
    assert "invoice" in registry.names()


def test_get_unknown_schema_raises_with_available() -> None:
    registry = SchemaRegistry()
    with pytest.raises(SchemaNotFound) as exc:
        registry.get("nope")
    assert "product" in str(exc.value)


def test_register_rejects_empty_schema_name() -> None:
    class NoName(Product):
        schema_name = ""

    registry = SchemaRegistry()
    with pytest.raises(SchemaInvalid):
        registry.register(NoName)


def test_schema_hash_is_stable_and_field_sensitive() -> None:
    h1 = Product.schema_sha256()
    h2 = Product.schema_sha256()
    assert h1 == h2

    class Modified(Product):
        extra_field: int | None = None

    assert Modified.schema_sha256() != h1


def test_validator_contract_lists_rules() -> None:
    contract = Invoice.validator_contract()
    assert "_totals_math" in contract
    assert "_iso_date" in contract


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        Product(name="x", hacker_field="evil")


def test_product_price_requires_currency() -> None:
    with pytest.raises(ValidationError) as exc:
        Product(name="x", price=Decimal("9.99"))
    assert "currency" in str(exc.value).lower()


def test_currency_is_uppercased() -> None:
    p = Product(name="x", currency="usd")
    assert p.currency == "USD"


def test_invoice_totals_must_reconcile() -> None:
    with pytest.raises(ValidationError) as exc:
        Invoice(
            invoice_number="A",
            subtotal=Decimal("100.00"),
            tax=Decimal("10.00"),
            total=Decimal("500.00"),
        )
    assert "does not equal" in str(exc.value)


def test_invoice_dates_must_be_iso() -> None:
    with pytest.raises(ValidationError) as exc:
        Invoice(invoice_number="A", total=Decimal("1"), invoice_date="March 5, 2026")
    assert "ISO 8601" in str(exc.value)


def test_invoice_item_line_math() -> None:
    with pytest.raises(ValidationError) as exc:
        InvoiceItem(
            description="x", quantity=Decimal("2"), unit_price=Decimal("3"), total=Decimal("7")
        )
    assert "does not equal" in str(exc.value)


def test_register_user_file(tmp_path: object) -> None:
    schema_file = tmp_path / "job.py"  # type: ignore[attr-defined]
    schema_file.write_text(  # type: ignore[attr-defined]
        '''
from extractor.schemas.base import BaseExtraction

class JobPosting(BaseExtraction):
    schema_name = "job_posting"
    title: str
''',
        encoding="utf-8",
    )
    registry = SchemaRegistry()
    names = registry.register_file(schema_file)  # type: ignore[arg-type]
    assert "job_posting" in names
    assert registry.get("job_posting").__name__ == "JobPosting"
