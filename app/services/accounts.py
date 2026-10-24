"""Account facts other areas read: the canonical balance query, and the
credit-card utilization and interest maths (#489).

Ask, the month read and the digest all need these, and imported them from the
accounts blueprint until #489. ⚠️ The two pure functions are ALSO Jinja
globals: blueprints/accounts.py registers them, because `_account_row.html`
calls them by name.
"""
import math

from app.db import db_cursor

# Days since the last check-in before an account reads as stale on /accounts.
CHECKIN_STALE_DAYS = 30

ACCOUNT_ROW_SQL = """
    SELECT a.account_id, a.account_name, a.type,
        COALESCE(SUM(CASE WHEN t.transaction_type = 'income'
            THEN t.amount ELSE -t.amount END), 0) AS balance,
        a.last_checked_in,
        (a.last_checked_in IS NULL
            OR a.last_checked_in < CURRENT_DATE - {stale_days}) AS checkin_stale,
        a.credit_limit,
        a.apr
    FROM account a
    LEFT JOIN transactions t ON a.account_id = t.account_id AND t.user_id = a.user_id
    WHERE a.user_id = %s {extra}
    GROUP BY a.account_id, a.account_name, a.type, a.last_checked_in,
        a.credit_limit, a.apr
    ORDER BY a.account_name
""".replace('{stale_days}', str(CHECKIN_STALE_DAYS))

# Utilization tinting thresholds (v10.10 Credit limits): amber from 30% (the
# classic keep-utilization-under-30% guidance), red from 80% (nearly maxed).
CREDIT_WARN_PCT = 30
CREDIT_DANGER_PCT = 80


def credit_utilization(balance, credit_limit):
    """Pure utilization math for a credit card — the single source of truth
    shared by the /accounts templates (blueprints/accounts.py registers it as a
    Jinja global so all five row-render sites get it), the ask tool, and the digest/insight facts.

    `balance` is the signed account balance (credit cards run negative);
    `credit_limit` may be None. Returns None when there is no usable limit,
    else {limit, debt, available, pct, bar_pct, tier}. `pct` is uncapped (an
    over-limit card reads e.g. 104%); `bar_pct` caps at 100 for the bar width;
    `available` goes negative when over limit.
    """
    if credit_limit is None:
        return None
    # psycopg2 hands numeric back as Decimal; coerce both so mixed
    # Decimal/float arithmetic can't raise.
    limit = float(credit_limit)
    # ⚠️ `math.isfinite`, not a bare `<= 0` (#309). NaN compares False to
    # everything, so it slips a range check — the same property
    # `helpers.parse_signed_amount` was written against, for the same reason:
    # `numeric(10,2)` accepts 'NaN', and one NaN card turned the cross-card
    # "available credit" sum on /accounts into nan. `credit_limit` is older
    # than `_parse_credit_limit`, so a stored value can predate the validation
    # that would now reject it — which is exactly the argument monthly_interest
    # below already makes for its own guard.
    if not math.isfinite(limit) or limit <= 0:
        return None
    debt = max(0.0, -float(balance))
    pct = round(debt / limit * 100, 1)
    tier = ('danger' if pct >= CREDIT_DANGER_PCT
            else 'warn' if pct >= CREDIT_WARN_PCT else 'ok')
    return {'limit': round(limit, 2), 'debt': round(debt, 2),
            'available': round(limit - debt, 2), 'pct': pct,
            'bar_pct': min(pct, 100.0), 'tier': tier}


def monthly_interest(balance, apr):
    """Approximate monthly interest cost of a credit card's current debt — the
    twin of credit_utilization(), shared by the /accounts row, the ask tool,
    and the digest/insight facts. debt × (apr/100)/12 approximates daily
    compounding, so every surface words the figure with '~'.

    `balance` is the signed account balance (credit cards run negative);
    `apr` is a percent and may be None. Returns None when there is no usable
    apr OR no debt (the "is this interesting?" gate lives here, like
    credit_utilization returning None = no bar), else {apr, debt, monthly}.
    Guards independently of _parse_apr — stored values could predate it.
    """
    if apr is None:
        return None
    # psycopg2 hands numeric back as Decimal; coerce both so mixed
    # Decimal/float arithmetic can't raise.
    apr = float(apr)
    # The same guard as credit_utilization above, deliberately spelled the same
    # way. This used to read `apr != apr`, which caught NaN and nothing else —
    # correct, but a second idiom for one rule, and the twin having a different
    # spelling is how it went unnoticed that the twin had no guard at all.
    if not math.isfinite(apr) or apr <= 0:
        return None
    debt = max(0.0, -float(balance))
    if debt <= 0:
        return None
    return {'apr': round(apr, 2), 'debt': round(debt, 2),
            'monthly': round(debt * (apr / 100.0) / 12.0, 2)}


def credit_card_utilization_facts(user_id):
    """Per-card facts for the user's Credit Card accounts, name order; [] when
    none qualify. Deterministic — the only source of card figures the AI
    narrates. A card qualifies with a usable limit OR usable interest (v10.15),
    and the keys are per-feature: {name} always, plus
    {limit, debt, available, utilization_pct} when a limit is set, plus
    {debt, apr, est_monthly_interest} when an apr is set and the card carries
    debt (both branches compute the same `debt`). Reports positive `debt`
    rather than the signed balance so the narration can't misread a minus
    sign."""
    with db_cursor() as cursor:
        cursor.execute(ACCOUNT_ROW_SQL.format(extra=""), (user_id,))
        rows = cursor.fetchall()
    facts = []
    for r in rows:
        if r.type != 'Credit Card':
            continue
        cu = credit_utilization(r.balance, r.credit_limit)
        mi = monthly_interest(r.balance, r.apr)
        if cu is None and mi is None:
            continue
        fact = {'name': r.account_name}
        if cu:
            fact.update({'limit': cu['limit'], 'debt': cu['debt'],
                         'available': cu['available'], 'utilization_pct': cu['pct']})
        if mi:
            fact.update({'debt': mi['debt'], 'apr': mi['apr'],
                         'est_monthly_interest': mi['monthly']})
        facts.append(fact)
    return facts
