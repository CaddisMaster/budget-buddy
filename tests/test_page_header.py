"""One header row per page (#235).

Home used to stack two topbars before the hero — base.html's (☰ + Add) and
dashboard.html's (greeting + days-left) — 133px of chrome at 1440px for what is
a page title and a page action, i.e. peers that belong on one line. The ☰ also
rendered on desktop, where the sidebar is permanently visible.

Two of these properties are only expressible against the stylesheet, following
the convention in test_design_system.py: a viewport-dependent rule is invisible
to a Flask test client, which applies no CSS and runs no JS.

⚠️ The desktop-collapse assertions are not tidiness. Removing the topbar ☰ on
desktop while leaving the sidebar's own ☰ would turn "collapse the sidebar"
into a ONE-WAY DOOR — the control that restored it was the one being removed,
and a collapsed sidebar leaves the app with no navigation at all.

⚠️ These read app/static/style.css and app/templates/, both of which DO ship in
the image (.dockerignore strips *.md and the compose files, not app/), so no
skipif guard is needed — see docs/testing.md before adding one about any other
repo file.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "app" / "static" / "style.css"
BASE_HTML = ROOT / "app" / "templates" / "base.html"

# Every page that extends base.html and therefore renders the header row.
# login.html is deliberately absent — it is the second shell and has no sidebar.
PAGES = [
    "/",
    "/transactions",
    "/transactions/new",
    "/scheduled",
    "/categories",
    "/accounts",
    "/budgets",
    "/transfers",
    "/goals",
    "/profile",
    "/change-password",
]

ADMIN_PAGES = ["/settings", "/admin/users", "/admin/create-user"]


@pytest.fixture(scope="module")
def css():
    return CSS_PATH.read_text()


@pytest.fixture(scope="module")
def css_no_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _header(html):
    """The page-header element's inner markup, or None."""
    match = re.search(
        r'<header class="page-header".*?>(.*?)</header>', html, re.S)
    return match.group(1) if match else None


def _mobile_block(css_no_comments):
    """The body of the max-width: 768px media query."""
    match = re.search(
        r"@media \(max-width: 768px\)\s*\{(.*?)\n\}", css_no_comments, re.S)
    assert match, "no mobile media block"
    return match.group(1)


# --- one row, on every page ---------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_exactly_one_header_row(client_a, path):
    html = client_a.get(path).get_data(as_text=True)
    assert html.count('class="page-header"') == 1, path


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_every_admin_page_renders_exactly_one_header_row(admin_client, path):
    html = admin_client.get(path).get_data(as_text=True)
    assert html.count('class="page-header"') == 1, path


@pytest.mark.parametrize("path", PAGES)
def test_the_title_and_the_actions_share_that_row(client_a, path):
    """The whole point of #235: a page title and a page action are peers."""
    header = _header(client_a.get(path).get_data(as_text=True))
    assert header is not None, f"{path} renders no page-header"
    assert "<h1" in header, f"{path}'s <h1> is not in the header row"
    assert "quick-add-btn" in header, f"{path}'s Add action is not in the row"


@pytest.mark.parametrize("path", PAGES)
def test_no_page_carries_a_second_heading_below_the_row(client_a, path):
    """A page that kept its own <h1> in the content block would render the
    title twice — which is exactly what the sweep must not leave behind."""
    html = client_a.get(path).get_data(as_text=True)
    assert html.count("<h1") == 1, path


def test_the_old_stacked_topbar_is_gone_everywhere(client_a):
    """base.html and dashboard.html both rendered `.page-topbar`; Home showed
    both at once. The class is retired, so its absence is the check."""
    for path in PAGES:
        html = client_a.get(path).get_data(as_text=True)
        assert "page-topbar" not in html, path


# --- Home keeps its parts -----------------------------------------------------


def test_home_still_greets_by_name(client_a, users):
    html = client_a.get("/").get_data(as_text=True)
    header = _header(html)
    assert header is not None
    assert 'id="greeting"' in header
    assert "Hello," in header


def test_home_still_carries_the_month_picker_and_the_add_button(client_a):
    header = _header(client_a.get("/").get_data(as_text=True))
    assert 'name="month"' in header, "the month picker left the header row"
    assert "quick-add-btn" in header, "the Add button left the header row"


def test_home_still_says_how_much_of_the_month_is_left(client_a):
    html = client_a.get("/").get_data(as_text=True)
    assert "left in the month." in html or "Last day of the month." in html


# --- the hamburger is mobile-only ---------------------------------------------


def test_the_hamburger_is_hidden_by_default_and_shown_on_mobile(css_no_comments):
    """Asserted as a property of the cascade, not a string: the base rule must
    not paint it, and the mobile block must. Written this way round on purpose
    — `[hidden]` is user-agent origin and loses to any author display rule, so
    the visibility has to be decided by the author rules themselves."""
    base_rule = re.search(r"\n\.hamburger \{([^}]*)\}", css_no_comments)
    assert base_rule, "no .hamburger rule"
    assert re.search(r"display:\s*none", base_rule.group(1)), \
        ".hamburger is painted on desktop, where the sidebar is always visible"

    mobile_rule = re.search(
        r"\.hamburger \{([^}]*)\}", _mobile_block(css_no_comments))
    assert mobile_rule, "the mobile block never brings the hamburger back"
    assert re.search(r"display:\s*(flex|block|inline-flex)",
                     mobile_rule.group(1)), \
        "the mobile hamburger is declared but not displayed"


def test_no_desktop_control_can_hide_the_sidebar(css_no_comments):
    """⚠️ The one-way door. Both ☰ buttons share the .hamburger class, so the
    rule above hides the sidebar's own close button too — but only if nothing
    else re-paints it at desktop width."""
    outside_mobile = css_no_comments.replace(
        _mobile_block(css_no_comments), "")
    for rule in re.findall(r"([^{}]*\.hamburger[^{}]*)\{([^}]*)\}",
                           outside_mobile):
        selector, body = rule
        assert not re.search(r"display:\s*(flex|block|inline-flex)", body), \
            f"`{selector.strip()}` shows a hamburger at desktop width"


def test_the_header_row_wraps_on_a_phone_instead_of_crushing_the_title(
        css_no_comments):
    """Caught in a real browser at 390px, never by the suite: Home's month
    picker sits in the same row as the greeting, and `.page-heading`'s
    `min-width: 0` (which desktop needs, so a long title truncates instead of
    shoving the actions off the row) let the title crush to ~150px — "Good
    afternoon, Dev" rendered as four lines of header.

    Both halves are the fix and neither works alone: `flex-wrap` gives the
    actions somewhere to go, and the min-width floor is what makes them
    actually go there rather than the title shrinking to make room."""
    mobile = _mobile_block(css_no_comments)

    header_rule = re.search(r"\.page-header \{([^}]*)\}", mobile)
    assert header_rule, "the mobile block never touches .page-header"
    assert re.search(r"flex-wrap:\s*wrap", header_rule.group(1)), \
        "the header row cannot wrap on a phone, so its actions squeeze the title"

    heading_rule = re.search(r"\.page-heading \{([^}]*)\}", mobile)
    assert heading_rule, "no mobile floor on .page-heading — the title crushes"
    assert re.search(r"min-width:\s*[1-9]", heading_rule.group(1)), \
        "a zero/absent min-width lets the title shrink instead of wrapping"


def test_the_sidebar_has_no_collapsed_state_left_to_get_stuck_in():
    """The desktop collapse branch went with the desktop ☰. Leaving the JS
    behind would let a control — or a stale cached page — put the sidebar into
    a state that nothing remaining can undo.

    ⚠️ Comments are stripped first, the same device as
    test_dashboard_merge.py's hardcoded-colour scan: the rule is about CODE,
    and base.html's comment names both retired classes ON PURPOSE, so that the
    next person to reach for them reads why they went."""
    source = BASE_HTML.read_text()
    code = re.sub(r"\{#.*?#\}", "", source, flags=re.S)   # Jinja comments
    code = re.sub(r"//[^\n]*", "", code)                  # JS line comments
    assert "collapsed" not in code, \
        "base.html can still collapse the sidebar with no way to restore it"
    assert "expanded" not in code, \
        "base.html can still expand main with no way to restore the sidebar"
