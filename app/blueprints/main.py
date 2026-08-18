import calendar
from datetime import datetime

import psycopg2
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import limiter
from app.blueprints.agent import load_agent_run
from app.blueprints.budgets import compute_budget_vs_actual
from app.blueprints.forecasts import compute_forecast, load_forecast
from app.blueprints.goals import build_goals_view
from app.blueprints.insights import _prev_month, compute_month_facts, load_insight
from app.blueprints.transactions import compute_next_due
from app.db import db_cursor
from app.helpers import ai_enabled, parse_month_param, recent_months

bp = Blueprint('main', __name__)


# Pure occurrence walkers over compute_next_due — used by the weekly digest's
# upcoming-week enumerator (digests.py imports them lazily to avoid a cycle).
def _advance_past(occ, frequency, anchor_day, second_day, day, guard=500):
    """First occurrence strictly after `day`, walking from `occ` with
    compute_next_due. Pure. The guard caps runaway walks on bad data."""
    steps = 0
    while occ <= day and steps < guard:
        occ = compute_next_due(occ, frequency, anchor_day=anchor_day,
                               second_day=second_day)
        steps += 1
    return occ


def upcoming_occurrences(next_due, frequency, anchor_day, second_day,
                         window_start, window_end, guard=500, end_date=None):
    """All occurrence dates with window_start < occ <= window_end. Pure.

    Start is exclusive (anything due today was already materialized by the
    due-runners); end is INCLUSIVE — a bill due on payday still has to be paid
    out of this check. Each phase gets its own guard budget so a long catch-up
    can't starve the collection loop (the forecasts._remaining_scheduled
    precedent).

    `end_date` is the schedule's optional last day (#32): nothing on or after it
    is enumerated, so a schedule finishing mid-window is truncated rather than
    advertised forever. It defaults to None — no end date — which is every
    schedule created before #32 and every caller that doesn't pass one."""
    occ = _advance_past(next_due, frequency, anchor_day, second_day,
                        window_start, guard)
    if end_date is not None and end_date < window_end:
        window_end = end_date
    out = []
    steps = 0
    while occ <= window_end and steps < guard:
        out.append(occ)
        occ = compute_next_due(occ, frequency, anchor_day=anchor_day,
                               second_day=second_day)
        steps += 1
    return out


# Doughnut tail fold (#108). A doughnut is a part-to-whole-at-a-glance form and
# is past its readable limit around six slices: #83 validated the palette with a
# CVD/contrast checker and only ~4 hues clear all-pairs separation, so the
# seven-slice production chart had pairs below the "hard to tell apart even with
# full colour vision" floor. Showing fewer segments is the fix; a ninth hue is
# not (see the --series-N comment in style.css).
#
# Presentation only, and deliberately NOT pushed into the SQL rollup — every
# other surface (budgets, insights, forecasts, the hero figures) needs the
# complete per-category figures. The card's own total is derived from cash_flow,
# not from this list, so folding cannot move it.
#
# ⚠️ The fold does NOT by itself fix the palette wrap, contrary to what #108
# assumed — but it is what makes the real fix possible. Capping the chart at 6
# real slices against 8 hues guarantees a free hue exists, which is the
# precondition assign_series_slots() below relies on (#111). Before the fold,
# probing for a free slot could simply fail.
def fold_chart_tail(rows, limit=6, label='Other'):
    """Top `limit` rows by total; the remainder summed into one entry. Pure.

    Takes and returns the chart payload's {'category', 'total'} dicts. The
    folded entry carries is_other=True — the template colours it neutral off
    that FLAG, never off the label, because a user may legitimately have a
    category actually named "Other" and it must keep its own hue.

    `limit` or fewer rows are returned unchanged, with no folded entry at all —
    the common case. Sorts defensively: every caller's query already ends
    ORDER BY total DESC, but a pure function shouldn't depend on that."""
    if len(rows) <= limit:
        return rows
    ranked = sorted(rows, key=lambda r: r['total'], reverse=True)
    tail = ranked[limit:]
    return ranked[:limit] + [{
        'category': label,
        'total': sum(r['total'] for r in tail),
        'is_other': True,
        'folded': len(tail),
    }]


# Palette-slot assignment (#111). Creation order alone WRAPPED: slot was
# `creation_index % 8`, so a user's 1st and 9th categories painted the same hex,
# and if both were drawn the chart showed two identical slices — the exact bug
# #83 set out to kill, resurfacing at a different input size. Seen in production
# on 0.3.0 with "Monthly Bills" and "Food & Dining".
#
# ⚠️ The rule this REPLACES — "colour must be a pure function of the category,
# never of the set drawn" (#83) — is mathematically incompatible with "no two
# drawn slices share a colour". A fixed per-category assignment cannot keep an
# arbitrary 6-subset distinct unless there are as many hues as categories, and
# #83's own validation caps the palette at 8. One of the two had to give.
#
# What makes giving it up safe NOW is the #108 fold: at most 6 real slices are
# drawn from 8 hues, so a free hue ALWAYS exists and probing always succeeds.
# #83 rejected probing when 10+ slices could be drawn and it would have failed
# anyway. The fold changed that arithmetic.
#
# Creation order is still the PREFERENCE, so a category keeps its familiar hue
# and only a genuinely colliding one moves.
def assign_series_slots(rows, category_order, palette_size=8):
    """Give every drawn row a DISTINCT palette slot. Pure.

    Preference is creation order (`category_order`), earliest-created wins a
    contested slot, and anything displaced takes the lowest free one. Rows
    flagged is_other are left alone — the folded slice paints from its own
    achromatic token, not from the palette.

    Only the drawn set is considered, so the expense and income views are
    assigned INDEPENDENTLY: they share one canvas but are never on screen
    together, and their union can exceed the palette."""
    real = [r for r in rows if not r.get('is_other')]
    ordered = sorted(real, key=lambda r: category_order.get(r['category'], 0))
    used, chosen = set(), {}
    for r in ordered:                       # pass 1 — preferred slot, if free
        pref = category_order.get(r['category'], 0) % palette_size
        if pref not in used:
            used.add(pref)
            chosen[r['category']] = pref
    for r in ordered:                       # pass 2 — displaced take lowest free
        name = r['category']
        if name in chosen:
            continue
        free = next((c for c in range(palette_size) if c not in used), None)
        if free is None:                    # >palette_size drawn: unreachable
            chosen[name] = category_order.get(name, 0) % palette_size
        else:
            used.add(free)
            chosen[name] = free
    return [r if r.get('is_other') else {**r, 'slot': chosen[r['category']]}
            for r in rows]


# #223 — the category doughnut became ranked bars, rendered server-side so a
# Jinja mistake is caught by a content assertion rather than showing as an empty
# string. Pure, like fold_chart_tail() and assign_series_slots() above, and it
# runs AFTER both: it consumes their output and adds only a width.
#
# The width is relative to the LARGEST row, not to the total. Bars sized by
# share of total leave every bar short as soon as spending is spread across
# categories, which is precisely when the comparison matters; scaling to the
# biggest row means the ranking is always legible. The figure beside each bar
# carries the actual amount, so nothing is inferred from the length alone.
def to_bar_rows(rows):
    """Add a `pct` (1-100) to each chart row, scaled to the largest. Pure."""
    biggest = max((float(r['total']) for r in rows), default=0.0)
    if biggest <= 0:
        return [{**r, 'pct': 0} for r in rows]
    # Floor at 1% so a tiny-but-real category still draws something a reader can
    # see next to its name, rather than an empty track that reads as a bug.
    return [{**r, 'pct': max(1, round(float(r['total']) / biggest * 100))}
            for r in rows]


# ── #223/#225 Home composition helpers ───────────────────────────────────────
# All pure, all here beside fold_chart_tail()/assign_series_slots()/to_bar_rows()
# for the same reason: the interesting cases are arithmetic, and arithmetic that
# needs a database and a browser to test does not get tested.


def days_left_in_month(today):
    """Whole days remaining after today, 0 on the last day. Pure."""
    return calendar.monthrange(today.year, today.month)[1] - today.day


def net_change(before, after):
    """Percent change between two nets, or None when it means nothing. Pure.

    ⚠️ Takes two FIGURES, not a series. It first read the last two months off
    the cash-flow payload, which is filtered by the month picker — so on the
    all-time view the hero showed "ALL TIME +$17,216" with a chip beside it
    reading "-122.5% vs. last month", two different scopes in one sentence.
    The caller now decides which two months are being compared.

    None when the earlier figure is zero: a percentage against zero is not a
    large number, it is undefined, and rendering it as one is how a quiet month
    starts claiming a 4000% improvement.
    """
    before, after = float(before), float(after)
    if before == 0:
        return None
    # abs() on the denominator: a net that goes -400 -> -200 has IMPROVED, and
    # dividing by a negative would report that as -50%.
    return round((after - before) / abs(before) * 100, 1)


def sparkline(series, key='balance', width=420, height=64, pad=4):
    """An SVG polyline for a series of {month, <key>} rows. Pure.

    Returns {'line': 'x,y x,y …', 'area': 'M… Z', 'last': (x, y)} or None when
    there is nothing to draw. A flat series is centred rather than divided by a
    zero range.
    """
    values = [float(r[key]) for r in series]
    if len(values) < 2:
        return None
    lo, hi = min(values), max(values)
    span = hi - lo
    inner = height - pad * 2
    step = width / (len(values) - 1)
    pts = []
    for i, v in enumerate(values):
        x = round(i * step, 2)
        y = round(pad + inner / 2 if span == 0
                  else pad + (hi - v) / span * inner, 2)
        pts.append((x, y))
    # :g so the path reads 0,20 rather than 0.0,20 — this string goes into the
    # page, and a rounded float prints its trailing zero.
    n = lambda v: f'{v:g}'
    line = ' '.join(f'{n(x)},{n(y)}' for x, y in pts)
    area = (f'M{n(pts[0][0])},{n(height)} L'
            + ' L'.join(f'{n(x)},{n(y)}' for x, y in pts)
            + f' L{n(pts[-1][0])},{n(height)} Z')
    return {'line': line, 'area': area, 'last': pts[-1]}


def budget_usage(budget_rows):
    """{used, total, pct} across every budgeted category, or None if nothing is
    budgeted. pct is uncapped on purpose — being 130% through the month's budget
    is exactly the state worth seeing. Pure."""
    total = sum(float(r['budget']) for r in budget_rows)
    if total <= 0:
        return None
    used = sum(float(r['actual']) for r in budget_rows)
    return {'used': used, 'total': total, 'pct': round(used / total * 100)}


def bills_outstanding(remaining_items):
    """{count, total} of scheduled EXPENSES still to post this month. Pure.

    Income is excluded deliberately: the question this answers on the dashboard
    is "what is still going to leave the account", and netting a salary against
    it makes the figure meaningless."""
    bills = [i for i in remaining_items if i.get('type') == 'expense']
    return {'count': len(bills), 'total': sum(float(i['amount']) for i in bills)}


@bp.route('/sw.js')
def service_worker():
    """The PWA service worker (v10.13). Served from the root — a worker's
    scope is capped at its URL's directory, and installability requires it to
    control start_url '/', so /static/sw.js wouldn't do. No @login_required:
    the browser re-fetches it outside any session."""
    return current_app.send_static_file('sw.js')


@bp.route('/healthz')
@limiter.exempt
def healthz():
    """Liveness/readiness probe for the Docker healthcheck, the deploy job's
    post-deploy verification, and uptime monitoring.

    No @login_required — a probe cannot hold a session, and requiring one would
    make the check assert the wrong thing. Exempt from the rate limit so that
    continuous polling can never trip it and manufacture a false alarm.

    Deliberately says almost nothing: status and whether the database answered.
    No version, no configuration, no exception text — this is the one endpoint
    guaranteed to be reachable by anyone, so it is the last place to leak
    anything. The real exception goes to the log, as everywhere else.

    A round-trip to Postgres is the point. Gunicorn accepting the connection
    only proves the process is up; the app is not actually serviceable if it
    cannot reach its database, and a probe that returned 200 in that state
    would be actively misleading."""
    try:
        with db_cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except psycopg2.Error:
        current_app.logger.exception('healthz: database check failed')
        return jsonify(status='error', database='unreachable'), 503
    return jsonify(status='ok', database='ok'), 200


@bp.route('/dashboard')
def dashboard():
    """Legacy URL — the dashboard became the home page (v10.13). The redirect
    keeps old bookmarks alive and carries the month filter; no @login_required
    since / enforces auth after the hop (the /analytics precedent)."""
    return redirect(url_for('main.index', month=request.args.get('month')))


@bp.route('/')
@login_required
def index():
    from app.blueprints.schedules import run_due_schedules  # lazy: avoids import cycle
    from app.blueprints.transfers import run_due_transfers
    run_due_schedules(current_user.id)
    run_due_transfers(current_user.id)
    selected_month = parse_month_param(request.args.get('month'))
    months = recent_months()
    today = datetime.today()
    filter_year = None
    filter_month = None
    if selected_month:
        filter_year, filter_month = (int(p) for p in selected_month.split('-'))

    with db_cursor() as cursor:
        if selected_month:
            cursor.execute("""
                SELECT c.name, SUM(t.amount) AS total
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_transfer = false AND t.is_adjustment = false
                AND EXTRACT(YEAR FROM t.transaction_date) = %s
                AND EXTRACT(MONTH FROM t.transaction_date) = %s
                GROUP BY c.name
                ORDER BY total DESC
            """, (current_user.id, filter_year, filter_month))
        else:
            cursor.execute("""
                SELECT c.name, SUM(t.amount) AS total
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                WHERE t.user_id = %s AND t.transaction_type = 'expense' AND t.is_transfer = false AND t.is_adjustment = false
                GROUP BY c.name
                ORDER BY total DESC
            """, (current_user.id,))
        spending = cursor.fetchall()

        # v10.12 income-by-category — the categorization payoff for income-kind
        # categories (salary vs freelance vs interest). Same shape/filters as
        # the spending rollup; feeds the Spending card's Expense/Income toggle.
        if selected_month:
            cursor.execute("""
                SELECT c.name, SUM(t.amount) AS total
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                WHERE t.user_id = %s AND t.transaction_type = 'income' AND t.is_transfer = false AND t.is_adjustment = false
                AND EXTRACT(YEAR FROM t.transaction_date) = %s
                AND EXTRACT(MONTH FROM t.transaction_date) = %s
                GROUP BY c.name
                ORDER BY total DESC
            """, (current_user.id, filter_year, filter_month))
        else:
            cursor.execute("""
                SELECT c.name, SUM(t.amount) AS total
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                WHERE t.user_id = %s AND t.transaction_type = 'income' AND t.is_transfer = false AND t.is_adjustment = false
                GROUP BY c.name
                ORDER BY total DESC
            """, (current_user.id,))
        income_by_category = cursor.fetchall()

        if selected_month:
            cursor.execute("""
                SELECT
                    TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                    SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
                    SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
                FROM transactions
                WHERE user_id = %s AND is_transfer = false AND is_adjustment = false
                AND EXTRACT(YEAR FROM transaction_date) = %s
                AND EXTRACT(MONTH FROM transaction_date) = %s
                GROUP BY DATE_TRUNC('month', transaction_date)
                ORDER BY DATE_TRUNC('month', transaction_date)
            """, (current_user.id, filter_year, filter_month))
        else:
            cursor.execute("""
                SELECT
                    TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                    SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END) AS income,
                    SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expenses
                FROM transactions
                WHERE user_id = %s AND is_transfer = false AND is_adjustment = false
                GROUP BY DATE_TRUNC('month', transaction_date)
                ORDER BY DATE_TRUNC('month', transaction_date)
            """, (current_user.id,))
        cash_flow = cursor.fetchall()

        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month,
                SUM(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE -amount END))
                OVER (ORDER BY DATE_TRUNC('month', transaction_date)) AS running_balance
            FROM transactions
            WHERE user_id = %s
            GROUP BY DATE_TRUNC('month', transaction_date)
            ORDER BY DATE_TRUNC('month', transaction_date)
        """, (current_user.id,))
        net_balance_trend = cursor.fetchall()

        cursor.execute("""
            SELECT
                a.account_name,
                COALESCE(SUM(CASE WHEN t.transaction_type = 'income' THEN t.amount ELSE -t.amount END), 0) AS balance
            FROM account a
            LEFT JOIN transactions t ON a.account_id = t.account_id AND t.user_id = a.user_id
            WHERE a.user_id = %s
            GROUP BY a.account_id, a.account_name
            ORDER BY balance DESC
        """, (current_user.id,))
        account_balances = cursor.fetchall()

        # Spending by day of week (merged from /analytics, v10.9) — expense totals
        # grouped Sunday-first by Postgres DOW.
        if selected_month:
            cursor.execute("""
                SELECT
                    EXTRACT(DOW FROM transaction_date) AS dow,
                    TO_CHAR(transaction_date, 'Day') AS day_name,
                    SUM(amount) AS total
                FROM transactions
                WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false AND is_transfer = false
                AND EXTRACT(YEAR FROM transaction_date) = %s
                AND EXTRACT(MONTH FROM transaction_date) = %s
                GROUP BY dow, day_name
                ORDER BY dow
            """, (current_user.id, filter_year, filter_month))
        else:
            cursor.execute("""
                SELECT
                    EXTRACT(DOW FROM transaction_date) AS dow,
                    TO_CHAR(transaction_date, 'Day') AS day_name,
                    SUM(amount) AS total
                FROM transactions
                WHERE user_id = %s AND transaction_type = 'expense' AND is_adjustment = false AND is_transfer = false
                GROUP BY dow, day_name
                ORDER BY dow
            """, (current_user.id,))
        spending_by_day = cursor.fetchall()

        # Year over year (merged from /analytics, v10.9) — expenses for the same
        # month last year. Only meaningful when a single month is selected; the
        # this-year side reuses the hero's expense total (same filters), computed
        # after the cursor closes.
        last_year_expenses = None
        if selected_month:
            cursor.execute("""
                SELECT SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END)
                FROM transactions
                WHERE user_id = %s AND is_adjustment = false AND is_transfer = false
                AND EXTRACT(YEAR FROM transaction_date) = %s
                AND EXTRACT(MONTH FROM transaction_date) = %s
            """, (current_user.id, filter_year - 1, filter_month))
            last_year_expenses = float(cursor.fetchone()[0] or 0)

        # Monthly budget vs this-month (or selected-month) actual.
        # Returns (category, budget, actual, remaining).
        budget_data = compute_budget_vs_actual(current_user.id, filter_year, filter_month)

        goals_view = build_goals_view(cursor, current_user.id)

        # Chart colour slots, keyed by category NAME because that is all the
        # chart payloads carry. Ordered by id — i.e. creation order — so a
        # category's colour never moves: not when the month filter changes the
        # set on screen, and not when a NEW category is added (it takes the
        # next slot instead of shifting everyone along). Both would happen if
        # the slot came from the rendered array's position.
        cursor.execute("SELECT id, name FROM categories WHERE user_id = %s ORDER BY id",
                       (current_user.id,))
        category_order = {row.name: i for i, row in enumerate(cursor.fetchall())}

    # AI cards (both independent of the chart month filter so their cache keys
    # stay stable; cache-only on load, no model call). Each is positioned at a
    # distinct moment in time and HIDDEN entirely when its target month has
    # nothing to say (v10.6) — so the dashboard never leads with a dead card:
    #   * Insight (v10.1) is RETROSPECTIVE → the last COMPLETE month, so on the
    #     1st of a new month it shows a fully-populated prior-month recap rather
    #     than an empty in-progress one. Shown only if that month had activity.
    #   * Forecast (v10.2) is PROSPECTIVE → the current month. Shown only when
    #     there's something to project (month-to-date activity OR a scheduled
    #     item still to land) — mirrors the generator's own not-enough-data gate.
        ai_on = ai_enabled()
        # ⚠️ Computed UNCONDITIONALLY since #223: the dashboard's "still to post"
        # figure reads `remaining_items`, and that is a plain fact about the
        # user's schedules, not an AI feature. It used to sit inside the `if
        # ai_on:` below purely because the forecast card was its only consumer —
        # leaving it there would mean the stat silently vanishing on an install
        # with no API key. The narration around it (`forecast`, `show_forecast`)
        # stays gated.
        forecast_facts = compute_forecast(current_user.id, today.year, today.month)
        insight = None
        insight_facts = None
        show_insight = False
        forecast = None
        show_forecast = False
        agent_run = None
        insight_year, insight_month = _prev_month(today.year, today.month)
        if ai_on:
            insight_facts = compute_month_facts(current_user.id, insight_year, insight_month)
            show_insight = insight_facts['income'] > 0 or insight_facts['expenses'] > 0
            if show_insight:
                insight = load_insight(cursor, current_user.id, insight_year, insight_month)

            show_forecast = not (forecast_facts['income_to_date'] == 0
                                 and forecast_facts['expenses_to_date'] == 0
                                 and not forecast_facts['remaining_items'])
            if show_forecast:
                forecast = load_forecast(cursor, current_user.id, today.year, today.month)

            # Money agent (v10.10) — the latest cached weekly run. Unlike the two
            # cards above there's no not-enough-data gate: the empty state IS the
            # card (it carries the Run-now button), so it shows whenever AI is on.
            agent_run = load_agent_run(cursor, current_user.id)

    # Chart payloads — plain lists the template renders with |tojson, which
    # HTML-escapes into the script block (the old json.dumps + |safe let a
    # </script> in a category/account name break out of it).
    # Both doughnut payloads fold their tail (#108) — the two pill-toggle views
    # share one canvas, one palette and one slot map, so both need it.
    spending_data = fold_chart_tail(
        [{'category': r[0], 'total': float(r[1])} for r in spending])
    income_by_category_data = fold_chart_tail(
        [{'category': r[0], 'total': float(r[1])} for r in income_by_category])
    cash_flow_data = [{'month': r[0], 'income': float(r[1]), 'expenses': float(r[2])} for r in cash_flow]
    net_balance_data = [{'month': r[0], 'balance': float(r[1])} for r in net_balance_trend]
    account_data = [{'account': r[0], 'balance': float(r[1])} for r in account_balances]
    budget_chart_data = [{'category': r[0], 'budget': float(r[1]), 'actual': float(r[2])} for r in budget_data]
    day_of_week_data = [{'day': r[1].strip(), 'total': float(r[2])} for r in spending_by_day]
    # Each drawn row carries its OWN palette slot (#111), assigned per view so
    # the two are guaranteed internally distinct. This replaced a shared
    # name→slot map, which could not express "these two views colour the same
    # category differently" and wrapped past eight categories.
    spending_data = assign_series_slots(spending_data, category_order)
    income_by_category_data = assign_series_slots(income_by_category_data,
                                                  category_order)

    # #223 — the same two views, shaped for the server-rendered bars. Only views
    # that HAVE rows go in, and insertion order is the display order: the
    # template shows the first and hides the rest, so a user with income
    # categorized but nothing spent still sees a populated section rather than
    # an empty one labelled "Spending by category".
    category_bars = {}
    if spending_data:
        category_bars['expense'] = to_bar_rows(spending_data)
    if income_by_category_data:
        category_bars['income'] = to_bar_rows(income_by_category_data)

    # #225 — the figures the composed hero and stat row state. Each is pure and
    # unit-tested in tests/test_home_composition.py.
    hero_spark = sparkline(net_balance_data)
    budget_used = budget_usage(budget_chart_data)
    bills_due = bills_outstanding(forecast_facts['remaining_items'])
    days_left = days_left_in_month(today)

    has_transactions = bool(cash_flow) or bool(spending)

    # v10.6 hero — income/expenses/net for the current view (a single selected
    # month, or all time). Derived from cash_flow (already fetched) — no extra query.
    hero_income = sum(float(r[1]) for r in cash_flow)
    hero_expenses = sum(float(r[2]) for r in cash_flow)
    summary = {
        'income': hero_income,
        'expenses': hero_expenses,
        'net': hero_income - hero_expenses,
        'savings_rate': ((hero_income - hero_expenses) / hero_income * 100) if hero_income > 0 else None,
        'label': selected_month if selected_month else 'All time',
    }

    # ⚠️ Only when ONE month is being viewed. Against "All time" there is no
    # "last month" the headline figure could be compared with, and inventing one
    # is how the two scopes got mixed.
    hero_delta = None
    if selected_month:
        prev_facts = compute_month_facts(current_user.id, *_prev_month(filter_year, filter_month))
        hero_delta = net_change(prev_facts['net'], summary['net'])

    # Year over year — only when a month is selected AND last year has data;
    # hero_expenses is the same-filtered this-year total.
    yoy = None
    if last_year_expenses:
        yoy = {
            'last_year': last_year_expenses,
            'this_year': hero_expenses,
            'change': round((hero_expenses - last_year_expenses)
                            / last_year_expenses * 100, 1),
        }

    return render_template('dashboard.html',
        summary=summary,
        yoy=yoy,
        spending_data=spending_data,
        income_by_category_data=income_by_category_data,
        category_bars=category_bars,
        hero_spark=hero_spark,
        hero_delta=hero_delta,
        budget_used=budget_used,
        bills_due=bills_due,
        days_left=days_left,
        cash_flow_data=cash_flow_data,
        net_balance_data=net_balance_data,
        account_data=account_data,
        budget_chart_data=budget_chart_data,
        day_of_week_data=day_of_week_data,
        months=months,
        selected_month=selected_month,
        has_transactions=has_transactions,
        goals=goals_view,
        ai_enabled=ai_on,
        show_insight=show_insight,
        insight=insight,
        facts=insight_facts,
        insight_year=insight_year,
        insight_month=insight_month,
        show_forecast=show_forecast,
        forecast=forecast,
        forecast_facts=forecast_facts,
        forecast_year=today.year,
        forecast_month=today.month,
        agent_run=agent_run
    )
