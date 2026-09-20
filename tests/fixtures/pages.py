"""HTML and PDF source fixtures for tests and the golden harness."""

from __future__ import annotations

INVOICE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Acme Invoice</title><script>analytics()</script></head>
<body>
  <main>
    <h1>Invoice INV-001</h1>
    <div class="meta">
      <span class="invoice-date">February 10, 2026</span>
      <span class="due-date">2026-03-12</span>
    </div>
    <div class="parties">
      <div class="seller">Seller: Acme Corp</div>
      <div class="buyer">Buyer: Globex LLC</div>
    </div>
    <table class="line-items">
      <tr><th>Description</th><th>Qty</th><th>Unit</th><th>Total</th></tr>
      <tr><td>Widget</td><td>10</td><td>$11.00</td><td>$110.00</td></tr>
      <tr><td>Gadget</td><td>2</td><td>$25.00</td><td>$50.00</td></tr>
    </table>
    <div class="totals">
      <div>Subtotal: $160.00</div>
      <div>Tax (6.25%): $10.00</div>
      <div>Total: $170.00</div>
    </div>
  </main>
  <footer>Copyright 2026</footer>
</body>
</html>
"""

PRODUCT_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Blue Widget</title>
<style>.promo { color: gold }</style>
<script>var tracking = "noise";</script></head>
<body>
  <nav><a href="/cats">Categories</a><a href="/cart">Cart</a></nav>
  <main>
    <article class="product" itemtype="https://schema.org/Product">
      <h1 class="name">Blue Widget</h1>
      <div class="brand">ACME</div>
      <span class="sku">BW-001</span>
      <div class="price" content="24.99">$24.99</div>
      <span class="currency">USD</span>
      <div class="availability">In Stock</div>
      <div class="rating">4.5 stars (128 reviews)</div>
      <p class="desc">A reliable blue widget for all occasions.</p>
    </article>
    <aside>Related items you may like…</aside>
  </main>
  <footer>© ACME</footer>
</body>
</html>
"""

# Same product, drifted DOM: renamed classes, swapped tags, injected wrapper,
# removed attributes. Extraction must still work because the pruned DOM (not
# selector rules) is the model's input.
PRODUCT_HTML_DRIFTED = """\
<!DOCTYPE html>
<html>
<head><title>ACME — Blue Widget — best price</title>
<script>var tracking2 = "more noise";</script></head>
<body>
  <div class="react-root v2">
    <section data-component="pdp" role="main">
      <div class="css-1x2y3z ProductHero__title">
        <h2 data-qa="product-title">Blue Widget</h2>
      </div>
      <ul class="breadcrumbs"><li>Home</li><li>Widgets</li></ul>
      <div class="ProductHero__brand">Brand: ACME</div>
      <div class="ProductHero__meta">
        <span data-qa="sku">SKU: BW-001</span>
        <span class="stockBadge in-stock">Availability: In Stock</span>
      </div>
      <div class="PriceBlock"><b class="PriceBlock__value">24.99</b>
        <span class="PriceBlock__code">USD</span></div>
      <div data-qa="rating">4.5</div><div data-qa="review-count">128</div>
      <p data-qa="description">A reliable blue widget for all occasions.</p>
    </section>
  </div>
</body>
</html>
"""


def scanned_pdf_bytes() -> bytes:
    """A text-free PDF: one page containing only an image.

    Built with PyMuPDF at test time so no binary fixture is needed.
    """
    import fitz  # noqa: F401  (PyMuPDF; use pymupdf name when available)

    try:
        import pymupdf as fitz_mod
    except ImportError:
        import fitz as fitz_mod

    doc = fitz_mod.open()
    page = doc.new_page(width=612, height=792)
    # draw a big gray rectangle as the "scan" — no text layer at all
    rect = fitz.Rect(72, 72, 540, 720)
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=(0.9, 0.9, 0.9), fill=(0.95, 0.95, 0.95))
    shape.commit()
    return doc.tobytes()


def text_pdf_bytes() -> bytes:
    """A text-layer PDF with invoice content."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    lines = [
        "INVOICE INV-2026-042",
        "Invoice date: 2026-02-10",
        "Seller: Acme Corp",
        "Buyer: Globex LLC",
        "Items:",
        "Widget  x10  @ 11.00 USD = 110.00",
        "Gadget  x2  @ 25.00 USD = 50.00",
        "Subtotal: 160.00",
        "Tax: 10.00",
        "Total: 170.00 USD",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=11)
        y += 18
    return doc.tobytes()
