"""Budget facts other areas read: what was spent against each budget this
month, and the log of every budget change (#489).

Home, the month read, the month projection, Ask and the categories page all
need these, and they used to import them from the budgets blueprint (the
categories page lazily, to dodge a cycle). The budgets cockpit itself, with its
suggestions, report and AI review, stays in blueprints/budgets.py.
"""
from datetime import datetime

from app.db import db_cursor


def record_budget_change(cursor, user_id, category_id, amount):
    """Append to budget_history (v10.9) — the only record of past budget
    amounts, since the budgets row upserts in place. amount None = cleared.
    Nothing reads the log yet; a future budget report will grade past months
    against the amount actually in effect.

    Call BEFORE the upsert/delete, inside the same transaction, so the log
    can't drift from the table. Skips no-ops (re-saving the current amount,
    clearing a budget that isn't set) so it stays a log of CHANGES.
    """
    cursor.execute("SELECT amount FROM budgets WHERE category_id = %s AND user_id = %s",
                   (category_id, user_id))
    row = cursor.fetchone()
    if amount is None:
        if row is None:
            return
    elif row is not None and float(row[0]) == float(amount):
        return
    cursor.execute("""
        INSERT INTO budget_history (category_id, amount, user_id)
        VALUES (%s, %s, %s)
    """, (category_id, amount, user_id))


def compute_budget_vs_actual(user_id, year=None, month=None):
    """Budget vs actual expense spending per category for one calendar month.

    Budgets are now a single monthly amount per category, so `budget` is that
    saved amount and `actual` is the sum of the user's non-adjustment,
    non-transfer expense transactions in that category during the given month.
    When `year`/`month` are omitted, the current calendar month is used (the
    monthly budget is constant, so an "all time" actual would be meaningless).
    Returns rows of (category_id, category, budget, actual, remaining).

    ⚠️ Grouped by `c.id`, NOT `c.name` (#315). `categories` has no uniqueness
    constraint on (user_id, name), so two categories can share one. Grouping by
    name collapsed two of them into a single row carrying ONE budget and BOTH
    categories' spending whenever their budget amounts were equal — reporting a
    user who was 90.00 under as 10.00 over. That figure is not merely rendered:
    `insights.compute_month_facts()` turns a negative `remaining` into an entry
    in the month read's `overruns`, which the prompt tells the model to treat as
    ground truth, so a fabricated overrun was narrated with confidence.
    Read consumers by ATTRIBUTE (`row.budget`), never by position — the id was
    added at the front, and `blueprints/budgets.py`'s two report queries already group this
    way (`GROUP BY t.category_id, c.name, …`).
    """
    if year and month:
        filter_year, filter_month = int(year), int(month)
    else:
        today = datetime.today()
        filter_year, filter_month = today.year, today.month
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT
                c.id AS category_id,
                c.name AS category,
                b.amount AS budget,
                COALESCE(SUM(t.amount), 0) AS actual,
                b.amount - COALESCE(SUM(t.amount), 0) AS remaining
            FROM budgets b
            JOIN categories c ON b.category_id = c.id
            LEFT JOIN transactions t ON t.category_id = b.category_id
                AND t.transaction_type = 'expense'
                AND t.is_adjustment = false
                AND t.is_transfer = false
                AND EXTRACT(YEAR FROM t.transaction_date) = %s
                AND EXTRACT(MONTH FROM t.transaction_date) = %s
                AND t.user_id = b.user_id
            WHERE b.user_id = %s
            GROUP BY c.id, c.name, b.amount
            ORDER BY c.name, c.id
        """, (filter_year, filter_month, user_id))
        rows = cursor.fetchall()
    return rows
