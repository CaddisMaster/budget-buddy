"""`db_cursor()` reuses connections, and a reused one carries nothing over (#401).

Every test here first shows the SAME backend served both blocks
(`pg_backend_pid()`); without that, the "carries nothing over" tests would pass
trivially against fresh connections and prove nothing about the pool.
"""
import threading

import psycopg2
import pytest

from app.db import POOL_IDLE_MAX, db_cursor


def _pid(cur):
    cur.execute("SELECT pg_backend_pid() AS pid")
    return cur.fetchone().pid


@pytest.mark.criterion(401, "Consecutive requests reuse a connection instead of opening a new one")
def test_consecutive_blocks_reuse_one_connection():
    with db_cursor() as cur:
        first = _pid(cur)
    with db_cursor() as cur:
        second = _pid(cur)
    assert second == first, "the second block opened a new connection"


def test_a_block_that_does_not_commit_leaks_nothing_to_the_next(users):
    """The clean path: a `db_cursor()` without commit=True never ends its
    transaction. Kept as-is, the next borrower would run inside it and see its
    uncommitted row."""
    with db_cursor() as cur:
        pid = _pid(cur)
        cur.execute(
            "INSERT INTO categories (name, user_id) VALUES ('pool-leak', %s)",
            (users["a"]["id"],))
    with db_cursor() as cur:
        assert _pid(cur) == pid, "not the same connection, so this proves nothing"
        cur.execute(
            "SELECT COUNT(*) AS n FROM categories WHERE name = 'pool-leak' AND user_id = %s",
            (users["a"]["id"],))
        assert cur.fetchone().n == 0, "an uncommitted write reached the next borrower"


@pytest.mark.criterion(401, "A failed write does not leave a broken transaction for the next borrower")
def test_a_swallowed_error_does_not_poison_the_next_borrower():
    """A block that catches its own database error exits cleanly, with its
    transaction ABORTED. Kept as-is, every statement from the next borrower
    would fail with InFailedSqlTransaction."""
    with db_cursor() as cur:
        pid = _pid(cur)
        try:
            cur.execute("SELECT no_such_column FROM users")
        except psycopg2.Error:
            pass
    with db_cursor() as cur:
        assert _pid(cur) == pid, "not the same connection, so this proves nothing"
        cur.execute("SELECT 1 AS one")
        assert cur.fetchone().one == 1


@pytest.mark.criterion(401, "Concurrent due-runner calls still hold separate connections and post exactly once")
def test_concurrent_borrowers_hold_separate_connections():
    """More simultaneous borrowers than the pool keeps idle: each must hold its
    own connection (a shared one would interleave their transactions, which is
    what FOR UPDATE relies on not happening), and none may be refused.
    Posting exactly once is the race scenarios' job in
    schedule_materialization.feature."""
    n = POOL_IDLE_MAX + 3
    barrier = threading.Barrier(n)
    pids, errors = [], []
    lock = threading.Lock()

    def hold():
        try:
            with db_cursor() as cur:
                pid = _pid(cur)
                barrier.wait(timeout=10)  # every thread holds its connection here
                with lock:
                    pids.append(pid)
        except Exception as e:  # pragma: no cover - surfaced via the assert
            errors.append(e)

    threads = [threading.Thread(target=hold) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(set(pids)) == n, f"{n} concurrent borrowers shared {len(set(pids))} connections"
