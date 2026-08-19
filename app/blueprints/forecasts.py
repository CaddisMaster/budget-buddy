"""The month-ahead projection: every NUMBER behind "where is this month going".

Was the v10.2 "Forecast" card — a cached AI narration of its own, sitting under
the Insight card on Home. #232 removed the card, the `/forecasts/generate` route
and the narration; the deterministic arithmetic here SURVIVED it and now feeds
two callers instead of one:

  * insights.build_read_facts() — the projection half of the one month read
  * ask.py's `month_projection` tool — the same figures, on demand

compute_forecast() produces month-to-date actuals, a day-weighted spend
projection and the remaining scheduled income/bills. It is pure of models and
always has been: nothing here calls Claude, and no figure it returns was ever
written by one.

⚠️ This module no longer defines a blueprint — it has no routes. The `forecasts`
TABLE still exists and is now unread; dropping it is a migration, and a migration
stands alone in its own PR.
"""
import calendar
from datetime import date

from app.blueprints.budgets import compute_budget_vs_actual
from app.blueprints.transactions import compute_next_due
from app.db import db_cursor

# Day-weighted projection guards. Below this much trailing spend, or this small a
# cumulative fraction (≈ the first day or two of a month), the curve is too noisy
# — fall back to a flat run-rate instead.
MIN_HISTORY = 50.0
MIN_FRACTION = 0.05
# History window for the spending shape: the 6 calendar months before this one.
HISTORY_MONTHS = 6


def project_expenses(spend_to_date, day_of_month, days_in_month, history_by_day):
    """Project full-month expenses from month-to-date spend. Pure (no DB) so the
    day-weighting math is directly unit-testable. Returns (amount, method).

    Day-weighted: the user's own history says what fraction of a typical month's
    spend has usually landed by `day_of_month`; scaling today's spend by that
    fraction corrects for bills that land early (rent on the 1st) or late. Falls
    back to a flat run-rate when there isn't enough history to trust the shape.
    `history_by_day` is {day-of-month: total expense on that day} over the window.
    """
    spend_to_date = float(spend_to_date)
    if day_of_month <= 0:
        return 0.0, 'flat'                       # month hasn't started
    if day_of_month >= days_in_month:
        return round(spend_to_date, 2), 'flat'   # month already complete

    flat = spend_to_date / day_of_month * days_in_month

    total_hist = sum(history_by_day.values())
    cum = sum(v for d, v in history_by_day.items() if d <= day_of_month)
    cum_fraction = (cum / total_hist) if total_hist > 0 else 0.0

    if total_hist >= MIN_HISTORY and cum_fraction >= MIN_FRACTION:
        projected = spend_to_date / cum_fraction
        method = 'day_weighted'
    else:
        projected = flat
        method = 'flat'

    projected = max(projected, spend_to_date)    # can't end having spent less
    return round(projected, 2), method


def _to_date_totals(cursor, user_id, year, month, cutoff):
    """(income, expenses) for (year, month) up to and including `cutoff` —
    non-adjustment, non-transfer, matching how Insight/the dashboard count real
    cash flow."""
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) AS expenses
        FROM transactions
        WHERE user_id = %s AND is_adjustment = false AND is_transfer = false
        AND EXTRACT(YEAR FROM transaction_date) = %s
        AND EXTRACT(MONTH FROM transaction_date) = %s
        AND transaction_date <= %s
    """, (user_id, year, month, cutoff))
    income, expenses = cursor.fetchone()
    return float(income), float(expenses)


def _history_by_day(cursor, user_id, month_first):
    """Expense totals keyed by day-of-month over the HISTORY_MONTHS calendar
    months *before* `month_first` — the spending-shape curve for the projection.
    Non-adjustment, non-transfer; the user's own rows only."""
    from dateutil.relativedelta import relativedelta
    window_start = month_first - relativedelta(months=HISTORY_MONTHS)
    cursor.execute("""
        SELECT EXTRACT(DAY FROM transaction_date)::int AS dom, SUM(amount)
        FROM transactions
        WHERE user_id = %s AND transaction_type = 'expense'
        AND is_adjustment = false AND is_transfer = false
        AND transaction_date >= %s AND transaction_date < %s
        GROUP BY dom
    """, (user_id, window_start, month_first))
    return {int(r[0]): float(r[1]) for r in cursor.fetchall()}


def _remaining_scheduled(cursor, user_id, today, month_last):
    """Active-schedule occurrences falling strictly after `today` and on or
    before `month_last` — the bills/paychecks still to land this month. Mirrors
    run_due_schedules' compute_next_due loop but materializes nothing (projection
    only) and is bounded by month-end."""
    cursor.execute("""
        SELECT description, amount, transaction_type, frequency,
               anchor_day, second_day, next_due, end_date
        FROM schedules
        WHERE is_active = true AND user_id = %s
          AND (end_date IS NULL OR next_due <= end_date)
    """, (user_id,))
    items = []
    for (desc, amount, ttype, freq, anchor_day, second_day,
         next_due, end_date) in cursor.fetchall():
        # #32: a schedule that finishes mid-month contributes only the
        # occurrences up to its end date — projecting past it would forecast
        # bills that can never be charged.
        stop = min(month_last, end_date) if end_date is not None else month_last
        occ = next_due
        # Advance to the first occurrence strictly after today (the dashboard's
        # run_due_schedules normally already did this; be robust if it didn't).
        # Each loop gets its OWN guard budget so a long catch-up in the first
        # can't starve the second into under-counting this month's occurrences.
        guard = 0
        while occ <= today and guard < 500:
            occ = compute_next_due(occ, freq, anchor_day=anchor_day, second_day=second_day)
            guard += 1
        guard = 0
        while occ <= stop and guard < 500:
            items.append({'description': desc, 'amount': float(amount),
                          'type': ttype, 'due': occ.isoformat()})
            occ = compute_next_due(occ, freq, anchor_day=anchor_day, second_day=second_day)
            guard += 1
    items.sort(key=lambda i: i['due'])
    return items


def compute_forecast(user_id, year, month):
    """Deterministic figure builder for the month-ahead projection — the ONLY
    source of numbers the forecast describes. Reads the user's own rows only.

    Returns {year, month, day_of_month, days_in_month,
             income_to_date, expenses_to_date, net_to_date,
             remaining_scheduled_income, remaining_scheduled_expense,
             remaining_items: [{description, amount, type, due}, ...],
             projected_income, projected_expenses, projected_net, method,
             total_budget, projected_over_budget}
    """
    year, month = int(year), int(month)
    today = date.today()
    days_in_month = calendar.monthrange(year, month)[1]
    month_first = date(year, month, 1)
    month_last = date(year, month, days_in_month)

    if today < month_first:
        day_of_month = 0                    # month hasn't started yet
    elif today >= month_last:
        day_of_month = days_in_month        # month fully elapsed
    else:
        day_of_month = today.day
    cutoff = min(today, month_last)

    with db_cursor() as cursor:
        income_td, expenses_td = _to_date_totals(cursor, user_id, year, month, cutoff)
        history = _history_by_day(cursor, user_id, month_first)
        remaining_items = _remaining_scheduled(cursor, user_id, today, month_last)

    rem_income = round(sum(i['amount'] for i in remaining_items if i['type'] == 'income'), 2)
    rem_expense = round(sum(i['amount'] for i in remaining_items if i['type'] == 'expense'), 2)

    projected_expenses, method = project_expenses(
        expenses_td, day_of_month, days_in_month, history)
    # Income arrives in lumps (paychecks), not at a daily pace, so project it from
    # what's already in plus the known upcoming scheduled income — not a run-rate.
    projected_income = round(income_td + rem_income, 2)
    projected_net = round(projected_income - projected_expenses, 2)

    # Budget context: project only the spend *within budgeted categories* (scaled
    # by the same run-rate factor that produced projected_expenses) and compare
    # that to the budget total. Comparing all-category projected spend to a
    # partial budget (only some categories have one) would read as an overrun
    # almost every time — a misleading figure the narration would then repeat.
    budget_rows = compute_budget_vs_actual(user_id, year, month)
    total_budget = round(sum(float(b) for _, b, _, _ in budget_rows), 2)
    budgeted_actual = sum(float(a) for _, _, a, _ in budget_rows)
    factor = (projected_expenses / expenses_td) if expenses_td > 0 else 0.0
    projected_budgeted_spend = round(budgeted_actual * factor, 2)
    projected_over_budget = (round(projected_budgeted_spend - total_budget, 2)
                             if total_budget > 0 else None)

    return {
        'year': year,
        'month': month,
        'day_of_month': day_of_month,
        'days_in_month': days_in_month,
        'income_to_date': round(income_td, 2),
        'expenses_to_date': round(expenses_td, 2),
        'net_to_date': round(income_td - expenses_td, 2),
        'remaining_scheduled_income': rem_income,
        'remaining_scheduled_expense': rem_expense,
        'remaining_items': remaining_items,
        'projected_income': projected_income,
        'projected_expenses': projected_expenses,
        'projected_net': projected_net,
        'method': method,
        'total_budget': total_budget,
        'projected_over_budget': projected_over_budget,
    }
