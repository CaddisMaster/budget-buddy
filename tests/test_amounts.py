"""Shared amount validation (parse_positive_amount).

float() parses 'nan' and 'inf', and neither trips an `<= 0` check — but a NaN
stored in a numeric column poisons every SUM() the dashboards aggregate. The
helper rejects non-finite values along with non-numbers and non-positives, and
every amount-taking form routes through it. Pure tests cover the helper; route
tests prove a 'nan' post writes nothing to any of the money tables.
"""
import logging

from app.db import get_db_connection
from app.helpers import MAX_AMOUNT, parse_positive_amount, parse_signed_amount
from tests.conftest import (
    count_transactions_like,
    count_transfer_schedules,
    fetch_budget_by_category,
    fetch_transaction,
)

HX = {"HX-Request": "true"}


def _count(table, user_id):
    """Row count for one of the money tables, straight from the DB."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = %s", (user_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


# --- parse_positive_amount (pure) --------------------------------------------

def test_valid_amount_parses():
    assert parse_positive_amount("42.50") == (42.5, None)


def test_whitespace_is_stripped():
    assert parse_positive_amount("  10 ") == (10.0, None)


def test_empty_and_none_are_required():
    assert parse_positive_amount("") == (None, "Amount is required")
    assert parse_positive_amount(None) == (None, "Amount is required")


def test_non_number_rejected():
    assert parse_positive_amount("abc") == (None, "Amount must be a valid number")


def test_non_finite_rejected():
    # float() accepts all of these; none may reach the DB (NaN poisons SUM()).
    for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
        assert parse_positive_amount(raw) == (
            None, "Amount must be a valid number"), raw


def test_zero_and_negative_rejected():
    assert parse_positive_amount("0") == (None, "Amount must be greater than zero")
    assert parse_positive_amount("-5") == (None, "Amount must be greater than zero")


def test_label_customizes_messages():
    assert parse_positive_amount("", label="Target amount") == (
        None, "Target amount is required")
    assert parse_positive_amount("nan", label="Target amount") == (
        None, "Target amount must be a valid number")


# --- parse_signed_amount (pure) -----------------------------------------------
# v10.9 balance check-in: a bank balance is signed (credit cards are negative)
# and may be exactly zero, so the strictly-positive check doesn't apply — but
# the NaN/inf guard absolutely still does.

def test_signed_accepts_negative_and_zero():
    assert parse_signed_amount("-512.10") == (-512.1, None)
    assert parse_signed_amount("0") == (0.0, None)
    assert parse_signed_amount("  42.50 ") == (42.5, None)


def test_signed_empty_and_none_are_required():
    assert parse_signed_amount("") == (None, "Amount is required")
    assert parse_signed_amount(None) == (None, "Amount is required")


def test_signed_non_number_and_non_finite_rejected():
    assert parse_signed_amount("abc") == (None, "Amount must be a valid number")
    for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
        assert parse_signed_amount(raw) == (
            None, "Amount must be a valid number"), raw


def test_signed_label_customizes_messages():
    assert parse_signed_amount("", label="Bank balance") == (
        None, "Bank balance is required")
    assert parse_signed_amount("nan", label="Bank balance") == (
        None, "Bank balance must be a valid number")


# --- every amount form rejects a NaN post ------------------------------------

def test_new_transaction_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/transactions/new", data={
        "amount": "nan",
        "description": "nan-desc",
        "transaction_date": "2026-07-01",
        "account_id": users["a"]["account_id"],
        "transaction_type": "expense",
    })
    assert resp.status_code == 302  # back to the form with a flash, no 500
    assert count_transactions_like(uid, "nan-desc") == 0


def test_edit_transaction_rejects_nan(client_a, users):
    tid = users["a"]["transaction_id"]
    resp = client_a.post(f"/transactions/{tid}/edit", headers=HX, data={
        "amount": "nan",
        "description": "poisoned",
        "transaction_date": "2026-07-01",
        "account_id": users["a"]["account_id"],
        "transaction_type": "expense",
    })
    assert resp.status_code == 200  # edit-row fragment with the error
    amount, description, _ = fetch_transaction(tid)
    assert float(amount) == 42.50  # seeded value untouched
    assert description != "poisoned"


def test_set_budget_rejects_nan(client_a, users):
    uid, cid = users["a"]["id"], users["a"]["category_id"]
    resp = client_a.post("/budgets/set", headers=HX,
                         data={"category_id": cid, "amount": "nan"})
    assert resp.status_code == 200  # error toast, no 500
    assert fetch_budget_by_category(uid, cid) is None


def test_create_schedule_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/scheduled", headers=HX, data={
        "transaction_type": "income",
        "amount": "nan",
        "account_id": users["a"]["account_id"],
        "frequency": "monthly",
        "next_due": "2027-01-01",
    })
    assert resp.status_code == 200
    assert _count("schedules", uid) == 0


def test_create_transfer_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    before = _count("transactions", uid)
    resp = client_a.post("/transfers", data={
        "from_account": users["a"]["account_id"],
        "to_account": users["a"]["account_id"],  # rejected on amount first anyway
        "amount": "nan",
        "transfer_date": "2026-07-01",
    })
    assert resp.status_code == 302
    assert _count("transactions", uid) == before  # no legs inserted


def test_create_transfer_schedule_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/transfers/recurring", headers=HX, data={
        "from_account": users["a"]["account_id"],
        "to_account": users["a"]["account_id"],
        "amount": "nan",
        "frequency": "monthly",
        "next_due": "2027-01-01",
    })
    assert resp.status_code == 200
    assert count_transfer_schedules(uid) == 0


def test_create_goal_rejects_nan(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/goals", headers=HX, data={
        "name": "nan goal",
        "target_amount": "nan",
        "account_id": users["a"]["account_id"],
    })
    assert resp.status_code == 200
    assert _count("goals", uid) == 0


# --- the ceiling (#312) ------------------------------------------------------
# The floor was only half a guard. Every amount column is numeric(10,2), which
# tops out at 99,999,999.99, so a well-formed larger number passed validation,
# reached the INSERT and came back as `numeric field overflow` — which the write
# handlers correctly treat as an UNEXPECTED failure: the user was told the
# system broke (with nothing to try again, since resubmitting fails
# identically) and the server logged a traceback for ordinary bad input.
# Same shape as the ?page bug: a range guarded at one end only.

def test_the_bound_matches_the_column():
    """The constant is only correct relative to the schema, so tie it to the
    schema rather than to itself. numeric(10,2) is 8 integer digits + 2 decimal
    places."""
    assert MAX_AMOUNT == 99_999_999.99
    assert len(str(int(MAX_AMOUNT))) == 8


def test_amount_at_the_ceiling_is_accepted():
    assert parse_positive_amount("99999999.99") == (99_999_999.99, None)


def test_amount_above_the_ceiling_is_rejected():
    """Asserted from BOTH sides with the test above — a cap set anywhere would
    satisfy "a big number fails", which is what makes a one-sided test useless
    here."""
    assert parse_positive_amount("100000000.00") == (
        None, "Amount must be 99,999,999.99 or less")
    assert parse_positive_amount("999999999999") == (
        None, "Amount must be 99,999,999.99 or less")


def test_the_ceiling_names_its_label():
    assert parse_positive_amount("1e12", label="Target amount") == (
        None, "Target amount must be 99,999,999.99 or less")


def test_the_negative_end_is_bounded_too():
    """parse_signed_amount takes a bank balance, which can be negative — and
    -100000000 overflows numeric(10,2) exactly as the positive end does."""
    assert parse_signed_amount("-99999999.99") == (-99_999_999.99, None)
    assert parse_signed_amount("-100000000.00", "Bank balance") == (
        None, "Bank balance must be 99,999,999.99 or less")


def test_ordinary_amounts_are_untouched():
    assert parse_positive_amount("42.50") == (42.5, None)
    assert parse_signed_amount("-1200") == (-1200.0, None)


# --- every amount form rejects an over-large post ---------------------------
# Mirrors the NaN sweep above: the point is that each form answers with a
# validation error rather than GENERIC_ERROR, and writes nothing.

TOO_BIG = "100000000"


def test_new_transaction_rejects_an_over_large_amount(client_a, users):
    uid = users["a"]["id"]
    before = _count("transactions", uid)
    resp = client_a.post("/transactions/new", data={
        "amount": TOO_BIG, "description": "huge",
        "transaction_date": "2026-06-10", "transaction_type": "expense",
        "category_id": users["a"]["category_id"],
        "account_id": users["a"]["account_id"]}, follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "99,999,999.99 or less" in body
    assert "Something went wrong" not in body
    assert _count("transactions", uid) == before


def test_an_over_large_amount_logs_no_exception(client_a, users, caplog):
    """The second half of the defect, and the half with no visible symptom.

    GENERIC_ERROR is documented as the message for an UNEXPECTED write failure
    and is paired with logger.exception(). Reaching it from ordinary user input
    put a traceback in the log for every fat-fingered amount, which makes the
    log noisier without making it more informative and buries the real
    unexpected failures among them.
    """
    with caplog.at_level(logging.ERROR):
        resp = client_a.post("/budgets/set", headers=HX, data={
            "category_id": users["a"]["category_id"], "amount": TOO_BIG})
    assert resp.status_code == 200
    assert not [r for r in caplog.records if r.exc_info], (
        "an over-large amount logged an exception traceback: "
        + "\n".join(r.getMessage() for r in caplog.records)
    )


def test_set_budget_rejects_an_over_large_amount(client_a, users):
    uid, cid = users["a"]["id"], users["a"]["category_id"]
    resp = client_a.post("/budgets/set", headers=HX,
                         data={"category_id": cid, "amount": TOO_BIG})
    assert resp.status_code == 200
    assert "99,999,999.99 or less" in resp.headers.get("HX-Trigger", "")
    assert fetch_budget_by_category(uid, cid) is None


def test_create_goal_rejects_an_over_large_amount(client_a, users):
    uid = users["a"]["id"]
    resp = client_a.post("/goals", headers=HX, data={
        "name": "big goal", "target_amount": TOO_BIG,
        "account_id": users["a"]["account_id"]})
    assert resp.status_code == 200
    # This form answers with a toast and no swap, so the message rides the
    # HX-Trigger header rather than the (empty) body.
    assert "99,999,999.99 or less" in resp.headers.get("HX-Trigger", "")
    assert _count("goals", uid) == 0


def test_account_checkin_rejects_an_over_large_balance(client_a, users):
    """The signed path — a bank balance, which is where parse_signed_amount is
    reached directly rather than through parse_positive_amount."""
    uid = users["a"]["id"]
    before = _count("transactions", uid)
    resp = client_a.post(f"/accounts/{users['a']['account_id']}/checkin",
                         headers=HX, data={"actual_balance": "-" + TOO_BIG})
    assert resp.status_code == 200
    assert "99,999,999.99 or less" in resp.get_data(as_text=True)
    assert _count("transactions", uid) == before
