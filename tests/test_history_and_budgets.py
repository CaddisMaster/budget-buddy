"""History reads as a ledger, Budgets shows position (#237, #238).

The two dense data pages. Both are genuinely tabular — #237 says so in as many
words ("this is NOT a candidate for cards") — so neither becomes cards; what
changes is hierarchy, alignment, and whether the page states its position
before its detail.

⚠️ Two properties here can only be asserted against the stylesheet, following
test_design_system.py: a Flask test client applies no CSS, and "which column is
the most legible" is a rendering question. The rest is markup and is asserted
against real responses.
"""
import re
from datetime import date
from pathlib import Path

import pytest

from app.blueprints.budgets import BudgetRow, summarize_budgets
from tests.conftest import create_budget, create_category, create_transaction

CSS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"


@pytest.fixture(scope="module")
def css():
    return re.sub(r"/\*.*?\*/", "", CSS_PATH.read_text(), flags=re.S)


def _desktop(css):
    """The stylesheet with the mobile block removed.

    ⚠️ The whole #237 defect lives in this distinction: `.c-amount` and friends
    were styled ONLY inside `@media (max-width: 768px)`, so on a desktop every
    column rendered at one weight and the two numbers people scan for read
    exactly like the description does.
    """
    return re.sub(r"@media \(max-width: 768px\)\s*\{.*?\n\}", "", css, flags=re.S)


# --- #237: the ledger reads as a ledger ---------------------------------------


def test_the_money_columns_are_emphasised_on_desktop_too(css):
    desktop = _desktop(css)
    for col in (".c-amount", ".c-bal"):
        rule = re.search(rf"{re.escape(col)}\b[^{{]*\{{([^}}]*)\}}", desktop)
        assert rule, f"{col} is styled only inside the mobile block"
        assert re.search(r"font-weight:\s*[6-9]\d\d", rule.group(1)), \
            f"{col} carries no emphasis on desktop"


def test_the_money_columns_use_tabular_figures(css):
    """"Money columns align on the decimal point" — which proportional digits
    cannot do, however the cell is aligned."""
    desktop = _desktop(css)
    for col in (".c-amount", ".c-bal"):
        rule = re.search(rf"{re.escape(col)}\b[^{{]*\{{([^}}]*)\}}", desktop)
        assert rule and "tabular-nums" in rule.group(1), \
            f"{col} does not use tabular figures, so its digits cannot align"
        assert re.search(r"text-align:\s*right", rule.group(1)), \
            f"{col} is not right-aligned, so the decimal points do not line up"


def test_the_supporting_columns_are_quieter_than_the_numbers(css):
    """Hierarchy is a comparison, not an absolute: the date/category/account
    cells must be visibly demoted relative to the two figures."""
    desktop = _desktop(css)
    rule = re.search(r"\.c-date[^{]*\{([^}]*)\}", desktop)
    assert rule, "the supporting columns are not demoted on desktop"
    assert "--text-muted" in rule.group(1), \
        "the supporting columns are the same ink as the figures"


def test_the_filters_sit_in_the_page_header(client_a):
    """#237: "the controls sit in the page header rather than as loose forms"."""
    html = client_a.get("/transactions").get_data(as_text=True)
    header = re.search(r'<header class="page-header".*?</header>', html, re.S)
    assert header, "no page header"
    assert 'name="search"' in header.group(0), "the search box is not in the header"
    assert 'name="month"' in header.group(0), "the month filter is not in the header"


def test_the_old_loose_toolbar_is_gone(client_a):
    html = client_a.get("/transactions").get_data(as_text=True)
    assert '<div class="toolbar">' not in html


def test_an_active_filter_is_visible_without_reading_the_url(client_a, users):
    """#237: the applied filter must be stated on the page."""
    html = client_a.get("/transactions?search=txn-A").get_data(as_text=True)
    assert "filter-chip" in html, "no chip states the active filter"
    chip = html[html.index("filter-chip"):]
    assert "txn-A" in chip[:400], "the chip does not name what is filtered"


def test_no_chip_when_nothing_is_filtered(client_a):
    html = client_a.get("/transactions").get_data(as_text=True)
    assert "filter-chip" not in html


def test_the_chip_clears_the_filter_it_names(client_a, users):
    """A chip that states a filter but cannot remove it is just a label."""
    html = client_a.get("/transactions?search=txn-A").get_data(as_text=True)
    chip = html[html.index("filter-chip"):][:600]
    assert "href" in chip, "the chip offers no way to clear the filter"


# --- #238: position, not arithmetic -------------------------------------------


def _row(name, effective, actual, is_set=True):
    return BudgetRow(cid=abs(hash(name)) % 10000, name=name, effective=effective,
                     is_set=is_set, suggested=None, actual=actual)


def test_the_overall_position_sums_only_budgets_actually_set():
    """⚠️ A suggestion is not a budget. Counting suggested amounts would state
    a total the user never chose, and the page would claim they are "under
    budget" against a number they have never seen."""
    rows = [_row("Rent", 1000, 900),
            _row("Guess", 500, 400, is_set=False)]
    s = summarize_budgets(rows)
    assert s.budgeted == 1000
    assert s.spent == 900


def test_the_overall_position_counts_what_is_over():
    rows = [_row("Rent", 1000, 1200), _row("Food", 300, 100)]
    s = summarize_budgets(rows)
    assert s.budgeted == 1300
    assert s.spent == 1300
    assert s.over_count == 1


def test_no_budgets_set_means_no_overall_line():
    assert summarize_budgets([_row("X", 100, 50, is_set=False)]) is None
    assert summarize_budgets([]) is None


def test_the_page_states_the_overall_position_before_the_detail(client_a, users):
    cid = create_category(users["a"]["id"], "Overall Cat")
    create_budget(users["a"]["id"], cid, 500)
    html = client_a.get("/budgets").get_data(as_text=True)
    assert "budget-overall" in html, "the page states no overall position"
    assert html.index("budget-overall") < html.index('id="budget-rows"'), \
        "the overall position comes after the per-category detail"


def test_an_over_budget_category_shows_how_far_over_as_a_proportion(
        client_a, users):
    cid = create_category(users["a"]["id"], "Overspent Cat")
    create_budget(users["a"]["id"], cid, 100)
    create_transaction(users["a"]["id"], users["a"]["account_id"], 150,
                       date.today(),
                       category_id=cid, transaction_type="expense")
    html = client_a.get("/budgets").get_data(as_text=True)
    assert "budget-bar" in html, "no proportion bar on the budget rows"


def test_over_budget_does_not_rely_on_colour_alone(client_a, users):
    """⚠️ #238 states this as a requirement, and the budget REPORT's legend
    used to read "Green = stayed under, red = went over" — an instruction to
    read by colour and nothing else."""
    cid = create_category(users["a"]["id"], "Colourless Cat")
    create_budget(users["a"]["id"], cid, 100)
    create_transaction(users["a"]["id"], users["a"]["account_id"], 150,
                       date.today(),
                       category_id=cid, transaction_type="expense")
    html = client_a.get("/budgets").get_data(as_text=True)
    over = html[html.index("Colourless Cat"):][:1200]
    assert "Over" in over, "over-budget is signalled by colour alone"


def test_the_report_legend_no_longer_tells_you_to_read_by_colour(client_a):
    html = client_a.get("/budgets").get_data(as_text=True)
    assert "Green = stayed under" not in html
