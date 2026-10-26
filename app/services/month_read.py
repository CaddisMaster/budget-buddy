"""The month's facts: every figure behind Home's month read, the Ask box's
month tools and the weekly digest (#232; moved here from the insights
blueprint in #489).

compute_month_facts() is the deterministic source of figures, and
build_read_facts() merges in forecasts.compute_forecast() under `projection`.
The model narrates these; it never computes one. ⚠️ compute_month_facts() and
category_spending() also feed Ask's `month_summary` / `spending_by_category`
tools on purpose, so the read and the box can never disagree.

month_worth_reading() is the gate shared by Home and POST /insights/read, so
the panel never asks for a read the route would refuse (docs/gotchas.md,
"Home's AI panel re-renders WHOLE, and its gate is shared"). load_read() reads
the cached read from the `insights` table.
"""
import json

from app.db import db_cursor
from app.services.accounts import credit_card_utilization_facts
from app.services.budgets import compute_budget_vs_actual
from app.services.forecasts import compute_forecast


def prev_month(year, month):
    """The calendar month before (year, month)."""
    if month == 1:
        return year - 1, 12
    return year, month - 1


def category_spending(cursor, user_id, year, month, limit=None):
    """Expense spending grouped by category for one month (non-adjustment,
    non-transfer), highest first → [(name, total_float), ...]. The single source
    for the per-category rollup, reused by the dashboard digest and the Ask tool
    so they can never disagree on what counts.

    ⚠️ Grouped by NAME on purpose, unlike compute_budget_vs_actual (#315).
    This is a "what did I spend on X" rollup, so two categories that share a
    label should be summed under it — that is the answer the reader wants, not
    the bug. The budget helper is different in kind: there, merging compares one
    category's budget against two categories' spending, which invents a figure.
    """
    sql = """
        SELECT c.name, SUM(t.amount) AS total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = %s AND t.transaction_type = 'expense'
        AND t.is_adjustment = false AND t.is_transfer = false
        AND EXTRACT(YEAR FROM t.transaction_date) = %s
        AND EXTRACT(MONTH FROM t.transaction_date) = %s
        GROUP BY c.name
        ORDER BY total DESC
    """
    params = [user_id, year, month]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    cursor.execute(sql, params)
    return [(r.name, float(r.total)) for r in cursor.fetchall()]


def _income_expense(cursor, user_id, year, month):
    """(income, expenses) totals for one month — non-adjustment, non-transfer,
    matching how the dashboard and analytics count real cash flow."""
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) AS expenses
        FROM transactions
        WHERE user_id = %s AND is_adjustment = false AND is_transfer = false
        AND EXTRACT(YEAR FROM transaction_date) = %s
        AND EXTRACT(MONTH FROM transaction_date) = %s
    """, (user_id, year, month))
    row = cursor.fetchone()
    return float(row.income), float(row.expenses)


def compute_month_facts(user_id, year, month):
    """Deterministic figure builder for one month — the ONLY source of numbers
    the digest describes. Reads the user's own rows only. Returns:

        {year, month, income, expenses, net, savings_rate,
         prev: {income, expenses, net},
         top_categories: [{name, amount}, ...],   # this month's top expenses
         overruns: [{category, budget, actual, over}, ...],  # actual > budget
         credit_cards: [{name, limit?, debt, available?, utilization_pct?,
                         apr?, est_monthly_interest?}, ...]}
           # CURRENT card snapshot (always present, [] when none).
           # Per-key presence: limit keys when a limit is set; apr +
           # est_monthly_interest when an apr is set and the card
           # carries debt
    """
    year, month = int(year), int(month)
    with db_cursor() as cursor:
        income, expenses = _income_expense(cursor, user_id, year, month)
        py, pm = prev_month(year, month)
        p_income, p_expenses = _income_expense(cursor, user_id, py, pm)

        top_categories = [{'name': n, 'amount': a} for n, a in
                          category_spending(cursor, user_id, year, month, limit=5)]

    # Budget overruns reuse the shared month-based helper (its own connection).
    # ⚠️ Read by attribute, not by unpacking (#315): the helper now returns a
    # category_id as well, and a positional unpack here is what would break
    # first — loudly, but only after the shape had already been changed.
    # The dicts deliberately keep their four keys and stay name-labelled: the
    # arithmetic was what was wrong, not the label, and these go into the
    # model's prompt, so widening them changes what the read is told for no gain.
    overruns = []
    for row in compute_budget_vs_actual(user_id, year, month):
        if float(row.remaining) < 0:
            overruns.append({
                'category': row.category,
                'budget': float(row.budget),
                'actual': float(row.actual),
                'over': round(float(row.actual) - float(row.budget), 2),
            })

    net = round(income - expenses, 2)
    savings_rate = round((income - expenses) / income * 100, 1) if income > 0 else None
    return {
        'year': year,
        'month': month,
        'income': round(income, 2),
        'expenses': round(expenses, 2),
        'net': net,
        'savings_rate': savings_rate,
        'prev': {
            'income': round(p_income, 2),
            'expenses': round(p_expenses, 2),
            'net': round(p_income - p_expenses, 2),
        },
        'top_categories': top_categories,
        'overruns': overruns,
        'credit_cards': credit_card_utilization_facts(user_id),
    }



def build_read_facts(user_id, year, month):
    """Every number the month read describes, from BOTH deterministic builders.

    The month's own figures sit at the top level (compute_month_facts) and the
    rest of the month hangs under `projection` (forecasts.compute_forecast), so
    one model call sees what the Insight card and the Forecast card saw between
    them. ⚠️ Keep the nesting — the prompt names `projection` and instructs the
    model to frame anything inside it as a projection rather than a fact.
    """
    facts = compute_month_facts(user_id, year, month)
    projection = compute_forecast(user_id, year, month)
    # Drop the keys the month half already carries, so the model is never handed
    # two spellings of one figure to choose between.
    facts['projection'] = {k: v for k, v in projection.items()
                           if k not in ('year', 'month')}
    return facts


def month_worth_reading(income, expenses, remaining_items):
    """True when the month is worth narrating: any activity, or anything still
    scheduled to land. An empty month gets the Ask box with no read rather than
    a paid call that can only say "nothing has happened yet".

    ⚠️ Pure, and shared by BOTH callers on purpose — the dashboard decides
    whether the panel should ask for a read, and this route decides whether to
    answer. Two separately-written gates would eventually disagree, and the
    failure is a panel that asks on every single load and is refused every time.
    The dashboard passes month-TO-DATE totals (it has the forecast facts in
    hand); the route passes the whole month's, which is a superset — so the
    route can never refuse a read the dashboard just asked for.
    """
    return bool(income or expenses or remaining_items)


def load_read(cursor, user_id, year, month):
    """Return the cached read for (user, month) as {summary, created_at}, or
    None. Takes an existing cursor so the dashboard reuses its connection.

    ⚠️ Rows written before #232 also carry a `tips` list. It is ignored rather
    than migrated — the panel renders `summary` only, and the next Refresh
    overwrites the row.
    """
    cursor.execute("""
        SELECT content, created_at FROM insights
        WHERE user_id = %s AND year = %s AND month = %s
    """, (user_id, int(year), int(month)))
    row = cursor.fetchone()
    if row is None:
        return None
    data = json.loads(row.content)
    data['created_at'] = row.created_at
    return data
