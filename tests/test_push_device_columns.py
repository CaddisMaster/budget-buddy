"""#438 — sql/39: push_subscriptions gains `device_label` and `last_seen_at`.

The schema half of #437, split out so the migration stands alone. Nothing in
the app reads either column yet; these tests pin what the database itself
promises, so #437 can build on it.

⚠️ Endpoints are built from `TEST_PREFIX`: `push_subscriptions.endpoint` is
globally UNIQUE, so a shared literal would be one row shared between xdist
workers (see docs/testing.md).

⚠️ `test_rows_that_predate_the_migration_stay_null` runs the REAL migration
file against a temporary copy of the pre-39 table, inside one transaction that
is rolled back. It is the test that fails if someone "simplifies" the file into
`ADD COLUMN last_seen_at ... DEFAULT now()`, which stamps every existing row
with the moment the migration ran. The temp table lives only in that
transaction: `db_cursor()` connections are pooled and outlive the block, so no
session state may survive it.
"""

import re
from pathlib import Path

import psycopg2
import pytest

from app.db import db_cursor
from tests.conftest import TEST_PREFIX

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = REPO_ROOT / "sql" / "39_push_device_columns.sql"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"


def _endpoint(name):
    return f"https://push.example/{TEST_PREFIX}device-columns-{name}"


def _insert_like_the_running_image(user_id, endpoint):
    """The INSERT `/push/subscribe` makes today, naming neither new column —
    which is exactly what the pre-#437 image keeps doing after sql/39 lands."""
    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
            "VALUES (%s, %s, 'p256dh-x', 'auth-x') RETURNING id",
            (user_id, endpoint))
        return cur.fetchone().id


def _row(sub_id):
    with db_cursor() as cur:
        cur.execute(
            "SELECT device_label, last_seen_at FROM push_subscriptions WHERE id = %s",
            (sub_id,))
        return cur.fetchone()


@pytest.mark.criterion(438, "A new subscription records when it was last seen")
def test_a_new_subscription_records_when_it_was_last_seen(users):
    sub_id = _insert_like_the_running_image(users["a"]["id"], _endpoint("new"))

    row = _row(sub_id)
    assert row.last_seen_at is not None
    assert row.device_label is None


@pytest.mark.criterion(438, "A device label is bounded")
def test_a_device_label_is_bounded(users):
    sub_id = _insert_like_the_running_image(users["a"]["id"], _endpoint("bounded"))

    # Positive control first: exactly 64 is legal, so the refusal below can only
    # be the length limit and not, say, a typo'd column name.
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE push_subscriptions SET device_label = %s WHERE id = %s",
                    ("x" * 64, sub_id))
    assert _row(sub_id).device_label == "x" * 64

    with pytest.raises(psycopg2.errors.StringDataRightTruncation):
        with db_cursor(commit=True) as cur:
            cur.execute("UPDATE push_subscriptions SET device_label = %s WHERE id = %s",
                        ("x" * 65, sub_id))
    assert _row(sub_id).device_label == "x" * 64


def _migration_statements():
    """sql/39's statements, pointed at a temp table and without its own
    BEGIN/COMMIT (the test owns the transaction, so it can roll back)."""
    text = MIGRATION.read_text()
    body = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("--"))
    body = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", body, flags=re.M)
    assert "public.push_subscriptions" in body, "the migration no longer names its table"
    return body.replace("public.push_subscriptions", "pg_temp.push_subscriptions")


@pytest.mark.skipif(not MIGRATION.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(438, "Existing subscriptions are not given a made-up history")
def test_rows_that_predate_the_migration_stay_null():
    with db_cursor() as cur:  # never commits: db_cursor rolls back on return
        # The table exactly as it stood before sql/39.
        cur.execute("""
            CREATE TEMP TABLE push_subscriptions (
                id SERIAL PRIMARY KEY,
                user_id integer NOT NULL,
                endpoint text NOT NULL UNIQUE,
                p256dh text NOT NULL,
                auth text NOT NULL,
                created_at timestamp without time zone DEFAULT now()
            ) ON COMMIT DROP
        """)
        cur.execute("INSERT INTO pg_temp.push_subscriptions (user_id, endpoint, p256dh, auth) "
                    "VALUES (1, 'old', 'p', 'a')")

        cur.execute(_migration_statements())
        # Re-running is a no-op, not an error (IF NOT EXISTS).
        cur.execute(_migration_statements())

        cur.execute("INSERT INTO pg_temp.push_subscriptions (user_id, endpoint, p256dh, auth) "
                    "VALUES (1, 'new', 'p', 'a')")
        cur.execute("SELECT endpoint, device_label, last_seen_at "
                    "FROM pg_temp.push_subscriptions ORDER BY id")
        old, new = cur.fetchall()

    assert old.device_label is None
    assert old.last_seen_at is None, (
        "sql/39 stamped a pre-existing row with a last_seen_at — that invents a "
        "history nobody observed. Add the column bare, then SET DEFAULT.")
    assert new.last_seen_at is not None, "rows inserted after sql/39 must get now()"
