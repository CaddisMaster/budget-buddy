"""Account transfer tests.

A transfer is a linked pair of ledger rows (expense out of A, income into B)
sharing a transfer_group_id. These assert the pair stays consistent through
create/edit/delete, that balances move but analytics excludes the transfer, and
that one user can't touch another user's transfer group.
"""
from datetime import date

from tests.conftest import (
    account_balance,
    count_transfer_legs,
    create_account,
    create_transfer,
)

TODAY = date.today().isoformat()


# --- create -----------------------------------------------------------------

def test_create_transfer_moves_both_balances(client_a, users):
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-dest")
    from_before = account_balance(from_acct)
    to_before = account_balance(to_acct)

    response = client_a.post(
        "/transfers",
        data={
            "from_account": from_acct,
            "to_account": to_acct,
            "amount": "150.00",
            "description": "rent savings",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert account_balance(from_acct) == from_before - 150.00
    assert account_balance(to_acct) == to_before + 150.00


def test_transfer_to_same_account_rejected(client_a, users):
    acct = users["a"]["account_id"]
    before = account_balance(acct)
    client_a.post(
        "/transfers",
        data={
            "from_account": acct,
            "to_account": acct,
            "amount": "50.00",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert account_balance(acct) == before  # nothing recorded


def test_a_bad_transfer_date_is_a_validation_error_not_a_server_fault(client_a, users):
    """#309, tranche 2 — the one-off transfer form did not validate its date.

    Every other form in the app parses its date and answers "Date must be a
    valid date": `transactions.py` does it on both create and edit, and the
    RECURRING transfer form twenty lines further down this same blueprint does
    it too. The one-off form checked only that the field was non-empty and
    handed the string to Postgres, which raised `invalid input syntax for type
    date`. That is caught as an unexpected write failure — so the user was told
    "Something went wrong — please try again" about a typo, and the server
    logged an exception traceback for it.

    Nothing was ever written, so this is about the report rather than the data:
    a generic fault message is unactionable (retrying the same date fails
    identically) and it buries genuine unexpected failures in the log.
    """
    from app.helpers import GENERIC_ERROR
    from tests.conftest import count_transactions_like

    other = create_account(users["a"]["id"], "bad-date-to")
    response = client_a.post(
        "/transfers",
        data={
            "from_account": users["a"]["account_id"],
            "to_account": other,
            "amount": "5.00",
            "transfer_date": "not-a-date",
            "description": "bad-transfer-date",
        },
        follow_redirects=True,
    )
    text = response.data.decode()
    assert "Date must be a valid date" in text
    assert GENERIC_ERROR not in text
    # The pre-existing behaviour that must not regress: nothing is written.
    assert count_transactions_like(users["a"]["id"], "bad-transfer-date") == 0


def test_a_valid_transfer_date_still_posts(client_a, users):
    """The other side of the guard — it must reject a non-date, not dates."""
    other = create_account(users["a"]["id"], "good-date-to")
    before = account_balance(other)
    client_a.post(
        "/transfers",
        data={
            "from_account": users["a"]["account_id"],
            "to_account": other,
            "amount": "12.00",
            "transfer_date": TODAY,
            "description": "good-transfer-date",
        },
        follow_redirects=True,
    )
    assert account_balance(other) == before + 12


# --- analytics exclusion vs balance inclusion -------------------------------

def test_transfer_excluded_from_analytics_but_kept_in_balance(client_a, users):
    # A's only real activity is the seeded 42.50 expense. A 500 transfer must
    # not inflate the income/expense figures, but must move account balances.
    # (v10.9: analytics merged into the dashboard — the hero shows the same
    # income/expense totals the old analytics summary did. Balances may
    # legitimately show the 500, so only the flow figures are asserted.)
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-savings")
    create_transfer(users["a"]["id"], from_acct, to_acct, 500.00, TODAY)

    response = client_a.get("/")
    assert response.status_code == 200
    # Real expense total stays 42.50, not 542.50; real income stays 0.
    assert b">$42.50<" in response.data
    assert b"542.50" not in response.data
    assert b">$0.00<" in response.data  # hero income untouched by the transfer
    # But the money really moved between accounts.
    assert account_balance(to_acct) == 500.00


# --- edit (acts on the pair) ------------------------------------------------

def test_edit_transfer_updates_both_legs(client_a, users):
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-2")
    gid = create_transfer(users["a"]["id"], from_acct, to_acct, 100.00, TODAY)

    response = client_a.post(
        f"/transfers/{gid}/edit",
        data={
            "from_account": from_acct,
            "to_account": to_acct,
            "amount": "250.00",
            "description": "bumped",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert count_transfer_legs(gid) == 2
    # Both legs now 250: destination gains the new amount, source loses it
    # (source also carries the seeded 42.50 expense → -292.50).
    assert account_balance(to_acct) == 250.00
    assert account_balance(from_acct) == -292.50


# --- delete (acts on the pair) ----------------------------------------------

def test_delete_transfer_removes_both_legs(client_a, users):
    from_acct = users["a"]["account_id"]
    to_acct = create_account(users["a"]["id"], "acct-A-3")
    gid = create_transfer(users["a"]["id"], from_acct, to_acct, 75.00, TODAY)
    assert count_transfer_legs(gid) == 2

    response = client_a.delete(f"/transfers/{gid}", follow_redirects=True)
    assert response.status_code == 200
    assert count_transfer_legs(gid) == 0


# --- isolation --------------------------------------------------------------

def test_cannot_delete_another_users_transfer(client_a, users):
    b_to = create_account(users["b"]["id"], "acct-B-2")
    gid = create_transfer(users["b"]["id"], users["b"]["account_id"], b_to, 60.00, TODAY)
    response = client_a.delete(f"/transfers/{gid}", follow_redirects=True)
    assert response.status_code == 404
    assert count_transfer_legs(gid) == 2  # B's transfer survives


def test_cannot_edit_another_users_transfer(client_a, users):
    b_to = create_account(users["b"]["id"], "acct-B-3")
    gid = create_transfer(users["b"]["id"], users["b"]["account_id"], b_to, 60.00, TODAY)
    response = client_a.post(
        f"/transfers/{gid}/edit",
        data={
            "from_account": users["b"]["account_id"],
            "to_account": b_to,
            "amount": "9999",
            "transfer_date": TODAY,
        },
        follow_redirects=True,
    )
    assert response.status_code == 404
    assert account_balance(b_to) == 60.00  # unchanged


def test_missing_transfer_group_returns_404(client_a, users):
    response = client_a.get("/transfers/99999999/edit", follow_redirects=True)
    assert response.status_code == 404


def test_history_renders_transfer_row(client_a, users):
    # Exercises the transfer-row branch in history.html (badge + group links).
    to_acct = create_account(users["a"]["id"], "acct-A-hist")
    gid = create_transfer(users["a"]["id"], users["a"]["account_id"], to_acct, 80.00, TODAY)
    response = client_a.get("/transactions")
    assert response.status_code == 200
    assert b"Transfer" in response.data
    assert f"/transfers/{gid}".encode() in response.data
