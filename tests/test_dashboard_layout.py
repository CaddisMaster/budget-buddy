"""#223 — the Home page's own layout.

The complaint was "cluttered", which is not a testable word. What IS testable
is each structural decision taken in response (Sean, 2026-08-18), and those are
what this file pins:

  1. the hero states Net ONCE (it printed the same figure twice)
  2. the four AI surfaces render inside ONE panel, not four stacked cards
  3. category spending is ranked bars in the main flow, not a doughnut
  4. the remaining five charts stay in the collapsed section
  5. the hero comes first — What's-new and quick-add sit below it
  6. year-over-year is one strip, not three full-height stat cards

⚠️ Content-asserting on purpose. A Jinja typo renders as an EMPTY STRING rather
than raising, so "the page still returns 200" proves nothing about a layout
change — see docs/gotchas.md.

⚠️ These do NOT re-assert what other files already hold: test_ai_collapse.py
owns the <details> read-state contract (ids, data-ai-key, data-generated, open
state) and test_dashboard_merge.py owns the chart payload (#108's fold, #111's
slots). This file asserts only where things SIT, so a future change to either
contract fails in one place rather than two.
"""
import re
from datetime import date
from pathlib import Path

from tests.conftest import create_category, create_transaction

AI_KEY = "ANTHROPIC_API_KEY"


def _prev_month(today):
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def _seed_month(user, *, expenses=(("Housing", 1850), ("Groceries", 912)),
                income=1500):
    """A month with enough shape for the hero, the bars and the AI panel.

    ⚠️ Seeds the PREVIOUS month as well, deliberately: `show_insight` is
    computed from the previous month's facts (main.py:392 — the insight is
    retrospective, the forecast prospective), so a current-month-only seed
    renders no insight card and would fail the panel test for a reason that has
    nothing to do with layout."""
    today = date.today()
    when = today.replace(day=min(today.day, 28))
    for name, amount in expenses:
        cat = create_category(user["id"], name)
        create_transaction(user["id"], user["account_id"], amount, when,
                           transaction_type="expense", category_id=cat)
    create_transaction(user["id"], user["account_id"], income, when,
                       transaction_type="income",
                       category_id=user["category_id"])

    p_year, p_month = _prev_month(today)
    create_transaction(user["id"], user["account_id"], 400,
                       date(p_year, p_month, 15),
                       transaction_type="expense",
                       category_id=user["category_id"])
    return when


def _between(html, start, end):
    """The slice of the page between two markers, both of which must exist."""
    assert start in html, f"{start!r} missing from the page"
    assert end in html, f"{end!r} missing from the page"
    return html.split(start, 1)[1].split(end, 1)[0]


# --- 1. the hero says it once -------------------------------------------------

def test_the_hero_states_net_once(client_a, users):
    # The hero printed the net figure as .hero-net AND again as a third
    # .hero-stat, so the single most prominent number on the page was
    # immediately repeated three lines below it.
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)
    hero = _between(html, '<div class="hero"', '</div><!--/hero-->')

    labels = [chunk.split("<", 1)[0].strip() for chunk in
              hero.split('class="hero-stat-label">')[1:]]
    assert labels == ["Income", "Expenses"], (
        f"the hero should break the net into its two parts, got {labels}")
    assert hero.count('class="hero-net') == 1


# --- 2. one AI panel, not four cards ------------------------------------------

def test_the_ai_surfaces_render_inside_one_panel(client_a, users, monkeypatch):
    monkeypatch.setenv(AI_KEY, "test-key")
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)

    panel = _between(html, 'id="month-read"', '<!--/month-read-->')
    for card in ('id="insight-card"', 'id="forecast-card"', 'id="agent-card"'):
        assert card in panel, f"{card} is not inside the read panel"
    # Ask moved into the panel's foot rather than being a fifth card.
    assert 'id="ask-question"' in panel


def test_no_read_panel_without_a_key(client_a, users, monkeypatch):
    # The wrapper must be gated with the things it wraps — an empty bordered
    # box on every AI-less install would be worse than the four cards were.
    monkeypatch.delenv(AI_KEY, raising=False)
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)
    assert 'id="month-read"' not in html
    assert "Ask your finances" not in html


# --- 3 + 4. bars in the flow, the rest collapsed ------------------------------

def test_category_spending_is_ranked_bars_in_the_main_flow(client_a, users):
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)

    bars = _between(html, 'class="cat-bars"', '<!--/cat-bars-->')
    assert "Housing" in bars and "Groceries" in bars
    assert "1,850" in bars, "a bar should carry its amount, not just a length"
    # Colour still comes from the palette, so dark mode follows and #111's slot
    # assignment keeps meaning something — but as a CLASS backed by a rule in
    # style.css, not an inline var(--series-{{ n }}). A token name assembled in
    # Jinja is invisible to test_param_hardening.py's undefined-property scan,
    # which is the only guard between a typo'd token and a bar that paints
    # nothing at all.
    assert re.search(r'class="cat-bar-fill s(\d|-other)"', bars), \
        "bars do not carry a palette slot class"
    css = (Path(__file__).resolve().parents[1]
           / "app" / "static" / "style.css").read_text()
    for slot in range(1, 9):
        assert f".cat-bar-fill.s{slot}" in css, f"no rule for slot {slot}"
    assert ".cat-bar-fill.s-other" in css, "the folded tail has no rule"
    # The doughnut it replaces is gone entirely — not merely hidden.
    assert 'id="spendingPie"' not in html


def test_the_remaining_charts_stay_in_the_collapsed_section(client_a, users):
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)

    charts = _between(html, 'id="charts-details"', "</details>")
    for canvas in ("accountBar", "dowBar", "cashFlowBar", "netBalanceLine",
                   "budgetBar"):
        assert f'id="{canvas}"' in charts, f"{canvas} left the charts section"


# --- 5. the hero opens the page -----------------------------------------------

def test_the_hero_comes_before_the_strip_and_the_quick_add(client_a, users):
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)

    hero = html.index('<div class="hero"')
    for later in ('id="whatsnew"', 'id="txn-form-wrap"'):
        assert html.index(later) > hero, (
            f"{later} still renders above the month's headline figure")


# --- 6. year-over-year is a strip ---------------------------------------------

def test_year_over_year_is_one_strip_not_three_cards(client_a, users):
    # Three full-height stat cards for one comparison was the clearest single
    # case of the page shouting every figure at the same volume.
    a = users["a"]
    when = _seed_month(a)
    create_transaction(a["id"], a["account_id"], 700,
                       when.replace(year=when.year - 1),
                       transaction_type="expense", category_id=a["category_id"])

    # ⚠️ Year-over-year only computes when a single month is SELECTED
    # (main.py:366) — the comparison is meaningless across "all months", so an
    # unfiltered request renders no strip at all and would fail this for a
    # reason unrelated to layout.
    html = client_a.get(f"/?month={when:%Y-%m}").get_data(as_text=True)
    strip = _between(html, 'class="yoy-strip"', '<!--/yoy-strip-->')
    assert "%" in strip, "the change figure is what the comparison is for"
    assert 'class="stat-card"' not in strip


# --- regressions found by LOOKING at the page, not by running it -------------
#
# Both of these shipped green through the whole suite and were caught only in a
# browser screenshot. They are the two failure modes a Flask test client
# structurally cannot see: markup that is present but invisible, and script that
# is present but throws.

def test_the_chart_scaffolding_survives_beside_the_bars(client_a, users):
    # #223 cut the category doughnut out of the inline chart script. The
    # defaults, the grid colours and the initialization guard were written ABOVE
    # the doughnut and went with it, leaving `initCharts()` called but never
    # defined — every chart on the page blank, with the suite still green
    # because the canvases are in the HTML either way.
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)

    assert 'id="charts-details"' in html
    # ⚠️ Word-boundary regexes, not substrings: `"const gridScales" in html`
    # passes against `const gridScalesXX`, so the first cut of this test could
    # not tell a rename from a deletion — verified by renaming it.
    for pattern in (r"\bfunction initCharts\s*\(",
                    r"\bconst gridScales\b",
                    r"\bconst gridColor\b",
                    r"\bChart\.defaults\.font\.family\b"):
        assert re.search(pattern, html), f"the chart script lost {pattern!r}"
    # Called and defined, never one without the other.
    assert re.search(r"\binitCharts\(\)", html)


def test_a_hidden_bar_list_is_actually_hidden():
    # The income list carries `hidden`, but the UA stylesheet's
    # `[hidden] { display: none }` lives in the user-agent origin — ANY author
    # `display` declaration beats it. `.cat-bars { display: flex }` therefore
    # rendered the income list straight under the expense list, and the page
    # showed Salary as though it were a cost.
    css = (Path(__file__).resolve().parents[1]
           / "app" / "static" / "style.css").read_text()
    rule = re.search(r"\.cat-bars\[hidden\]\s*\{([^}]*)\}", css)
    assert rule, ".cat-bars[hidden] has no rule, so `hidden` does nothing here"
    assert "display: none" in rule.group(1)
