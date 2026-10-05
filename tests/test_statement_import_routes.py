"""#445 — the statement import routes: the guards the acceptance criteria don't
spell out. The criteria themselves are tests/features/statement_import.feature.

⚠️ EVERY TEST HERE GOES THROUGH `ai_stubbed`. The dev container's environment
can hold a real ANTHROPIC_API_KEY, so a route test that reached an unstubbed
seam would make a real, billed call. The fixture sets a fake key and replaces
both seams the import reaches.
"""
import io
import re
from datetime import date, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import ai
from app.db import db_cursor
from app.statements import MAX_FILE_BYTES
from tests.helpers import create_account, create_category

TODAY = date.today()


@pytest.fixture
def ai_stubbed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"mapping": [], "categorize": []}

    def map_columns(rows, date_formats, today, api_key):
        calls["mapping"].append(rows)
        return ai._CsvMapping(header_row=0, date_col=0, date_format="%Y-%m-%d",
                              description_col=1, amount_col=2, out_is_negative=True,
                              debit_col=None, credit_col=None)

    def categorize(rows, category_names, today, api_key):
        calls["categorize"].append(rows)
        return ai._Suggestions(suggestions=[])

    monkeypatch.setattr(ai, "_call_csv_mapping_model", map_columns)
    monkeypatch.setattr(ai, "_call_categorize_model", categorize)
    return calls


def _csv(*lines):
    rows = ["Date,Description,Amount"] + [f"{d.isoformat()},{desc},{amt}" for d, desc, amt in lines]
    return ("\n".join(rows) + "\n").encode()


def _upload(client, account_id, payload, filename="s.csv"):
    return client.post("/transactions/import",
                       data={"account_id": str(account_id),
                             "statement": (io.BytesIO(payload), filename)},
                       content_type="multipart/form-data")


def _apply(client, account_id, extra=(), **lines):
    """Post an apply form directly: each kwarg is line_<i>=dict of its fields."""
    data = [("account_id", str(account_id)), *extra]
    for key, fields in lines.items():
        i = key.split("_")[1]
        data.append(("apply", i))
        data += [(f"{name}_{i}", str(value)) for name, value in fields.items()]
    return client.post("/transactions/import/apply", data=MultiDict(data))


def _rows(account_id):
    with db_cursor() as cur:
        cur.execute("SELECT description, amount, category_id, is_pending, import_ref "
                    "FROM transactions WHERE account_id = %s ORDER BY id", (account_id,))
        return cur.fetchall()


GOOD = {"date": TODAY.isoformat(), "amount": "12.34", "direction": "out",
        "description": "GROCERY", "ref": "csv:abc"}


@pytest.fixture
def checking(users):
    return create_account(users["a"]["id"], "Checking")


def test_an_anonymous_upload_is_sent_to_log_in(anon_client, ai_stubbed):
    resp = anon_client.post("/transactions/import")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


@pytest.mark.parametrize("change", [
    {"amount": "NaN"}, {"amount": "-5"}, {"amount": "0"}, {"amount": "1e9"},
    {"direction": "sideways"}, {"date": "2026-02-30"}, {"date": ""},
    {"ref": "r" * 256},
])
def test_a_forged_line_is_refused_and_nothing_is_written(client_a, checking, ai_stubbed, change):
    """The review round-trips lines as hidden fields, so apply validates each
    one exactly as the Add Transaction form would."""
    resp = _apply(client_a, checking, line_0={**GOOD, **change})
    assert resp.status_code == 302
    assert _rows(checking) == []
    assert "1 line could not be added" in _flashes(client_a)


def test_another_users_category_is_dropped_not_written(client_a, users, checking, ai_stubbed):
    _apply(client_a, checking, line_0={**GOOD, "category": users["b"]["category_id"]})
    rows = _rows(checking)
    assert len(rows) == 1 and rows[0].category_id is None


def test_an_owned_category_is_written(client_a, users, checking, ai_stubbed):
    _apply(client_a, checking, line_0={**GOOD, "category": users["a"]["category_id"]})
    assert _rows(checking)[0].category_id == users["a"]["category_id"]


def test_another_users_pending_row_cannot_be_marked_posted(client_a, users, checking, ai_stubbed):
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO transactions (amount, description, account_id, "
                    "transaction_date, transaction_type, is_pending, user_id) "
                    "VALUES (5, 'theirs', %s, CURRENT_DATE, 'expense', true, %s) RETURNING id",
                    (users["b"]["account_id"], users["b"]["id"]))
        theirs = cur.fetchone().id
    _apply(client_a, checking, line_0={**GOOD, "pending": theirs})
    with db_cursor() as cur:
        cur.execute("SELECT is_pending FROM transactions WHERE id = %s", (theirs,))
        assert cur.fetchone().is_pending is True
    assert _rows(checking) == [], "a pending line never falls through to an insert"


def _flashes(client):
    with client.session_transaction() as session:
        return " ".join(m for _c, m in session.pop("_flashes", []))


def test_applying_the_same_review_twice_adds_once(client_a, checking, ai_stubbed):
    """A double click or the back button. The second apply is quiet, not an
    error: the unique reference (#444) turns it into a no-op."""
    _apply(client_a, checking, line_0=GOOD)
    assert "Added 1 transaction" in _flashes(client_a)
    _apply(client_a, checking, line_0=GOOD)
    assert "Added 0 transactions" in _flashes(client_a)
    assert len(_rows(checking)) == 1


def test_applying_to_another_users_account_is_not_found(client_a, users, ai_stubbed):
    resp = _apply(client_a, users["b"]["account_id"], line_0=GOOD)
    assert resp.status_code == 404
    assert "GROCERY" not in [r.description for r in _rows(users["b"]["account_id"])]


def test_a_ledger_that_agrees_says_so(client_a, users, checking, ai_stubbed):
    """Compared AS OF the statement's end date: a row entered after it is not
    part of the statement and must not count."""
    end = TODAY - timedelta(days=1)
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO transactions (amount, description, account_id, "
                    "transaction_date, transaction_type, user_id) "
                    "VALUES (99, 'after the statement', %s, CURRENT_DATE, 'expense', %s)",
                    (checking, users["a"]["id"]))
    resp = _apply(client_a, checking,
                  extra=[("closing_balance", "-12.34"), ("end_date", end.isoformat())],
                  line_0={**GOOD, "date": end.isoformat()})
    assert resp.status_code == 302
    page = client_a.get(resp.headers["Location"]).get_data(as_text=True)
    assert "agrees with the statement&#39;s closing balance" in page


def test_an_oversized_file_is_refused_unread(client_a, checking, ai_stubbed):
    resp = _upload(client_a, checking, b"x" * (MAX_FILE_BYTES + 1))
    assert resp.status_code == 413
    assert ai_stubbed["mapping"] == []


def test_the_model_sees_only_the_first_rows_of_a_csv(client_a, checking, ai_stubbed):
    lines = [(TODAY - timedelta(days=n % 20), f"SHOP {n}", "-1.00") for n in range(60)]
    resp = _upload(client_a, checking, _csv(*lines))
    assert resp.status_code == 200
    assert len(ai_stubbed["mapping"]) == 1
    sent = ai_stubbed["mapping"][0]
    assert len(sent) == 10, "the header plus a few samples, never the statement"
    assert "SHOP 59" not in str(sent)


def test_a_failed_mapping_call_refuses_the_file(client_a, checking, ai_stubbed, monkeypatch):
    def broken(*args):
        raise ai.ParseError("boom")
    monkeypatch.setattr(ai, "_call_csv_mapping_model", broken)
    resp = _upload(client_a, checking, _csv((TODAY, "SHOP", "-1.00")))
    assert resp.status_code == 400
    assert "could not be read right now" in resp.get_data(as_text=True)


def test_a_failed_category_call_still_shows_the_review(client_a, checking, ai_stubbed, monkeypatch):
    def broken(*args):
        raise ai.ParseError("boom")
    monkeypatch.setattr(ai, "_call_categorize_model", broken)
    resp = _upload(client_a, checking, _csv((TODAY, "SHOP", "-1.00")))
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200 and 'data-status="missing"' in body
    assert "Categories couldn't be suggested" in body


def test_a_suggestion_of_the_wrong_kind_is_not_shown(client_a, users, checking, ai_stubbed,
                                                     monkeypatch):
    """Money in is never pre-filled with an expense category."""
    expense = users["a"]["category_id"]
    create_category(users["a"]["id"], "Salary", kind="income")
    with db_cursor() as cur:
        cur.execute("SELECT name FROM categories WHERE id = %s", (expense,))
        name = cur.fetchone().name

    def categorize(rows, category_names, today, api_key):
        return ai._Suggestions(suggestions=[
            ai._Suggestion(id=r["id"], category=name, confidence="high") for r in rows])
    monkeypatch.setattr(ai, "_call_categorize_model", categorize)

    resp = _upload(client_a, checking, _csv((TODAY, "PAYROLL", "100.00")))
    body = resp.get_data(as_text=True)
    assert 'data-status="missing"' in body
    assert f'value="{expense}" selected' not in body


def test_transfer_looking_lines_are_not_sent_for_categories(client_a, checking, ai_stubbed):
    resp = _upload(client_a, checking, _csv((TODAY, "ONLINE TRANSFER TO SAVINGS", "-50.00"),
                                            (TODAY, "GROCERY", "-5.00")))
    assert resp.status_code == 200
    sent = [r["description"] for call in ai_stubbed["categorize"] for r in call]
    assert sent == ["GROCERY"]
    body = resp.get_data(as_text=True)
    transfer = re.search(r'<tr data-line="\d+" data-status="missing">(?:(?!</tr>).)*'
                         r'ONLINE TRANSFER(?:(?!</tr>).)*</tr>', body, re.S).group(0)
    assert "looks like a transfer" in transfer
    assert re.search(r'name="apply"', transfer), "it can still be ticked by hand"
    assert not re.search(r'name="apply"[^>]*\bchecked\b', transfer), \
        "a transfer imported as spending double-counts it, so it starts unchecked"


@pytest.mark.parametrize("path", ["/transactions/import", "/transactions/import/apply"])
def test_the_writes_are_not_found_without_ai(client_a, checking, monkeypatch, path):
    """Not just the page: the POSTs too, or a hand-made request reaches a
    model seam with no key behind it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client_a.post(path, data={"account_id": str(checking)})
    assert resp.status_code == 404
