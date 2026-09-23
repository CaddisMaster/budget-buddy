"""#314 — a NaN cannot be stored in a money column.

PostgreSQL's `numeric` accepts `NaN`, so every money column in this schema could
hold one. Nothing in the app writes one today — `helpers.parse_signed_amount()`
rejects non-finite input — but that validator is younger than the columns, and
`accounts.monthly_interest` already says so in its own docstring ("stored values
could predate it"). #314 takes that seriously: `goals.compute_goal_projection()`
raises `ValueError: cannot convert float NaN to integer` on a NaN target, which
is a 500 on a page the user can reach, and a NaN `credit_limit` used to turn the
whole "Total available credit" figure into `nan`.

The fix is the database refusing what the application already refuses, so a guard
cannot be forgotten at a new call site.

⚠️ **THE OBVIOUS CONSTRAINT DOES NOT WORK, AND FAILS OPEN.** #314 proposed

    CHECK (credit_limit IS NULL OR credit_limit = credit_limit)   -- NaN <> NaN

which is IEEE 754 float semantics. PostgreSQL `numeric` is not IEEE: it defines
NaN as EQUAL to itself so that the type can be indexed and sorted. Measured, not
assumed:

    SELECT 'NaN'::numeric = 'NaN'::numeric;   ->  t

So `v = v` is true for a NaN, the CHECK passes, and the row is stored. Written
that way the migration would have shipped, gone green, and protected nothing.
The form that works is `<> 'NaN'::numeric`, which is what `sql/38` uses.

⚠️ **Why the tests below UPDATE an existing row rather than INSERT a new one.**
A CHECK is evaluated on both, but building a fresh valid row for nine columns
across seven tables means satisfying every FK and NOT NULL on each — and a test
that fails because it got the fixture wrong looks exactly like a test that failed
because the constraint is missing. Updating one column of a row the fixtures
already built keeps the NaN the only variable.

⚠️ Each rejection test has a **matching acceptance test on the same column**. A
rejection test alone passes if the UPDATE is broken for any reason at all — a
typo'd table name raises too, and `pytest.raises(psycopg2.Error)` cannot tell the
difference.
"""

import re
from pathlib import Path

import psycopg2
import pytest

from app.blueprints.goals import compute_goal_projection
from app.db import db_cursor
from tests.helpers import create_account, create_budget, create_goal, create_schedule, create_transfer_schedule

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = REPO_ROOT / "sql" / "schema.sql"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"


# ---------------------------------------------------------------------------
# The columns under protection
# ---------------------------------------------------------------------------
#
# ⚠️ This list is hand-maintained ON PURPOSE, and
# `test_every_numeric_money_column_is_covered` derives the same set from
# schema.sql and asserts they agree. The list alone would go quiet as the schema
# grows — a new numeric column simply would not be tested, and nothing would say
# so. Derivation alone cannot notice a column that was DELETED from the list.
# Keeping both means either kind of drift is loud.


def _build_account(users):
    return create_account(users["a"]["id"], "nan-probe", "Credit Card",
                          credit_limit=500, apr=19.99)


def _build_goal(users):
    return create_goal(users["a"]["id"], users["a"]["account_id"],
                       target_amount=1000, baseline=0)


def _build_schedule(users):
    return create_schedule(users["a"]["id"], users["a"]["account_id"],
                           amount=25, frequency="monthly", next_due="2026-12-01")


def _build_transfer_schedule(users):
    other = create_account(users["a"]["id"], "nan-probe-dest")
    return create_transfer_schedule(users["a"]["id"], users["a"]["account_id"],
                                    other, amount=25, frequency="monthly",
                                    next_due="2026-12-01")


def _build_budget(users):
    return create_budget(users["a"]["id"], users["a"]["category_id"], amount=200)


def _build_budget_history(users):
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO budget_history (category_id, amount, user_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (users["a"]["category_id"], 200, users["a"]["id"]),
        )
        return cur.fetchone()[0]


def _existing_transaction(users):
    return users["a"]["transaction_id"]


# (table, primary-key column, money column, nullable, row builder)
COLUMNS = [
    ("account", "account_id", "credit_limit", True, _build_account),
    ("account", "account_id", "apr", True, _build_account),
    ("transactions", "id", "amount", False, _existing_transaction),
    ("budgets", "id", "amount", False, _build_budget),
    ("goals", "id", "target_amount", False, _build_goal),
    ("goals", "id", "baseline_amount", False, _build_goal),
    ("schedules", "id", "amount", False, _build_schedule),
    ("transfer_schedules", "id", "amount", False, _build_transfer_schedule),
    ("budget_history", "id", "amount", True, _build_budget_history),
]

IDS = [f"{table}.{column}" for table, _, column, _, _ in COLUMNS]


def _set(table, pk_column, column, row_id, value):
    """UPDATE one money column. Raises psycopg2.Error if the database refuses."""
    with db_cursor(commit=True) as cur:
        cur.execute(
            f"UPDATE {table} SET {column} = %s WHERE {pk_column} = %s",
            (value, row_id),
        )


def _read(table, pk_column, column, row_id):
    with db_cursor() as cur:
        cur.execute(
            f"SELECT {column} AS value FROM {table} WHERE {pk_column} = %s",
            (row_id,),
        )
        return cur.fetchone().value


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table,pk_column,column,nullable,build", COLUMNS, ids=IDS)
def test_the_database_refuses_a_nan(users, table, pk_column, column, nullable,
                                    build):
    """The assertion the whole issue exists for.

    ⚠️ Asserts `CheckViolation` specifically, not bare `psycopg2.Error`. Any
    mistake in this test — wrong table, wrong column — also raises, and a broad
    `except` would report the constraint as present when the test never reached
    it.
    """
    row_id = build(users)

    with pytest.raises(psycopg2.errors.CheckViolation) as caught:
        _set(table, pk_column, column, row_id, float("nan"))

    assert "finite" in str(caught.value), (
        f"{table}.{column} rejected the NaN, but not via the constraint sql/38 "
        f"adds — the error was: {caught.value}"
    )


@pytest.mark.parametrize("table,pk_column,column,nullable,build", COLUMNS, ids=IDS)
def test_a_finite_value_is_still_accepted(users, table, pk_column, column,
                                          nullable, build):
    """The control for the test above.

    Without this, a rejection test passes for any reason the UPDATE fails at
    all, and the constraint could be rejecting every write.
    """
    row_id = build(users)

    _set(table, pk_column, column, row_id, 12.34)

    assert float(_read(table, pk_column, column, row_id)) == pytest.approx(12.34)


@pytest.mark.parametrize("table,pk_column,column,nullable,build", COLUMNS, ids=IDS)
def test_the_column_extremes_are_still_accepted(users, table, pk_column, column,
                                                nullable, build):
    """A constraint that narrowed the usable range would be a worse bug than the
    one being fixed, and every value here was legal before sql/38."""
    row_id = build(users)
    # apr is numeric(5,2); every other money column is numeric(10,2).
    biggest = 999.99 if column == "apr" else 12345678.90

    for value in (0, biggest, -biggest, 0.01):
        _set(table, pk_column, column, row_id, value)
        assert float(_read(table, pk_column, column, row_id)) == pytest.approx(value)


@pytest.mark.parametrize(
    "table,pk_column,column,nullable,build",
    [entry for entry in COLUMNS if entry[3]],
    ids=[f"{t}.{c}" for t, _, c, nullable, _ in COLUMNS if nullable],
)
def test_null_is_still_accepted_where_the_column_is_nullable(users, table,
                                                             pk_column, column,
                                                             nullable, build):
    """`NULL` is meaningful in three of these columns — `credit_limit` and `apr`
    mean "not set", and `budget_history.amount` means "cleared". A CHECK passes
    on NULL by definition, so this holds without an `IS NULL OR` clause; the test
    exists because that is easy to doubt and easy to "fix" wrongly later.
    """
    row_id = build(users)

    _set(table, pk_column, column, row_id, None)

    assert _read(table, pk_column, column, row_id) is None


def test_the_goal_projection_cannot_be_handed_a_nan_from_the_database(users):
    """#314's fourth criterion, stated the way the fix actually works.

    `compute_goal_projection()` is pure and still raises on a NaN — that is
    correct, `math.ceil` refusing NaN loudly is the only reason this was ever
    noticed. What changes is that the database can no longer supply one, so the
    path from a stored row to that ValueError is closed at the source rather than
    guarded at the call site.
    """
    goal_id = _build_goal(users)

    with pytest.raises(psycopg2.errors.CheckViolation):
        _set("goals", "id", "target_amount", goal_id, float("nan"))

    stored = _read("goals", "id", "target_amount", goal_id)
    projection = compute_goal_projection(
        target_amount=float(stored), saved=100.0, target_date=None,
        monthly_net_inflow=50.0,
    )
    assert projection["remaining"] == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# The list above cannot go quiet
# ---------------------------------------------------------------------------

# `<name> numeric(<precision>,<scale>)` in a CREATE TABLE body.
_NUMERIC = re.compile(r"^\s+(\w+)\s+numeric\(", re.M)
_TABLE = re.compile(r"^CREATE TABLE public\.(\w+)", re.M)


def _schema_numeric_columns():
    """Every `numeric` column in schema.sql, as {table}.{column}."""
    text = SCHEMA.read_text()
    found = set()
    for match in _TABLE.finditer(text):
        table = match.group(1)
        body = text[match.end():]
        end = body.find(");")
        for column in _NUMERIC.finditer(body[:end]):
            found.add(f"{table}.{column.group(1)}")
    return found


@pytest.mark.skipif(not SCHEMA.exists(), reason=_NOT_IN_IMAGE)
def test_every_numeric_money_column_is_covered():
    """A numeric column added later must be added to COLUMNS or fail here.

    The point of deriving this from schema.sql rather than trusting the list: a
    hand-maintained list only ever fails for what someone remembered to add to
    it, so it goes quieter as the schema grows instead of going red.
    """
    from_schema = _schema_numeric_columns()
    assert len(from_schema) > 5, (
        f"only parsed {len(from_schema)} numeric columns out of schema.sql — the "
        "regex is broken, not the schema. Without this floor the comparison "
        "below would pass vacuously."
    )

    covered = {f"{table}.{column}" for table, _, column, _, _ in COLUMNS}

    assert from_schema == covered, (
        "schema.sql and this file's COLUMNS list disagree.\n"
        f"  in schema.sql but untested: {sorted(from_schema - covered)}\n"
        f"  tested but not in schema.sql: {sorted(covered - from_schema)}\n"
        "A new money column needs a row in COLUMNS and a CHECK in a migration."
    )
