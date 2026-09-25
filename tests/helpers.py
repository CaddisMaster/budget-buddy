"""Plain test helpers — database setup, teardown and lookups, with no pytest in them.

Split out of `conftest.py` for #356 so that behave's `environment.py` can share
them with pytest. `conftest.py` keeps what is pytest's own: the per-worker
`TEST_PREFIX` and the usernames built from it, and the seven fixtures. Behave
builds its own prefix, and these functions take the username as an argument,
so neither runner can reach the other's rows through this module.

⚠️ Nothing here may import pytest or read `TEST_PREFIX`. A helper that needs
a user takes the username; that is what keeps the module runner-neutral.

Teardown deletes child rows explicitly before the user row: the user_id FKs
cascade, but transactions->categories / transactions->account use ON DELETE
RESTRICT, so letting the user-cascade fire first can hit a RESTRICT violation.
"""
import time

import psycopg2

from app import bcrypt
from app.db import get_db_connection

PASSWORD = "test-password-123"

# bcrypt cost for test users (#384). The app hashes at Flask-Bcrypt's default
# of 12, ~0.18s a hash; at 4 it is ~0.001s, and every pytest `users` fixture and
# every behave scenario creates two users. ⚠️ Passed per call, not via
# `BCRYPT_LOG_ROUNDS`: Flask-Bcrypt reads that once in `init_app`, at import, so
# setting it from a fixture is a silent no-op. Login is unaffected, because
# `check_password_hash` reads the cost from the stored hash.
TEST_BCRYPT_ROUNDS = 4


def _wait_for_db(attempts=10, delay=1.0):
    """The db container may still be starting when `docker compose run` fires;
    retry a trivial connection until it answers (or give up and let the test
    error loudly)."""
    last_err = None
    for _ in range(attempts):
        try:
            conn = get_db_connection()
            conn.close()
            return
        except psycopg2.OperationalError as err:
            last_err = err
            time.sleep(delay)
    raise RuntimeError(f"Database not reachable for tests: {last_err}")


def refuse_a_database_that_holds_users():
    """Raise unless the `users` table is empty — call once, before any test runs.

    ⚠️ The suite runs against its own database (#400): `test.sh` rebuilds it from
    `sql/schema.sql` before each runner, and CI loads a fresh one. Both start
    with no users. So a user present at the start means this is somebody's REAL
    database — the dev one, most likely, because the process inherited the dev
    server's `DB_NAME` — and a run there would sweep its users: anything that
    iterates every user (`materialize_all_users()`) posts into their ledgers.

    Checked by emptiness rather than by name, because CI's database and the dev
    one are both called `budget`."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_database(), COUNT(*) FROM users")
        dbname, count = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if count:
        raise RuntimeError(
            f"Refusing to run the tests: database {dbname!r} already holds "
            f"{count} user(s), so it is not a fresh test database. Run the suite "
            "through ./test.sh, which builds one (#400).")


def _create_user(username, password, is_admin=False):
    conn = get_db_connection()
    cur = conn.cursor()
    pw_hash = bcrypt.generate_password_hash(password, rounds=TEST_BCRYPT_ROUNDS).decode("utf-8")
    cur.execute(
        "INSERT INTO users (username, password_hash, is_admin) "
        "VALUES (%s, %s, %s) RETURNING id",
        (username, pw_hash, is_admin),
    )
    user_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return user_id


def _seed_basic_data(user_id, label):
    """Give a user one category, one account, and one transaction, all tagged
    with `label` so a listing can be checked for the right owner's rows."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO categories (name, user_id) VALUES (%s, %s) RETURNING id",
        (f"cat-{label}", user_id),
    )
    category_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO account (account_name, type, user_id) "
        "VALUES (%s, %s, %s) RETURNING account_id",
        (f"acct-{label}", "bank", user_id),
    )
    account_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO transactions "
        "(amount, description, category_id, account_id, transaction_type, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (42.50, f"txn-{label}", category_id, account_id, "expense", user_id),
    )
    transaction_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {
        "category_id": category_id,
        "account_id": account_id,
        "transaction_id": transaction_id,
    }


def _delete_user(username):
    """Remove a test user and all of its data in FK-safe order."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    if row:
        user_id = row[0]
        cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM budget_history WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM budgets WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM goals WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM schedules WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM transfer_schedules WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM insights WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM agent_runs WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM push_subscriptions WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM reminder_log WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM categories WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM account WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    cur.close()
    conn.close()


def _login(client, username, password=PASSWORD):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def fetch_transaction(transaction_id):
    """Read a transaction straight from the DB (bypassing the app) so isolation
    tests can confirm what actually changed."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount, description, user_id FROM transactions WHERE id = %s",
        (transaction_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def fetch_category(category_id):
    """Return (name, description, user_id) for a category, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT name, description, user_id FROM categories WHERE id = %s",
        (category_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def fetch_account(account_id):
    """Return (account_name, type, user_id) for an account, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT account_name, type, user_id FROM account WHERE account_id = %s",
        (account_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def fetch_budget(budget_id):
    """Return (category_id, amount, user_id) for a budget, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT category_id, amount, user_id FROM budgets WHERE id = %s",
        (budget_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def fetch_category_kind(category_id):
    """Return a category's kind straight from the DB, or None if missing."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT kind FROM categories WHERE id = %s", (category_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def find_category_id(user_id, name):
    """Look up a user's category id by name (CRUD tests create via the app,
    then need the generated id to verify/clean up)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM categories WHERE user_id = %s AND name = %s",
        (user_id, name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def create_category(user_id, name, kind="expense"):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO categories (name, kind, user_id) VALUES (%s, %s, %s) RETURNING id",
        (name, kind, user_id),
    )
    cid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return cid


def create_budget(user_id, category_id, amount):
    """Insert a monthly budget override (one row per user+category)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO budgets (category_id, amount, user_id) "
        "VALUES (%s, %s, %s) RETURNING id",
        (category_id, amount, user_id),
    )
    bid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return bid


def fetch_budget_history(user_id, category_id):
    """Return [(amount, changed_at), ...] oldest-first from the v10.9
    append-only budget_history log (amount None = a recorded clear)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount, changed_at FROM budget_history "
        "WHERE user_id = %s AND category_id = %s ORDER BY id",
        (user_id, category_id),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def fetch_budget_by_category(user_id, category_id):
    """Return (id, amount) for a user's budget in a category, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, amount FROM budgets WHERE user_id = %s AND category_id = %s",
        (user_id, category_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def create_transaction(user_id, account_id, amount, transaction_date,
                       transaction_type="expense", category_id=None,
                       is_adjustment=False):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions "
        "(amount, description, category_id, account_id, transaction_date, "
        " transaction_type, is_adjustment, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (amount, "seed", category_id, account_id, transaction_date,
         transaction_type, is_adjustment, user_id),
    )
    tid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return tid


def create_account(user_id, name, account_type="Bank Account",
                   credit_limit=None, apr=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO account (account_name, type, credit_limit, apr, user_id) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING account_id",
        (name, account_type, credit_limit, apr, user_id),
    )
    aid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return aid


def create_transfer(user_id, from_account, to_account, amount, transfer_date):
    """Insert a transfer the same way the app does: a linked expense/income pair
    sharing a transfer_group_id. Returns the group id."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT nextval('transfer_group_seq')")
    gid = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO transactions (amount, description, transaction_date, "
        "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
        "VALUES (%s, 'seed-transfer', %s, %s, 'expense', true, %s, %s)",
        (amount, transfer_date, from_account, gid, user_id),
    )
    cur.execute(
        "INSERT INTO transactions (amount, description, transaction_date, "
        "account_id, transaction_type, is_transfer, transfer_group_id, user_id) "
        "VALUES (%s, 'seed-transfer', %s, %s, 'income', true, %s, %s)",
        (amount, transfer_date, to_account, gid, user_id),
    )
    conn.commit()
    cur.close()
    conn.close()
    return gid


def create_goal(user_id, account_id, target_amount, target_date=None, baseline=0,
                goal_type="save"):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO goals (name, target_amount, target_date, account_id, "
        "baseline_amount, goal_type, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        ("seed-goal", target_amount, target_date, account_id, baseline, goal_type,
         user_id),
    )
    gid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return gid


def create_schedule(user_id, account_id, amount, frequency, next_due,
                    transaction_type="expense", category_id=None,
                    anchor_day=None, second_day=None, is_active=True,
                    end_date=None, is_variable_amount=False,
                    description="seed-schedule"):
    """Insert a schedule template directly (bypassing the form).

    `description` is overridable because #191's notification quotes it, so a test
    asserting on the payload needs a name of its own rather than the shared
    'seed-schedule' every other test counts.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO schedules (amount, description, category_id, account_id, "
        "transaction_type, frequency, anchor_day, second_day, next_due, end_date, "
        "is_active, is_variable_amount, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id",
        (amount, description, category_id, account_id, transaction_type, frequency,
         anchor_day, second_day, next_due, end_date, is_active, is_variable_amount,
         user_id),
    )
    sid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return sid


def fetch_schedule(schedule_id):
    """Return (amount, frequency, next_due, user_id) for a schedule, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount, frequency, next_due, user_id FROM schedules WHERE id = %s",
        (schedule_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def create_transfer_schedule(user_id, from_account, to_account, amount, frequency,
                             next_due, anchor_day=None, second_day=None,
                             is_active=True, description="seed-auto-transfer",
                             end_date=None):
    """Insert a recurring-transfer template directly (bypassing the form)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transfer_schedules (amount, description, from_account_id, "
        "to_account_id, frequency, anchor_day, second_day, next_due, end_date, "
        "is_active, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (amount, description, from_account, to_account, frequency, anchor_day,
         second_day, next_due, end_date, is_active, user_id),
    )
    tsid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return tsid


def fetch_transfer_schedule(schedule_id):
    """Return (amount, frequency, next_due, from_account_id, to_account_id,
    user_id) for a recurring transfer, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount, frequency, next_due, from_account_id, to_account_id, "
        "user_id FROM transfer_schedules WHERE id = %s",
        (schedule_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def count_transfer_schedules(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM transfer_schedules WHERE user_id = %s", (user_id,)
    )
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def count_transactions_like(user_id, description):
    """Count a user's transactions with the given description (to verify
    schedule-generated rows)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM transactions WHERE user_id = %s AND description = %s",
        (user_id, description),
    )
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def count_posted_from_schedules(owner_id):
    """(posted, owned): transactions posted from `owner_id`'s schedules, found
    by `schedule_id` whoever's ledger they landed in, and how many of those
    landed in the owner's own.

    ⚠️ Counting by the transaction's `user_id` alone cannot see a runner that
    posts one user's schedule under another user's id: the owner's count
    stays 0, which is exactly what "never posts another user's schedule"
    expects (#395). `owned` keeps the check `count_transactions_like` made."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE t.user_id = s.user_id) "
        "FROM transactions t JOIN schedules s ON s.id = t.schedule_id "
        "WHERE s.user_id = %s",
        (owner_id,),
    )
    posted, owned = cur.fetchone()
    cur.close()
    conn.close()
    return posted, owned


def account_balance(account_id):
    """Net income − expense for an account, straight from the DB (for asserts)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
        "THEN amount ELSE -amount END), 0) "
        "FROM transactions WHERE account_id = %s",
        (account_id,),
    )
    bal = cur.fetchone()[0]
    cur.close()
    conn.close()
    return float(bal)


def count_transfer_legs(group_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM transactions WHERE transfer_group_id = %s",
        (group_id,),
    )
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def create_insight(user_id, year, month, content, model="claude-haiku-4-5"):
    """Insert a cached insight row directly (bypassing the model/route)."""
    import json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO insights (user_id, year, month, content, model) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (user_id, year, month, json.dumps(content), model),
    )
    iid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return iid


def fetch_insight(user_id, year, month):
    """Return (content, model, user_id) for a cached insight, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT content, model, user_id FROM insights "
        "WHERE user_id = %s AND year = %s AND month = %s",
        (user_id, year, month),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def create_agent_run(user_id, period_start, content, model="claude-sonnet-4-6"):
    """Insert a cached money-agent run directly (bypassing the model/route)."""
    import json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agent_runs (user_id, period_start, content, model) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, period_start, json.dumps(content), model),
    )
    rid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return rid


def fetch_agent_runs(user_id):
    """Return [(period_start, content, model), ...] for a user's cached runs,
    oldest week first."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT period_start, content, model FROM agent_runs "
        "WHERE user_id = %s ORDER BY period_start",
        (user_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def fetch_goal(goal_id):
    """Return (name, target_amount, account_id, user_id, goal_type,
    baseline_amount, target_date) for a goal, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT name, target_amount, account_id, user_id, goal_type, "
        "baseline_amount, target_date FROM goals WHERE id = %s",
        (goal_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row
