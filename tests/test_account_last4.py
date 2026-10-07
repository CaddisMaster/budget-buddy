"""#471 — sql/41: accounts gain `number_last4`, unique per user.

The schema half of #461 (work out which account a statement belongs to),
split out so the migration stands alone. Nothing in the app reads or writes
the column yet; these tests pin what the database itself promises, so #461 can
build on it.

The literal digits "1234" are safe under xdist: the unique index is scoped by
user, and every worker's `users` fixture creates its own users.

⚠️ `test_the_migration_matches_the_fresh_schema` runs the REAL migration file
against a temporary pre-41 table inside one transaction that is rolled back
(the device #438's and #444's tests use). The temp table lives only in that
transaction: `db_cursor()` connections are pooled, so no session state may
survive it.
"""

import re
from pathlib import Path

import psycopg2
import pytest

from app.db import db_cursor
from tests.helpers import create_account

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = REPO_ROOT / "sql" / "41_account_number_last4.sql"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"


def _set_last4(account_id, digits):
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE account SET number_last4 = %s WHERE account_id = %s",
                    (digits, account_id))


def _last4(account_id):
    with db_cursor() as cur:
        cur.execute("SELECT number_last4 FROM account WHERE account_id = %s", (account_id,))
        return cur.fetchone().number_last4


@pytest.mark.criterion(471, "Existing and new accounts carry no digits")
def test_an_account_added_by_hand_carries_no_digits(client_a, users):
    resp = client_a.post("/accounts", data={"name": "Brand new", "type": "Bank Account"})
    assert resp.status_code in (200, 302)
    with db_cursor() as cur:
        cur.execute("SELECT number_last4 FROM account WHERE user_id = %s AND account_name = %s",
                    (users["a"]["id"], "Brand new"))
        assert cur.fetchone().number_last4 is None
    assert _last4(users["a"]["account_id"]) is None   # the fixture's existing account


@pytest.mark.criterion(471, "Only four digits are stored")
@pytest.mark.parametrize("digits", ["12345", "12a4", "123", "", " 123", "１２３４"])
def test_only_four_digits_are_stored(users, digits):
    account = users["a"]["account_id"]
    _set_last4(account, "0042")          # positive control: four digits are legal
    assert _last4(account) == "0042"
    with pytest.raises((psycopg2.errors.CheckViolation,
                        psycopg2.errors.StringDataRightTruncation)):
        _set_last4(account, digits)


@pytest.mark.criterion(471, "Two of my accounts cannot claim the same digits")
def test_two_of_my_accounts_cannot_share_digits(users):
    a = users["a"]
    discover = create_account(a["id"], "Discover", "Credit Card")
    _set_last4(a["account_id"], "1234")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        _set_last4(discover, "1234")


@pytest.mark.criterion(471, "Another user's account may end in the same digits")
def test_another_users_account_may_share_digits(users):
    _set_last4(users["a"]["account_id"], "1234")
    _set_last4(users["b"]["account_id"], "1234")
    assert _last4(users["b"]["account_id"]) == "1234"


def test_any_number_of_accounts_may_have_no_digits(users):
    """The index is partial: every account without digits coexists."""
    a = users["a"]
    create_account(a["id"], "One")
    create_account(a["id"], "Two")
    with db_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM account "
                    "WHERE user_id = %s AND number_last4 IS NULL", (a["id"],))
        assert cur.fetchone().n >= 3


def _migration_statements():
    """sql/41's statements, pointed at a temp table and without BEGIN/COMMIT
    (the test owns the transaction, so it can roll back)."""
    body = "\n".join(line for line in MIGRATION.read_text().splitlines()
                     if not line.lstrip().startswith("--"))
    body = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", body, flags=re.M)
    assert "public.account" in body, "the migration no longer names its table"
    return body.replace("public.account", "pg_temp.account")


def _shape(cur, schema):
    """The column, its CHECK and the index on it, schema qualifiers removed."""
    cur.execute("""
        SELECT data_type, character_maximum_length AS max_len, is_nullable,
               column_default
        FROM information_schema.columns
        WHERE table_name = 'account' AND column_name = 'number_last4'
          AND table_schema = %s
    """, (schema,))
    column = cur.fetchone()
    cur.execute("""
        SELECT pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = 'account' AND n.nspname = %s
          AND c.conname = 'account_number_last4_is_digits'
    """, (schema,))
    check = cur.fetchone()
    cur.execute("""
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'account' AND indexname = 'account_number_last4_uniq'
          AND schemaname = %s
    """, (schema,))
    index = cur.fetchone()
    assert None not in (column, check, index), f"nothing found in {schema}"
    return (tuple(column), check.definition,
            re.sub(r"ON \S*account", "ON account", index.indexdef))


@pytest.mark.skipif(not MIGRATION.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(471, "The migration and the fresh schema agree")
def test_the_migration_matches_the_fresh_schema():
    with db_cursor() as cur:  # never commits: db_cursor rolls back on return
        fresh = _shape(cur, "public")

        # Enough of the pre-41 table for the migration to apply to.
        cur.execute("""
            CREATE TEMP TABLE account (
                account_id SERIAL PRIMARY KEY,
                user_id integer NOT NULL
            ) ON COMMIT DROP
        """)
        cur.execute(_migration_statements())
        # Re-running is a no-op, not an error (IF NOT EXISTS).
        cur.execute(_migration_statements())

        cur.execute("SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema()")
        migrated = _shape(cur, cur.fetchone().nspname)

    assert migrated == fresh
