"""#151 — when each scheduled job last actually finished.

The gap this closes: /settings could report that the scheduler was switched ON,
by reading its env var, but not that any job had run. Since #33 those are
different questions — the daily job materializes the ledger for every user and is
gated on nothing, so a dead thread means recurring transactions silently stop
appearing while the env var still reads '1' and /healthz stays green.

⚠️ **job_runs.job_name is GLOBALLY UNIQUE and the table has no user_id**, which
makes it the same trap as push_subscriptions.endpoint in #128. TEST_PREFIX
protects rows scoped by user; it does nothing for a column whose uniqueness spans
every xdist worker by definition. Two workers both writing 'daily_tasks' address
ONE row, and the ON CONFLICT DO UPDATE means the last writer wins mid-test.

So the split here is deliberate:

  * Anything asserting on an exact value writes a JOB() name — prefixed, and
    therefore this worker's own row.
  * The two tests that exercise the real writers (run_daily_tasks writes the real
    'daily_tasks' name; nothing can change that) assert only that a row exists
    and is RECENT. Both are true no matter which worker wrote last, so they
    cannot race.
  * Most coverage needs no database at all — summarize_job_runs is pure and takes
    its rows as an argument. Prefer that.

Every test sets the environment explicitly. The dev container may legitimately
carry ENABLE_DIGEST_SCHEDULER or a real RESEND_API_KEY, so a test that assumed
"unset by default" would pass or fail depending on whose machine it ran on — the
same reasoning as test_integration_status.py.
"""
from collections import namedtuple
from datetime import datetime, timedelta

import psycopg2
import pytest

from app.blueprints.reminders import run_daily_tasks
from app.db import db_cursor
from app.jobs import (
    DAILY,
    JOBS,
    NEVER,
    NOT_SCHEDULED,
    OK,
    STALE,
    WEEKLY_DIGEST,
    load_job_runs,
    record_job_run,
    summarize_job_runs,
)
from tests.conftest import TEST_PREFIX

NOW = datetime(2026, 8, 3, 18, 0, 0)

# The row shape load_job_runs returns (NamedTupleCursor). Built by hand here so
# the pure rules can be exercised without a database.
Run = namedtuple('Run', 'job_name last_run_at summary checked_at')

THRESHOLDS = {job_id: threshold for job_id, _n, _d, threshold in JOBS}
DAILY_THRESHOLD = THRESHOLDS[DAILY]
DIGEST_THRESHOLD = THRESHOLDS[WEEKLY_DIGEST]


def JOB(name):
    """A job name unique to this xdist worker. See the module docstring — the
    column is globally UNIQUE, so a shared literal is a shared row."""
    return f"{TEST_PREFIX}{name}"


def _row(job_name, ago, summary=None):
    return Run(job_name, NOW - ago, summary, NOW)


def _state(rows, name):
    (row,) = [r for r in rows if r["name"] == name]
    return row["state"]


DAILY_NAME, DIGEST_NAME = [display for _id, display, _d, _t in JOBS]


@pytest.fixture
def cleanup_jobs():
    """Delete only THIS worker's job rows, before and after. A blanket DELETE
    would take out a parallel worker's rows mid-test."""
    def _clear():
        with db_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM job_runs WHERE job_name LIKE %s",
                           (TEST_PREFIX + '%',))
    _clear()
    yield
    _clear()


# --- the pure rule ---------------------------------------------------------

def test_a_recent_run_is_on_schedule():
    rows = summarize_job_runs([_row(DAILY, timedelta(hours=2))],
                              scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    assert _state(rows, DAILY_NAME) == OK


def test_an_overdue_run_is_stale():
    rows = summarize_job_runs([_row(DAILY, DAILY_THRESHOLD + timedelta(minutes=1))],
                              scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    assert _state(rows, DAILY_NAME) == STALE


def test_the_threshold_boundary_is_not_yet_stale():
    # Exactly at the threshold is still OK — staleness is strictly greater-than,
    # so a job that runs every 24h to the second never flickers.
    rows = summarize_job_runs([_row(DAILY, DAILY_THRESHOLD)],
                              scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    assert _state(rows, DAILY_NAME) == OK


def test_a_registered_job_with_no_row_has_never_run():
    rows = summarize_job_runs([], scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    assert _state(rows, DAILY_NAME) == NEVER


def test_every_job_is_reported_even_with_no_rows():
    rows = summarize_job_runs([], scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    assert len(rows) == len(JOBS)


# --- the not_scheduled state: the panel must not cry wolf ------------------

def test_scheduler_off_means_nothing_is_overdue():
    # THE load-bearing one. Locally and in CI the scheduler does not run at all,
    # so "never run" is the CORRECT state and must not render as a fault. A panel
    # that reports a problem on every developer machine teaches its reader to
    # ignore it — the same failure mode the third integration state avoids.
    rows = summarize_job_runs([], scheduler_on=False, digest_registered=True,
                              checked_at=NOW)
    assert {r["state"] for r in rows} == {NOT_SCHEDULED}


def test_scheduler_off_overrides_an_ancient_row():
    ancient = _row(DAILY, timedelta(days=400))
    rows = summarize_job_runs([ancient], scheduler_on=False,
                              digest_registered=True, checked_at=NOW)
    assert _state(rows, DAILY_NAME) == NOT_SCHEDULED


def test_the_digest_is_not_scheduled_without_mail():
    # The digest job is registered only when mail_enabled(); the daily job always
    # is. So an unconfigured Resend key must silence the digest row WITHOUT
    # silencing the daily one.
    rows = summarize_job_runs([], scheduler_on=True, digest_registered=False,
                              checked_at=NOW)
    assert _state(rows, DIGEST_NAME) == NOT_SCHEDULED
    assert _state(rows, DAILY_NAME) == NEVER


def test_the_digest_keeps_its_own_wider_threshold():
    # A weekly job 3 days late is fine; a daily job 3 days late is not. One
    # shared threshold would make the digest permanently overdue.
    age = timedelta(days=3)
    rows = summarize_job_runs([_row(DAILY, age), _row(WEEKLY_DIGEST, age)],
                              scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    assert _state(rows, DAILY_NAME) == STALE
    assert _state(rows, DIGEST_NAME) == OK
    assert DIGEST_THRESHOLD > DAILY_THRESHOLD


def test_the_summary_is_carried_through():
    rows = summarize_job_runs([_row(DAILY, timedelta(hours=1), 'materialized 3 user(s)')],
                              scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    (daily,) = [r for r in rows if r["name"] == DAILY_NAME]
    assert daily["summary"] == 'materialized 3 user(s)'
    assert daily["last_run_at"] == NOW - timedelta(hours=1)


def test_an_unknown_job_name_is_ignored():
    # A row left behind by a renamed or removed job must not appear as a mystery
    # entry — JOBS is the display list, the table is just storage.
    rows = summarize_job_runs([_row('retired_job', timedelta(hours=1))],
                              scheduler_on=True, digest_registered=True,
                              checked_at=NOW)
    assert [r["name"] for r in rows] == [DAILY_NAME, DIGEST_NAME]


# --- the writer ------------------------------------------------------------

def test_recording_a_run_is_an_upsert_not_an_append(cleanup_jobs):
    name = JOB('upsert-probe')
    record_job_run(name, 'first')
    record_job_run(name, 'second')

    with db_cursor() as cursor:
        cursor.execute("SELECT job_name, summary FROM job_runs WHERE job_name = %s",
                       (name,))
        rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0].summary == 'second'


def test_recording_a_run_moves_the_timestamp(cleanup_jobs):
    name = JOB('timestamp-probe')
    record_job_run(name, 'first')
    with db_cursor() as cursor:
        cursor.execute("SELECT last_run_at FROM job_runs WHERE job_name = %s", (name,))
        first = cursor.fetchone().last_run_at
    record_job_run(name, 'second')
    with db_cursor() as cursor:
        cursor.execute("SELECT last_run_at FROM job_runs WHERE job_name = %s", (name,))
        second = cursor.fetchone().last_run_at
    assert second >= first


def test_a_failed_recording_never_breaks_the_job(monkeypatch):
    # THE other load-bearing one. Both callers have already done their real work
    # by the time this runs — materializing every user's ledger, or sending a
    # batch of emails. A bookkeeping write must never be the thing that fails
    # them, so the failure is swallowed rather than propagated.
    def boom(*a, **kw):
        raise psycopg2.OperationalError('database is on fire')

    monkeypatch.setattr('app.jobs.db_cursor', boom)
    record_job_run(JOB('never-written'), 'summary')      # must not raise


def test_load_returns_the_database_clock(cleanup_jobs):
    # Staleness is a subtraction, and checked_at must come from the same clock as
    # last_run_at — not Python's. The scheduler runs America/New_York, the
    # container is UTC, and every timestamp column here is naive.
    record_job_run(JOB('clock-probe'), 'x')
    with db_cursor() as cursor:
        rows = load_job_runs(cursor)
    mine = [r for r in rows if r.job_name == JOB('clock-probe')]
    assert len(mine) == 1
    assert mine[0].checked_at is not None
    assert mine[0].checked_at >= mine[0].last_run_at


def test_both_timestamps_are_naive(cleanup_jobs):
    # Regression, and the reason load_job_runs casts: bare now() is timestamptz,
    # which psycopg2 returns offset-AWARE, while last_run_at is a naive
    # `timestamp without time zone`. Subtracting the two raises TypeError rather
    # than quietly giving a wrong answer — so this fails loudly if the cast is
    # ever "tidied" away, which is better than the panel 500ing in production.
    record_job_run(JOB('naive-probe'), 'x')
    with db_cursor() as cursor:
        rows = load_job_runs(cursor)
    (mine,) = [r for r in rows if r.job_name == JOB('naive-probe')]
    assert mine.checked_at.tzinfo is None
    assert mine.last_run_at.tzinfo is None
    assert isinstance(mine.checked_at - mine.last_run_at, timedelta)


# --- the real writers ------------------------------------------------------
# ⚠️ These write the REAL job name, which every worker shares. They therefore
# assert only that a recent row EXISTS — true whoever wrote last, so no race.

def test_the_daily_job_records_itself(users):
    run_daily_tasks()
    with db_cursor() as cursor:
        cursor.execute("SELECT last_run_at, summary, now()::timestamp AS checked_at "
                       "FROM job_runs WHERE job_name = %s", (DAILY,))
        row = cursor.fetchone()
    assert row is not None
    assert row.checked_at - row.last_run_at < timedelta(minutes=5)
    assert 'materialized' in row.summary


def test_the_daily_job_reports_as_on_schedule_after_running(users):
    run_daily_tasks()
    with db_cursor() as cursor:
        rows = load_job_runs(cursor)
    summary = summarize_job_runs(rows, scheduler_on=True, digest_registered=True)
    assert _state(summary, DAILY_NAME) == OK


# --- the route -------------------------------------------------------------

def test_an_admin_sees_the_scheduled_jobs_table(admin_client):
    response = admin_client.get('/settings')
    assert response.status_code == 200
    body = response.data.decode()
    assert 'Scheduled jobs' in body
    assert DAILY_NAME in body
    assert DIGEST_NAME in body


def test_every_badge_actually_renders(admin_client, monkeypatch):
    # Jinja renders a typo'd attribute as an EMPTY STRING rather than raising, so
    # a state the template never exercises is a state that can silently vanish.
    # Locally the scheduler is off and every row reads 'Not scheduled here', so
    # without this the other three badges would go untested — load_job_runs is
    # stubbed to make each one deterministic rather than depending on whatever a
    # parallel worker last wrote to the shared 'daily_tasks' row.
    monkeypatch.setenv('ENABLE_DIGEST_SCHEDULER', '1')
    monkeypatch.setenv('RESEND_API_KEY', 'k' * 40)

    cases = {
        'On schedule': [Run(DAILY, NOW, 'did work', NOW),
                        Run(WEEKLY_DIGEST, NOW, None, NOW)],
        'Overdue': [Run(DAILY, NOW - timedelta(days=30), None, NOW),
                    Run(WEEKLY_DIGEST, NOW - timedelta(days=30), None, NOW)],
        'Has never run': [],
    }
    for badge, rows in cases.items():
        monkeypatch.setattr('app.blueprints.admin.load_job_runs', lambda _c, r=rows: r)
        body = admin_client.get('/settings').data.decode()
        assert badge in body, f'{badge!r} badge did not render'


def test_the_summary_reaches_the_page(admin_client, monkeypatch):
    monkeypatch.setenv('ENABLE_DIGEST_SCHEDULER', '1')
    monkeypatch.setattr('app.blueprints.admin.load_job_runs',
                        lambda _c: [Run(DAILY, NOW, 'materialized 7 user(s)', NOW)])
    assert 'materialized 7 user(s)' in admin_client.get('/settings').data.decode()


def test_a_non_admin_sees_no_job_information(client_a):
    response = client_a.get('/settings', follow_redirects=True)
    assert 'Scheduled jobs' not in response.data.decode()


def test_anon_is_redirected(anon_client):
    assert anon_client.get('/settings').status_code == 302
