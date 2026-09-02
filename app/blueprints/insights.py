"""#232 — the month read: the ONE narrated line in Home's "Ask your finances".

Supersedes the v10.1 "Insight" card. Home used to stack four AI surfaces — the
monthly insight, the month-ahead forecast, the weekly money check and the Ask
box — each with its own card, cache and Generate button. They are one panel now:
a single read of the month, then the box.

The split of responsibility is unchanged and is the whole point:

  * build_read_facts() produces every NUMBER, deterministically, by merging the
    two existing builders — compute_month_facts() (this month, last month,
    overruns, cards) and forecasts.compute_forecast() (what is still to land).
    This is the only source of figures.
  * app.ai.generate_month_read() turns those facts into ONE short paragraph — it
    never does arithmetic, so nothing it returns is trusted as a figure.

Cached one-row-per-(user, month) in `insights`, as the insight card was, so the
dashboard renders it instantly and a page load never calls the model; the panel
asks for a missing read itself (hx-trigger="load"), and Refresh overwrites via
the unique upsert. Gated on ai_enabled() and degrades gracefully (an error toast
and a still-usable Ask box, never a broken page).

⚠️ compute_month_facts() and category_spending() are ALSO the Ask tools'
`month_summary` / `spending_by_category` feeders — the read and the box are
wired to the same numbers on purpose, so they can never disagree.
"""
import json
from datetime import datetime

from flask import Blueprint, make_response, render_template
from flask_login import current_user, login_required

from app import limiter
from app.ai import MODEL, ParseError, generate_month_read
from app.blueprints.accounts import credit_card_utilization_facts
from app.blueprints.budgets import compute_budget_vs_actual
from app.blueprints.forecasts import compute_forecast
from app.db import db_cursor
from app.helpers import hx_toast

bp = Blueprint('insights', __name__)


def _prev_month(year, month):
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
    return [(r[0], float(r[1])) for r in cursor.fetchall()]


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
    income, expenses = cursor.fetchone()
    return float(income), float(expenses)


def compute_month_facts(user_id, year, month):
    """Deterministic figure builder for one month — the ONLY source of numbers
    the digest describes. Reads the user's own rows only. Returns:

        {year, month, income, expenses, net, savings_rate,
         prev: {income, expenses, net},
         top_categories: [{name, amount}, ...],   # this month's top expenses
         overruns: [{category, budget, actual, over}, ...],  # actual > budget
         credit_cards: [{name, limit?, debt, available?, utilization_pct?,
                         apr?, est_monthly_interest?}, ...]}
           # v10.10 — CURRENT card snapshot (always present, [] when none).
           # Per-key presence: limit keys when a limit is set; apr +
           # est_monthly_interest (v10.15) when an apr is set and the card
           # carries debt
    """
    year, month = int(year), int(month)
    with db_cursor() as cursor:
        income, expenses = _income_expense(cursor, user_id, year, month)
        py, pm = _prev_month(year, month)
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
    data = json.loads(row[0])
    data['created_at'] = row[1]
    return data


@bp.route('/insights/read', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def read():
    """Generate (or Refresh) this month's read and swap the whole panel.

    Always the CURRENT month — the panel is Home's "how is this month going",
    and taking a month from the form would be a tamperable parameter with no
    caller. The dashboard renders a cached read directly; this endpoint is hit
    two ways, both HTMX: the panel's own hx-trigger="load" when nothing is
    cached yet, and the Refresh button.

    ⚠️ It returns the ENTIRE panel, Ask box included — the read and the box are
    one feature, and a fragment carrying only the paragraph would swap the input
    out of the page.
    """
    today = datetime.today()
    year, month = today.year, today.month

    def _panel(read_row, just_generated=False):
        return make_response(render_template(
            'partials/_ask_panel.html',
            read=read_row, ai_enabled=True, read_pending=False,
            just_generated=just_generated))

    facts = build_read_facts(current_user.id, year, month)
    if not month_worth_reading(facts['income'], facts['expenses'],
                               facts['projection']['remaining_items']):
        return hx_toast(_panel(None), 'Not enough data this month yet', 'error')

    try:
        result = generate_month_read(facts)
    except ParseError:
        return hx_toast(_panel(None), "Couldn't read this month right now", 'error')

    with db_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO insights (user_id, year, month, content, model)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, year, month)
            DO UPDATE SET content = EXCLUDED.content, model = EXCLUDED.model,
                          created_at = now()
            RETURNING created_at
        """, (current_user.id, year, month, json.dumps(result), MODEL))
        created_at = cursor.fetchone().created_at

    read_row = dict(result)
    # The DB timestamp, not datetime.today() — the panel prints it, and a
    # route-local clock would disagree with what the next page load reads back.
    read_row['created_at'] = created_at
    return _panel(read_row, just_generated=True)
