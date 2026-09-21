"""Static HTML fetch: httpx fallback when a browser is not available.

Deployment targets like Vercel Functions cannot run Playwright's bundled
Chromium (no system browser libs, no apt). The fallback fetches the raw HTML
over HTTP and feeds it through the same DOM pruning + extraction pipeline, so
``POST /extract {"url": ...}`` still works server-side — minus JS rendering.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from extractor.cache import sha256_bytes
from extractor.config import Settings
from extractor.errors import SourceError
from extractor.sources.base import PageContent, SourceDocument
from extractor.sources.prune import prune_dom

FETCH_TIMEOUT_SECONDS = 30.0
MAX_BYTES = 4_000_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


async def fetch_static(settings: Settings, url: str) -> SourceDocument:
    """Fetch ``url`` over plain HTTP and normalize it like HtmlSource does."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SourceError(f"unsupported URL scheme: {url!r}")

    headers = {"user-agent": settings.user_agent or DEFAULT_USER_AGENT}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT_SECONDS,
            headers=headers,
        ) as client:
            response = await client.get(url)
    except Exception as exc:
        raise SourceError(f"failed to fetch {url}: {exc}") from exc

    if response.status_code >= 400:
        raise SourceError(f"HTTP {response.status_code} while fetching {url}")

    raw_bytes = response.content[:MAX_BYTES]
    try:
        html = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
    except LookupError:  # unknown encoding reported by the server
        html = raw_bytes.decode("utf-8", errors="replace")

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = m.group(1).strip()

    return SourceDocument(
        kind="html",
        origin=url,
        raw_sha256=sha256_bytes(raw_bytes),
        pages=[
            PageContent(
                text=prune_dom(html),
                page_number=1,
                metadata={
                    "url": url,
                    "title": title,
                    "canonical_url": _canonical(html, url),
                    "fetch_mode": "static",
                },
            )
        ],
        metadata={"status": response.status_code, "fetch_mode": "static"},
    )


def _canonical(html: str, base_url: str) -> str:
    from urllib.parse import urljoin

    m = re.search(
        r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']", html, re.I
    )
    if m:
        return urljoin(base_url, m.group(1))
    return base_url
