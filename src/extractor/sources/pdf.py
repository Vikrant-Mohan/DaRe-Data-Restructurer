"""PyMuPDF PDF source: text-layer extraction with scanned-page vision fallback.

Pages with a usable text layer are extracted directly (deterministic, cheap);
pages below the text-density threshold are rendered to PNG and routed through
a vision-capable model via the LLM layer.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from extractor.cache import sha256_bytes
from extractor.config import Settings
from extractor.errors import SourceError
from extractor.sources.base import PageContent, SourceDocument

# Rough A4/Letter usable area at 72pt: ~6.5in x 9in -> ~58 sq in.
_MIN_PAGE_AREA_SQ_IN = 20.0


class PdfSource:
    """Normalize a PDF into per-page PageContent with vision flags."""

    def __init__(self, settings: Settings, data: bytes, origin: str) -> None:
        self.settings = settings
        self.data = data
        self.origin = origin

    async def fetch(self) -> SourceDocument:
        try:
            try:
                import pymupdf as fitz  # modern import name
            except ImportError:  # pragma: no cover - older PyMuPDF
                import fitz  # type: ignore[no-redef]
        except ImportError as exc:  # pragma: no cover
            raise SourceError("PyMuPDF is not installed; pip install pymupdf") from exc


        try:
            doc = fitz.open(stream=self.data, filetype="pdf")
        except Exception as exc:
            raise SourceError(f"cannot open PDF {self.origin}: {exc}") from exc

        try:
            doc_any: Any = doc  # pymupdf stubs are incomplete for iteration
            pages: list[PageContent] = []
            zoom = self.settings.pdf_dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for i, page in enumerate(doc_any):
                page_number = i + 1
                text = _extract_page_text(page)
                area = _page_area_sq_in(page)
                density = (len(text) / area) if area > _MIN_PAGE_AREA_SQ_IN else float(len(text))
                needs_vision = density < self.settings.pdf_text_density
                image_b64: str | None = None
                if needs_vision:
                    image_b64 = _render_page_b64(page, matrix)
                pages.append(
                    PageContent(
                        text=text,
                        page_number=page_number,
                        image_base64=image_b64,
                        image_mime="image/png",
                        needs_vision=needs_vision,
                        metadata={
                            "char_count": len(text),
                            "density": round(density, 1),
                            "rendered_dpi": self.settings.pdf_dpi if needs_vision else None,
                        },
                    )
                )
            meta: dict[str, Any] = {}
            try:
                meta = {k: str(v) for k, v in (doc.metadata or {}).items() if v}
            except Exception:
                pass
            return SourceDocument(
                kind="pdf",
                origin=self.origin,
                raw_sha256=sha256_bytes(self.data),
                pages=pages,
                metadata=meta,
            )
        finally:
            doc.close()


def pdf_from_file(settings: Settings, path: Path) -> tuple[bytes, str]:
    """Load a PDF file from disk (shared by CLI and API upload path)."""
    if not path.exists():
        raise SourceError(f"file not found: {path}")
    data = path.read_bytes()
    if data[:5] != b"%PDF-":
        raise SourceError(f"not a PDF file: {path}")
    return data, str(path)


def _extract_page_text(page: Any) -> str:
    """Extract text in reading order with section breaks."""
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        blocks = []
    lines: list[str] = []
    for block in blocks:
        x0, _y0, _x1, _y1, text, _block_no, block_type = block[:7]
        if block_type != 0:  # images etc.
            continue
        cleaned = " ".join(str(text).split())
        if cleaned:
            lines.append(f"[x={x0:.0f}] {cleaned}")
    return "\n".join(lines)


def _page_area_sq_in(page: Any) -> float:
    try:
        rect = page.rect
        return (rect.width / 72.0) * (rect.height / 72.0)
    except Exception:
        return 60.0


def _render_page_b64(page: Any, matrix: Any) -> str:
    try:
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        png = pix.tobytes("png")
        return base64.b64encode(png).decode("ascii")
    except Exception:
        return ""
