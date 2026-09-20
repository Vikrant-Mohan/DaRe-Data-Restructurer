"""Pre-built valid/invalid model responses for scripted backend tests."""

from __future__ import annotations

import json
from decimal import Decimal

from extractor.schemas.examples import Invoice, InvoiceItem, Product


def invalid_invoice_json() -> str:
    """Fails Invoice._totals_math: subtotal != sum(items)."""
    return json.dumps(
        {
            "invoice_number": "INV-001",
            "invoice_date": "2026-02-10",
            "currency": "USD",
            "subtotal": "150.00",
            "tax": "10.00",
            "total": "170.00",
            "items": [
                {
                    "description": "Widget",
                    "quantity": "10",
                    "unit_price": "11.00",
                    "total": "110.00",
                },
                {
                    "description": "Gadget",
                    "quantity": "2",
                    "unit_price": "25.00",
                    "total": "50.00",
                },
            ],
        }
    )


def valid_product() -> Product:
    return Product(
        name="Blue Widget",
        brand="ACME",
        sku="BW-001",
        price=Decimal("24.99"),
        currency="USD",
        availability="In Stock",
        rating=4.5,
        review_count=128,
        description="A reliable blue widget for all occasions.",
    )


def invalid_product_json() -> str:
    return valid_product().model_dump_json()


def valid_invoice() -> Invoice:
    return Invoice(
        invoice_number="INV-001",
        invoice_date="2026-02-10",
        currency="USD",
        subtotal=Decimal("160.00"),
        tax=Decimal("10.00"),
        total=Decimal("170.00"),
        items=[
            InvoiceItem(
                description="Widget",
                quantity=Decimal("10"),
                unit_price=Decimal("11.00"),
                total=Decimal("110.00"),
            ),
            InvoiceItem(
                description="Gadget",
                quantity=Decimal("2"),
                unit_price=Decimal("25.00"),
                total=Decimal("50.00"),
            ),
        ],
    )
