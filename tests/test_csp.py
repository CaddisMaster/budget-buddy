"""#403 — the structural half of the Content-Security-Policy work.

The policy itself, sent on every page, is checked from the outside in
tests/features/content_security_policy.feature. What lives here has no actor:
it reads the templates, because an inline handler is the one kind of script a
nonce can never cover, and a test client never runs JavaScript, so a handler
the browser refuses would leave every other test green.
"""
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
ALL = sorted(TEMPLATES.rglob("*.html"))

# Attributes the browser treats as script. `on…=` handlers are blocked outright
# by a nonce-based script-src; htmx's `hx-on` and its `[…]` trigger filters are
# compiled with Function(), which needs 'unsafe-eval'; a javascript: URL is
# inline script by another name.
_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.I)
_HX_ON = re.compile(r"\shx-on(?::|-)", re.I)
_TRIGGER_FILTER = re.compile(r"""hx-trigger\s*=\s*["'][^"']*\[""", re.I)
_JS_URL = re.compile(r"""(?:href|src|action)\s*=\s*["']\s*javascript:""", re.I)
_SCRIPT_TAG = re.compile(r"<script\b([^>]*)>", re.I)


def test_the_templates_were_found():
    """Every check below loops over ALL; an empty list would pass them all."""
    assert len(ALL) > 20
    assert TEMPLATES / "base.html" in ALL


@pytest.mark.criterion(403, "No template carries an inline event handler")
@pytest.mark.parametrize("pattern,what", [
    (_HANDLER, "an inline on…= event handler"),
    (_HX_ON, "an hx-on attribute (htmx evaluates it with Function())"),
    (_TRIGGER_FILTER, "an hx-trigger filter (htmx evaluates it with Function())"),
    (_JS_URL, "a javascript: URL"),
], ids=["on-handler", "hx-on", "trigger-filter", "javascript-url"])
def test_no_template_carries_script_in_an_attribute(pattern, what):
    hits = [f"{p.relative_to(TEMPLATES)}:{n}"
            for p in ALL
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if pattern.search(line)]
    assert not hits, f"{what} — the policy blocks it, silently: {hits}"


@pytest.mark.parametrize("sample", [
    '<select name="m" onchange="this.form.submit()">',
    '<button onclick="go()">',
    '<form hx-on::after-request="x()">',
    '<input hx-trigger="keyup[key==\'Enter\']">',
    '<a href="javascript:void(0)">',
])
def test_each_pattern_catches_what_it_names(sample):
    """The scan above is only as good as its patterns. Each sample is a real
    shape that was in the templates before #403, and one pattern must flag it."""
    assert any(p.search(" " + sample) for p in
               (_HANDLER, _HX_ON, _TRIGGER_FILTER, _JS_URL))


def test_every_inline_script_tag_asks_for_the_nonce():
    """A block written without `nonce=` renders fine, passes every request
    test, and does nothing in a browser. The behave scenario catches it only
    on pages it can reach with the data it has; this catches it in the source."""
    bare = []
    for p in ALL:
        for attrs in _SCRIPT_TAG.findall(p.read_text()):
            if "src=" in attrs:
                continue
            if 'nonce="{{ csp_nonce() }}"' not in attrs:
                bare.append(f"{p.relative_to(TEMPLATES)}: <script{attrs}>")
    assert not bare, f"inline <script> without the nonce: {bare}"
