"""#191 — a push alert when a bill whose amount varies has posted.

A bill due the same day every month for a different amount each time (the filed
example is electricity) materializes carrying LAST month's figure. The #33
reminder cannot serve here: it fires the evening BEFORE and quotes that same
stale figure. This is the post-posting counterpart.

The design fork that these tests exist to pin down is
`test_a_bill_posted_by_a_page_load_is_still_alerted`. Reading the SCHEDULES to
find "what was due today" is the obvious implementation and it is wrong: three
page-load paths also call run_due_schedules(), so opening the app in the morning
posts the bill and advances next_due, leaving the evening job nothing to see —
on exactly the days the user was engaged. The pass reads the LEDGER instead, via
transactions.schedule_id (sql/35).

⚠️ This file drives a GLOBAL sweep (send_posted_bill_alerts reads every user with
a subscription), so it carries the scheduler_sweep xdist group and every endpoint
comes from TEST_PREFIX. Assertions are on THIS worker's own endpoints, never on a
global count — see tests/test_push_reminders.py's docstring for the full trap.
"""
from datetime import date, timedelta

import pytest

import app.pusher as pusher
from app.blueprints.reminders import (
    _posted_notification,
    _posted_variable_bills,
    run_daily_tasks,
    send_posted_bill_alerts,
)
from app.blueprints.schedules import run_due_schedules
from app.db import get_db_connection
from tests.conftest import (
    TEST_PREFIX,
    create_account,
    create_schedule,
    create_transaction,
)

TODAY = date.today()

pytestmark = pytest.mark.xdist_group("scheduler_sweep")


def EP(name):
    """An endpoint unique to this worker — the column is globally UNIQUE, so a
    shared literal is a shared row (#128)."""
    return f"https://push.example/{TEST_PREFIX}vb-{name}"


DEFAULT_ENDPOINT = EP("device")


# --- helpers ----------------------------------------------------------------

def _enable_push(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-private")


def _disable_push(monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)


def _capture(monkeypatch, fail_with=None, fail_endpoints=None):
    """Stub the network seam, recording only THIS worker's sends.

    Both halves are worker-scoped deliberately: the pass under test sweeps every
    user in the database, so an unfiltered recorder collects other workers'
    devices, and an unconditional failure stub would delete their subscriptions
    mid-test."""
    sent = []

    def fake(subscription_info, data, private_key, subject):
        import json
        endpoint = subscription_info["endpoint"]
        if TEST_PREFIX not in endpoint:
            return {"status_code": 201}          # another worker's device
        if fail_with is not None and (fail_endpoints is None
                                      or endpoint in fail_endpoints):
            raise fail_with
        sent.append({"endpoint": endpoint, "payload": json.loads(data)})
        return {"status_code": 201}

    monkeypatch.setattr(pusher, "_call_webpush", fake)
    return sent


def _add_subscription(user_id, endpoint=DEFAULT_ENDPOINT):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
        "VALUES (%s, %s, 'p', 'a') "
        "ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id",
        (user_id, endpoint))
    conn.commit()
    cur.close()
    conn.close()


def _subscription_exists(endpoint):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
    found = cur.fetchone() is not None
    cur.close()
    conn.close()
    return found


def _claims(user_id):
    """This user's 'posted' claims — the idempotency markers."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT source_id, occurrence_date FROM reminder_log "
                "WHERE user_id = %s AND source = 'posted' ORDER BY source_id",
                (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _schedule_id_of(user_id, description):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT schedule_id FROM transactions "
                "WHERE user_id = %s AND description = %s", (user_id, description))
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def _variable_bill(user_id, *, description, amount=142.55, due=None,
                   variable=True):
    """A schedule due today (or the given date), materialized the way a page load
    would materialize it. Returns the schedule id."""
    account = create_account(user_id, TEST_PREFIX + "vb-acct-" + description)
    sid = create_schedule(user_id, account, amount, "monthly",
                          due or TODAY, is_variable_amount=variable,
                          description=description)
    run_due_schedules(user_id)
    return sid


# --- the link column --------------------------------------------------------

def test_materializing_stamps_the_schedule_on_the_transaction(users):
    """The link #191 rests on. Without it there is no way, after the fact, to ask
    which schedule posted a row."""
    a = users["a"]["id"]
    sid = _variable_bill(a, description="vb-stamped")

    assert _schedule_id_of(a, "vb-stamped") == [sid]


def test_a_hand_entered_transaction_has_no_schedule(users):
    """The column is nullable and stays NULL for anything a user typed — which is
    also what keeps every row predating sql/35 out of the alert window."""
    a = users["a"]["id"]
    account = create_account(a, TEST_PREFIX + "vb-manual")
    tid = create_transaction(a, account, 20, TODAY)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT schedule_id FROM transactions WHERE id = %s", (tid,))
    schedule_id = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert schedule_id is None


def test_deleting_a_schedule_keeps_the_transactions_it_posted(users):
    """ON DELETE SET NULL, not CASCADE. A posted row is money that really moved;
    it must survive the template being deleted."""
    a = users["a"]["id"]
    sid = _variable_bill(a, description="vb-doomed")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM schedules WHERE id = %s", (sid,))
    conn.commit()
    cur.close()
    conn.close()

    assert _schedule_id_of(a, "vb-doomed") == [None]


# --- which rows the pass finds ----------------------------------------------

def test_only_variable_schedules_produce_items(users):
    a = users["a"]["id"]
    _variable_bill(a, description="vb-varies", variable=True)
    _variable_bill(a, description="vb-fixed", variable=False)

    found = {i["description"] for i in _posted_variable_bills(a, TODAY)}
    assert found == {"vb-varies"}


def test_the_window_is_a_few_days_not_just_today(users):
    """A day the job did not run is recovered rather than lost. The claim is per
    transaction, so the widened window cannot re-notify a row already alerted."""
    a = users["a"]["id"]
    _variable_bill(a, description="vb-yesterday", due=TODAY - timedelta(days=1))

    assert [i["description"] for i in _posted_variable_bills(a, TODAY)] \
        == ["vb-yesterday"]


def test_an_old_posting_is_outside_the_window(users):
    a = users["a"]["id"]
    _variable_bill(a, description="vb-ancient", due=TODAY - timedelta(days=30))

    assert _posted_variable_bills(a, TODAY) == []


def test_items_are_scoped_to_one_user(users):
    a, b = users["a"]["id"], users["b"]["id"]
    _variable_bill(b, description="vb-belongs-to-b")

    assert _posted_variable_bills(a, TODAY) == []
    assert len(_posted_variable_bills(b, TODAY)) == 1


# --- the payload ------------------------------------------------------------

def test_the_notification_names_the_bill_and_the_amount():
    payload = _posted_notification({"description": "Electric", "amount": 142.55,
                                    "due": TODAY, "source": "posted",
                                    "source_id": 1})

    assert "Electric" in payload["title"]
    assert "142.55" in payload["title"]
    assert payload["body"]


def test_the_notification_opens_a_real_page():
    """⚠️ Stated as a test because the useful-looking target is a trap:
    /transactions/<id>/edit returns an HTMX <tr> fragment, so a notification tap
    would open a bare table row."""
    payload = _posted_notification({"description": "Electric", "amount": 10.0,
                                    "due": TODAY, "source": "posted",
                                    "source_id": 1})

    assert payload["url"] == "/transactions"
    assert "/edit" not in payload["url"]


# --- sending ----------------------------------------------------------------

def test_a_posted_variable_bill_alerts_the_users_devices(users, monkeypatch):
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(a)
    _variable_bill(a, description="vb-electric", amount=142.55)

    send_posted_bill_alerts(today=TODAY)

    assert [s["endpoint"] for s in sent] == [DEFAULT_ENDPOINT]
    assert "vb-electric" in sent[0]["payload"]["title"]


def test_a_bill_posted_by_a_page_load_is_still_alerted(users, monkeypatch):
    """⚠️ THE load-bearing test, and the reason the pass reads the ledger.

    run_due_schedules() fires on '/', '/transactions' and '/scheduled' as well as
    from the daily job. A user who opens the app in the morning has already
    materialized the bill and advanced next_due by the time the evening job runs,
    so an implementation that asks the SCHEDULES what is due today finds nothing
    and stays silent — on precisely the days the user was using the app.

    This test materializes exactly the way a page load does, then runs the job.
    """
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(a)

    account = create_account(a, TEST_PREFIX + "vb-pageload")
    create_schedule(a, account, 88.10, "monthly", TODAY,
                    is_variable_amount=True, description="vb-pageload")
    run_due_schedules(a)          # the 09:00 page load

    # next_due really has moved past today — the state the naive design breaks on
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT next_due FROM schedules WHERE user_id = %s "
                "AND description = 'vb-pageload'", (a,))
    assert cur.fetchone()[0] > TODAY
    cur.close()
    conn.close()

    send_posted_bill_alerts(today=TODAY)   # the 18:00 job

    assert [s["payload"]["title"] for s in sent if "vb-pageload" in s["payload"]["title"]]


def test_a_fixed_bill_is_never_alerted(users, monkeypatch):
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(a)
    _variable_bill(a, description="vb-netflix", variable=False)

    send_posted_bill_alerts(today=TODAY)

    assert sent == []


def test_the_same_posting_is_not_alerted_twice(users, monkeypatch):
    """The reminder_log claim. Two runs on the same day — a retry, or the job
    firing after a page load already triggered one — send once."""
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(a)
    _variable_bill(a, description="vb-once")

    send_posted_bill_alerts(today=TODAY)
    send_posted_bill_alerts(today=TODAY)

    assert len(sent) == 1
    assert len(_claims(a)) == 1


def test_the_claim_is_keyed_on_the_transaction(users, monkeypatch):
    """source='posted' with a TRANSACTION id, never 'schedule' — that key is
    already claimed by the due-tomorrow reminder for the same occurrence, so
    sharing it would let whichever fired first suppress the other."""
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    _capture(monkeypatch)
    _add_subscription(a)
    _variable_bill(a, description="vb-claimed")

    send_posted_bill_alerts(today=TODAY)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM transactions WHERE user_id = %s "
                "AND description = 'vb-claimed'", (a,))
    txn_id = cur.fetchone()[0]
    cur.close()
    conn.close()

    assert _claims(a) == [(txn_id, TODAY)]


def test_nothing_is_sent_when_push_is_unconfigured(users, monkeypatch):
    a = users["a"]["id"]
    _disable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(a)
    _variable_bill(a, description="vb-nopush")

    assert send_posted_bill_alerts(today=TODAY) == 0
    assert sent == []
    # and nothing was claimed, so it will alert once push is configured
    assert _claims(a) == []


def test_another_users_bill_reaches_nobody_else(users, monkeypatch):
    a, b = users["a"]["id"], users["b"]["id"]
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(a, EP("a-device"))
    _add_subscription(b, EP("b-device"))
    _variable_bill(b, description="vb-b-only")

    send_posted_bill_alerts(today=TODAY)

    assert [s["endpoint"] for s in sent] == [EP("b-device")]


def test_a_dead_subscription_is_deleted(users, monkeypatch):
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    dead = EP("dead")
    _capture(monkeypatch, fail_with=pusher.PushGone("410"), fail_endpoints={dead})
    _add_subscription(a, dead)
    _variable_bill(a, description="vb-dead")

    send_posted_bill_alerts(today=TODAY)

    assert not _subscription_exists(dead)


def test_a_transient_failure_keeps_the_subscription(users, monkeypatch):
    """The opposite branch: a 500 from the push service says nothing about the
    device, so the row stays and tomorrow's run reaches it."""
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    flaky = EP("flaky")
    _capture(monkeypatch, fail_with=pusher.PushError("503"), fail_endpoints={flaky})
    _add_subscription(a, flaky)
    _variable_bill(a, description="vb-flaky")

    send_posted_bill_alerts(today=TODAY)

    assert _subscription_exists(flaky)


# --- the daily job ----------------------------------------------------------

def test_the_daily_job_runs_the_alert_pass(users, monkeypatch):
    """Materialize FIRST, then alert — which is what lets a bill posted by this
    very run be alerted on the same run, with no wait until tomorrow."""
    a = users["a"]["id"]
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    _add_subscription(a)
    account = create_account(a, TEST_PREFIX + "vb-job")
    create_schedule(a, account, 61.20, "monthly", TODAY,
                    is_variable_amount=True, description="vb-daily")

    users_done, reminders, alerts = run_daily_tasks(today=TODAY)

    assert users_done >= 1
    assert [s for s in sent if "vb-daily" in s["payload"]["title"]]


# --- the form ---------------------------------------------------------------

def test_the_flag_round_trips_through_the_create_form(client_a, users):
    a = users["a"]["id"]
    account = create_account(a, TEST_PREFIX + "vb-form")
    client_a.post("/scheduled", data={
        "amount": "50", "description": "vb-created", "account_id": str(account),
        "transaction_type": "expense", "frequency": "monthly",
        "next_due": (TODAY + timedelta(days=5)).isoformat(),
        "is_variable_amount": "true",
    })

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_variable_amount FROM schedules WHERE user_id = %s "
                "AND description = 'vb-created'", (a,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row is not None and row[0] is True


def test_an_unticked_box_means_false(client_a, users):
    """A checkbox posts nothing when unticked, which must read as false rather
    than as "unchanged" — otherwise the flag could never be turned back off."""
    a = users["a"]["id"]
    account = create_account(a, TEST_PREFIX + "vb-form-off")
    sid = create_schedule(a, account, 50, "monthly", TODAY + timedelta(days=5),
                          is_variable_amount=True, description="vb-turn-off")

    client_a.post(f"/scheduled/{sid}/edit", data={
        "amount": "50", "description": "vb-turn-off", "account_id": str(account),
        "transaction_type": "expense", "frequency": "monthly",
        "next_due": (TODAY + timedelta(days=5)).isoformat(),
    })

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_variable_amount FROM schedules WHERE id = %s", (sid,))
    still_variable = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert still_variable is False


def test_the_scheduled_page_shows_which_bills_vary(client_a, users):
    """Content-asserting, because Jinja renders a typo'd attribute as an empty
    string: a flag nobody can see is a flag nobody will trust."""
    a = users["a"]["id"]
    account = create_account(a, TEST_PREFIX + "vb-visible")
    create_schedule(a, account, 77, "monthly", TODAY + timedelta(days=5),
                    is_variable_amount=True, description="vb-shown")

    html = client_a.get("/scheduled").get_data(as_text=True)

    assert "vb-shown" in html
    # Anchored to the row, not the page: the create form's own help text sits on
    # this page too, so a bare `"varies" in html` could pass on copy alone.
    row = html.split('id="schedule-', 1)[1]
    assert "vb-shown" in row and "varies" in row


def test_the_create_form_offers_the_checkbox(client_a):
    html = client_a.get("/scheduled").get_data(as_text=True)
    assert 'name="is_variable_amount"' in html
