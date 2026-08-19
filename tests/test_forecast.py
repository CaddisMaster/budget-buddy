"""Tests for the month-ahead projection arithmetic (blueprints/forecasts.py).

Was "v10.2 Forecast" — a cached AI card with its own route. #232 removed the
card, the route and the narration; the ARITHMETIC survived and now feeds the one
month read (insights.build_read_facts) and the `month_projection` Ask tool, so
these tests cover more surface than they used to, not less.

No model seam is stubbed here because none is reached: nothing in
blueprints/forecasts.py calls Claude. The narration tests that used to live
below moved to test_month_read.py.
"""
import calendar
from datetime import date, timedelta

import pytest
from dateutil.relativedelta import relativedelta

from app.blueprints.forecasts import compute_forecast, project_expenses
from tests.conftest import (
    create_account,
    create_budget,
    create_category,
    create_schedule,
    create_transaction,
)


def _this_month():
    t = date.today()
    return t.year, t.month


# --- project_expenses (pure, no DB) -----------------------------------------

def test_project_expenses_flat_when_no_history():
    # day 10 of 30, spent 100, no history → flat run-rate 100/10*30 = 300.
    amount, method = project_expenses(100, 10, 30, {})
    assert amount == 300.0
    assert method == "flat"


def test_project_expenses_day_weighted_corrects_for_shape():
    # By day 12, history says ~40% of a month's spend has landed (400 of 1000),
    # so $340 so far projects to 340 / 0.4 = $850.
    history = {5: 400.0, 20: 600.0}
    amount, method = project_expenses(340, 12, 30, history)
    assert amount == 850.0
    assert method == "day_weighted"


def test_project_expenses_floor_falls_back_to_flat():
    # Day 1 with all historical spend landing late → cum fraction ~0 (< floor) →
    # flat, not a divide-by-zero blowup.
    history = {25: 1000.0}
    amount, method = project_expenses(50, 1, 30, history)
    assert method == "flat"
    assert amount == 50 / 1 * 30


def test_project_expenses_tiny_history_falls_back_to_flat():
    # Total history below MIN_HISTORY → don't trust the shape.
    amount, method = project_expenses(100, 10, 30, {5: 40.0})
    assert method == "flat"
    assert amount == 300.0


def test_project_expenses_complete_month_returns_actual():
    assert project_expenses(500, 30, 30, {5: 999.0}) == (500.0, "flat")


def test_project_expenses_before_month_starts_is_zero():
    assert project_expenses(0, 0, 31, {}) == (0.0, "flat")


# --- compute_forecast (deterministic, DB-backed) ----------------------------

def test_compute_forecast_only_sees_own_rows(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_a = create_account(a, "fc-iso-a")
    acct_b = create_account(b, "fc-iso-b")
    today = date.today().isoformat()
    create_transaction(a, acct_a, 50, today, "income")
    create_transaction(b, acct_b, 9999, today, "income")  # B's money
    fc = compute_forecast(a, *_this_month())
    assert fc["income_to_date"] == 50.0    # B's 9999 never leaks in
    assert fc["projected_income"] >= 50.0


def test_compute_forecast_empty_month_is_zeroed(users):
    a = users["a"]["id"]
    fc = compute_forecast(a, 2000, 1)
    assert fc["income_to_date"] == 0 and fc["expenses_to_date"] == 0
    assert fc["projected_expenses"] == 0 and fc["projected_income"] == 0
    assert fc["remaining_items"] == []
    assert fc["method"] == "flat"


def test_compute_forecast_includes_remaining_scheduled_income(users):
    a = users["a"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — no remaining-this-month window")
    acct = create_account(a, "fc-sched")
    due = date(today.year, today.month, days_in_month)  # later this month
    create_schedule(a, acct, 2000, "monthly", due, transaction_type="income")
    fc = compute_forecast(a, today.year, today.month)
    assert fc["remaining_scheduled_income"] == 2000.0
    assert any(i["due"] == due.isoformat() for i in fc["remaining_items"])
    assert fc["projected_income"] >= 2000.0


def test_compute_forecast_excludes_other_users_schedules(users):
    a, b = users["a"]["id"], users["b"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — no remaining-this-month window")
    acct_b = create_account(b, "fc-sched-b")
    due = date(today.year, today.month, days_in_month)
    create_schedule(b, acct_b, 5000, "monthly", due, transaction_type="income")
    fc = compute_forecast(a, today.year, today.month)
    assert fc["remaining_scheduled_income"] == 0.0   # B's schedule never leaks


def test_compute_forecast_includes_remaining_scheduled_expense(users):
    a = users["a"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — no remaining-this-month window")
    acct = create_account(a, "fc-sched-exp")
    due = date(today.year, today.month, days_in_month)
    create_schedule(a, acct, 1500, "monthly", due, transaction_type="expense")
    fc = compute_forecast(a, today.year, today.month)
    assert fc["remaining_scheduled_expense"] == 1500.0
    assert any(i["due"] == due.isoformat() and i["type"] == "expense"
               for i in fc["remaining_items"])
    # Remaining scheduled expense is surfaced as CONTEXT only — it is NOT added on
    # top of the day-weighted projection (the curve already encodes recurring-bill
    # timing). The projection reflects only month-to-date spend (the small fixture
    # seed), so the $1500 bill never inflates it.
    assert fc["projected_expenses"] < 1500.0


def test_compute_forecast_day_weighted_path(users):
    a = users["a"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — complete month returns the actual (flat)")
    acct = create_account(a, "fc-dw")
    month_first = date(today.year, today.month, 1)
    # 6 months of history, all dated the 1st → cumulative fraction by any day ~1.0,
    # total $600 (well over the MIN_HISTORY floor) → the day-weighted path engages.
    for i in range(1, 7):
        d = month_first - relativedelta(months=i)
        create_transaction(a, acct, 100, d.isoformat(), "expense")
    create_transaction(a, acct, 80, today.isoformat(), "expense")  # some MTD spend
    fc = compute_forecast(a, today.year, today.month)
    assert fc["method"] == "day_weighted"


def test_compute_forecast_over_budget_excludes_unbudgeted(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "fc-bud")
    budgeted = create_category(a, "fc-budgeted")
    unbudgeted = create_category(a, "fc-unbudgeted")
    create_budget(a, budgeted, 5000)
    create_transaction(a, acct, 100, today.isoformat(), "expense", category_id=budgeted)
    create_transaction(a, acct, 50000, today.isoformat(), "expense", category_id=unbudgeted)
    fc = compute_forecast(a, today.year, today.month)
    assert fc["projected_over_budget"] is not None
    # The unbudgeted $50k must NOT count against the $5k budget (the v10.2.1 fix).
    # projected budgeted spend is at most 100×days_in_month < 5000 → always under.
    assert fc["projected_over_budget"] < 0
    factor = fc["projected_expenses"] / fc["expenses_to_date"]
    assert fc["projected_over_budget"] == pytest.approx(100 * factor - 5000, abs=1.0)


def test_compute_forecast_past_month_uses_actual(users):
    a = users["a"]["id"]
    acct = create_account(a, "fc-past")
    create_transaction(a, acct, 200, "2020-03-10", "expense")
    create_transaction(a, acct, 500, "2020-03-05", "income")
    fc = compute_forecast(a, 2020, 3)
    assert fc["day_of_month"] == 31            # fully elapsed
    assert fc["expenses_to_date"] == 200
    assert fc["projected_expenses"] == 200     # complete month → the actual
    assert fc["projected_income"] == 500
    assert fc["method"] == "flat"


def test_compute_forecast_future_month_is_zeroed(users):
    a = users["a"]["id"]
    fc = compute_forecast(a, date.today().year + 1, 1)
    assert fc["day_of_month"] == 0             # month hasn't started
    assert fc["projected_expenses"] == 0 and fc["projected_income"] == 0
    assert fc["remaining_items"] == []


def test_forecast_materialized_schedule_not_double_counted(users):
    a = users["a"]["id"]
    today = date.today()
    acct = create_account(a, "fc-dc")
    create_schedule(a, acct, 2222, "monthly", today, transaction_type="income")
    from app.blueprints.schedules import run_due_schedules
    run_due_schedules(a)   # materializes today's occurrence + advances next_due
    fc = compute_forecast(a, today.year, today.month)
    assert fc["income_to_date"] >= 2222        # now a real transaction
    # ...and it must NOT also be projected as a remaining item (no double-count).
    assert fc["remaining_scheduled_income"] == 0.0
    assert all(i["type"] != "income" or i["amount"] != 2222.0
               for i in fc["remaining_items"])


# --- schedules: the end-date gate ------------------------------------------

def test_compute_forecast_excludes_a_schedule_past_its_end_date(users):
    # #32 — projecting past a schedule's end date forecasts bills that can never
    # be charged, which is exactly the poisoned-forecast damage the issue names.
    a = users["a"]["id"]
    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day >= days_in_month:
        pytest.skip("last day of month — no remaining-this-month window")
    acct = create_account(a, "fc-ended")
    due = date(today.year, today.month, days_in_month)
    create_schedule(a, acct, 3000, "monthly", due, transaction_type="income",
                    end_date=today - timedelta(days=1))
    fc = compute_forecast(a, today.year, today.month)
    assert fc["remaining_scheduled_income"] == 0.0
    assert not any(i["due"] == due.isoformat() for i in fc["remaining_items"])
