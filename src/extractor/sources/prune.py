"""DOM pruning: turn raw HTML into a compact, stable, LLM-readable digest.

This is pure Python (stdlib ``html.parser``) so the exact same deterministic
normalization applies to Playwright-rendered pages and to offline fixture
files — no browser needed for tests or the golden harness.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Elements whose entire subtree is dropped.
DROP_SUBTREE = {
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "canvas",
    "template",
    "link",
    "meta",
    "head",
    "option",
    "select",
    "datalist",
}

VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "param",
    "source",
    "track",
    "wbr",
}

# Attributes worth keeping for structure/semantics; everything else is noise.
KEEP_ATTRS = {
    "id",
    "class",
    "name",
    "itemprop",
    "itemtype",
    "itemscope",
    "content",
    "role",
    "alt",
    "title",
    "datetime",
    "colspan",
    "rowspan",
}
DYNAMIC_ATTRS = {"href", "src", "srcset", "action"}

_WS = re.compile(r"\s+")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)

MAX_TEXT_PER_NODE = 300
MAX_ATTR_VALUE = 120


def _clean(value: str) -> str:
    return _WS.sub(" ", value).strip()


def _is_hidden(tag: str, attrs: dict[str, str | None]) -> bool:
    if tag in {"input"} and attrs.get("type") == "hidden":
        return True
    if "hidden" in attrs or attrs.get("aria-hidden") == "true":
        return True
    style = attrs.get("style") or ""
    return bool(style and _HIDDEN_STYLE.search(style))


def _filter_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> str:
    attr_map: dict[str, str | None] = {}
    for key, value in attrs:
        attr_map.setdefault(key, value)
    if _is_hidden(tag, attr_map):
        return " HIDDEN"
    parts: list[str] = []
    for key, value in sorted(attr_map.items()):
        if value is None:
            continue
        if key in KEEP_ATTRS:
            v = _clean(value)[:MAX_ATTR_VALUE]
            if v:
                parts.append(f'{key}="{v}"')
        elif key in DYNAMIC_ATTRS:
            v = _clean(value)[:MAX_ATTR_VALUE]
            if v:
                parts.append(f'{key}="{v}"')
        elif key.startswith("data-"):
            v = _clean(value)[:MAX_ATTR_VALUE]
            if v:
                parts.append(f'{key}="{v}"')
    return (" " + " ".join(parts)) if parts else ""


class _PruningParser(HTMLParser):
    """Serializes a pruned DOM as readable pseudo-HTML, one element per line."""

    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.max_chars = max_chars
        # Stack of open tags we are skipping (DROP_SUBTREE or hidden elements).
        self.skip_stack: list[str] = []
        self.open_stack: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _emit(self, line: str) -> None:
        if len(self.out) >= self.max_chars:
            return
        indent = "  " * len(self.open_stack)
        self.out.append(indent + line)

    def _total(self) -> int:
        return sum(len(s) + 1 for s in self.out)

    def _is_skipped_tag(self, tag: str) -> bool:
        return tag in DROP_SUBTREE or tag == "select"

    # -- parser events ----------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_stack:
            if tag not in VOID_ELEMENTS:
                self.skip_stack.append(tag)
            return
        if self._is_skipped_tag(tag):
            if tag not in VOID_ELEMENTS:
                self.skip_stack.append(tag)
            return
        attr_s = _filter_attrs(tag, attrs)
        if attr_s == " HIDDEN":
            if tag not in VOID_ELEMENTS:
                self.skip_stack.append(tag)
            return
        if tag in VOID_ELEMENTS:
            self._emit(f"<{tag}{attr_s}/>")
            return
        self._emit(f"<{tag}{attr_s}>")
        self.open_stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_stack:
            return
        if self._is_skipped_tag(tag):
            return
        attr_s = _filter_attrs(tag, attrs)
        if attr_s == " HIDDEN":
            return
        self._emit(f"<{tag}{attr_s}/>")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_stack:
            # close the innermost skipped tag (tolerates sloppy nesting)
            if tag in self.skip_stack:
                while self.skip_stack and self.skip_stack.pop() != tag:
                    pass
            return
        if tag in self.open_stack:
            # close intervening unclosed tags (sloppy HTML)
            while self.open_stack:
                open_tag = self.open_stack.pop()
                self._emit(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self.skip_stack or not self.open_stack:
            return
        text = _clean(data)
        if not text:
            return
        if len(text) > MAX_TEXT_PER_NODE:
            text = text[: MAX_TEXT_PER_NODE - 1] + "…"
        self._emit(text)


def prune_dom(html: str, max_chars: int = 120_000) -> str:
    """Normalize raw HTML into a compact structural digest.

    - drops script/style/iframe/svg/head noise and hidden elements
    - keeps semantic attributes (id, class, itemprop, data-*, href, src)
    - collapses whitespace, truncates long text nodes
    - hard-caps the output size for token control
    """
    parser = _PruningParser(max_chars)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    text = "\n".join(line for line in parser.out if line.strip())
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[DOM TRUNCATED]"
    return text or "(empty page)"
