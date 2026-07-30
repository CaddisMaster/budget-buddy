"""#86 Pending transactions — a display flag for a provisional amount.

A fuel pump authorises $1.00 and the real amount lands days later. A pending row
is pinned to the top of History so it can't be forgotten, and the flag is cleared
by hand once the amount has been corrected.

Two settled decisions this file pins down (Sean, 2026-07-29):

  1. A pending row counts NORMALLY in every figure. It is not an exclusion like
     is_adjustment — the money did leave the account.
  2. Clearing is a MANUAL toggle. Editing the amount does not clear it.

And one design decision made while implementing: the pin is sorted in Python
AFTER the page is fetched, never as an `is_pending DESC` prefix on the SQL, because
the running-balance walk seeds itself from "every filtered row older than this
page" using that same ORDER BY. The balance tests below are what guard that.
"""
from datetime import date, timedelta

from app.db import get_db_connection
from tests.conftest import create_account, create_category, create_transaction


def _mark_pending(transaction_id):
    """Set the flag directly — the create form is covered separately."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE transactions SET is_pending = true WHERE id = %s", (transaction_id,))
    conn.commit()
    cur.close()
    conn.close()


def _is_pending(transaction_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_pending FROM transactions WHERE id = %s", (transaction_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0]


def _row_order(body, descriptions):
    """Positions of each description in the rendered page, in page order."""
    return [body.index(d) for d in descriptions]


# --- creating -----------------------------------------------------------------

def test_create_with_pending_box_sets_the_flag(client_a, users):
    a = users["a"]
    resp = client_a.post("/transactions/new", data={
        "amount": "1.00", "description": "gas hold", "transaction_date": str(date.today()),
        "account_id": a["account_id"], "transaction_type": "expense", "is_pending": "true",
    }, follow_redirects=True)
    assert resp.status_code == 200

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_pending FROM transactions WHERE description = 'gas hold' AND user_id = %s",
                (a["id"],))
    assert cur.fetchone()[0] is True
    cur.close()
    conn.close()


def test_create_without_the_box_is_posted(client_a, users):
    a = users["a"]
    client_a.post("/transactions/new", data={
        "amount": "20.00", "description": "settled thing", "transaction_date": str(date.today()),
        "account_id": a["account_id"], "transaction_type": "expense",
    }, follow_redirects=True)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_pending FROM transactions WHERE description = 'settled thing' AND user_id = %s",
                (a["id"],))
    assert cur.fetchone()[0] is False
    cur.close()
    conn.close()


def test_pending_row_renders_a_badge(client_a, users):
    # Content-asserting: HistoryRow is built positionally, so a mis-threaded
    # field renders as an empty string in Jinja rather than raising.
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    body = client_a.get("/transactions").data.decode()
    assert "pending-badge" in body
    assert "Pending" in body


def test_posted_rows_render_no_badge(client_a):
    body = client_a.get("/transactions").data.decode()
    assert "pending-badge" not in body


# --- pinning ------------------------------------------------------------------

def test_pending_row_is_pinned_above_posted_rows(client_a, users):
    a = users["a"]
    old = create_transaction(a["id"], a["account_id"], 1.00,
                             date.today() - timedelta(days=40), category_id=a["category_id"])
    _mark_pending(old)
    create_transaction(a["id"], a["account_id"], 60.00, date.today(),
                       category_id=a["category_id"])

    body = client_a.get("/transactions").data.decode()
    # The pending row is dated 40 days ago; in pure date order it would be LAST.
    pending_pos = body.index(f'id="txn-{old}"')
    other_positions = [body.index(f'id="txn-{t}"') for t in _other_ids(a["id"], old)]
    assert other_positions, "no posted rows to compare against"
    assert all(pending_pos < p for p in other_positions)


def _other_ids(user_id, exclude_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM transactions WHERE user_id = %s AND id <> %s", (user_id, exclude_id))
    ids = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


def test_posted_rows_below_stay_date_descending(client_a, users):
    a = users["a"]
    pending = create_transaction(a["id"], a["account_id"], 1.00,
                                 date(2026, 1, 5), category_id=a["category_id"])
    _mark_pending(pending)
    create_transaction(a["id"], a["account_id"], 10.00, date(2026, 3, 1),
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 11.00, date(2026, 4, 1),
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 12.00, date(2026, 5, 1),
                       category_id=a["category_id"])

    body = client_a.get("/transactions?month=2026-03").data.decode()
    assert "pending-badge" not in body     # Jan row isn't in a March filter

    body = client_a.get("/transactions").data.decode()
    # Newest posted first, oldest last — the stable sort must not scramble them.
    assert _row_order(body, ["$12.00", "$11.00", "$10.00"]) == \
        sorted(_row_order(body, ["$12.00", "$11.00", "$10.00"]))


def test_several_pending_rows_stay_newest_first(client_a, users):
    a = users["a"]
    for day, amount in ((1, 5.00), (10, 6.00), (20, 7.00)):
        tid = create_transaction(a["id"], a["account_id"], amount, date(2026, 2, day),
                                 category_id=a["category_id"])
        _mark_pending(tid)

    body = client_a.get("/transactions").data.decode()
    # Newest pending (the 20th, $7) first; all three above the fixture's row.
    positions = _row_order(body, ["$7.00", "$6.00", "$5.00", "$42.50"])
    assert positions == sorted(positions)


def test_pinning_does_not_override_the_month_filter(client_a, users):
    # Pinning reorders within the filtered set; it does not smuggle a row past
    # the filter, or the filter would stop meaning anything.
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 99.00, date(2025, 8, 4),
                             category_id=a["category_id"])
    _mark_pending(tid)

    body = client_a.get(f"/transactions?month={date.today().strftime('%Y-%m')}").data.decode()
    assert f'id="txn-{tid}"' not in body
    assert "$99.00" not in body


# --- the running balance ------------------------------------------------------

def test_pending_row_shows_an_em_dash_instead_of_a_balance(client_a, users):
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    body = client_a.get("/transactions").data.decode()
    # Anchored to the balance cell — a bare "—" also matches the "— none —"
    # option labels in the edit selects, so it would pass without the feature.
    assert '<td class="c-bal" style="color:var(--text-muted)">—</td>' in body
    # And the posted rows still show real balances.
    assert '<td class="c-bal ' in body


def test_posted_balances_are_unchanged_by_a_pending_row(client_a, users):
    """The property that guards the walk. A pending row is pinned out of date
    order, but it still participates in the balance exactly as before — so the
    posted rows' balances must be identical to what they are when the same row is
    posted. If the pin were an ORDER BY prefix, this is the test that breaks."""
    a = users["a"]
    create_transaction(a["id"], a["account_id"], 100.00, date(2026, 6, 1),
                       category_id=a["category_id"])
    tid = create_transaction(a["id"], a["account_id"], 30.00, date(2026, 6, 2),
                             category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 20.00, date(2026, 6, 3),
                       category_id=a["category_id"])

    posted_body = client_a.get("/transactions?month=2026-06").data.decode()
    _mark_pending(tid)
    pending_body = client_a.get("/transactions?month=2026-06").data.decode()

    # The two rows that stayed posted keep their exact balance cells. The
    # template renders ${{ ...|money }}, so a negative balance reads "$-100.00".
    for balance in ("$-100.00", "$-150.00"):
        assert balance in posted_body, f"{balance} missing from the baseline render"
        assert balance in pending_body, f"{balance} changed when a sibling went pending"


def test_page_one_top_balance_still_reflects_the_full_net(client_a, users):
    # Pages connect via the seed query; pinning must not disturb it.
    a = users["a"]
    for i in range(30):
        create_transaction(a["id"], a["account_id"], 10.00, date(2026, 7, 1) + timedelta(days=i),
                           category_id=a["category_id"])
    tid = create_transaction(a["id"], a["account_id"], 5.00, date(2026, 7, 2),
                             category_id=a["category_id"])
    _mark_pending(tid)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(CASE WHEN transaction_type='income' THEN amount ELSE -amount END), 0) "
                "FROM transactions WHERE user_id = %s", (a["id"],))
    full_net = cur.fetchone()[0]
    cur.close()
    conn.close()

    body = client_a.get("/transactions").data.decode()
    assert f"$-{abs(full_net):,.2f}" in body


# --- marking posted -----------------------------------------------------------

def test_mark_posted_clears_the_flag(client_a, users):
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    resp = client_a.post(f"/transactions/{tid}/mark-posted", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert _is_pending(tid) is False
    assert "pending-badge" not in resp.data.decode()


def test_mark_posted_returns_the_whole_tbody(client_a, users):
    # Un-pinning reorders every row, so a single-row swap would be wrong.
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    body = client_a.post(f"/transactions/{tid}/mark-posted",
                         headers={"HX-Request": "true"}).data.decode()
    assert 'id="txn-rows"' in body
    assert "<html" not in body      # a fragment, not a page


def test_mark_posted_returns_the_row_to_date_order(client_a, users):
    a = users["a"]
    old = create_transaction(a["id"], a["account_id"], 77.00,
                             date(2026, 1, 9), category_id=a["category_id"])
    _mark_pending(old)
    create_transaction(a["id"], a["account_id"], 88.00, date.today(),
                       category_id=a["category_id"])

    pinned = client_a.get("/transactions").data.decode()
    assert pinned.index("$77.00") < pinned.index("$88.00")

    client_a.post(f"/transactions/{old}/mark-posted", headers={"HX-Request": "true"})
    after = client_a.get("/transactions").data.decode()
    assert after.index("$88.00") < after.index("$77.00")   # back in date order


def test_mark_posted_cannot_set_the_flag(client_a, users):
    # One direction only — calling it on a posted row is a no-op, never a toggle.
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    client_a.post(f"/transactions/{tid}/mark-posted", headers={"HX-Request": "true"})
    assert _is_pending(tid) is False


# --- editing ------------------------------------------------------------------

def test_editing_the_amount_does_not_clear_the_flag(client_a, users):
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    resp = client_a.post(f"/transactions/{tid}/edit", data={
        "amount": "47.31", "description": "gas", "transaction_date": str(date.today()),
        "account_id": a["account_id"], "transaction_type": "expense",
    }, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert _is_pending(tid) is True          # settled decision #2

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT amount FROM transactions WHERE id = %s", (tid,))
    assert float(cur.fetchone()[0]) == 47.31
    cur.close()
    conn.close()


# --- pending counts everywhere (settled decision #1) --------------------------

def test_pending_expense_counts_toward_month_spending(client_a, users):
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 50.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    body = client_a.get(f"/?month={date.today().strftime('%Y-%m')}").data.decode()
    # 50.00 pending + the fixture's 42.50 posted expense.
    assert "$92.50" in body


def test_pending_row_is_an_autocategorize_candidate(client_a, users, monkeypatch):
    # is_pending must not act like is_adjustment, which IS excluded here.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 15.00, date.today())  # no category
    _mark_pending(tid)
    from app.blueprints.transactions import count_uncategorized
    assert count_uncategorized(a["id"]) >= 1


# --- the CSV export keeps pure date order ------------------------------------

def test_export_ignores_pending_status(client_a, users):
    # An export that leads with pending rows is surprising; only History pins.
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 3.00, date(2026, 1, 3),
                             category_id=a["category_id"])
    _mark_pending(tid)
    create_transaction(a["id"], a["account_id"], 4.00, date(2026, 9, 4),
                       category_id=a["category_id"])

    body = client_a.get("/transactions/export").data.decode()
    # Newest first: the September row precedes the pending January one.
    assert body.index("2026-09-04") < body.index("2026-01-03")


def test_cleanup_candidates_keep_date_order(client_a, users):
    a = users["a"]
    old_pending = create_transaction(a["id"], a["account_id"], 8.00, date(2026, 1, 8))
    _mark_pending(old_pending)
    newer = create_transaction(a["id"], a["account_id"], 9.00, date(2026, 8, 9))

    from app.blueprints.transactions import _load_cleanup_candidates
    ids = [r['id'] for r in _load_cleanup_candidates(a["id"])]   # dicts, not rows
    assert ids.index(newer) < ids.index(old_pending)


# --- isolation ----------------------------------------------------------------

def test_another_users_pending_row_is_not_shown(client_a, users):
    b = users["b"]
    tid = create_transaction(b["id"], b["account_id"], 123.45, date.today(),
                             category_id=b["category_id"])
    _mark_pending(tid)
    body = client_a.get("/transactions").data.decode()
    assert "$123.45" not in body
    assert f'id="txn-{tid}"' not in body


def test_mark_posted_on_another_users_row_is_404(client_a, users):
    b = users["b"]
    tid = create_transaction(b["id"], b["account_id"], 10.00, date.today(),
                             category_id=b["category_id"])
    _mark_pending(tid)
    resp = client_a.post(f"/transactions/{tid}/mark-posted", headers={"HX-Request": "true"})
    assert resp.status_code == 404
    assert _is_pending(tid) is True      # unchanged


def test_mark_posted_on_a_missing_row_is_404(client_a):
    assert client_a.post("/transactions/99999999/mark-posted").status_code == 404


def test_mark_posted_requires_login(anon_client, users):
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    resp = anon_client.post(f"/transactions/{tid}/mark-posted")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert _is_pending(tid) is True


def test_pending_flag_survives_an_unrelated_users_activity(client_a, users):
    a, b = users["a"], users["b"]
    mine = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                              category_id=a["category_id"])
    _mark_pending(mine)
    theirs = create_transaction(b["id"], b["account_id"], 2.00, date.today(),
                                category_id=b["category_id"])
    _mark_pending(theirs)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE transactions SET is_pending = false WHERE id = %s AND user_id = %s",
                (theirs, b["id"]))
    conn.commit()
    cur.close()
    conn.close()
    assert _is_pending(mine) is True


# --- schedules and transfers are never born pending --------------------------

def test_a_transfer_leg_has_no_mark_posted_button(client_a, users):
    from tests.conftest import create_transfer
    a = users["a"]
    other = create_account(a["id"], "Savings")
    create_transfer(a["id"], a["account_id"], other, 25.00, date.today())
    body = client_a.get("/transactions").data.decode()
    assert "mark-posted" not in body


def test_a_new_category_does_not_disturb_pending(client_a, users):
    a = users["a"]
    tid = create_transaction(a["id"], a["account_id"], 1.00, date.today(),
                             category_id=a["category_id"])
    _mark_pending(tid)
    create_category(a["id"], "Fuel")
    assert _is_pending(tid) is True
