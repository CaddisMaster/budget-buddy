"""#444 — sql/40: transactions gain `import_ref`, unique per (user, account).

The schema half of #445 (statement import), split out so the migration stands
alone. Nothing in the app reads or writes the column yet; these tests pin what
the database itself promises, so #445 can build on it.

The literal reference "FIT-1" is safe under xdist: the unique index is scoped
by account, and every worker's `users` fixture creates its own accounts.

⚠️ `test_the_migration_matches_the_fresh_schema` runs the REAL migration file
against a temporary pre-40 table inside one transaction that is rolled back
(the device #438's test uses). The temp table lives only in that transaction:
`db_cursor()` connections are pooled, so no session state may survive it.
"""

import re
from pathlib import Path

import psycopg2
import pytest

from app.db import db_cursor
from tests.helpers import create_account

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = REPO_ROOT / "sql" / "40_transactions_import_ref.sql"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"


def _insert(user_id, account_id, import_ref):
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO transactions (amount, description, transaction_date, "
            "account_id, transaction_type, import_ref, user_id) "
            "VALUES (10, 'imported', CURRENT_DATE, %s, 'expense', %s, %s) RETURNING id",
            (account_id, import_ref, user_id))
        return cur.fetchone().id


def _refs(user_id):
    with db_cursor() as cur:
        cur.execute("SELECT account_id, import_ref FROM transactions "
                    "WHERE user_id = %s AND import_ref IS NOT NULL ORDER BY id",
                    (user_id,))
        return [(r.account_id, r.import_ref) for r in cur.fetchall()]


@pytest.mark.criterion(444, "Existing and hand-entered rows carry no reference")
def test_a_hand_entered_transaction_carries_no_reference(client_a, users):
    a = users["a"]
    resp = client_a.post("/transactions/new", data={
        "amount": "12.34", "description": "by hand", "transaction_type": "expense",
        "transaction_date": "2026-09-01", "account_id": a["account_id"],
        "category_id": a["category_id"]}, follow_redirects=False)
    assert resp.status_code in (302, 303), resp.status_code

    with db_cursor() as cur:
        cur.execute("SELECT import_ref FROM transactions "
                    "WHERE user_id = %s AND description = 'by hand'", (a["id"],))
        rows = cur.fetchall()
    # Presence first, so the NULL check below cannot pass on zero rows.
    assert len(rows) == 1
    assert rows[0].import_ref is None


@pytest.mark.criterion(444, "The same reference cannot be imported twice into one account")
def test_the_same_reference_twice_in_one_account_is_refused(users):
    a = users["a"]
    _insert(a["id"], a["account_id"], "FIT-1")

    with pytest.raises(psycopg2.errors.UniqueViolation):
        _insert(a["id"], a["account_id"], "FIT-1")
    assert _refs(a["id"]) == [(a["account_id"], "FIT-1")]


@pytest.mark.criterion(444, "The same reference may appear in a different account")
def test_the_same_reference_in_another_account_is_stored(users):
    a = users["a"]
    visa = create_account(a["id"], "Visa", "Credit Card")
    _insert(a["id"], a["account_id"], "FIT-1")
    _insert(a["id"], visa, "FIT-1")
    assert _refs(a["id"]) == [(a["account_id"], "FIT-1"), (visa, "FIT-1")]


def test_another_user_may_hold_the_same_reference(users):
    """Two people can bank at the same bank; one's import never blocks the other's.
    Holds without user_id in the index, because each user's accounts are their own."""
    _insert(users["a"]["id"], users["a"]["account_id"], "FIT-1")
    _insert(users["b"]["id"], users["b"]["account_id"], "FIT-1")
    assert _refs(users["b"]["id"]) == [(users["b"]["account_id"], "FIT-1")]


def test_rows_without_a_reference_are_not_constrained(users):
    """The index is partial: any number of hand-entered rows coexist."""
    a = users["a"]
    _insert(a["id"], a["account_id"], None)
    _insert(a["id"], a["account_id"], None)


def test_a_reference_is_bounded(users):
    a = users["a"]
    # Positive control: exactly 255 is legal, so the refusal is the bound.
    _insert(a["id"], a["account_id"], "x" * 255)
    with pytest.raises(psycopg2.errors.StringDataRightTruncation):
        _insert(a["id"], a["account_id"], "y" * 256)


def _migration_statements():
    """sql/40's statements, pointed at a temp table and without BEGIN/COMMIT
    (the test owns the transaction, so it can roll back)."""
    body = "\n".join(line for line in MIGRATION.read_text().splitlines()
                     if not line.lstrip().startswith("--"))
    body = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", body, flags=re.M)
    assert "public.transactions" in body, "the migration no longer names its table"
    return body.replace("public.transactions", "pg_temp.transactions")


def _shape(cur, schema):
    """The import_ref column and the index on it, schema-qualifiers removed."""
    cur.execute("""
        SELECT data_type, character_maximum_length AS max_len, is_nullable,
               column_default
        FROM information_schema.columns
        WHERE table_name = 'transactions' AND column_name = 'import_ref'
          AND table_schema = %s
    """, (schema,))
    column = cur.fetchone()
    cur.execute("""
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'transactions' AND indexname = 'transactions_import_ref_uniq'
          AND schemaname = %s
    """, (schema,))
    index = cur.fetchone()
    assert column is not None and index is not None, f"nothing found in {schema}"
    return tuple(column), re.sub(r"ON \S*transactions", "ON transactions", index.indexdef)


@pytest.mark.skipif(not MIGRATION.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(444, "The migration and the fresh schema agree")
def test_the_migration_matches_the_fresh_schema():
    with db_cursor() as cur:  # never commits: db_cursor rolls back on return
        fresh = _shape(cur, "public")

        # Enough of the pre-40 table for the migration to apply to.
        cur.execute("""
            CREATE TEMP TABLE transactions (
                id SERIAL PRIMARY KEY,
                account_id integer,
                user_id integer NOT NULL
            ) ON COMMIT DROP
        """)
        cur.execute(_migration_statements())
        # Re-running is a no-op, not an error (IF NOT EXISTS).
        cur.execute(_migration_statements())

        cur.execute("SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema()")
        migrated = _shape(cur, cur.fetchone().nspname)

    assert migrated == fresh
