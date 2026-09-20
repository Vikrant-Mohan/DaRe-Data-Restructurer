"""Tests for the pure-Python DOM pruner (the DOM-drift defense)."""

from extractor.sources.prune import prune_dom


def test_drops_script_style_and_head_noise() -> None:
    html = """
    <html><head><title>T</title><script>var x = 1; TRACKING();</script>
    <style>.a { color: red }</style></head>
    <body><h1>Hello</h1><script>moreJs()</script><p>World</p></body></html>
    """
    out = prune_dom(html)
    assert "TRACKING" not in out
    assert "moreJs" not in out
    assert "color: red" not in out
    assert "Hello" in out
    assert "World" in out


def test_keeps_semantic_attributes() -> None:
    html = '<div id="price" class="big" data-testid="cost" onclick="x()">$12</div>'
    out = prune_dom(html)
    assert 'id="price"' in out
    assert 'class="big"' in out
    assert 'data-testid="cost"' in out
    assert "onclick" not in out


def test_hidden_elements_are_dropped() -> None:
    html = '<div style="display:none">SECRET</div><p>visible</p>'
    out = prune_dom(html)
    assert "SECRET" not in out
    assert "visible" in out


def test_long_text_is_truncated_but_bounded() -> None:
    html = "<p>" + ("word " * 500) + "</p>"
    out = prune_dom(html)
    assert len(out) < 1000


def test_output_is_deterministic() -> None:
    html = "<div class='a'><span>Hello</span><b>world</b></div>"
    assert prune_dom(html) == prune_dom(html)


def test_sloppy_html_does_not_crash() -> None:
    html = "<div><p>unclosed <span>tags <b>everywhere</div></p>"
    out = prune_dom(html)
    assert "unclosed" in out
