"""Playwright HTML source: JS rendering, auto-wait, and DOM pruning.

The pruned DOM (not raw HTML) is what the LLM sees: scripts, styles, iframes,
hidden elements and other noise are stripped, semantic attributes are kept,
and output is deterministic — so structural drift on the target site (renamed
classes, moved nodes) does not break extraction.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from extractor.cache import sha256_bytes
from extractor.config import Settings
from extractor.errors import SourceError
from extractor.sources.base import PageContent, SourceDocument
from extractor.sources.prune import prune_dom

BLOCK_RESOURCE_TYPES = {"image", "media", "font"}


class HtmlSource:
    """Fetch a URL with Playwright (Chromium) and normalize it."""

    def __init__(self, settings: Settings, url: str) -> None:
        self.settings = settings
        self.url = url
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise SourceError(f"unsupported URL scheme: {url!r}")

    async def fetch(self) -> SourceDocument:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise SourceError(
                "Playwright is not installed; run: pip install playwright && playwright install chromium"
            ) from exc

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(
                        user_agent=self.settings.user_agent, ignore_https_errors=True
                    )
                    page.set_default_timeout(self.settings.page_timeout_ms)
                    await page.route(
                        "**/*", self._maybe_block_route
                    )
                    response = await page.goto(
                        self.url, wait_until=self.settings.nav_wait_until
                    )
                    if response is not None and response.status >= 400:
                        raise SourceError(
                            f"HTTP {response.status} while fetching {self.url}"
                        )
                    # settle pass: wait out late JS mutations (best effort)
                    await self._settle(page)
                    html = await page.content()
                    title = await page.title()
                finally:
                    await browser.close()
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"failed to fetch {self.url}: {exc}") from exc

        raw_bytes = html.encode("utf-8")
        digest = sha256_bytes(raw_bytes)
        pruned = prune_dom(html)
        return SourceDocument(
            kind="html",
            origin=self.url,
            raw_sha256=digest,
            pages=[
                PageContent(
                    text=pruned,
                    page_number=1,
                    metadata={
                        "url": self.url,
                        "title": title or "",
                        "canonical_url": self._canonical(html),
                    },
                )
            ],
            metadata={"status": response.status if response else 0},
        )

    # -- internals ---------------------------------------------------------
    @staticmethod
    async def _maybe_block_route(route: Any) -> None:
        """Skip heavy resource types we never need for text extraction."""
        try:
            if route.request.resource_type in BLOCK_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            pass

    @staticmethod
    async def _settle(page: Any, quiet_ms: int = 700, max_wait_ms: int = 4000) -> None:
        try:
            start = 0
            last_len = -1
            while start < max_wait_ms:
                await page.wait_for_timeout(quiet_ms // 2)
                length = await page.evaluate("document.body ? document.body.innerText.length : 0")
                if length == last_len:
                    start += quiet_ms // 2
                else:
                    start = 0
                    last_len = length
        except Exception:
            pass

    def _canonical(self, html: str) -> str:
        import re

        m = re.search(
            r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']", html, re.I
        )
        if m:
            return urljoin(self.url, m.group(1))
        return self.url
