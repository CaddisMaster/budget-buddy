"""#33 — bill-due push reminders + the daily materialize pass.

No real Web Push calls anywhere: the single network seam, app.pusher._call_webpush,
is monkeypatched, exactly as the ai.py and mailer.py seams are. The VAPID keys are
set per-test with monkeypatch.setenv so push_enabled() flips deliberately rather
than depending on the environment — CI has no keys, and neither does local dev.

The load-bearing test in here is test_materialize_runs_even_when_push_unconfigured:
the daily job carries BOTH responsibilities, and gating the whole thing on push
config would mean a missing third-party key silently stops the ledger updating.

⚠️ Every endpoint that reaches the database is built with EP(), which folds in
TEST_PREFIX. push_subscriptions.endpoint is GLOBALLY UNIQUE — deliberately, it is
the push service's identity for one browser install — so a bare literal is ONE row
that every xdist worker fights over, and _add_subscription's ON CONFLICT moves it
to whichever worker inserted last. That silently reassigns another worker's device
mid-test. Never write a raw endpoint string here; the failure looks like a flake in
a file you did not touch.

⚠️ **That is necessary but not sufficient (#157).** Isolating the ROWS does not
isolate the RESULT: send_due_reminders() is a global sweep over every user, so a
call from this worker drives other workers' subscriptions through this process's
stub too. Two rules follow, both enforced in _capture():

  * Assert on `sent` (worker-scoped) — never on send_due_reminders()'s RETURN
    VALUE, which counts every worker's notifications. The one exception is the
    push-unconfigured test, where the gate short-circuits before the sweep and
    the env var it reads is per-process.
  * A failing stub must fail SELECTIVELY by endpoint. An unconditional PushGone
    deletes parallel workers' subscriptions, turning this file's setup into
    another file's missing row.
"""
import json
import threading
from datetime import date, timedelta

import pytest

import app.pusher as pusher
from app.blueprints.reminders import (
    _due_tomorrow,
    materialize_all_users,
    run_daily_tasks,
    send_due_reminders,
)
from app.db import get_db_connection
from tests.conftest import (
    TEST_PREFIX,
    count_transactions_like,
    create_account,
    create_schedule,
    create_transfer_schedule,
)

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)

# ⚠️ Every test in this file shares one worker (#157). send_due_reminders() is a
# global sweep and claims occurrences in reminder_log as it goes, so two workers
# running it concurrently steal each other's claims — the owning worker then
# sends nothing and its assertions fail. Grouping serializes the sweep against
# the only other file that triggers one (tests/test_job_runs.py, via
# run_daily_tasks) while leaving the rest of the suite fully parallel.
# Requires `--dist loadgroup`, set in pytest.ini.
pytestmark = pytest.mark.xdist_group("scheduler_sweep")


def EP(name):
    """A push endpoint unique to this xdist worker. See the module docstring —
    the column is globally UNIQUE, so a shared literal is a shared row."""
    return f"https://push.example/{TEST_PREFIX}{name}"


# A module-level singleton rather than an EP() call in the signature below, which
# ruff rejects as a mutable-default hazard (B008).
DEFAULT_ENDPOINT = EP("abc")


# --- helpers ----------------------------------------------------------------

def _enable_push(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-private")


def _disable_push(monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)


def _capture(monkeypatch, fail_with=None):
    """Stub the network seam; return the list it records THIS WORKER's sends into.

    ⚠️ Both halves are scoped to this worker's endpoints on purpose (#157), and
    the reason is not obvious: `send_due_reminders()` is a GLOBAL sweep. It reads
    `SELECT DISTINCT user_id FROM push_subscriptions` with no user filter — which
    is correct, it is the daily job for everybody — so calling it from one xdist
    worker drives every OTHER worker's subscriptions through this process's stub
    as well.

    Two things follow, and #128 fixed neither because it was about the rows
    rather than what the stub does with them:

      * RECORDING. An unfiltered `sent` list contains other workers' endpoints,
        so `len(sent) == 1` and set-equality assertions fail whenever a parallel
        worker happens to have a bill due. That is the intermittent failure this
        helper's filter removes.

      * FAILING. `fail_with` used to raise for EVERY endpoint. A `PushGone` stub
        therefore deleted parallel workers' subscriptions mid-test — the exact
        hazard CLAUDE.md warns about — turning one test's setup into another
        test's missing row. Raising only for our own endpoints keeps the failure
        selective.

    Other workers' sends are answered with a success and dropped on the floor:
    they are not this test's business, and swallowing them keeps their `sent`
    lists honest too.
    """
    sent = []

    def fake(subscription_info, data, private_key, subject):
        endpoint = subscription_info["endpoint"]
        if TEST_PREFIX not in endpoint:
            return {"status_code": 201}     # another worker's device — not ours
        if fail_with is not None:
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
        "VALUES (%s, %s, 'p256dh-x', 'auth-x') "
        "ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id "
        "RETURNING id",
        (user_id, endpoint))
    sid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return sid


def _subscription_count(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = %s",
                (user_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def _reminder_count(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM reminder_log WHERE user_id = %s", (user_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


# --- the gate (pure) --------------------------------------------------------

def test_push_disabled_without_keys(monkeypatch):
    _disable_push(monkeypatch)
    assert pusher.push_enabled() is False


def test_push_enabled_with_both_keys(monkeypatch):
    _enable_push(monkeypatch)
    assert pusher.push_enabled() is True


def test_push_needs_both_keys(monkeypatch):
    _disable_push(monkeypatch)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "only-public")
    assert pusher.push_enabled() is False


def test_send_push_without_keys_raises(monkeypatch):
    _disable_push(monkeypatch)
    with pytest.raises(pusher.PushError):
        pusher.send_push({"endpoint": EP("x")}, {"title": "t"})


# --- subscribe / unsubscribe routes -----------------------------------------

def test_subscribe_requires_login(anon_client):
    resp = anon_client.post("/push/subscribe", json={})
    assert resp.status_code == 302


def test_subscribe_stores_the_device(client_a, users, monkeypatch):
    _enable_push(monkeypatch)
    resp = client_a.post("/push/subscribe", json={
        "endpoint": EP("dev-1"),
        "keys": {"p256dh": "key-1", "auth": "auth-1"}})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert _subscription_count(users["a"]["id"]) == 1


def test_resubscribing_the_same_device_upserts(client_a, users, monkeypatch):
    _enable_push(monkeypatch)
    body = {"endpoint": EP("dev-1"),
            "keys": {"p256dh": "key-1", "auth": "auth-1"}}
    client_a.post("/push/subscribe", json=body)
    client_a.post("/push/subscribe", json=body)
    # One device, not two — the endpoint is the identity.
    assert _subscription_count(users["a"]["id"]) == 1


def test_subscribe_rejects_incomplete_payload(client_a, users, monkeypatch):
    _enable_push(monkeypatch)
    resp = client_a.post("/push/subscribe", json={
        "endpoint": EP("dev-1"), "keys": {"p256dh": "key-1"}})
    assert resp.status_code == 400
    assert _subscription_count(users["a"]["id"]) == 0


def test_subscribe_rejects_non_https_endpoint(client_a, users, monkeypatch):
    _enable_push(monkeypatch)
    resp = client_a.post("/push/subscribe", json={
        "endpoint": "http://push.example/dev-1",
        "keys": {"p256dh": "key-1", "auth": "auth-1"}})
    assert resp.status_code == 400
    assert _subscription_count(users["a"]["id"]) == 0


def test_subscribe_refuses_when_push_unconfigured(client_a, users, monkeypatch):
    _disable_push(monkeypatch)
    resp = client_a.post("/push/subscribe", json={
        "endpoint": EP("dev-1"),
        "keys": {"p256dh": "key-1", "auth": "auth-1"}})
    assert resp.status_code == 503
    # Nothing stored that would never be sent to.
    assert _subscription_count(users["a"]["id"]) == 0


def test_unsubscribe_cannot_remove_another_users_device(client_b, users, monkeypatch):
    _enable_push(monkeypatch)
    _add_subscription(users["a"]["id"], EP("a-device"))
    resp = client_b.post("/push/unsubscribe",
                         json={"endpoint": EP("a-device")})
    assert resp.status_code == 200
    # B's request succeeds but touches nothing of A's.
    assert _subscription_count(users["a"]["id"]) == 1


def test_profile_hides_push_ui_without_keys(client_a, monkeypatch):
    _disable_push(monkeypatch)
    body = client_a.get("/profile").data.decode()
    assert "Bill reminders" not in body
    assert "push-toggle" not in body


def test_profile_shows_push_ui_with_keys(client_a, monkeypatch):
    _enable_push(monkeypatch)
    body = client_a.get("/profile").data.decode()
    assert "Bill reminders" in body
    assert "push-toggle" in body


# --- the reminder window ----------------------------------------------------

def test_due_tomorrow_finds_a_schedule_due_tomorrow(users):
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 120, "monthly", TOMORROW, transaction_type="expense")
    items = _due_tomorrow(a, TODAY)
    assert [i["due"] for i in items] == [TOMORROW]
    assert items[0]["amount"] == 120.0


def test_due_tomorrow_ignores_today_and_next_week(users):
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 55, "monthly", TODAY)                    # already posted
    create_schedule(a, acct, 66, "monthly", TODAY + timedelta(days=7))
    assert _due_tomorrow(a, TODAY) == []


def test_due_tomorrow_respects_the_end_date(users):
    # #32 interlock: a schedule that has finished must never be reminded about.
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 77, "weekly", TOMORROW,
                    end_date=TODAY - timedelta(days=1))
    assert _due_tomorrow(a, TODAY) == []


def test_due_tomorrow_includes_transfers(users):
    a = users["a"]["id"]
    acct1 = create_account(a, "rem-1")
    acct2 = create_account(a, "rem-2")
    create_transfer_schedule(a, acct1, acct2, 300, "monthly", TOMORROW)
    items = _due_tomorrow(a, TODAY)
    assert len(items) == 1 and items[0]["source"] == "transfer"


# --- sending ----------------------------------------------------------------

def test_reminder_is_sent_for_a_bill_due_tomorrow(users, monkeypatch):
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 42.5, "monthly", TOMORROW, transaction_type="expense")
    _add_subscription(a, EP("a-1"))

    # ⚠️ The return value is a GLOBAL count — send_due_reminders sweeps every
    # user — so it cannot be asserted exactly under xdist. The claim moves onto
    # `sent`, which _capture scopes to this worker. See #157.
    send_due_reminders(today=TODAY)
    assert len(sent) == 1
    assert "42.50" in sent[0]["payload"]["title"]
    assert "tomorrow" in sent[0]["payload"]["body"].lower()


def test_reminder_is_not_sent_twice_for_the_same_occurrence(users, monkeypatch):
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 42.5, "monthly", TOMORROW)
    _add_subscription(a, EP("a-1"))

    send_due_reminders(today=TODAY)
    send_due_reminders(today=TODAY)     # the job runs again the same day
    assert len(sent) == 1
    assert _reminder_count(a) == 1


def test_reminder_goes_to_every_device(users, monkeypatch):
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 42.5, "monthly", TOMORROW)
    _add_subscription(a, EP("phone"))
    _add_subscription(a, EP("laptop"))

    # Global return value again (#157) — the "both devices" claim lives on the
    # worker-scoped set, which is the stronger assertion anyway: it names WHICH
    # endpoints were reached, not merely how many.
    send_due_reminders(today=TODAY)
    assert {s["endpoint"] for s in sent} == {EP("phone"),
                                             EP("laptop")}


def test_dead_subscription_is_deleted_not_retried(users, monkeypatch):
    _enable_push(monkeypatch)
    _capture(monkeypatch, fail_with=pusher.PushGone("gone (410)"))
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 42.5, "monthly", TOMORROW)
    _add_subscription(a, EP("dead"))

    send_due_reminders(today=TODAY)
    assert _subscription_count(a) == 0


def test_transient_failure_keeps_the_subscription(users, monkeypatch):
    # A 500 from the push service is worth another go tomorrow; only 404/410
    # mean "stop sending here".
    _enable_push(monkeypatch)
    _capture(monkeypatch, fail_with=pusher.PushError("503 service unavailable"))
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 42.5, "monthly", TOMORROW)
    _add_subscription(a, EP("flaky"))

    send_due_reminders(today=TODAY)
    assert _subscription_count(a) == 1


def test_reminders_are_per_user(users, monkeypatch):
    _enable_push(monkeypatch)
    sent = _capture(monkeypatch)
    a, b = users["a"]["id"], users["b"]["id"]
    acct_a = create_account(a, "rem-a")
    create_schedule(a, acct_a, 111, "monthly", TOMORROW)
    _add_subscription(a, EP("a-only"))
    _add_subscription(b, EP("b-only"))

    send_due_reminders(today=TODAY)
    endpoints = {s["endpoint"] for s in sent}
    # Both endpoints here belong to this worker, so the set equality is a real
    # claim about isolation between users A and B — which is what it was always
    # meant to test. Before #157 it was also silently asserting that no PARALLEL
    # worker had a bill due, which is why it was the one that went red.
    assert endpoints == {EP("a-only")}   # B never hears about A's bill


def test_no_reminders_when_push_unconfigured(users, monkeypatch):
    _disable_push(monkeypatch)
    sent = _capture(monkeypatch)
    a = users["a"]["id"]
    acct = create_account(a, "rem-acct")
    create_schedule(a, acct, 42.5, "monthly", TOMORROW)
    _add_subscription(a, EP("a-1"))

    # This return value IS safe to assert exactly, unlike the two above: the
    # push_enabled() gate short-circuits to 0 before the sweep, and it reads env
    # vars, which are per-PROCESS — so _disable_push affects this worker only.
    assert send_due_reminders(today=TODAY) == 0
    assert sent == []
    assert _reminder_count(a) == 0      # nothing claimed either


# --- materialization (the half that must NEVER be gated) --------------------

def test_materialize_posts_for_a_user_who_never_logged_in(users):
    # The gap this closes: materialization used to happen only on a page load,
    # so a user who didn't visit got nothing posted — understating their digest,
    # forecast and agent view of their own data.
    a = users["a"]["id"]
    acct = create_account(a, "mat-acct")
    create_schedule(a, acct, 30, "weekly", TODAY - timedelta(weeks=2))
    assert count_transactions_like(a, "seed-schedule") == 0

    materialize_all_users()
    # -14, -7 and today.
    assert count_transactions_like(a, "seed-schedule") == 3


def test_materialize_runs_even_when_push_unconfigured(users, monkeypatch):
    """THE gating trap. The daily job carries materialization as well as
    reminders; hanging the whole thing off a third-party key would mean a
    missing VAPID (or Resend) credential silently stops the ledger updating."""
    _disable_push(monkeypatch)
    sent = _capture(monkeypatch)
    a = users["a"]["id"]
    acct = create_account(a, "mat-acct")
    create_schedule(a, acct, 30, "monthly", TODAY - timedelta(days=1))

    users_done, reminders_sent = run_daily_tasks(today=TODAY)

    assert reminders_sent == 0 and sent == []      # push correctly silent
    assert users_done >= 1
    assert count_transactions_like(a, "seed-schedule") == 1   # ledger still updated


def test_materialize_respects_the_end_date(users):
    # #32 interlock on the write side: the job must not back-fill a schedule
    # that has already finished.
    a = users["a"]["id"]
    acct = create_account(a, "mat-acct")
    long_ago = TODAY - timedelta(weeks=6)
    create_schedule(a, acct, 30, "weekly", long_ago,
                    end_date=long_ago - timedelta(days=1))
    materialize_all_users()
    assert count_transactions_like(a, "seed-schedule") == 0


def test_materialize_is_isolated_per_user(users):
    a, b = users["a"]["id"], users["b"]["id"]
    acct_b = create_account(b, "mat-b")
    create_schedule(b, acct_b, 30, "monthly", TODAY - timedelta(days=1))
    materialize_all_users()
    assert count_transactions_like(a, "seed-schedule") == 0
    assert count_transactions_like(b, "seed-schedule") == 1


def test_daily_job_racing_a_page_load_materializes_once(users):
    """The FOR UPDATE twin of test_schedules.py's concurrency test. Those locks
    used to guard two page loads; now the scheduler thread races them too, which
    is what makes them load-bearing rather than merely prudent."""
    from app.blueprints.schedules import run_due_schedules

    a = users["a"]["id"]
    acct = create_account(a, "race-acct")
    create_schedule(a, acct, 30, "monthly", TODAY - timedelta(days=1))

    barrier = threading.Barrier(4)
    errors = []

    def as_page_load():
        barrier.wait()
        try:
            run_due_schedules(a)
        except Exception as e:  # pragma: no cover - surfaced via the assert
            errors.append(e)

    def as_daily_job():
        barrier.wait()
        try:
            materialize_all_users()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = ([threading.Thread(target=as_page_load) for _ in range(2)]
               + [threading.Thread(target=as_daily_job) for _ in range(2)])
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert count_transactions_like(a, "seed-schedule") == 1
