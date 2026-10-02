"""Steps for content_security_policy.feature (#403).

The pages come from the app's own url_map: every rule that answers a GET and
takes no URL arguments. A hand-written list would only ever fail for the pages
someone remembered to add, so it is derived. The exclusions are listed, each
with its reason.

⚠️ Types and `_user` come from `tests/features/support.py`, imported before any
step below is defined. See that module for why the import order matters.
"""
import re

from behave import then, when

from tests.features.support import _user  # noqa: F401  (registers the shared types first)

# Endpoints a plain GET must not be sent to in a test, and why.
SKIPPED = {
    "admin.backup_database": "runs a real pg_dump",
}

# Pages known to carry inline script. If none of them is swept, the
# "every inline script" check below has nothing to look at and proves nothing.
MUST_CARRY_INLINE_SCRIPT = {"/", "/transactions", "/profile", "/admin/create-user"}

_SCRIPT = re.compile(r"<script\b([^>]*)>", re.I)
_NONCE_ATTR = re.compile(r'\bnonce="([^"]+)"')


def _directives(header):
    out = {}
    for part in header.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


@when("user {who:Who} opens every page the app serves")
def when_opens_every_page(context, who):
    app = context.app
    rules = [r for r in app.url_map.iter_rules()
             if "GET" in r.methods and not r.arguments
             and r.endpoint not in SKIPPED and r.endpoint != "static"]
    context.responses = {}
    for rule in rules:
        resp = context.client.get(rule.rule)
        context.responses[rule.rule] = resp
    html = {p for p, r in context.responses.items()
            if r.status_code == 200 and r.mimetype == "text/html"}
    assert MUST_CARRY_INLINE_SCRIPT <= html, (
        f"pages expected to render were not swept as HTML: "
        f"{sorted(MUST_CARRY_INLINE_SCRIPT - html)}")
    context.html_pages = html


@then("every response sends a script-src of the app's own files and that response's nonce")
def then_script_src(context):
    for path, resp in context.responses.items():
        header = resp.headers.get("Content-Security-Policy", "")
        d = _directives(header)
        sources = d.get("script-src")
        assert sources, f"{path}: no script-src in {header!r}"
        nonces = [s for s in sources if s.startswith("'nonce-")]
        others = [s for s in sources if not s.startswith("'nonce-")]
        assert others == ["'self'"], f"{path}: script-src allows {others}"
        assert len(nonces) == 1, f"{path}: expected one nonce, got {nonces}"
        assert d.get("object-src") == ["'none'"], f"{path}: object-src {d.get('object-src')}"
        assert d.get("base-uri") == ["'self'"], f"{path}: base-uri {d.get('base-uri')}"
        assert d.get("frame-ancestors") == ["'none'"], f"{path}: {header!r}"


@then("every inline script on those pages carries that response's nonce")
def then_inline_scripts_nonced(context):
    inline_seen = set()
    for path in context.html_pages:
        resp = context.responses[path]
        sources = _directives(resp.headers["Content-Security-Policy"])["script-src"]
        nonce = next(s for s in sources if s.startswith("'nonce-"))[len("'nonce-"):-1]
        for attrs in _SCRIPT.findall(resp.get_data(as_text=True)):
            if "src=" in attrs:
                continue
            inline_seen.add(path)
            found = _NONCE_ATTR.search(attrs)
            assert found and found.group(1) == nonce, (
                f"{path}: an inline <script{attrs}> does not carry this response's nonce")
    assert MUST_CARRY_INLINE_SCRIPT <= inline_seen, (
        f"no inline script found on {sorted(MUST_CARRY_INLINE_SCRIPT - inline_seen)}, "
        "so this check looked at nothing there")


@then("no two responses share a nonce")
def then_nonces_unique(context):
    seen = []
    for resp in context.responses.values():
        sources = _directives(resp.headers["Content-Security-Policy"])["script-src"]
        seen += [s for s in sources if s.startswith("'nonce-")]
    assert len(seen) == len(context.responses), "a response carried no nonce"
    assert len(set(seen)) == len(seen), (
        f"{len(seen) - len(set(seen))} responses reused a nonce, so one page's "
        "nonce would unlock another's injected script")
