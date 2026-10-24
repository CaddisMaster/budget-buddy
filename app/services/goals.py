"""A goal's progress and projection: the figures behind the Goals page and
Home's goals widget (#489, from the goals blueprint).

compute_goal_projection() is pure (no DB) and shared by save and payoff goals;
a payoff goal is interest-aware when its card carries an apr. build_goals_view()
and build_single_goal_view() turn goal rows into the view dicts both pages
render. Home imported build_goals_view from the goals blueprint until #489.
"""
import math
from datetime import date

from dateutil.relativedelta import relativedelta

GOAL_SELECT = (
    "SELECT g.id, g.name, g.target_amount, g.target_date, g.account_id, "
    "g.baseline_amount, g.goal_type, a.account_name, a.apr AS account_apr "
    "FROM goals g JOIN account a ON g.account_id = a.account_id "
    "WHERE g.user_id = %s"
)

# How many recent months feed the "projected completion" pace estimate.
INFLOW_WINDOW_MONTHS = 3

# The interest simulation's horizon: a payoff that hasn't cleared in 50 years
# is "never" for projection purposes (also bounds the loop).
PROJECTION_MAX_MONTHS = 600


def compute_goal_projection(target_amount, saved, target_date,
                            monthly_net_inflow, today=None, apr=None):
    """Pure projection math for a savings goal — no DB access, easy to unit test
    (sibling of compute_next_due()).

    Inputs are plain numbers/dates; the caller feeds in `saved` (account balance
    minus the goal's baseline) and `monthly_net_inflow` (recent average net flow
    into the linked account). Returns a dict describing progress and two
    forward-looking projections:

      - required_per_month: with a target_date, how much to set aside each month
        to hit the target on time (None if no date or already complete).
      - projected_date / on_track: from the recent inflow pace, when the goal is
        projected to complete and whether that beats the target_date.

    `apr` (v10.15, payoff goals only — the linked card's rate) makes all three
    interest-aware: est_monthly_interest = remaining × apr/100/12 (the same
    approximation as accounts.monthly_interest, hence the '~' wording),
    required_per_month becomes the amortized payment, and the pace projection
    simulates interest accruing month by month — a pace that can't outrun the
    interest projects no finish date (on_track False when there's a target).
    With apr=None every figure is unchanged from the pre-v10.15 math.
    """
    today = today or date.today()
    target_amount = float(target_amount)
    saved = float(saved)
    monthly_net_inflow = float(monthly_net_inflow)
    if apr is not None:
        apr = float(apr)  # psycopg2 numeric → Decimal; coerce
        # Spelled exactly as accounts.credit_utilization / monthly_interest
        # (#309). This read `apr != apr`, which is correct but was a THIRD
        # spelling of one rule across two files — and the divergence is how it
        # went unnoticed that credit_utilization had no guard at all. Identical
        # lines are the mechanism; a comment saying "remember the NaN case"
        # is what was already there and did not work.
        if not math.isfinite(apr) or apr <= 0:  # unusable → interest-free math
            apr = None

    remaining = round(target_amount - saved, 2)
    percent = round(saved / target_amount * 100, 1) if target_amount > 0 else 0.0
    percent = max(0.0, min(percent, 100.0))
    complete = remaining <= 0

    monthly_rate = apr / 100.0 / 12.0 if apr is not None else None
    est_monthly_interest = None
    if monthly_rate and not complete:
        est_monthly_interest = round(remaining * monthly_rate, 2)

    required_per_month = None
    if target_date and not complete:
        delta = relativedelta(target_date, today)
        months_left = delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)
        if months_left <= 0:
            months_left = 1  # target is here/overdue — needs it within the month
        if monthly_rate:
            # Amortized payment: clears `remaining` at this rate in months_left
            # equal installments (straight-line would land short by the interest).
            required_per_month = round(
                remaining * monthly_rate
                / (1 - (1 + monthly_rate) ** -months_left), 2)
        else:
            required_per_month = round(remaining / months_left, 2)

    projected_date = None
    on_track = None
    if not complete:
        months_to_finish = None
        if monthly_net_inflow > 0:
            if monthly_rate:
                # Simulate: interest accrues, then the month's paydown lands.
                # A pace at/below the monthly interest never clears the balance
                # and runs into the cap → no projected date.
                bal = remaining
                months = 0
                while bal > 0 and months < PROJECTION_MAX_MONTHS:
                    bal = bal * (1 + monthly_rate) - monthly_net_inflow
                    months += 1
                if bal <= 0:
                    months_to_finish = months
            else:
                months_to_finish = math.ceil(remaining / monthly_net_inflow)
        if months_to_finish is not None:
            projected_date = today + relativedelta(months=months_to_finish)
            if target_date:
                on_track = projected_date <= target_date
        elif target_date:
            # No positive inflow (or interest outpaces it) → never projected
            # to finish at the current pace.
            on_track = False
    elif target_date:
        on_track = True

    return {
        'remaining': remaining,
        'percent': percent,
        'complete': complete,
        'required_per_month': required_per_month,
        'projected_date': projected_date,
        'on_track': on_track,
        'monthly_net_inflow': round(monthly_net_inflow, 2),
        'est_monthly_interest': est_monthly_interest,
    }


def account_balance(cursor, user_id, account_id):
    """Net income − expense for one account (transfers included — they move real
    money). Same shape as the per-account balance on the Accounts page."""
    cursor.execute(
        "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
        "THEN amount ELSE -amount END), 0) "
        "FROM transactions WHERE user_id = %s AND account_id = %s",
        (user_id, account_id),
    )
    return float(cursor.fetchone()[0])


def _recent_monthly_inflow(cursor, user_id, account_id, months=INFLOW_WINDOW_MONTHS):
    """Average net inflow per month into an account over the last `months`
    months — drives the projected-completion estimate. Transfers count: a
    transfer into the account is exactly the kind of contribution we want."""
    cursor.execute(
        "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
        "THEN amount ELSE -amount END), 0) "
        "FROM transactions WHERE user_id = %s AND account_id = %s "
        "AND transaction_date >= %s",
        (user_id, account_id, date.today() - relativedelta(months=months)),
    )
    total = float(cursor.fetchone()[0])
    return total / months


def _goal_view(cursor, user_id, row):
    """Turn one goal row (GOAL_SELECT shape) into a view dict with progress +
    projections. For a payoff goal the same math reads as: saved = paid off
    since creation, remaining = the current actual debt (self-correcting if
    more gets charged), complete = balance back at $0. Payoff goals feed the
    linked account's apr into the projection (v10.15) — interest only makes
    sense against a debt, so save goals pass None."""
    balance = account_balance(cursor, user_id, row.account_id)
    saved = balance - float(row.baseline_amount)
    inflow = _recent_monthly_inflow(cursor, user_id, row.account_id)
    apr = row.account_apr if row.goal_type == 'payoff' else None
    projection = compute_goal_projection(row.target_amount, saved,
                                         row.target_date, inflow, apr=apr)
    return {
        'id': row.id,
        'name': row.name,
        'type': row.goal_type,
        'target_amount': float(row.target_amount),
        'target_date': row.target_date,
        'account_name': row.account_name,
        'saved': round(saved, 2),
        **projection,
    }


def build_goals_view(cursor, user_id):
    """Load a user's goals with computed progress + projections. Shared by the
    Goals page and the dashboard widget so the math lives in one place."""
    cursor.execute(GOAL_SELECT + " ORDER BY g.created_at DESC", (user_id,))
    return [_goal_view(cursor, user_id, r) for r in cursor.fetchall()]


def build_single_goal_view(cursor, user_id, goal_id):
    """View dict for one goal, or None if it isn't this user's."""
    cursor.execute(GOAL_SELECT + " AND g.id = %s", (user_id, goal_id))
    row = cursor.fetchone()
    return _goal_view(cursor, user_id, row) if row else None
