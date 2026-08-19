"""Per-page behaviour from the five same-shape overhauls.

The shared "state first" property lives in test_state_first_pages.py. What is
here is the part each issue asked for on its own page, and in two cases the
part that is genuinely load-bearing rather than cosmetic.
"""
import re
from datetime import date, timedelta
from pathlib import Path

from app.blueprints.schedules import pick_next_due
from tests.conftest import (
    create_account,
    create_category,
    create_schedule,
    create_transfer_schedule,
    fetch_category_kind,
    find_category_id,
)

TODAY = date.today()


class _Row:
    """Stands in for a SCHEDULE_ROW_SQL namedtuple — pick_next_due reads four
    fields and is pure, so the real query is not needed to state its rules."""

    def __init__(self, ident, next_due, is_active=True, is_finished=False):
        self.id = ident
        self.next_due = next_due
        self.is_active = is_active
        self.is_finished = is_finished


# --- #241: what is due next ---------------------------------------------------


def test_the_soonest_schedule_is_the_next_due_one():
    rows = [_Row(1, TODAY + timedelta(days=9)), _Row(2, TODAY + timedelta(days=2))]
    assert pick_next_due(rows).id == 2


def test_the_table_order_is_not_the_answer():
    """⚠️ The rows arrive ordered by transaction_type FIRST, so the top row is
    routinely not the soonest — which is exactly why reading the table's first
    row would have been wrong."""
    income_first = _Row(1, TODAY + timedelta(days=20))
    expense_later = _Row(2, TODAY + timedelta(days=1))
    assert pick_next_due([income_first, expense_later]).id == 2


def test_an_inactive_schedule_is_never_next_due():
    rows = [_Row(1, TODAY + timedelta(days=1), is_active=False),
            _Row(2, TODAY + timedelta(days=8))]
    assert pick_next_due(rows).id == 2


def test_a_finished_schedule_is_never_next_due():
    """#32's end_date: it still renders in the table, but it will not post."""
    rows = [_Row(1, TODAY + timedelta(days=1), is_finished=True),
            _Row(2, TODAY + timedelta(days=8))]
    assert pick_next_due(rows).id == 2


def test_nothing_live_means_no_next_due_line():
    assert pick_next_due([]) is None
    assert pick_next_due([_Row(1, TODAY, is_active=False)]) is None


def test_the_page_names_what_is_due_next(client_a, users):
    create_schedule(users["a"]["id"], users["a"]["account_id"], 12.34, "monthly",
                    TODAY + timedelta(days=3), description="Broadband")
    html = client_a.get("/scheduled").get_data(as_text=True)
    assert "next-due" in html, "no next-due line on the page"
    head = html[:html.index('id="schedule-rows"')]
    assert "Broadband" in head, "what is due next is not stated above the table"


# --- #243: grouping, and the swap that grouping makes unsafe ------------------


def test_categories_are_grouped_by_kind_not_merely_labelled(client_a, users):
    create_category(users["a"]["id"], "ZZ Salary", kind="income")
    html = client_a.get("/categories").get_data(as_text=True)
    assert "Expense</h2>" in html and "Income</h2>" in html


def test_an_income_category_is_added_under_income_not_at_the_top(client_a, users):
    """⚠️ The reason the add swap re-renders the whole listing. With the old
    prepend-into-one-tbody, a new income category landed under the Expense
    heading — visibly filed under the wrong kind until the next page load."""
    resp = client_a.post("/categories",
                         data={"name": "Royalties", "kind": "income"},
                         headers={"HX-Request": "true"})
    body = resp.get_data(as_text=True)
    assert "Royalties" in body
    income_at = body.index("Income</h2>")
    assert body.index("Royalties") > income_at, \
        "the new income category rendered above the Income heading"


def test_flipping_a_kind_re_renders_the_whole_listing(client_a, users):
    """A kind change moves the row to the other group, so a single-row swap
    would leave it under the heading it no longer belongs to. The route
    retargets in that case — and only that case."""
    cid = find_category_id(users["a"]["id"], "cat-A")
    resp = client_a.post(f"/categories/{cid}/edit",
                         data={"name": "cat-A", "description": "", "kind": "income"},
                         headers={"HX-Request": "true"})
    assert fetch_category_kind(cid) == "income"
    assert resp.headers.get("HX-Retarget") == "#category-rows"
    assert resp.headers.get("HX-Reswap") == "innerHTML"


def test_a_rename_keeps_the_cheap_single_row_swap(client_a, users):
    """The retarget is scoped to a kind change on purpose — a rename must not
    pay for a whole-listing re-render."""
    cid = find_category_id(users["a"]["id"], "cat-A")
    resp = client_a.post(f"/categories/{cid}/edit",
                         data={"name": "Renamed", "description": "", "kind": "expense"},
                         headers={"HX-Request": "true"})
    assert "HX-Retarget" not in resp.headers
    assert resp.get_data(as_text=True).lstrip().startswith("<tr")


# --- #239: accounts as objects, not cells -------------------------------------


def test_an_account_renders_as_a_card_carrying_its_own_state(client_a, users):
    create_account(users["a"]["id"], "Overhaul Card", "Credit Card",
                   credit_limit=1000, apr=19.99)
    html = client_a.get("/accounts").get_data(as_text=True)
    assert "account-card" in html
    assert "Overhaul Card" in html
    # the limit bar and the interest line are on the card, not buried in a cell
    assert "credit-bar" in html
    assert "APR" in html


def test_the_edit_form_posts_the_card_not_a_table_row(client_a, users):
    """⚠️ hx-include had to move from `closest tr` when the row became a card;
    a stale selector posts an empty form and silently blanks the account."""
    resp = client_a.get(f"/accounts/{users['a']['account_id']}/edit")
    body = resp.get_data(as_text=True)
    assert 'hx-include="closest .account-card"' in body
    assert "closest tr" not in body


# --- #242: which way the money goes -------------------------------------------


def test_an_automatic_transfer_states_its_direction(client_a, users):
    dest = create_account(users["a"]["id"], "Direction Dest")
    create_transfer_schedule(users["a"]["id"], users["a"]["account_id"], dest,
                             75, "monthly", TODAY + timedelta(days=5))
    html = client_a.get("/transfers").get_data(as_text=True)
    assert "transfer-direction" in html
    assert "transfer-arrow" in html
    row = html[html.index("transfer-direction"):]
    assert row.index("Direction Dest") < row.index("</td>"), \
        "the destination is not in the direction cell"


def test_the_two_kinds_of_transfer_are_separate_sections(client_a, users):
    create_account(users["a"]["id"], "Second Account")  # /transfers needs two
    html = client_a.get("/transfers").get_data(as_text=True)
    assert html.count('class="transfer-section"') == 2
    assert "Transfer now" in html and "Transfer every month" in html


def test_moving_money_now_is_still_the_top_of_the_page(client_a, users):
    """⚠️ The deliberate exception to the shared shape: /transfers exists to
    move money now, so that form is the page's purpose and is NOT demoted."""
    create_account(users["a"]["id"], "Second Account")  # /transfers needs two
    html = client_a.get("/transfers").get_data(as_text=True)
    assert html.index("Transfer now") < html.index("Transfer every month")
    assert html.index('<form method="post">') < html.index("add-panel")


def test_the_direction_cell_stays_a_table_cell():
    """⚠️ Caught in a browser, invisible to markup assertions: a <td> given
    `display: flex` leaves table layout, its colspan stops applying, and every
    column to its right shifts one place left — headers no longer sit above
    their own values. The first cut of the direction cell did exactly this.
    """
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "style.css").read_text()
    rule = re.search(r"\n\.transfer-direction\b[^{]*\{([^}]*)\}", css)
    assert rule, "no .transfer-direction rule"
    assert not re.search(r"display:\s*(flex|grid|block|inline-flex)", rule.group(1)), \
        "the direction cell is given a non-table display, which breaks its colspan"
