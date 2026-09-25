import os
import threading
from contextlib import contextmanager

import psycopg2
from psycopg2.extensions import TRANSACTION_STATUS_UNKNOWN
from psycopg2.extras import NamedTupleCursor

# The most IDLE connections one process keeps for reuse (#401): gunicorn's 4
# threads. A borrower who finds none idle opens a fresh one, exactly as every
# borrower did before the pool; it is kept on return only while fewer than this
# many are idle, and closed otherwise. So concurrency is never refused, and the
# connections held open are bounded — which matters under xdist, where ten
# worker processes each keep their own pool against Postgres's default limit
# of 100 connections.
POOL_IDLE_MAX = 4

_idle = []
_idle_pid = None
_pool_lock = threading.Lock()


def _connect_kwargs():
    return {
        'host': os.getenv('DB_HOST'),
        'port': os.getenv('DB_PORT'),
        'dbname': os.getenv('DB_NAME'),
        'user': os.getenv('DB_APP_USER') or os.getenv('DB_USER'),
        'password': os.getenv('DB_APP_PASSWORD') or os.getenv('DB_PASSWORD'),
    }


def get_db_connection():
    """Connect as the least-privileged role available.

    DB_APP_USER is the `budget_app` role: DML only, no DDL, not a superuser
    (sql/30_app_role.sql). It falls back to DB_USER — which compose also uses
    as POSTGRES_USER, i.e. the superuser — so an environment that has not been
    migrated yet keeps working exactly as before. The fallback is what makes
    adopting the role a deliberate change to .env rather than a breaking one.

    pg_dump in /admin/backup deliberately keeps using DB_USER: a dump taken by
    a non-owner is not reliably complete.
    """
    return psycopg2.connect(**_connect_kwargs())


def borrow_connection():
    """An idle pooled connection, or a fresh one when none is idle.

    ⚠️ Keyed on the pid, because a connection must never cross a fork: a child
    inheriting its parent's idle list would share the parent's sockets, and two
    processes talking over one Postgres connection corrupt it. A child sees a
    different pid and starts an empty list; the inherited connections are
    abandoned, not closed, since closing them would close the parent's too."""
    global _idle, _idle_pid
    pid = os.getpid()
    with _pool_lock:
        if _idle_pid != pid:
            _idle, _idle_pid = [], pid
        while _idle:
            conn = _idle.pop()
            if not conn.closed:
                return conn
    return get_db_connection()


def return_connection(conn):
    """Return a connection with NO transaction left open, or close it.

    ⚠️ The rollback is load-bearing, and it runs on the clean path too. A
    `db_cursor()` without commit=True never ends its transaction — psycopg2
    opens one implicitly on the first statement, and before the pool, closing
    the connection ended it. Kept without a rollback, the next borrower would
    run inside the previous one's transaction: seeing its uncommitted writes,
    holding its FOR UPDATE locks, or failing on an aborted transaction it never
    started. A connection that cannot be rolled back is closed, not kept."""
    keep = not conn.closed and conn.info.transaction_status != TRANSACTION_STATUS_UNKNOWN
    if keep:
        try:
            conn.rollback()
        except psycopg2.Error:
            keep = False
    if keep:
        with _pool_lock:
            if _idle_pid == os.getpid() and len(_idle) < POOL_IDLE_MAX:
                _idle.append(conn)
                return
    conn.close()


@contextmanager
def db_cursor(commit=False):
    """Yield a NamedTupleCursor on a pooled connection, then always return it.

    Rows are namedtuples — read `row.amount` (positional `row[1]` still works).
    Every SELECT column therefore needs a unique, valid-identifier name; alias
    expressions and duplicate JOIN columns with AS. Pass commit=True for write
    paths (commits on a clean exit). Any exception rolls back and re-raises,
    so callers can `flash` the error as before.

    The connection comes from this process's pool (#401) and goes back to it
    with any transaction rolled back — see `return_connection`.
    """
    conn = borrow_connection()
    cursor = conn.cursor(cursor_factory=NamedTupleCursor)
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except psycopg2.Error:
            pass  # a dead connection; return_connection closes it
        raise
    finally:
        cursor.close()
        return_connection(conn)
