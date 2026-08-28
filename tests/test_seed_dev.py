"""Tests for scripts/seed_dev.py — the reproducible dev dataset generator (#69).

Most of these exercise `build_seed_plan()`, which is pure: given a date, a seed
and a month count it returns plain Python values with no database and no clock
read. That is what makes "the same seed produces the same data" assertable at
all, and it keeps the expensive DB round-trip down to the few properties that
genuinely need one.
"""
import pathlib
import re
from datetime import date
from decimal import Decimal

import pytest
from dateutil.relativedelta import relativedelta

from app.db import get_db_connection
from scripts import seed_dev
from tests.conftest import TEST_PREFIX

TODAY = date(2026, 7, 28)
# Built from TEST_PREFIX, not hardcoded: the prefix carries the xdist worker id,
# and two workers seeding the same username would collide on users.username.
SEED_USER = TEST_PREFIX + "seed_user"
SEED_PASSWORD = "seed-password-123"


# ── The wipe list, against the schema it has to cover (#309, tranche 7) ──────

# ⚠️ THIS IS THE THIRD COPY of "every table a user owns, children first".
# #267 found the `verify` skill carrying a hand-maintained copy of
# `conftest._delete_user`'s list, watched it rot when sql/36 dropped two tables,
# and fixed it by making the skill CALL the function. `tests/test_verify_skill.py`
# then pinned the two survivors. It did not reach `seed_dev.WIPE_ORDER`, which
# lives in scripts/ and cannot import from tests/ — the script is deliberately
# standalone, so deduplicating it is not on the table. Pinning it is.

_SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "sql" / "schema.sql"

# `CREATE TABLE public.<name> (` — schema.sql qualifies every one.
_CREATE_TABLE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?public\.(\w+)", re.I)
# A `user_id` column inside a CREATE TABLE body is what makes a table user-owned.
_USER_ID = re.compile(r"^\s*user_id\b", re.M)


def _user_owned_tables():
    """Tables in sql/schema.sql that carry a user_id column."""
    text = _SCHEMA.read_text()
    bounds = [(m.group(1), m.end()) for m in _CREATE_TABLE.finditer(text)]
    assert bounds, "sql/schema.sql declares no CREATE TABLE — the parser is broken"

    owned = set()
    for i, (name, start) in enumerate(bounds):
        end = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
        body = text[start:end]
        # Stop at the closing paren so a following table's columns cannot leak in.
        body = body.split("\n);", 1)[0]
        if _USER_ID.search(body):
            owned.add(name)
    return owned


def test_the_wipe_list_covers_every_table_a_user_owns():
    """A table missing here is not reliably loud, which is the whole problem.

    `wipe_user()` deletes the listed tables and then the user row. A missed
    table whose FK is ON DELETE CASCADE is swept up by that last DELETE, so the
    list drifts with NO symptom — until someone adds a table with ON DELETE
    RESTRICT (transactions → categories/account already are), and then
    `--force` fails at the user row for a reason that points at the wrong file.
    """
    owned = _user_owned_tables()
    assert len(owned) >= 10, (
        f"only found {len(owned)} user-owned table(s) in sql/schema.sql — the "
        "parser is broken, not the schema. Without this floor a regex that "
        "matched nothing would make the assertion below vacuously true."
    )

    missing = sorted(owned - set(seed_dev.WIPE_ORDER))
    assert not missing, (
        f"seed_dev.WIPE_ORDER does not delete from {missing}, which sql/schema.sql "
        "gives a user_id. Add them, children before parents."
    )


def test_the_wipe_list_only_names_tables_that_exist():
    """The rot #267 actually saw: sql/36 dropped two tables and one copy of the
    list kept naming them. Stated against schema.sql rather than a live database
    so it fails in the PR that drops the table, not on someone's next run."""
    text = _SCHEMA.read_text()
    declared = set(_CREATE_TABLE.findall(text))
    gone = sorted(set(seed_dev.WIPE_ORDER) - declared)
    assert not gone, (
        f"seed_dev.WIPE_ORDER deletes from {gone}, which sql/schema.sql does not "
        "declare. A dropped table left in the list is the #267 failure exactly."
    )


# ── Pure generator ───────────────────────────────────────────────────────────

def test_same_seed_and_date_produce_identical_data():
    """The whole point of the script: reproducible, not merely re-creatable."""
    first = seed_dev.build_seed_plan(TODAY, seed=4242)
    second = seed_dev.build_seed_plan(TODAY, seed=4242)
    assert first["transactions"] == second["transactions"]
    assert first["transfers"] == second["transfers"]
    assert first["goals"] == second["goals"]
    assert first["closing_balances"] == second["closing_balances"]


def test_different_seeds_produce_different_data():
    a = seed_dev.build_seed_plan(TODAY, seed=1)
    b = seed_dev.build_seed_plan(TODAY, seed=2)
    assert a["transactions"] != b["transactions"]


def test_dates_are_derived_from_today_not_hardcoded():
    """The dataset must stay 'the last six months' forever rather than ageing
    into an empty dashboard, so shifting `today` must shift the whole window."""
    plan = seed_dev.build_seed_plan(TODAY)
    later = seed_dev.build_seed_plan(TODAY + relativedelta(years=1))
    assert later["window_start"] == plan["window_start"] + relativedelta(years=1)
    assert max(t["date"] for t in later["transactions"]) > TODAY


def test_history_spans_the_requested_window_and_ends_at_today():
    plan = seed_dev.build_seed_plan(TODAY, months=6)
    dates = [t["date"] for t in plan["transactions"]]
    assert plan["window_start"] == date(2026, 2, 1)
    assert min(dates) == plan["window_start"]
    # Something happened within the last few days, so the current month is not
    # a near-empty stub on the dashboard.
    assert max(dates) >= TODAY - relativedelta(days=5)
    assert max(dates) <= TODAY


def test_month_count_is_respected():
    short = seed_dev.build_seed_plan(TODAY, months=2)
    long = seed_dev.build_seed_plan(TODAY, months=12)
    assert short["window_start"] == date(2026, 6, 1)
    assert long["window_start"] == date(2025, 8, 1)
    assert len(long["transactions"]) > len(short["transactions"])


def test_every_amount_is_a_positive_real_number():
    """parse_positive_amount() rejects NaN and non-positive amounts everywhere in
    the app; seeded rows must satisfy the same rule or they poison every SUM()."""
    plan = seed_dev.build_seed_plan(TODAY)
    amounts = (
        [t["amount"] for t in plan["transactions"]]
        + [t["amount"] for t in plan["transfers"]]
        + [s["amount"] for s in plan["schedules"]]
        + [s["amount"] for s in plan["transfer_schedules"]]
    )
    assert amounts
    for amount in amounts:
        assert isinstance(amount, Decimal)
        assert amount > 0
        assert amount == amount  # NaN is the one value that fails this
        assert amount.as_tuple().exponent >= -2  # two decimal places at most


def test_accounts_include_a_credit_card_with_limit_and_apr():
    plan = seed_dev.build_seed_plan(TODAY)
    cards = [a for a in plan["accounts"] if a[1] == "Credit Card"]
    assert cards, "the utilization and interest surfaces need a Credit Card"
    assert all(limit and apr for _n, _t, limit, apr, _o in cards)
    # apr is capped at 100 by the app's own units-typo guard.
    assert all(0 < apr <= 100 for _n, _t, _l, apr, _o in cards)


def test_categories_cover_both_kinds():
    plan = seed_dev.build_seed_plan(TODAY)
    kinds = {kind for _n, kind, _d, _b in plan["categories"]}
    assert kinds == {"expense", "income"}


def test_income_and_expense_transactions_both_exist():
    plan = seed_dev.build_seed_plan(TODAY)
    types = {t["type"] for t in plan["transactions"]}
    assert types == {"expense", "income"}


def test_analytics_exclusions_are_represented():
    """Transfers and adjustments are excluded from analytics, so a dev dataset
    that contains neither can't show that exclusion working."""
    plan = seed_dev.build_seed_plan(TODAY)
    assert plan["transfers"], "no transfer pairs generated"
    adjustments = [t for t in plan["transactions"] if t["is_adjustment"]]
    assert len(adjustments) >= 2
    # An adjustment is never categorized — the check-in route posts None.
    assert all(t["category"] is None for t in adjustments)


def test_transfers_carry_two_distinct_accounts():
    plan = seed_dev.build_seed_plan(TODAY)
    for mv in plan["transfers"]:
        assert mv["from_account"] != mv["to_account"]


def test_schedules_and_goals_exist():
    plan = seed_dev.build_seed_plan(TODAY)
    assert plan["schedules"]
    assert plan["transfer_schedules"]
    assert plan["goals"]


def test_schedule_next_due_is_always_in_the_future():
    """A past next_due would make the first page load materialize extra rows,
    quietly breaking the reproducibility this script exists to provide."""
    plan = seed_dev.build_seed_plan(TODAY)
    for sch in plan["schedules"] + plan["transfer_schedules"]:
        assert sch["next_due"] > TODAY, sch["description"]


def test_one_schedule_carries_an_end_date():
    plan = seed_dev.build_seed_plan(TODAY)
    ended = [s for s in plan["schedules"] if s["end_date"]]
    assert ended, "no schedule exercises the #32 end_date rules"
    # Not yet finished: finished means next_due > end_date.
    assert all(s["next_due"] <= s["end_date"] for s in ended)


def test_payoff_goal_snapshots_a_real_historical_debt():
    """Mirrors the create-goal route: baseline is the (negative) balance at
    creation and target is what it takes to reach $0, so the projection's
    `saved = balance - baseline` reads as 'paid off so far'."""
    plan = seed_dev.build_seed_plan(TODAY)
    payoff = [g for g in plan["goals"] if g["goal_type"] == "payoff"]
    assert payoff, "no payoff goal generated"
    goal = payoff[0]
    assert goal["baseline_amount"] < 0
    assert goal["target_amount"] == -goal["baseline_amount"]
    # The card is being paid down faster than it is spent on, so the goal shows
    # progress rather than sitting at zero.
    current = plan["closing_balances"][goal["account"]]
    assert current > goal["baseline_amount"]


def test_save_goal_is_partially_complete():
    plan = seed_dev.build_seed_plan(TODAY)
    goal = next(g for g in plan["goals"] if g["goal_type"] == "save")
    saved = plan["closing_balances"][goal["account"]] - goal["baseline_amount"]
    assert 0 < saved < goal["target_amount"]


def test_credit_cards_close_in_debt_and_within_their_limit():
    """A card balance IS the debt, and utilization only means something when the
    debt sits inside the limit."""
    plan = seed_dev.build_seed_plan(TODAY)
    limits = {name: limit for name, acct_type, limit, _apr, _o in plan["accounts"]
              if acct_type == "Credit Card"}
    for name, limit in limits.items():
        balance = plan["closing_balances"][name]
        assert balance < 0, f"{name} carries no debt"
        assert -balance < limit, f"{name} is over its limit"


def test_bank_accounts_close_positive():
    plan = seed_dev.build_seed_plan(TODAY)
    for name, acct_type, _l, _a, _o in plan["accounts"]:
        if acct_type == "Bank Account":
            assert plan["closing_balances"][name] > 0


@pytest.mark.parametrize("months", [2, 3, 6, 12, 24, 36])
def test_the_fixture_scales_with_the_window(months):
    """The interesting balances must hold for any --months, not just the default.

    With a fixed opening debt and a fixed goal target, a long enough window pays
    the card off entirely (a credit card closing with a positive balance) and
    overshoots the savings target (a goal that renders as already complete).
    Both are derived from the window for exactly that reason.
    """
    plan = seed_dev.build_seed_plan(TODAY, months=months)
    limits = {n: limit for n, t, limit, _a, _o in plan["accounts"] if t == "Credit Card"}
    for name, limit in limits.items():
        balance = plan["closing_balances"][name]
        assert balance < 0, f"{name} closed positive at months={months}"
        assert -balance < limit, f"{name} exceeded its limit at months={months}"

    save = next(g for g in plan["goals"] if g["goal_type"] == "save")
    saved = plan["closing_balances"][save["account"]] - save["baseline_amount"]
    assert 0 < saved < save["target_amount"], f"save goal off at months={months}"

    payoff = next(g for g in plan["goals"] if g["goal_type"] == "payoff")
    assert plan["closing_balances"][payoff["account"]] > payoff["baseline_amount"]


def test_budgets_cover_some_but_not_all_expense_categories():
    """Both cockpit paths should be visible: a saved override, and the
    fall-back-to-suggested-average case for a category with no row."""
    plan = seed_dev.build_seed_plan(TODAY)
    budgeted = {name for name, _amount in plan["budgets"]}
    expense = {n for n, kind, _d, _b in plan["categories"] if kind == "expense"}
    assert budgeted
    assert budgeted < expense
    # Income categories are never budgeted.
    income = {n for n, kind, _d, _b in plan["categories"] if kind == "income"}
    assert not (budgeted & income)


# ── Database round-trip ──────────────────────────────────────────────────────

def _delete_seed_user():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (SEED_USER,))
    row = cur.fetchone()
    if row:
        seed_dev.wipe_user(cur, row[0])
        conn.commit()
    cur.close()
    conn.close()


@pytest.fixture
def seeded_user(app):
    """A fully seeded user, written through the script's own code path."""
    _delete_seed_user()
    plan = seed_dev.build_seed_plan(date.today(), months=3)
    conn = get_db_connection()
    cur = conn.cursor()
    user_id, counts = seed_dev.write_plan(cur, plan, SEED_USER, SEED_PASSWORD)
    conn.commit()
    cur.close()
    conn.close()
    yield {"id": user_id, "plan": plan, "counts": counts}
    _delete_seed_user()


def _count(user_id, table):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = %s", (user_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def test_write_plan_persists_every_table(seeded_user):
    user_id = seeded_user["id"]
    plan = seeded_user["plan"]
    assert _count(user_id, "account") == len(plan["accounts"])
    assert _count(user_id, "categories") == len(plan["categories"])
    assert _count(user_id, "budgets") == len(plan["budgets"])
    assert _count(user_id, "budget_history") == len(plan["budgets"])
    assert _count(user_id, "schedules") == len(plan["schedules"])
    assert _count(user_id, "transfer_schedules") == len(plan["transfer_schedules"])
    assert _count(user_id, "goals") == len(plan["goals"])
    assert _count(user_id, "transactions") == (
        len(plan["transactions"]) + 2 * len(plan["transfers"])
    )


def test_transfer_legs_are_a_linked_pair(seeded_user):
    """Both legs share one transfer_group_id, both are is_transfer, and neither
    carries a category — the shape transfers.py writes."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT transfer_group_id, COUNT(*), "
        "       COUNT(*) FILTER (WHERE transaction_type = 'expense'), "
        "       COUNT(*) FILTER (WHERE transaction_type = 'income'), "
        "       BOOL_AND(is_transfer), COUNT(category_id) "
        "FROM transactions WHERE user_id = %s AND transfer_group_id IS NOT NULL "
        "GROUP BY transfer_group_id",
        (seeded_user["id"],),
    )
    groups = cur.fetchall()
    cur.close()
    conn.close()
    assert groups
    for _gid, total, expenses, incomes, all_transfer, categorized in groups:
        assert total == 2
        assert expenses == 1
        assert incomes == 1
        assert all_transfer
        assert categorized == 0


def test_seeded_user_can_log_in_and_load_a_populated_dashboard(app, seeded_user):
    """bcrypt hashes written directly must satisfy Flask-Bcrypt, and the whole
    point of the dataset is that the dashboard renders something."""
    client = app.test_client()
    resp = client.post(
        "/login",
        data={"username": SEED_USER, "password": SEED_PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    page = client.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Groceries" in body
    assert seed_dev.CHECKING in body


def test_loading_the_dashboard_materializes_nothing(app, seeded_user):
    """The due-runners fire on '/'. Seeded next_due dates are in the future, so
    a page load must not write rows — otherwise the dataset stops being
    reproducible the moment anyone opens the app."""
    before = _count(seeded_user["id"], "transactions")
    client = app.test_client()
    client.post("/login", data={"username": SEED_USER, "password": SEED_PASSWORD})
    client.get("/")
    client.get("/transactions")
    client.get("/scheduled")
    client.get("/transfers")
    assert _count(seeded_user["id"], "transactions") == before


def test_seeding_refuses_when_the_user_already_exists(app, seeded_user, capsys):
    """A seed script that silently doubles a dataset is worse than one that
    stops, so the no-force path must exit non-zero and write nothing."""
    before = _count(seeded_user["id"], "transactions")
    code = seed_dev.main(["--username", SEED_USER, "--months", "1"])
    assert code == 1
    assert "already exists" in capsys.readouterr().err
    assert _count(seeded_user["id"], "transactions") == before


def test_force_replaces_the_existing_user(app, seeded_user):
    """--force must clear the old rows rather than adding to them, and the
    replacement user is a genuinely new row."""
    old_id = seeded_user["id"]
    code = seed_dev.main(["--username", SEED_USER, "--months", "1", "--force"])
    assert code == 0
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE username = %s", (SEED_USER,))
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT COUNT(*) FROM transactions WHERE user_id = %s", (old_id,))
    assert cur.fetchone()[0] == 0
    cur.close()
    conn.close()


def test_dry_run_writes_nothing(app, capsys):
    _delete_seed_user()
    code = seed_dev.main(["--username", SEED_USER, "--dry-run"])
    assert code == 0
    assert "Would seed" in capsys.readouterr().out
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE username = %s", (SEED_USER,))
    assert cur.fetchone()[0] == 0
    cur.close()
    conn.close()


def test_seeded_data_is_scoped_to_its_own_user(seeded_user, users):
    """Every table carries user_id; a seeded dataset must not leak into the
    fixture users' rows."""
    other = users["a"]["id"]
    for table in ("transactions", "categories", "account", "goals", "schedules"):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = %s", (other,)
        )
        before = cur.fetchone()[0]
        cur.close()
        conn.close()
        # The fixture user owns only what conftest gave them.
        assert before <= 1
