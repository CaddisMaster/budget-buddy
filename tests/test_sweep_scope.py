"""#411 — a daily-job test never touches another test's users.

`materialize_all_users()` posts every due schedule of EVERY user in the
database, against the real clock. Under xdist, "every user" includes the users
other workers' tests are halfway through using. The `scheduler_sweep` group
serialized the sweeping files against each other and nothing else, so a sweep
in one worker posted a schedule another worker's test had just made, and that
test then saw a posted bill where it expected one still to come.

It was date-dependent, which is why it hid: `test_month_read`'s bill is due on
the 16th of a month whose clock is frozen at the 15th, so only from the 17th
did the real clock call it overdue. It failed 2 runs in 10 on 2026-09-25.

The fix is in conftest: an autouse fixture scopes the sweep's user query to
this worker's users (`helpers.only_users_prefixed`). These tests model "another
worker" with a prefix no worker ever has.
"""
import calendar
import os
from datetime import date, timedelta

import pytest

from app.blueprints import reminders
from app.blueprints.insights import build_read_facts
from app.blueprints.reminders import materialize_all_users
from tests.conftest import TEST_PREFIX
from tests.helpers import (
    PASSWORD,
    _create_user,
    _delete_user,
    _seed_basic_data,
    create_account,
    create_category,
    create_schedule,
    create_transaction,
    fetch_schedule,
    only_users_prefixed,
)

# Stands for any user that is not this worker's. Unique per worker, so two
# workers running this file at once never share the row.
ELSEWHERE = "__elsewhere__" + os.environ.get("PYTEST_XDIST_WORKER", "") + "victim"

# "Another worker" for a sweep that must not see this worker's users. Matches no
# user at all: see the month-read test for what a real prefix here did.
NO_SUCH_WORKER = "__no_such_worker__"

_NEXT_DUE = 2  # fetch_schedule's column


def _overdue_schedule(user_id, account_id, due):
    return create_schedule(user_id, account_id, 10, "monthly", due,
                           description="sweep-scope")


@pytest.mark.criterion(
    411, "A sweep in one worker never posts a schedule belonging to another worker's user")
def test_a_sweep_never_posts_a_schedule_belonging_to_another_workers_user(users):
    """Counted by the schedule, not the ledger: an unscoped runner could post
    under the wrong id, but it always advances the schedule it posted."""
    assert not ELSEWHERE.startswith(TEST_PREFIX)
    _delete_user(ELSEWHERE)
    theirs_id = _create_user(ELSEWHERE, PASSWORD)
    try:
        theirs_acct = _seed_basic_data(theirs_id, "elsewhere")["account_id"]
        overdue = date.today() - timedelta(days=1)
        theirs = _overdue_schedule(theirs_id, theirs_acct, overdue)
        ours = _overdue_schedule(users["a"]["id"], users["a"]["account_id"], overdue)

        materialize_all_users()

        # Positive control: the sweep ran, and posted this worker's schedule.
        assert fetch_schedule(ours)[_NEXT_DUE] > overdue
        assert fetch_schedule(theirs)[_NEXT_DUE] == overdue, (
            "the sweep posted a schedule belonging to a user outside this worker")
    finally:
        _delete_user(ELSEWHERE)


@pytest.mark.criterion(
    411, "The month read's bill is still to land on any day of the real month")
def test_the_month_reads_bill_is_still_to_land_on_any_day_of_the_real_month(
        users, forecast_today, monkeypatch):
    """The failing test's own setup, then another worker's sweep, run with its
    clock at the LAST day of the month. The bill is overdue to that sweep
    whatever today really is, so this can fail on the 3rd as well as the 25th,
    where the original failure could only happen after the 16th."""
    a = users["a"]["id"]
    acct = create_account(a, "scope-bill-acct")
    cat = create_category(a, "scope-bill-cat")
    year, month = forecast_today.year, forecast_today.month
    create_transaction(a, acct, 500, date(year, month, 1), "income")
    due = forecast_today + timedelta(days=1)
    create_schedule(a, acct, 75, "monthly", due, category_id=cat,
                    transaction_type="expense", description="read-bill")

    month_end = date(year, month, calendar.monthrange(year, month)[1])

    class _MonthEnd(date):
        @classmethod
        def today(cls):
            return month_end

    monkeypatch.setattr("app.blueprints.schedules.date", _MonthEnd)
    # ⚠️ A prefix that matches NO user. It was "__elsewhere__" at first, which
    # also matched the victim the test above creates in a parallel worker, so
    # this sweep posted it: 3 failures in 30 runs, all of them this suite's own.
    unscoped = reminders._users_with_schedules.__wrapped__
    monkeypatch.setattr(reminders, "_users_with_schedules",
                        only_users_prefixed(NO_SUCH_WORKER, unscoped))

    materialize_all_users()

    projection = build_read_facts(a, year, month)["projection"]
    assert projection["remaining_scheduled_expense"] == 75.0


def test_worker_gw1_does_not_claim_worker_gw10s_users():
    """xdist ids are gw1, gw10, …, and `__pytest__gw1` is a prefix of
    `__pytest__gw10user_a`. A plain startswith() would let worker gw1's sweep
    post gw10's schedules, which is #411 again for any run with -n 11 or more."""
    base = "__gwprobe__" + os.environ.get("PYTEST_XDIST_WORKER", "")
    gw1, gw10 = base + "gw1user", base + "gw10user"
    ids = {}
    try:
        for name in (gw1, gw10):
            _delete_user(name)
            ids[name] = _create_user(name, PASSWORD)
        scoped = only_users_prefixed(base + "gw1", lambda cursor: list(ids.values()))
        from app.db import db_cursor
        with db_cursor() as cursor:
            assert scoped(cursor) == [ids[gw1]]
    finally:
        for name in (gw1, gw10):
            _delete_user(name)
