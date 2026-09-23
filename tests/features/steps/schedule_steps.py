"""Steps for the schedule features (#356).

Every step is a thin call into the same helpers and app functions the pytest
twins in `test_schedules.py` / `test_push_reminders.py` use — the Gherkin says
WHAT happens, and this file is the only place that knows HOW.

⚠️ Step functions get distinct names on purpose. behave's convention is to
call every one `step_impl`, which ruff reports as F811 (redefinition) — and
silencing that rule would also hide a real redefinition somewhere else.
"""
import re
import threading
from datetime import date, timedelta

from behave import given, register_type, then, when

from app.blueprints.reminders import materialize_all_users
from app.blueprints.schedules import run_due_schedules
from app.blueprints.transactions import VALID_FREQUENCIES
from app.db import get_db_connection
from tests.helpers import _login, count_transactions_like, create_schedule, fetch_schedule

HX = {"HX-Request": "true"}

# The description `create_schedule` gives every row it inserts, and so the one
# every posted transaction carries.
SEEDED = "seed-schedule"


# ── Relative dates ──────────────────────────────────────────────────────────
#
# Scenarios speak in days relative to today, because every rule here is
# relative to today: "due yesterday" means the same thing on any date the suite
# runs, where a literal date would go stale or need a frozen clock.

def _pattern(regex):
    """What `parse.with_pattern` does, without importing `parse` — it reaches
    this environment only as a dependency of behave, and nothing pins it."""
    def mark(func):
        func.pattern = regex
        return func
    return mark


_REL = r"yesterday|today|tomorrow|in \d+ days?|\d+ (?:day|week)s? ago"


@_pattern(_REL)
def _relative_date(text):
    today = date.today()
    fixed = {"yesterday": -1, "today": 0, "tomorrow": 1}
    if text in fixed:
        return today + timedelta(days=fixed[text])
    count, unit = re.search(r"(\d+) (day|week)", text).groups()
    delta = timedelta(days=int(count)) if unit == "day" else timedelta(weeks=int(count))
    return today + delta if text.startswith("in ") else today - delta


register_type(Rel=_relative_date)


# ⚠️ Constrained types, not bare `{}`. parse's default field is a lazy `.+?`
# that happily spans spaces, so "has a paused monthly expense schedule" also
# matches "has a {frequency} {kind} schedule" with frequency="paused" — and the
# generic step, registered first, wins. Each field below matches only the words
# it can legitimately hold.

@_pattern("|".join(VALID_FREQUENCIES))
def _frequency(text):
    return text


@_pattern(r"income|expense")
def _kind(text):
    return text


@_pattern(r"[AB]")
def _who(text):
    return text


register_type(Freq=_frequency, Kind=_kind, Who=_who)


def _user(context, who):
    return context.users[who.lower()]


# ── Given ───────────────────────────────────────────────────────────────────

def _make_schedule(context, who, frequency, kind, due, end=None, active=True):
    user = _user(context, who)
    context.schedule_id = create_schedule(
        user["id"], user["account_id"], 50, frequency, due,
        transaction_type=kind, end_date=end, is_active=active)


@given("user {who:Who} has a {frequency:Freq} {kind:Kind} schedule due {due:Rel}")
def given_a_schedule(context, who, frequency, kind, due):
    _make_schedule(context, who, frequency, kind, due)


@given("user {who:Who} has a {frequency:Freq} {kind:Kind} schedule due {due:Rel}, ending {end:Rel}")
def given_a_schedule_with_an_end(context, who, frequency, kind, due, end):
    _make_schedule(context, who, frequency, kind, due, end=end)


@given("user {who:Who} has a paused {frequency:Freq} {kind:Kind} schedule due {due:Rel}")
def given_a_paused_schedule(context, who, frequency, kind, due):
    _make_schedule(context, who, frequency, kind, due, active=False)


@given("user {who:Who} is signed in")
def given_signed_in(context, who):
    client = context.app.test_client()
    _login(client, _user(context, who)["username"])
    context.client = client


# ── When ────────────────────────────────────────────────────────────────────

@when("user {who:Who}'s due schedules run")
@when("user {who:Who}'s due schedules run again")
def when_due_schedules_run(context, who):
    run_due_schedules(_user(context, who)["id"])


def _race(context, workers):
    """Start every worker on one barrier so they hit the same window together.

    Each call opens its own connection, so this is real interleaving against
    the `FOR UPDATE` row lock — the property the two scenarios exist for.
    """
    barrier = threading.Barrier(len(workers))
    context.errors = []

    def run(work):
        barrier.wait()
        try:
            work()
        except Exception as err:  # surfaced by "no run failed"
            context.errors.append(err)

    threads = [threading.Thread(target=run, args=(work,)) for work in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


@when("{pages:d} page loads run user {who:Who}'s due schedules at the same instant")
def when_page_loads_race(context, pages, who):
    uid = _user(context, who)["id"]
    _race(context, [lambda: run_due_schedules(uid)] * pages)


@when("{pages:d} page loads and {jobs:d} daily jobs run at the same instant")
def when_page_loads_race_the_daily_job(context, pages, jobs):
    # The page loads act for user A, whose schedule the scenario set up; the
    # daily job acts for everybody, which is what makes it a real race.
    uid = _user(context, "A")["id"]
    _race(context, [lambda: run_due_schedules(uid)] * pages + [materialize_all_users] * jobs)


@when("user {who:Who} creates a {frequency:Freq} {kind:Kind} schedule starting {start:Rel}")
def when_creating_a_schedule(context, who, frequency, kind, start):
    context.response = context.client.post("/scheduled", headers=HX, data={
        "transaction_type": kind,
        "amount": "30",
        "account_id": _user(context, who)["account_id"],
        "frequency": frequency,
        "next_due": start.isoformat(),
    })


# ── Then ────────────────────────────────────────────────────────────────────

@then("{count:d} transaction has been posted from user {who:Who}'s schedules")
@then("{count:d} transactions have been posted from user {who:Who}'s schedules")
def then_posted(context, count, who):
    posted = count_transactions_like(_user(context, who)["id"], SEEDED)
    assert posted == count, f"expected {count} posted, found {posted}"


@then("the schedule's next due date is after today")
def then_next_due_moved_on(context):
    next_due = fetch_schedule(context.schedule_id)[2]
    assert next_due > date.today(), f"next_due is {next_due}, not after today"


@then("no run failed")
def then_no_run_failed(context):
    assert context.errors == [], f"runs raised: {context.errors!r}"


@then("user {who:Who} has {count:d} schedule")
@then("user {who:Who} has {count:d} schedules")
def then_schedule_count(context, who, count):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schedules WHERE user_id = %s", (_user(context, who)["id"],))
    found = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert found == count, f"expected {count} schedules, found {found}"


@then('it is refused with "{message}"')
def then_refused(context, message):
    response = context.response
    assert response.status_code == 200, response.status_code
    toast = response.headers.get("HX-Trigger", "")
    assert message in toast, f"expected {message!r} in the toast, got {toast!r}"
    assert '"kind": "error"' in toast, toast
