"""Unit tests for compute_budget_vs_actual() (budgets blueprint).

As of v7.0 a budget is a single monthly amount per category (no period dates).
`actual` is the sum of that user's non-adjustment, non-transfer expense
transactions in the same category during the queried calendar month; with no
month given the helper defaults to the current month. Seed a budget + dated
transactions and assert the (category_id, category, budget, actual, remaining)
it returns.

⚠️ Read rows by ATTRIBUTE, never by position. #315 prepended `category_id`,
which silently reassigned every index — `row[1]` went from the budget to the
name. Attribute access is also what app code uses, per the namedtuple rule
in app/db.py.

These run against the dev Postgres via the `users` fixture (USER_A); all seeded
rows are cleaned up when that fixture tears the test users down.
"""
from datetime import datetime

import pytest

from app.blueprints.budgets import compute_budget_vs_actual
from tests.conftest import create_budget, create_category, create_transaction


def _row_for(rows, name):
    # By attribute, not r[0]: #315 put a category_id at the front, and a
    # positional lookup here would have started matching the id instead.
    return next((r for r in rows if r.category == name), None)


@pytest.fixture
def bva(users):
    """A budget of 100 with a mix of in-month, other-month, adjustment, and
    income transactions in June 2026 — only the two in-month expenses (50 total)
    should count toward `actual`."""
    a = users["a"]
    cid = create_category(a["id"], "BVA")
    create_budget(a["id"], cid, 100.0)
    acct = a["account_id"]
    create_transaction(a["id"], acct, 30.0, "2026-06-10", category_id=cid)
    create_transaction(a["id"], acct, 20.0, "2026-06-15", category_id=cid)
    create_transaction(a["id"], acct, 999.0, "2026-07-05", category_id=cid)            # other month
    create_transaction(a["id"], acct, 5.0, "2026-06-12", category_id=cid,
                       is_adjustment=True)                                              # excluded
    create_transaction(a["id"], acct, 200.0, "2026-06-12", category_id=cid,
                       transaction_type="income")                                       # excluded
    return {"a": a, "cid": cid}


def test_budget_amount_returned(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert row is not None
    assert float(row.budget) == 100.0


def test_actual_only_counts_in_month_non_adjustment_expenses(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    # 30 + 20 only; 999 (July), 5 (adjustment), 200 (income) all excluded.
    assert float(row.actual) == 50.0


def test_remaining_is_budget_minus_actual(bva):
    row = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert float(row.remaining) == 50.0


def test_over_budget_remaining_goes_negative(users):
    a = users["a"]
    cid = create_category(a["id"], "Tight")
    create_budget(a["id"], cid, 40.0)
    create_transaction(a["id"], a["account_id"], 50.0, "2026-06-10", category_id=cid)
    row = _row_for(compute_budget_vs_actual(a["id"], "2026", "06"), "Tight")
    assert float(row.actual) == 50.0
    assert float(row.remaining) == -10.0


def test_actual_scoped_to_queried_month(bva):
    # The July expense (999) only shows up when July is the queried month.
    july = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "07"), "BVA")
    assert float(july.actual) == 999.0
    june = _row_for(compute_budget_vs_actual(bva["a"]["id"], "2026", "06"), "BVA")
    assert float(june.actual) == 50.0


def test_no_month_defaults_to_current_month(users):
    # With no year/month, actual is this calendar month's spend.
    a = users["a"]
    cid = create_category(a["id"], "Now")
    create_budget(a["id"], cid, 500.0)
    today = datetime.today().strftime("%Y-%m-%d")
    create_transaction(a["id"], a["account_id"], 70.0, today, category_id=cid)
    row = _row_for(compute_budget_vs_actual(a["id"]), "Now")
    assert row is not None
    assert float(row.actual) == 70.0


def test_only_returns_own_budgets(bva, users):
    # USER_B has no budgets, so A's "BVA" must never appear in B's result.
    b_rows = compute_budget_vs_actual(users["b"]["id"], "2026", "06")
    assert _row_for(b_rows, "BVA") is None


# --- two categories sharing a name (#315) -----------------------------------

def test_two_categories_with_the_same_name_are_not_merged(users):
    """#315 — the helper grouped by `c.name`, so two same-named categories
    carrying the SAME budget amount collapsed into ONE row holding one budget
    and BOTH categories' spending, inventing an overrun out of two categories
    that were each comfortably under.

    ⚠️ The budgets must be EQUAL. Postgres groups by the whole key, so unequal
    amounts already produced two rows and merely looked odd — a test written
    with different budgets passes against the unfixed code and proves nothing.
    """
    a = users["a"]
    first = create_category(a["id"], "BVADup")
    second = create_category(a["id"], "BVADup")
    assert first != second
    create_budget(a["id"], first, 100.0)
    create_budget(a["id"], second, 100.0)
    acct = a["account_id"]
    create_transaction(a["id"], acct, 40.0, "2026-06-10", category_id=first)
    create_transaction(a["id"], acct, 70.0, "2026-06-11", category_id=second)

    rows = [r for r in compute_budget_vs_actual(a["id"], "2026", "06")
            if r.category == "BVADup"]

    assert len(rows) == 2, (
        "the two categories collapsed into one row — 200.00 budgeted and "
        "110.00 spent were reported as 100.00 against 110.00"
    )
    assert sorted(float(r.actual) for r in rows) == [40.0, 70.0]
    assert all(float(r.budget) == 100.0 for r in rows)
    # The whole point: neither category is over its own budget.
    assert all(float(r.remaining) > 0 for r in rows)
    # Each row names which category it is, so the rows are tellable apart.
    assert {r.category_id for r in rows} == {first, second}


def test_a_single_category_is_unaffected(bva):
    """The grouping change must not alter the ordinary one-category case."""
    rows = [r for r in compute_budget_vs_actual(bva["a"]["id"], "2026", "06")
            if r.category == "BVA"]
    assert len(rows) == 1
    assert float(rows[0].budget) == 100.0
    assert float(rows[0].actual) == 50.0
    assert float(rows[0].remaining) == 50.0
    assert rows[0].category_id == bva["cid"]
