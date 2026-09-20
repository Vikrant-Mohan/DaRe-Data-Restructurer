"""Example extraction schemas: e-commerce product and invoice.

These demonstrate the intended style: strict types, constraints in the schema
itself, and cross-field validators whose error messages become retry feedback.
Copy them into your own schema module and adapt.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from extractor.schemas.base import BaseExtraction


class Product(BaseExtraction):
    """A product listing extracted from a page (or several, via merging)."""

    schema_name = "product"
    css_hints = ["[itemtype*='Product']", ".product", "#product", "main"]
    schema_description = "Extract the product entity from the page: name, price, availability, reviews."

    name: str = Field(min_length=1)
    brand: str | None = None
    sku: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    availability: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    description: str | None = None
    image_url: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @model_validator(mode="after")
    def _price_needs_currency(self) -> Product:
        if self.price is not None and not self.currency:
            raise ValueError(
                "price was extracted but currency is missing: find the currency symbol "
                "or ISO code next to the price (e.g. $ -> USD) and set 'currency'"
            )
        return self


class InvoiceItem(BaseExtraction):
    """A single line item on an invoice."""

    schema_name = "invoice_item"
    schema_description = "One invoice line item."

    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    total: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def _line_math(self) -> InvoiceItem:
        expected = (self.quantity * self.unit_price).quantize(Decimal("0.01"))
        if abs(expected - self.total.quantize(Decimal("0.01"))) > Decimal("0.01"):
            raise ValueError(
                f"line item '{self.description}': total {self.total} does not equal "
                f"quantity {self.quantity} x unit_price {self.unit_price} (expected ~{expected})"
            )
        return self


class Invoice(BaseExtraction):
    """An invoice extracted from a PDF or HTML page."""

    schema_name = "invoice"
    css_hints = ["table", "main", "body"]
    schema_description = "Extract the invoice: seller, buyer, dates, line items, totals."

    invoice_number: str = Field(min_length=1)
    invoice_date: str | None = None  # ISO 8601 date (YYYY-MM-DD)
    due_date: str | None = None
    seller_name: str | None = None
    buyer_name: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    items: list[InvoiceItem] = Field(default_factory=list)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax: Decimal | None = Field(default=None, ge=0)
    total: Decimal = Field(ge=0)

    @field_validator("invoice_date", "due_date")
    @classmethod
    def _iso_date(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from datetime import date

        try:
            date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(
                f"date '{v}' is not ISO 8601 (YYYY-MM-DD); normalize it, e.g. "
                f"'March 5, 2026' -> '2026-03-05'"
            ) from exc
        return v

    @model_validator(mode="after")
    def _totals_math(self) -> Invoice:
        problems: list[str] = []
        if self.items:
            items_sum = sum((i.total for i in self.items), Decimal("0")).quantize(Decimal("0.01"))
            if self.subtotal is not None:
                diff = abs(items_sum - self.subtotal.quantize(Decimal("0.01")))
                if diff > Decimal("0.01"):
                    problems.append(
                        f"subtotal {self.subtotal} does not equal sum of item totals {items_sum}"
                    )
            if self.subtotal is None:
                problems.append(
                    "subtotal missing: it should equal the sum of item totals "
                    f"({items_sum}) unless tax/shipping are itemized separately"
                )
        if self.subtotal is not None and self.tax is not None:
            expected = (self.subtotal + self.tax).quantize(Decimal("0.01"))
            if abs(expected - self.total.quantize(Decimal("0.01"))) > Decimal("0.01"):
                problems.append(
                    f"total {self.total} does not equal subtotal {self.subtotal} + tax {self.tax} "
                    f"(expected ~{expected}); check for discounts or shipping fees and adjust"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self
