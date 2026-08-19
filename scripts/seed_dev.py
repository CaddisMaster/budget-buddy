#!/usr/bin/env python3
"""Fill an empty database with synthetic development data.

The dev dataset used to exist only as rows in one machine's Docker volume,
which meant it could not be reproduced from the repository: a fresh clone, a
second machine, or a disposable environment all started schema-only, and the
only way to get a useful dashboard was to copy a dump of *real* financial data
between machines. This script replaces that with a committed generator.

Nothing here comes from a production or personal database. Every merchant,
amount and date is invented by a seeded PRNG.

Deliberately standalone (same reasoning as ``scripts/migrate.py``): it talks to
psycopg2 directly rather than importing the app, so it cannot be affected by —
or accidentally trigger — application startup. Password hashes are produced with
``bcrypt`` directly, which Flask-Bcrypt's ``check_password_hash`` accepts
unchanged, so the seeded user can log in normally.

The split below matters for testing: ``build_seed_plan()`` is **pure** — given a
date and a seed it returns the same nested dict of plain Python values every
time, touching no database and no clock. ``write_plan()`` is the only part that
writes. That is the same shape as ``compute_next_due()`` /
``project_expenses()`` elsewhere in the codebase, and it lets the interesting
properties be asserted without a database.

Usage::

    python scripts/seed_dev.py                    # user 'dev', 6 months
    python scripts/seed_dev.py --username sean
    python scripts/seed_dev.py --months 12
    python scripts/seed_dev.py --force            # wipe that user first
    python scripts/seed_dev.py --dry-run          # print a summary, write nothing

Inside the dev stack::

    docker compose exec web python scripts/seed_dev.py
"""

import argparse
import os
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from functools import partial

import bcrypt
import psycopg2
from dateutil.relativedelta import relativedelta

# A fixed default so two runs on the same day produce byte-identical data.
DEFAULT_SEED = 20260728
DEFAULT_USERNAME = 'dev'
DEFAULT_PASSWORD = 'dev-password-123'
DEFAULT_MONTHS = 6

# ── Static shape of the dataset ──────────────────────────────────────────────
# Kept as module constants rather than inline literals so a reader can see the
# whole fixture at a glance, and so tests can reference the names.

CHECKING = 'Everyday Checking'
SAVINGS = 'Rainy Day Savings'
CASHBACK = 'Cashback Card'
TRAVEL = 'Travel Card'

def accounts_for(months):
    """The account fixture, as (name, type, credit_limit, apr, opening_balance).

    Opening balances are written as is_adjustment rows dated at the window start
    — the same shape the balance check-in feature produces, and excluded from
    analytics, so they seed realistic balances without distorting any spending
    chart. Credit cards open negative: a card balance IS the debt.

    The Cashback card's opening debt and limit SCALE with the window rather than
    being fixed. Its monthly payment deliberately exceeds its monthly spending,
    so a fixed opening debt would be fully cleared by a long enough --months and
    the card would close with a positive balance — a state a real credit card
    never reaches. Growing the opening debt faster than the paydown rate keeps
    the card in debt for any window length, and scaling the limit alongside
    keeps utilization in a realistic band.
    """
    cashback_debt = Decimal(1100 + 140 * months)
    # Round the limit up to the next $500 above ~55% utilization at the window
    # start, so the card opens amber-tinted rather than nearly maxed.
    cashback_limit = Decimal((int(cashback_debt / Decimal('0.55')) // 500 + 1) * 500)
    return [
        (CHECKING, 'Bank Account', None, None, Decimal('3200.00')),
        (SAVINGS, 'Bank Account', None, None, Decimal('4100.00')),
        (CASHBACK, 'Credit Card', cashback_limit, Decimal('19.99'), -cashback_debt),
        (TRAVEL, 'Credit Card', Decimal('8000.00'), Decimal('22.49'), Decimal('-420.00')),
    ]

# (name, kind, description, monthly_budget or None)
# Rent and Health are deliberately left unbudgeted so the cockpit exercises both
# paths: a saved override, and the fall-back-to-suggested-average case.
CATEGORIES = [
    ('Rent', 'expense', 'Monthly housing payment', None),
    ('Groceries', 'expense', 'Supermarket and food shopping', Decimal('650.00')),
    ('Dining Out', 'expense', 'Restaurants, cafes, takeaway', Decimal('260.00')),
    ('Transport', 'expense', 'Fuel, transit, parking', Decimal('180.00')),
    ('Utilities', 'expense', 'Power, water, internet', Decimal('220.00')),
    ('Entertainment', 'expense', 'Streaming, events, hobbies', Decimal('120.00')),
    ('Shopping', 'expense', 'Clothes, household, general', Decimal('300.00')),
    ('Health', 'expense', 'Pharmacy, appointments, fitness', None),
    ('Salary', 'income', 'Regular employment income', None),
    ('Freelance', 'income', 'Occasional contract work', None),
    ('Interest', 'income', 'Savings interest', None),
]

MERCHANTS = {
    'Groceries': ['Greenfield Market', 'Corner Grocer', 'Harvest Foods',
                  'Riverside Produce', 'Daily Basket'],
    'Dining Out': ['The Copper Pot', 'Nine Bean Coffee', 'Saltwater Diner',
                   'Noodle House', 'Pinewood Cafe'],
    'Transport': ['City Transit', 'Fuel Stop 24', 'Metro Parking',
                  'Northline Rail'],
    'Entertainment': ['Streamly', 'Odeon Row Cinema', 'Vinyl & Co',
                      'Riverbank Climbing'],
    'Shopping': ['Loom & Thread', 'Hardware Yard', 'Paper Lane',
                 'Wayfarer Outfitters'],
    'Health': ['Mill Street Pharmacy', 'Dr Alvarez Clinic', 'Forge Gym'],
}


def _last_day(year, month):
    """Last calendar day of the given month."""
    return (date(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).day


def _month_day(year, month, eom, n):
    """The `n`th of the given month, clamped to its last day.

    Takes year/month/eom explicitly rather than closing over the enclosing
    loop's variables — a late-binding closure here would be a real bug the day
    anyone deferred one of these calls.
    """
    return date(year, month, min(n, eom))


def _money(rng, low, high):
    """A positive two-decimal amount in [low, high]."""
    return Decimal(str(round(rng.uniform(low, high), 2)))


def _month_starts(window_start, today):
    """Every month-start date from the window start through today's month."""
    out = []
    cur = window_start
    while cur <= today:
        out.append(cur)
        cur += relativedelta(months=1)
    return out


def _next_monthly_due(today, day):
    """The next occurrence of `day`-of-month strictly after today.

    Schedules seed next_due FORWARD so the due-runners never back-fill on the
    first page load — the same rule the create-schedule route follows.
    """
    candidate = date(today.year, today.month, min(day, _last_day(today.year, today.month)))
    if candidate > today:
        return candidate
    nxt = today + relativedelta(months=1)
    return date(nxt.year, nxt.month, min(day, _last_day(nxt.year, nxt.month)))


def _balance_series(transactions, transfers, account):
    """Running balance for one account, oldest first, as (date, balance) pairs.

    Used to snapshot a payoff goal at the card's WORST point rather than at an
    arbitrary date — see build_seed_plan().
    """
    events = []
    for txn in transactions:
        if txn['account'] == account:
            delta = txn['amount'] if txn['type'] == 'income' else -txn['amount']
            events.append((txn['date'], delta))
    for mv in transfers:
        if mv['to_account'] == account:
            events.append((mv['date'], mv['amount']))
        elif mv['from_account'] == account:
            events.append((mv['date'], -mv['amount']))
    events.sort(key=lambda e: e[0])
    running = Decimal('0.00')
    series = []
    for when, delta in events:
        running += delta
        series.append((when, running))
    return series


def _next_semimonthly_due(today, lo, hi):
    """Next semi-monthly pay day strictly after today, for pay days lo and hi."""
    for day in (lo, hi):
        clamped = min(day, _last_day(today.year, today.month))
        candidate = date(today.year, today.month, clamped)
        if candidate > today:
            return candidate
    nxt = today + relativedelta(months=1)
    return date(nxt.year, nxt.month, min(lo, _last_day(nxt.year, nxt.month)))


def build_seed_plan(today, seed=DEFAULT_SEED, months=DEFAULT_MONTHS):
    """Return the complete dataset as plain Python values. Pure.

    No database access, no clock read, no un-seeded randomness — the same
    (today, seed, months) always yields identical output, which is what makes
    the dataset reproducible across machines rather than merely re-creatable.

    Dates are derived from `today` rather than hardcoded, so the dataset stays
    "the last N months" forever instead of ageing into an empty dashboard.

    The window is calendar-aligned — it starts on the 1st of the month N-1
    months back — because every month-filtered surface in the app reads better
    against whole months. One consequence worth knowing: ``months=1`` run during
    the first days of a month produces only those few days of history, so there
    may be no transfers yet and no payoff goal (the card has not had time to be
    paid down). That degrades correctly rather than inventing data; the default
    of six months is never affected.
    """
    rng = random.Random(seed)
    window_start = today.replace(day=1) - relativedelta(months=months - 1)
    accounts = accounts_for(months)

    transactions = []
    transfers = []
    balances = {name: Decimal('0.00') for name, *_ in accounts}

    def spend(when, account, category, amount, description):
        transactions.append({
            'date': when, 'amount': amount, 'description': description,
            'category': category, 'account': account, 'type': 'expense',
            'is_adjustment': False,
        })
        balances[account] -= amount

    def earn(when, account, category, amount, description):
        transactions.append({
            'date': when, 'amount': amount, 'description': description,
            'category': category, 'account': account, 'type': 'income',
            'is_adjustment': False,
        })
        balances[account] += amount

    def move(when, from_account, to_account, amount, description):
        transfers.append({
            'date': when, 'amount': amount, 'description': description,
            'from_account': from_account, 'to_account': to_account,
        })
        balances[from_account] -= amount
        balances[to_account] += amount

    # Opening balances — is_adjustment, so analytics ignores them entirely.
    for name, _type, _limit, _apr, opening in accounts:
        if opening == 0:
            continue
        transactions.append({
            'date': window_start,
            'amount': abs(opening),
            'description': 'Opening balance',
            'category': None,
            'account': name,
            'type': 'income' if opening > 0 else 'expense',
            'is_adjustment': True,
        })
        balances[name] += opening

    # ── Recurring monthly rhythm ─────────────────────────────────────────────
    for month_start in _month_starts(window_start, today):
        year, month = month_start.year, month_start.month
        eom = _last_day(year, month)
        day = partial(_month_day, year, month, eom)

        if day(1) <= today:
            spend(day(1), CHECKING, 'Rent', Decimal('1450.00'), 'Rent — Alder Court')
        if day(2) <= today:
            move(day(2), CHECKING, SAVINGS, Decimal('400.00'), 'Monthly savings')
        if day(6) <= today:
            spend(day(6), CHECKING, 'Utilities', _money(rng, 95, 165), 'Northgate Energy')
        if day(12) <= today:
            spend(day(12), CASHBACK, 'Utilities', Decimal('79.99'), 'Fiber internet')

        # Semi-monthly pay, the 15th and the last day of the month.
        for pay_day in (15, eom):
            if day(pay_day) <= today:
                earn(day(pay_day), CHECKING, 'Salary', Decimal('2150.00'), 'Salary')

        if day(eom) <= today:
            earn(day(eom), SAVINGS, 'Interest', _money(rng, 2, 9), 'Savings interest')

        # The Cashback payment deliberately EXCEEDS what that card is spent on
        # each month, so its debt trends down and the payoff goal shows real
        # progress. The Travel payment deliberately falls slightly short, so one
        # card is improving and one is drifting — the more interesting pair to
        # look at on the accounts page.
        if day(20) <= today:
            move(day(20), CHECKING, CASHBACK, Decimal('975.00'), 'Card payment')
        if day(21) <= today:
            move(day(21), CHECKING, TRAVEL, Decimal('85.00'), 'Card payment')

        # Freelance work lands in roughly one month out of three.
        if rng.random() < 0.34 and day(18) <= today:
            earn(day(18), CHECKING, 'Freelance', _money(rng, 300, 900),
                 'Contract invoice')

    # ── Day-by-day discretionary spending ────────────────────────────────────
    # Weekday-aware so the dashboard's day-of-week chart has real shape rather
    # than uniform noise.
    cursor_date = window_start
    while cursor_date <= today:
        weekday = cursor_date.weekday()

        # Groceries: a weekly-ish shop, more likely at the weekend.
        if rng.random() < (0.34 if weekday >= 5 else 0.12):
            spend(cursor_date,
                  CHECKING if rng.random() < 0.55 else CASHBACK,
                  'Groceries', _money(rng, 24, 165),
                  rng.choice(MERCHANTS['Groceries']))

        if rng.random() < (0.30 if weekday >= 4 else 0.16):
            spend(cursor_date, CASHBACK, 'Dining Out', _money(rng, 9, 58),
                  rng.choice(MERCHANTS['Dining Out']))

        if weekday < 5 and rng.random() < 0.42:
            spend(cursor_date, CHECKING, 'Transport', _money(rng, 3.5, 14),
                  'City Transit')
        if rng.random() < 0.06:
            spend(cursor_date, CASHBACK, 'Transport', _money(rng, 32, 68),
                  'Fuel Stop 24')

        if rng.random() < 0.07:
            spend(cursor_date, CASHBACK, 'Entertainment', _money(rng, 12, 60),
                  rng.choice(MERCHANTS['Entertainment']))

        if rng.random() < 0.09:
            spend(cursor_date,
                  TRAVEL if rng.random() < 0.4 else CASHBACK,
                  'Shopping', _money(rng, 15, 180),
                  rng.choice(MERCHANTS['Shopping']))

        if rng.random() < 0.025:
            spend(cursor_date, CHECKING, 'Health', _money(rng, 20, 140),
                  rng.choice(MERCHANTS['Health']))

        cursor_date += timedelta(days=1)

    # A mid-window reconciliation, so an is_adjustment row exists somewhere
    # other than the opening balances.
    checkin_date = today - relativedelta(months=2)
    transactions.append({
        'date': checkin_date, 'amount': Decimal('12.40'),
        'description': 'Balance check-in', 'category': None,
        'account': SAVINGS, 'type': 'expense', 'is_adjustment': True,
    })
    balances[SAVINGS] -= Decimal('12.40')

    # ── Goals ────────────────────────────────────────────────────────────────
    # The save goal's target is DERIVED from what the account actually reached,
    # not hardcoded: a fixed target would be overshot by a long enough --months
    # and the goal would render as already complete.
    saved_so_far = balances[SAVINGS]
    save_target = Decimal((int(saved_so_far * Decimal('1.55')) // 500 + 1) * 500)
    goals = [{
        'name': 'Emergency fund',
        'target_amount': save_target,
        'target_date': today + relativedelta(months=10),
        'account': SAVINGS,
        'baseline_amount': Decimal('0.00'),
        'goal_type': 'save',
    }]

    # The payoff goal mirrors the create-goal route: baseline = the (negative)
    # balance at creation, target = what it takes to reach $0, so the projection
    # reads `saved = balance - baseline` as "paid off so far". Snapshotting at
    # the card's WORST point rather than an arbitrary date guarantees visible
    # progress — a fixed three-months-ago lookback can land on a stretch where
    # the card happened to move the wrong way.
    series = _balance_series(transactions, transfers, CASHBACK)
    if series:
        _worst_date, worst_balance = min(series, key=lambda pair: (pair[1], pair[0]))
        if worst_balance < 0 and balances[CASHBACK] > worst_balance:
            goals.append({
                'name': f'Pay off {CASHBACK}',
                'target_amount': -worst_balance,
                'target_date': today + relativedelta(months=9),
                'account': CASHBACK,
                'baseline_amount': worst_balance,
                'goal_type': 'payoff',
            })

    # ── Schedules ────────────────────────────────────────────────────────────
    # next_due is always in the FUTURE: the due-runners materialize forward only,
    # and a past next_due would make the first page load write extra rows,
    # quietly breaking reproducibility.
    schedules = [
        {'amount': Decimal('1450.00'), 'description': 'Rent — Alder Court',
         'category': 'Rent', 'account': CHECKING, 'type': 'expense',
         'frequency': 'monthly', 'anchor_day': 1, 'second_day': None,
         'next_due': _next_monthly_due(today, 1), 'end_date': None},
        {'amount': Decimal('2150.00'), 'description': 'Salary',
         'category': 'Salary', 'account': CHECKING, 'type': 'income',
         'frequency': 'semimonthly', 'anchor_day': 15, 'second_day': 31,
         'next_due': _next_semimonthly_due(today, 15, 31), 'end_date': None},
        {'amount': Decimal('79.99'), 'description': 'Fiber internet',
         'category': 'Utilities', 'account': CASHBACK, 'type': 'expense',
         'frequency': 'monthly', 'anchor_day': 12, 'second_day': None,
         'next_due': _next_monthly_due(today, 12), 'end_date': None},
        # Carries an end_date so the finished-schedule rules (#32) are visible
        # in dev without hand-building a row.
        {'amount': Decimal('312.00'), 'description': 'Car insurance',
         'category': 'Transport', 'account': CHECKING, 'type': 'expense',
         'frequency': 'quarterly', 'anchor_day': 9, 'second_day': None,
         'next_due': _next_monthly_due(today, 9),
         'end_date': today + relativedelta(months=14)},
    ]

    transfer_schedules = [
        {'amount': Decimal('400.00'), 'description': 'Monthly savings',
         'from_account': CHECKING, 'to_account': SAVINGS,
         'frequency': 'monthly', 'anchor_day': 2, 'second_day': None,
         'next_due': _next_monthly_due(today, 2), 'end_date': None},
        {'amount': Decimal('550.00'), 'description': 'Card payment',
         'from_account': CHECKING, 'to_account': CASHBACK,
         'frequency': 'monthly', 'anchor_day': 20, 'second_day': None,
         'next_due': _next_monthly_due(today, 20), 'end_date': None},
    ]

    transactions.sort(key=lambda t: (t['date'], t['description']))
    transfers.sort(key=lambda t: (t['date'], t['description']))

    return {
        'window_start': window_start,
        'accounts': accounts,
        'categories': CATEGORIES,
        'budgets': [(name, amount) for name, _k, _d, amount in CATEGORIES if amount],
        'transactions': transactions,
        'transfers': transfers,
        'schedules': schedules,
        'transfer_schedules': transfer_schedules,
        'goals': goals,
        'closing_balances': balances,
    }


# ── Database side ────────────────────────────────────────────────────────────
# Every table a seeded user can own, ordered so that children go before parents.
# transactions → categories/account are ON DELETE RESTRICT, so the user-row
# cascade alone is not enough (the same trap tests/conftest.py documents).
WIPE_ORDER = [
    'reminder_log', 'push_subscriptions', 'agent_runs',
    'insights', 'budget_history', 'budgets', 'goals',
    'transfer_schedules', 'schedules', 'transactions', 'categories', 'account',
]


def get_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'db'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
    )


def find_user(cursor, username):
    cursor.execute('SELECT id FROM users WHERE username = %s', (username,))
    row = cursor.fetchone()
    return row[0] if row else None


def wipe_user(cursor, user_id):
    """Remove every row a seeded user owns, then the user."""
    for table in WIPE_ORDER:
        cursor.execute(f'DELETE FROM {table} WHERE user_id = %s', (user_id,))
    cursor.execute('DELETE FROM users WHERE id = %s', (user_id,))


def write_plan(cursor, plan, username, password):
    """Insert the plan for a NEW user and return (user_id, counts)."""
    pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(12)).decode('utf-8')
    cursor.execute(
        'INSERT INTO users (username, password_hash, is_admin) '
        'VALUES (%s, %s, true) RETURNING id',
        (username, pw_hash),
    )
    user_id = cursor.fetchone()[0]

    account_ids = {}
    for name, acct_type, limit, apr, _opening in plan['accounts']:
        cursor.execute(
            'INSERT INTO account (account_name, type, credit_limit, apr, user_id) '
            'VALUES (%s, %s, %s, %s, %s) RETURNING account_id',
            (name, acct_type, limit, apr, user_id),
        )
        account_ids[name] = cursor.fetchone()[0]

    category_ids = {}
    for name, kind, description, _budget in plan['categories']:
        cursor.execute(
            'INSERT INTO categories (name, description, kind, user_id) '
            'VALUES (%s, %s, %s, %s) RETURNING id',
            (name, description, kind, user_id),
        )
        category_ids[name] = cursor.fetchone()[0]

    for name, amount in plan['budgets']:
        cursor.execute(
            'INSERT INTO budgets (category_id, amount, user_id) VALUES (%s, %s, %s)',
            (category_ids[name], amount, user_id),
        )
        # record_budget_change() writes this log on every real set, so seeding
        # budgets without it would produce a state the app never creates.
        cursor.execute(
            'INSERT INTO budget_history (category_id, amount, user_id) '
            'VALUES (%s, %s, %s)',
            (category_ids[name], amount, user_id),
        )

    for txn in plan['transactions']:
        cursor.execute(
            'INSERT INTO transactions (amount, description, transaction_date, '
            'category_id, account_id, transaction_type, is_adjustment, user_id) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (txn['amount'], txn['description'], txn['date'],
             category_ids[txn['category']] if txn['category'] else None,
             account_ids[txn['account']], txn['type'], txn['is_adjustment'],
             user_id),
        )

    # Transfers are a linked expense+income pair sharing one transfer_group_id,
    # both is_transfer, no category — matching transfers.py exactly.
    for mv in plan['transfers']:
        cursor.execute("SELECT nextval('transfer_group_seq')")
        gid = cursor.fetchone()[0]
        for account_name, leg in ((mv['from_account'], 'expense'),
                                  (mv['to_account'], 'income')):
            cursor.execute(
                'INSERT INTO transactions (amount, description, transaction_date, '
                'account_id, transaction_type, is_transfer, transfer_group_id, user_id) '
                'VALUES (%s, %s, %s, %s, %s, true, %s, %s)',
                (mv['amount'], mv['description'], mv['date'],
                 account_ids[account_name], leg, gid, user_id),
            )

    for sch in plan['schedules']:
        cursor.execute(
            'INSERT INTO schedules (amount, description, category_id, account_id, '
            'transaction_type, frequency, anchor_day, second_day, next_due, '
            'end_date, is_active, user_id) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)',
            (sch['amount'], sch['description'], category_ids[sch['category']],
             account_ids[sch['account']], sch['type'], sch['frequency'],
             sch['anchor_day'], sch['second_day'], sch['next_due'],
             sch['end_date'], user_id),
        )

    for sch in plan['transfer_schedules']:
        cursor.execute(
            'INSERT INTO transfer_schedules (amount, description, from_account_id, '
            'to_account_id, frequency, anchor_day, second_day, next_due, '
            'end_date, is_active, user_id) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)',
            (sch['amount'], sch['description'], account_ids[sch['from_account']],
             account_ids[sch['to_account']], sch['frequency'], sch['anchor_day'],
             sch['second_day'], sch['next_due'], sch['end_date'], user_id),
        )

    for goal in plan['goals']:
        cursor.execute(
            'INSERT INTO goals (name, target_amount, target_date, account_id, '
            'baseline_amount, goal_type, user_id) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (goal['name'], goal['target_amount'], goal['target_date'],
             account_ids[goal['account']], goal['baseline_amount'],
             goal['goal_type'], user_id),
        )

    counts = {
        'accounts': len(plan['accounts']),
        'categories': len(plan['categories']),
        'budgets': len(plan['budgets']),
        'transactions': len(plan['transactions']) + 2 * len(plan['transfers']),
        'transfer pairs': len(plan['transfers']),
        'schedules': len(plan['schedules']),
        'transfer schedules': len(plan['transfer_schedules']),
        'goals': len(plan['goals']),
    }
    return user_id, counts


def summarize(plan, counts=None):
    lines = [f"  window starts     {plan['window_start']}"]
    for label, value in (counts or {}).items():
        lines.append(f'  {label:<20}{value}')
    lines.append('  closing balances')
    for name, balance in plan['closing_balances'].items():
        lines.append(f'    {name:<22}{balance:>12,.2f}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--username', default=DEFAULT_USERNAME)
    parser.add_argument('--password', default=DEFAULT_PASSWORD)
    parser.add_argument('--months', type=int, default=DEFAULT_MONTHS,
                        help='how many months of history to generate')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED,
                        help='PRNG seed; the same seed yields the same data')
    parser.add_argument('--force', action='store_true',
                        help='delete the existing user and all their data first')
    parser.add_argument('--dry-run', action='store_true',
                        help='print what would be written, touch nothing')
    args = parser.parse_args(argv)

    if args.months < 1:
        parser.error('--months must be at least 1')

    plan = build_seed_plan(date.today(), seed=args.seed, months=args.months)

    if args.dry_run:
        print(f'Would seed {args.username!r}:')
        print(summarize(plan, {
            'accounts': len(plan['accounts']),
            'categories': len(plan['categories']),
            'transactions': len(plan['transactions']) + 2 * len(plan['transfers']),
            'transfer pairs': len(plan['transfers']),
            'goals': len(plan['goals']),
        }))
        return 0

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cursor:
                existing = find_user(cursor, args.username)
                if existing and not args.force:
                    print(
                        f'User {args.username!r} already exists (id {existing}).\n'
                        'Refusing to write into a database that already has data — '
                        'pass --force to delete that user and everything they own, '
                        'or choose another --username.',
                        file=sys.stderr,
                    )
                    return 1
                if existing:
                    print(f'--force: removing existing user {args.username!r} '
                          f'(id {existing})')
                    wipe_user(cursor, existing)
                user_id, counts = write_plan(cursor, plan, args.username, args.password)
    finally:
        conn.close()

    print(f'Seeded {args.username!r} (id {user_id}):')
    print(summarize(plan, counts))
    print(f'\nLog in at http://localhost:5001 with '
          f'{args.username} / {args.password}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
