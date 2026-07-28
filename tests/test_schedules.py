"""v10.0 Scheduled — recurring income/expense schedules.

Covers the pure next-due math, the run_due_schedules generator (materialize +
catch-up + isolation + active gate + concurrent-run locking), and the inline-CRUD
routes (fragment shape, persistence, ownership 404s). The route tests rely on the
dev DB via the shared fixtures; CSRF + rate limiter are disabled under test.
"""
import threading
from datetime import date, timedelta

from app.blueprints.schedules import (
    compute_initial_semimonthly_due,
    run_due_schedules,
)
from app.db import get_db_connection
from tests.conftest import (
    count_transactions_like,
    create_schedule,
    fetch_schedule,
)

HX = {"HX-Request": "true"}


def _schedule_count(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schedules WHERE user_id = %s", (user_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def _scalar(sql, params):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def _latest_schedule_id(user_id):
    return _scalar(
        "SELECT id FROM schedules WHERE user_id = %s ORDER BY id DESC LIMIT 1",
        (user_id,))


def _fetch_end_date(schedule_id):
    return _scalar("SELECT end_date FROM schedules WHERE id = %s", (schedule_id,))


# --- compute_initial_semimonthly_due (pure) ---------------------------------

def test_semimonthly_due_before_both_pay_days():
    # 5th, pay days 10 & 25 → next is the 10th this month.
    assert compute_initial_semimonthly_due(10, 25, date(2026, 6, 5)) == date(2026, 6, 10)


def test_semimonthly_due_between_pay_days():
    # 22nd, pay days 10 & 25 → 10th passed, next is the 25th (the bug scenario).
    assert compute_initial_semimonthly_due(10, 25, date(2026, 6, 22)) == date(2026, 6, 25)


def test_semimonthly_due_after_both_rolls_to_next_month():
    assert compute_initial_semimonthly_due(10, 25, date(2026, 6, 28)) == date(2026, 7, 10)


def test_semimonthly_due_on_a_pay_day_is_today():
    assert compute_initial_semimonthly_due(10, 25, date(2026, 6, 10)) == date(2026, 6, 10)


def test_semimonthly_due_clamps_last_day():
    # Pay day 31 in a 30-day month after the 15th → 30th.
    assert compute_initial_semimonthly_due(15, 31, date(2026, 6, 20)) == date(2026, 6, 30)


# --- run_due_schedules ------------------------------------------------------

def test_due_schedule_materializes_one_and_advances(users):
    uid = users["a"]["id"]
    yesterday = date.today() - timedelta(days=1)
    sid = create_schedule(uid, users["a"]["account_id"], 50, "monthly", yesterday,
                          transaction_type="income")
    run_due_schedules(uid)
    assert count_transactions_like(uid, "seed-schedule") == 1
    # next_due advanced into the future so a re-run doesn't duplicate.
    assert fetch_schedule(sid)[2] > date.today()
    run_due_schedules(uid)
    assert count_transactions_like(uid, "seed-schedule") == 1


def test_far_behind_schedule_catches_up(users):
    uid = users["a"]["id"]
    three_weeks_ago = date.today() - timedelta(weeks=3)
    create_schedule(uid, users["a"]["account_id"], 20, "weekly", three_weeks_ago)
    run_due_schedules(uid)
    # Occurrences at -21, -14, -7, and today → 4 generated.
    assert count_transactions_like(uid, "seed-schedule") == 4


def test_future_schedule_generates_nothing(users):
    uid = users["a"]["id"]
    next_week = date.today() + timedelta(days=7)
    create_schedule(uid, users["a"]["account_id"], 20, "weekly", next_week)
    run_due_schedules(uid)
    assert count_transactions_like(uid, "seed-schedule") == 0


def test_inactive_schedule_generates_nothing(users):
    uid = users["a"]["id"]
    yesterday = date.today() - timedelta(days=1)
    create_schedule(uid, users["a"]["account_id"], 20, "monthly", yesterday,
                    is_active=False)
    run_due_schedules(uid)
    assert count_transactions_like(uid, "seed-schedule") == 0


def test_concurrent_runs_materialize_exactly_once(users):
    # gunicorn serves requests on multiple threads, so two page loads can run
    # run_due_schedules at the same time. The FOR UPDATE row lock must serialize
    # them: exactly one occurrence gets materialized, never a duplicate. Each
    # call opens its own connection (db_cursor), so real thread interleaving is
    # exercised here.
    uid = users["a"]["id"]
    yesterday = date.today() - timedelta(days=1)
    sid = create_schedule(uid, users["a"]["account_id"], 50, "monthly", yesterday)

    barrier = threading.Barrier(4)
    errors = []

    def run():
        barrier.wait()  # line all threads up on the same race window
        try:
            run_due_schedules(uid)
        except Exception as e:  # pragma: no cover - surfaced via the assert
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert count_transactions_like(uid, "seed-schedule") == 1
    assert fetch_schedule(sid)[2] > date.today()


def test_run_due_schedules_is_user_scoped(users):
    a_id, b_id = users["a"]["id"], users["b"]["id"]
    yesterday = date.today() - timedelta(days=1)
    create_schedule(a_id, users["a"]["account_id"], 20, "monthly", yesterday)
    run_due_schedules(b_id)  # B's run must not fire A's schedule
    assert count_transactions_like(a_id, "seed-schedule") == 0
    run_due_schedules(a_id)
    assert count_transactions_like(a_id, "seed-schedule") == 1


# --- inline CRUD routes -----------------------------------------------------

def test_create_schedule_returns_row_fragment(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/scheduled", headers=HX, data={
        "transaction_type": "income",
        "amount": "1500",
        "description": "Paycheck",
        "account_id": users["a"]["account_id"],
        "frequency": "monthly",
        "next_due": (date.today() + timedelta(days=3)).isoformat(),
    })
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "<html" not in body.lower()
    assert "schedule-" in body
    assert _schedule_count(uid) == 1


def test_create_semimonthly_computes_next_due(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/scheduled", headers=HX, data={
        "transaction_type": "income",
        "amount": "1000",
        "account_id": users["a"]["account_id"],
        "frequency": "semimonthly",
        "anchor_day": "10",
        "second_day": "25",
    })
    assert resp.status_code == 200
    assert _schedule_count(uid) == 1


def test_create_schedule_rejects_past_date(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/scheduled", headers=HX, data={
        "transaction_type": "expense",
        "amount": "30",
        "account_id": users["a"]["account_id"],
        "frequency": "monthly",
        "next_due": (date.today() - timedelta(days=2)).isoformat(),
    })
    assert resp.status_code == 200
    assert _schedule_count(uid) == 0  # nothing created on validation failure


def test_edit_then_delete_schedule(client_a, users):
    uid = users["a"]["id"]
    sid = create_schedule(uid, users["a"]["account_id"], 40, "monthly",
                          date.today() + timedelta(days=5))
    # Edit form fragment.
    resp = client_a.get(f"/scheduled/{sid}/edit", headers=HX)
    assert resp.status_code == 200
    assert "<html" not in resp.data.decode().lower()
    # Save an update.
    resp = client_a.post(f"/scheduled/{sid}/edit", headers=HX, data={
        "transaction_type": "expense",
        "amount": "99",
        "account_id": users["a"]["account_id"],
        "frequency": "monthly",
        "next_due": (date.today() + timedelta(days=5)).isoformat(),
    })
    assert resp.status_code == 200
    assert float(fetch_schedule(sid)[0]) == 99
    # Delete.
    resp = client_a.delete(f"/scheduled/{sid}", headers=HX)
    assert resp.status_code == 200
    assert _schedule_count(uid) == 0


def test_cannot_edit_or_delete_other_users_schedule(client_a, users):
    b_sid = create_schedule(users["b"]["id"], users["b"]["account_id"], 10,
                            "monthly", date.today() + timedelta(days=5))
    assert client_a.get(f"/scheduled/{b_sid}/edit", headers=HX).status_code == 404
    assert client_a.get(f"/scheduled/{b_sid}/row", headers=HX).status_code == 404
    assert client_a.delete(f"/scheduled/{b_sid}", headers=HX).status_code == 404
    # B's schedule untouched.
    assert fetch_schedule(b_sid) is not None


# --- #32 end date -----------------------------------------------------------

def test_schedule_stops_materializing_at_its_end_date(users):
    # The issue's headline scenario. A weekly schedule three weeks overdue with
    # an end date two weeks back: the catch-up loop must post the occurrences up
    # to the end date and then stop, not run to today.
    uid = users["a"]["id"]
    three_weeks_ago = date.today() - timedelta(weeks=3)
    create_schedule(uid, users["a"]["account_id"], 20, "weekly", three_weeks_ago,
                    end_date=three_weeks_ago + timedelta(weeks=1))
    run_due_schedules(uid)
    # Occurrences at -21 and -14 only; -7 and today are past the end date.
    assert count_transactions_like(uid, "seed-schedule") == 2


def test_finished_schedule_never_backfills(users):
    # The trap called out in the issue: a schedule whose next_due went stale AND
    # whose end date has since passed must post NOTHING when the user finally
    # logs in — not a back-fill of every occurrence it "missed".
    uid = users["a"]["id"]
    long_ago = date.today() - timedelta(weeks=8)
    create_schedule(uid, users["a"]["account_id"], 20, "weekly", long_ago,
                    end_date=long_ago - timedelta(days=1))
    run_due_schedules(uid)
    assert count_transactions_like(uid, "seed-schedule") == 0


def test_schedule_ending_today_still_posts_today(users):
    # The end date is the last day the schedule RUNS, not the first day it
    # doesn't — an off-by-one here silently drops a real final payment.
    uid = users["a"]["id"]
    today = date.today()
    create_schedule(uid, users["a"]["account_id"], 20, "weekly", today,
                    end_date=today)
    run_due_schedules(uid)
    assert count_transactions_like(uid, "seed-schedule") == 1


def test_schedule_without_end_date_is_unchanged(users):
    # Every schedule that existed before #32 has end_date NULL. Same catch-up
    # count as test_far_behind_schedule_catches_up.
    uid = users["a"]["id"]
    three_weeks_ago = date.today() - timedelta(weeks=3)
    create_schedule(uid, users["a"]["account_id"], 20, "weekly", three_weeks_ago,
                    end_date=None)
    run_due_schedules(uid)
    assert count_transactions_like(uid, "seed-schedule") == 4


def test_finished_schedule_shows_as_finished(client_a, users):
    uid = users["a"]["id"]
    long_ago = date.today() - timedelta(weeks=8)
    create_schedule(uid, users["a"]["account_id"], 20, "weekly", long_ago,
                    end_date=long_ago + timedelta(days=1))
    run_due_schedules(uid)
    body = client_a.get("/scheduled").data.decode()
    # Visibly finished, not silently inactive (the fourth Gherkin scenario).
    assert "Finished" in body


def test_running_schedule_shows_its_end_date(client_a, users):
    uid = users["a"]["id"]
    end = date.today() + timedelta(days=90)
    create_schedule(uid, users["a"]["account_id"], 20, "monthly",
                    date.today() + timedelta(days=7), end_date=end)
    body = client_a.get("/scheduled").data.decode()
    assert f"until {end.isoformat()}" in body
    assert "Finished" not in body


def test_end_date_before_next_date_is_rejected(client_a, users):
    today = date.today()
    resp = client_a.post("/scheduled", data={
        "transaction_type": "expense", "amount": "25",
        "description": "__pytest__end-date-reject",
        "account_id": users["a"]["account_id"], "frequency": "monthly",
        "next_due": (today + timedelta(days=30)).isoformat(),
        "end_date": (today + timedelta(days=10)).isoformat(),
    }, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    # Nothing written — the whole point of validating before the INSERT.
    assert _schedule_count(users["a"]["id"]) == 0


def test_invalid_end_date_is_rejected(client_a, users):
    today = date.today()
    resp = client_a.post("/scheduled", data={
        "transaction_type": "expense", "amount": "25",
        "account_id": users["a"]["account_id"], "frequency": "monthly",
        "next_due": (today + timedelta(days=7)).isoformat(),
        "end_date": "not-a-date",
    }, headers=HX)
    assert resp.status_code == 200
    assert "showToast" in resp.headers.get("HX-Trigger", "")
    assert _schedule_count(users["a"]["id"]) == 0


def test_end_date_persists_and_clears(client_a, users):
    today = date.today()
    end = today + timedelta(days=60)
    client_a.post("/scheduled", data={
        "transaction_type": "expense", "amount": "25",
        "description": "__pytest__end-date-persist",
        "account_id": users["a"]["account_id"], "frequency": "monthly",
        "next_due": (today + timedelta(days=7)).isoformat(),
        "end_date": end.isoformat(),
    }, headers=HX)
    sid = _latest_schedule_id(users["a"]["id"])
    assert _fetch_end_date(sid) == end

    # Blank clears it — the schedule runs indefinitely again.
    client_a.post(f"/scheduled/{sid}/edit", data={
        "transaction_type": "expense", "amount": "25",
        "description": "__pytest__end-date-persist",
        "account_id": users["a"]["account_id"], "frequency": "monthly",
        "next_due": (today + timedelta(days=7)).isoformat(),
        "end_date": "",
    }, headers=HX)
    assert _fetch_end_date(sid) is None
