"""#151 — when each scheduled job last actually finished.

`/settings` could already report the background scheduler, but only by reading
its env var at request time (`admin.scheduler_enabled()`). That answers "was the
switch set when this process started", not "is the job running". Those were the
same question while the scheduler's only job was the digest email — a missed
digest is self-evident, because no email arrives.

They stopped being the same question in `0.2.0`. Since #33 the daily job also
calls `materialize_all_users()`, which turns a recurring schedule into a real
transaction row for every user whether or not anyone logs in, and it is
deliberately gated on nothing at all. If that thread dies, or
`ENABLE_DIGEST_SCHEDULER` is dropped from the server `.env` by some future
change, the env var still reads what it reads, `/healthz` stays green because
the database is reachable, and recurring transactions silently stop appearing —
indistinguishable from "nothing was due", and most likely noticed weeks later
via wrong balances.

Its own module rather than living in `admin.py`, because the writers are the two
scheduler jobs: `reminders.py` and `digests.py` importing from the
user-management blueprint would couple them to it for a bookkeeping call. The
single-purpose root modules (`mailer.py`, `pusher.py`, `github.py`) are the
shape this follows — though unlike those three this one does touch the database,
since it is a data concern rather than an outbound network seam.

⚠️ `record_job_run()` swallows its own failures. Both callers already isolate
per-user failures so one bad user cannot abort a batch; a bookkeeping write must
never be the thing that breaks a job which has already done its work.
"""
from contextlib import suppress
from datetime import timedelta

import psycopg2

from app.db import db_cursor

# Job identifiers. These match the APScheduler job ids registered in
# app/__init__.py — not enforced anywhere, but keeping them equal is what makes
# a log line and a panel row obviously the same job.
DAILY = 'daily_tasks'
WEEKLY_DIGEST = 'weekly_digest'

# Display states. `not_scheduled` is the one that stops the panel crying wolf:
# a job that is not registered has no business being called overdue.
OK = 'ok'
STALE = 'stale'
NEVER = 'never'
NOT_SCHEDULED = 'not_scheduled'

# (job_name, display name, what it does, how long before a run counts as stale)
#
# The thresholds are deliberately generous. 48h for a daily job — one missed run
# is a restart landing badly, two is a problem. 9 days for a weekly one — 7 plus
# room for a late Sunday. Both comfortably clear the misfire_grace_time=3600
# already configured on the jobs themselves. A false "overdue" costs trust in the
# panel, which is the same reasoning as the generous length floors in
# admin.INTEGRATIONS.
JOBS = (
    (DAILY, 'Daily tasks',
     'Materializes recurring transactions, then sends bill reminders',
     timedelta(hours=48)),
    (WEEKLY_DIGEST, 'Weekly digest',
     'The Sunday summary email',
     timedelta(days=9)),
)


def record_job_run(job_name, summary=None):
    """Record that `job_name` finished just now. Upsert — one row per job.

    Called at the END of a job, never at dispatch: a job that starts and throws
    must not leave a row that looks like a job that worked.

    Never raises. A failure here means the panel shows a stale timestamp, which
    is a monitoring gap; letting it propagate would mean a bookkeeping write can
    fail a job that has already materialized everyone's transactions. The first
    is strictly better than the second, so this is one of the deliberately-broad
    catches (see CLAUDE.md) — the caller has already done its real work and there
    is nothing useful it could do with the error.
    """
    with suppress(psycopg2.Error):
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                """INSERT INTO job_runs (job_name, last_run_at, summary)
                        VALUES (%s, now(), %s)
                   ON CONFLICT (job_name) DO UPDATE
                        SET last_run_at = now(), summary = EXCLUDED.summary""",
                (job_name, summary))


def load_job_runs(cursor):
    """Every recorded run, plus the database's own clock.

    `checked_at` comes back from the SAME query on purpose. Staleness is a
    subtraction, and doing it against Python's `datetime.now()` would compare two
    clocks: the scheduler runs on America/New_York, the container is UTC, and
    every timestamp column in this schema is naive. Selecting the clock alongside
    the rows keeps it to one and leaves the arithmetic pure and testable.

    ⚠️ The `::timestamp` cast is load-bearing, not decoration. Bare `now()` is
    `timestamptz`, which psycopg2 hands back as an offset-AWARE datetime, while
    `last_run_at` is `timestamp without time zone` and arrives naive — and
    subtracting one from the other raises TypeError rather than returning a wrong
    answer. The cast applies exactly the conversion Postgres already performs
    when `DEFAULT now()` is stored into that naive column, so both sides of the
    subtraction are the same clock in the same representation.
    """
    cursor.execute("SELECT job_name, last_run_at, summary, "
                   "now()::timestamp AS checked_at FROM job_runs")
    return cursor.fetchall()


def summarize_job_runs(rows, *, scheduler_on, digest_registered, checked_at=None):
    """One display row per known job: {name, description, last_run_at, summary, state}.

    Pure — takes the rows and the clock, touches no database and no request
    context, for the same reason `admin.integration_status()` is pure: the
    interesting rules are then unit-testable without a client or a fixture.

    `state` is one of the four module constants:

        OK             ran inside its window
        STALE          registered, and overdue — the state this exists to surface
        NEVER          registered and expected to run, but no row yet
        NOT_SCHEDULED  not registered on this box, so silence is correct

    That last state carries the weight. The scheduler starts only on
    ENABLE_DIGEST_SCHEDULER=1, and the digest job is registered only when
    mail_enabled() — so locally, and on any box without a Resend key, "never run"
    is the EXPECTED state and must not render as a fault. Reporting it as a
    problem would train the reader to ignore the panel, which is exactly what the
    third integration state was added to avoid.
    """
    recorded = {row.job_name: row for row in rows}
    if checked_at is None:
        checked_at = next((row.checked_at for row in rows), None)

    display = []
    for job_name, name, description, threshold in JOBS:
        row = recorded.get(job_name)
        registered = scheduler_on and (digest_registered
                                       if job_name == WEEKLY_DIGEST else True)

        if not registered:
            state = NOT_SCHEDULED
        elif row is None:
            state = NEVER
        elif checked_at is not None and checked_at - row.last_run_at > threshold:
            state = STALE
        else:
            state = OK

        display.append({
            'name': name,
            'description': description,
            'last_run_at': row.last_run_at if row else None,
            'summary': row.summary if row else None,
            'state': state,
        })
    return display
