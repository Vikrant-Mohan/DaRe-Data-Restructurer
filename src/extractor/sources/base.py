"""Source abstraction: turn a URL or file into a normalized SourceDocument."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from extractor.cache import sha256_bytes, sha256_text
from extractor.errors import SourceError


@dataclass
class PageContent:
    """One normalized page of a source document."""

    text: str  # extracted text (pruned DOM text for HTML, PDF text layer)
    page_number: int = 1  # 1-based
    image_base64: str | None = None  # PNG bytes, base64 — set for scanned pages
    image_mime: str = "image/png"
    needs_vision: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)  # url, title, etc.


@dataclass
class SourceDocument:
    """Normalized, hash-addressed source ready for extraction."""

    kind: str  # html | pdf
    origin: str  # url or file path
    raw_sha256: str  # hash of the raw bytes as fetched
    pages: list[PageContent]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)


@runtime_checkable
class Source(Protocol):
    """A source fetches raw content and normalizes it to a SourceDocument."""

    async def fetch(self) -> SourceDocument: ...


def load_bytes(path: Path, kind_hint: str = "") -> tuple[bytes, str]:
    """Read a file and infer its kind ('html' | 'pdf')."""
    if not path.exists():
        raise SourceError(f"file not found: {path}")
    data = path.read_bytes()
    kind = kind_hint
    if not kind:
        if data[:5] == b"%PDF-":
            kind = "pdf"
        elif path.suffix.lower() in {".html", ".htm", ".xhtml"}:
            kind = "html"
        else:
            kind = "html"
    if kind == "pdf" and data[:5] != b"%PDF-":
        raise SourceError(f"not a PDF file: {path}")
    return data, kind


def hash_bytes(data: bytes) -> str:
    return sha256_bytes(data)


def hash_text(text: str) -> str:
    return sha256_text(text)
