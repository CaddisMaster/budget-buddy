"""v10.1.1 Hardening — security-bundle regressions.

Covers the pure CSV formula-injection sanitizer, the Flask after_request
security headers, the session cookie flags, and the constant-bcrypt login path
(no username enumeration / no error on a missing user). The IDOR write-side
guards live in test_isolation.py alongside the other isolation tests; the
rate-limit isn't unit-tested because the limiter is disabled under test.
"""
import csv
import io
from datetime import date
from types import SimpleNamespace

from app.blueprints.transactions import _csv_safe, _export_kind
from app.db import get_db_connection
from tests.conftest import PASSWORD, USER_A, create_account, create_transaction, create_transfer

# --- CSV formula-injection sanitizer (pure) ---------------------------------

def test_csv_safe_neutralizes_formula_prefixes():
    for trigger in ("=SUM(1)", "+1", "-1", "@cmd", "\ttab", "\rreturn"):
        out = _csv_safe(trigger)
        assert out == "'" + trigger


def test_csv_safe_leaves_normal_values_untouched():
    assert _csv_safe("groceries") == "groceries"
    assert _csv_safe("") == ""
    assert _csv_safe(42.5) == 42.5            # non-strings pass through
    assert _csv_safe(None) is None


def test_csv_export_neutralizes_formula(client_a, users):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (amount, description, account_id, "
        "transaction_type, transaction_date, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (5.0, "=SUM(A1:A2)", users["a"]["account_id"], "expense",
         "2026-01-01", users["a"]["id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    resp = client_a.get("/transactions/export")
    assert resp.status_code == 200
    assert "'=SUM(A1:A2)" in resp.data.decode()   # apostrophe-prefixed


# --- CSV Kind column (#87) ---------------------------------------------------
#
# The export mirrors the History VIEW, not the analytics rollups: it deliberately
# carries transfer legs and balance adjustments as rows, because that is what the
# page shows. History badges them; the CSV had no column that did, so summing
# Amount double-counted every transfer and folded in adjustments with nothing in
# the file to filter on.


def test_export_kind_is_pure():
    assert _export_kind(SimpleNamespace(is_transfer=True, is_adjustment=False)) == "transfer"
    assert _export_kind(SimpleNamespace(is_transfer=False, is_adjustment=True)) == "adjustment"
    assert _export_kind(SimpleNamespace(is_transfer=False, is_adjustment=False)) == ""
    # Flags are independent columns; transfer wins if a row is somehow both.
    assert _export_kind(SimpleNamespace(is_transfer=True, is_adjustment=True)) == "transfer"


def _export_rows(client):
    resp = client.get("/transactions/export")
    assert resp.status_code == 200
    return list(csv.DictReader(io.StringIO(resp.data.decode())))


def test_export_header_carries_kind(client_a):
    resp = client_a.get("/transactions/export")
    header = resp.data.decode().splitlines()[0]
    assert header == "Date,Type,Amount,Description,Category,Account,Kind"


def test_export_labels_each_row_kind(client_a, users):
    a = users["a"]
    other = create_account(a["id"], "Savings")
    create_transaction(a["id"], a["account_id"], 11.00, date(2026, 3, 2),
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 22.00, date(2026, 3, 3),
                       is_adjustment=True)
    create_transfer(a["id"], a["account_id"], other, 33.00, date(2026, 3, 4))

    by_amount = {r["Amount"]: r["Kind"] for r in _export_rows(client_a)}
    assert by_amount["11.00"] == ""            # ordinary transaction
    assert by_amount["22.00"] == "adjustment"
    assert by_amount["33.00"] == "transfer"    # both legs share the amount


def test_export_keeps_transfer_and_adjustment_rows(client_a, users):
    # The fix is a column, NOT a filter — dropping the rows would break
    # "download what you see" and make the CSV disagree with History.
    a = users["a"]
    other = create_account(a["id"], "Savings")
    create_transaction(a["id"], a["account_id"], 44.00, date(2026, 4, 2),
                       is_adjustment=True)
    create_transfer(a["id"], a["account_id"], other, 55.00, date(2026, 4, 3))

    kinds = [r["Kind"] for r in _export_rows(client_a)]
    assert kinds.count("transfer") == 2        # both legs still present
    assert kinds.count("adjustment") == 1


def test_export_kind_lets_a_spreadsheet_reconcile(client_a, users):
    # The reported symptom: summing Amount does not match the app. Summing only
    # the blank-Kind rows does.
    a = users["a"]
    other = create_account(a["id"], "Savings")
    create_transaction(a["id"], a["account_id"], 100.00, date(2026, 5, 2),
                       category_id=a["category_id"])
    create_transaction(a["id"], a["account_id"], 7.00, date(2026, 5, 3),
                       is_adjustment=True)
    create_transfer(a["id"], a["account_id"], other, 500.00, date(2026, 5, 4))

    rows = _export_rows(client_a)
    everything = sum(float(r["Amount"]) for r in rows)
    real_only = sum(float(r["Amount"]) for r in rows if r["Kind"] == "")
    # 1000 of transfer legs + 7 of adjustment is what used to be invisible.
    assert everything - real_only == 1007.00
    assert real_only == 100.00 + 42.50         # + the fixture's seeded expense


def test_export_kind_is_formula_sanitized(client_a, users):
    # Kind goes through _csv_safe like every other cell. It can only ever be one
    # of three safe literals, so this pins the wiring, not the values.
    a = users["a"]
    create_transaction(a["id"], a["account_id"], 9.00, date(2026, 6, 1),
                       is_adjustment=True)
    assert all(not r["Kind"].startswith("'") for r in _export_rows(client_a))


# --- security headers (after_request) ---------------------------------------

def test_security_headers_present(anon_client):
    resp = anon_client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


# --- session cookie flags ----------------------------------------------------

def test_login_sets_hardened_cookie_flags(anon_client, users):
    resp = anon_client.post(
        "/login",
        data={"username": USER_A, "password": PASSWORD},
        follow_redirects=False,
    )
    set_cookie = " ".join(resp.headers.get_all("Set-Cookie"))
    assert "session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    # Secure is gated on COOKIE_SECURE (unset under test), so it must NOT appear.
    assert "Secure" not in set_cookie


# --- login timing / username enumeration ------------------------------------

def test_login_with_unknown_user_is_rejected_cleanly(anon_client, users):
    # The always-run bcrypt dummy-hash path must not error on a missing user.
    resp = anon_client.post(
        "/login",
        data={"username": "__pytest__nope", "password": "whatever"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data
