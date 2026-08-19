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

from tests.conftest import create_category, create_goal, create_transaction

CSS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"

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

    # One net figure in the hero, full stop.
    assert hero.count('class="hero-net') == 1
    # ⚠️ #225 moved income and expenses OUT of the hero into their own cards
    # beside it, so the hero must no longer carry stat rows at all — that markup
    # is exactly what printed the net a second time.
    assert "hero-stat" not in hero

    # The two parts are still on the page, as peers of the hero rather than
    # repetitions inside it.
    band = _between(html, 'class="hero-band"', '<!--/hero-band-->')
    assert "Money in" in band and "Money out" in band


# --- 2. one AI panel, not four cards ------------------------------------------

def test_the_ai_surfaces_render_inside_one_panel(client_a, users, monkeypatch):
    """#223 merged four AI cards into one panel visually; #232 made them ONE
    feature. What is asserted here is the end state: a single panel, the read
    and the box inside it, and no trace of the three cards it replaced."""
    monkeypatch.setenv(AI_KEY, "test-key")
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)

    panel = _between(html, 'id="ask-panel"', '<!--/ask-panel-->')
    assert 'id="ask-question"' in panel
    for gone in ('id="insight-card"', 'id="forecast-card"', 'id="agent-card"'):
        assert gone not in html, f"{gone} survived the fold into one panel"


def test_no_read_panel_without_a_key(client_a, users, monkeypatch):
    # The wrapper must be gated with the things it wraps — an empty bordered
    # box on every AI-less install would be worse than the four cards were.
    monkeypatch.delenv(AI_KEY, raising=False)
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)
    assert 'id="ask-panel"' not in html
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

def test_the_hero_comes_before_the_strip(client_a, users):
    """⚠️ This used to check the quick-add box was below the hero too. #232
    removed that feature outright, so the ordering property now has one
    subject — do not re-add an assertion about #txn-form-wrap here: Home has no
    such element any more, and `index()` would raise rather than fail."""
    _seed_month(users["a"])
    html = client_a.get("/").get_data(as_text=True)

    hero = html.index('<div class="hero"')
    assert html.index('id="whatsnew"') > hero, (
        "the What's-new strip still renders above the month's headline figure")
    assert 'id="txn-form-wrap"' not in html


# --- 6. year-over-year is a strip ---------------------------------------------

def test_year_over_year_is_a_tile_among_peers_not_three_cards(client_a, users):
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
    row = _between(html, 'class="stat-row"', '<!--/stat-row-->')
    assert "%" in row, "the change figure is what the comparison is for"
    # The three full-height cards this replaced are gone for good.
    assert 'class="stat-card"' not in row
    # Year-over-year is a peer of the other tiles, not a headline of its own.
    assert "<h2>Year over year</h2>" not in html


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


# --- 7. #233: the band balances, and a lone stat tile fills its row ----------
#
# Both of these are gaps you can only see in a browser, and both were measured
# rather than guessed: 380px of void under the AI panel, and 776px of empty row
# beside a single stat tile.

def test_goals_render_below_the_band_not_inside_it(client_a, users, monkeypatch):
    """Goals under the ranked bars made the left column ~750px against the
    panel's ~371 — the void beside the panel and the tall goal stack were one
    problem. Out of the band the two columns end together and the goal cards
    lay out 2-up."""
    a = users["a"]
    _seed_month(a)
    create_goal(a["id"], a["account_id"], 1000, baseline=0)
    monkeypatch.setenv(AI_KEY, "test-key")
    html = client_a.get("/").get_data(as_text=True)

    band = _between(html, 'class="home-band"', '<!--/home-band-->')
    assert "goal-grid" not in band, "Goals is back inside the band"
    assert 'class="goals-head"' in html          # it still renders...
    assert html.index('<!--/home-band-->') < html.index('class="goals-head"')


def test_the_goals_rollup_sits_on_the_heading_line(client_a, users, monkeypatch):
    """One card, not three: the rollup shares the heading row rather than
    getting a card above two cards. It still comes from the shared partial, so
    /goals and Home can never disagree on the arithmetic."""
    a = users["a"]
    _seed_month(a)
    create_goal(a["id"], a["account_id"], 1000, baseline=0)
    monkeypatch.setenv(AI_KEY, "test-key")
    html = client_a.get("/").get_data(as_text=True)

    head = _between(html, 'class="goals-head"', "</div>")
    assert ">Goals<" in head
    assert "goal-summary" in head


def test_a_lone_stat_tile_is_not_capped_to_a_third_of_the_row():
    """⚠️ Stated against the STYLESHEET, because no request renders CSS and the
    defect was invisible to every route test: `.stat-tile` had
    `max-width: 380px`, so a month with only one tile to show left 776px of the
    row empty. Tiles are flex-grow with no cap now — one spans, two halve,
    three third it."""
    css = CSS_PATH.read_text()
    rule = css.split(".stat-tile {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1" in rule
    assert "max-width" not in rule


def test_the_band_columns_end_together():
    """`align-items: start` left whichever column was shorter with a ragged gap
    under it, and the read's length is not fixed, so neither column can be
    assumed the taller one."""
    css = CSS_PATH.read_text()
    band = css.split(".home-band { display: grid;", 1)[1].split("}", 1)[0]
    assert "align-items: stretch" in band
