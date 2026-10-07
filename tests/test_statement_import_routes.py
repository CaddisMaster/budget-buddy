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
from app.blueprints import imports
from app.db import db_cursor
from app.statements import MAX_FILE_BYTES, MAX_IMAGE_BYTES, MAX_IMAGES
from tests.helpers import create_account, create_category

TODAY = date.today()


@pytest.fixture
def ai_stubbed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"mapping": [], "categorize": [], "screenshots": [], "shot_lines": []}

    def map_columns(rows, date_formats, today, api_key):
        calls["mapping"].append(rows)
        return ai._CsvMapping(header_row=0, date_col=0, date_format="%Y-%m-%d",
                              description_col=1, amount_col=2, out_is_negative=True,
                              debit_col=None, credit_col=None)

    def categorize(rows, category_names, today, api_key):
        calls["categorize"].append(rows)
        return ai._Suggestions(suggestions=[])

    def read_shots(images, today, api_key):
        calls["screenshots"].append(images)
        return ai._ScreenshotRead(lines=list(calls["shot_lines"]))

    monkeypatch.setattr(ai, "_call_csv_mapping_model", map_columns)
    monkeypatch.setattr(ai, "_call_categorize_model", categorize)
    monkeypatch.setattr(ai, "_call_screenshot_model", read_shots)
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



# ── #446: transfers ─────────────────────────────────────────────────────────

PAYMENT = (TODAY - timedelta(days=2), "PAYMENT - THANK YOU", "500.00")


def _plain(account_id, user_id, amount, days_ago, kind="expense", description="Visa payment",
           category_id=None):
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO transactions (amount, description, account_id, transaction_date, "
                    "transaction_type, category_id, user_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (amount, description, account_id, TODAY - timedelta(days=days_ago), kind,
                     category_id, user_id))
        return cur.fetchone().id


@pytest.fixture
def visa(users):
    return create_account(users["a"]["id"], "Visa", "Credit Card")


def _line_row(body, description):
    return re.search(r'<tr data-line="\d+" data-status="\w+">(?:(?!</tr>).)*'
                     + re.escape(description) + r'(?:(?!</tr>).)*</tr>', body, re.S).group(0)


def test_a_transfer_line_with_a_counterpart_comes_preselected_and_ticked(
        client_a, users, checking, visa, ai_stubbed):
    row_id = _plain(checking, users["a"]["id"], "500.00", 3)
    body = _upload(client_a, visa, _csv(PAYMENT)).get_data(as_text=True)
    line = _line_row(body, "PAYMENT - THANK YOU")
    assert f'data-pairs-with="{row_id}"' in line
    assert f'value="xfer:{checking}" selected' in line
    assert re.search(r'name="apply"[^>]*\bchecked\b', line)


def test_without_a_counterpart_a_transfer_line_waits_for_a_choice(
        client_a, checking, visa, ai_stubbed):
    body = _upload(client_a, visa, _csv(PAYMENT)).get_data(as_text=True)
    line = _line_row(body, "PAYMENT - THANK YOU")
    assert "data-pairs-with" not in line and "selected>" not in line.split("optgroup")[-1]
    assert not re.search(r'name="apply"[^>]*\bchecked\b', line)
    assert f'value="xfer:{checking}"' in line, "the choice is still offered"


TRANSFER = {"date": PAYMENT[0].isoformat(), "amount": "500.00", "direction": "in",
            "description": "PAYMENT - THANK YOU", "ref": "csv:pay"}


def test_a_transfer_with_the_import_account_itself_is_refused(client_a, visa, ai_stubbed):
    _apply(client_a, visa, line_0={**TRANSFER, "category": f"xfer:{visa}"})
    assert _rows(visa) == []
    assert "1 line could not be added" in _flashes(client_a)


@pytest.mark.parametrize("value", ["xfer:", "xfer:abc", "xfer:1.5"])
def test_a_malformed_transfer_choice_is_refused(client_a, visa, ai_stubbed, value):
    _apply(client_a, visa, line_0={**TRANSFER, "category": value})
    assert _rows(visa) == []


def test_applying_a_transfer_twice_leaves_no_lone_leg(client_a, checking, visa, ai_stubbed):
    for _ in range(2):
        _apply(client_a, visa, line_0={**TRANSFER, "category": f"xfer:{checking}"})
    assert len(_rows(visa)) == 1
    assert len(_rows(checking)) == 1, "the second apply made a leg with no partner"


def test_two_lines_never_claim_the_same_counterpart(client_a, users, checking, visa, ai_stubbed):
    _plain(checking, users["a"]["id"], "500.00", 2)
    second = {**TRANSFER, "ref": "csv:pay2"}
    _apply(client_a, visa, line_0={**TRANSFER, "category": f"xfer:{checking}"},
           line_1={**second, "category": f"xfer:{checking}"})
    rows = _rows(checking)
    assert len(rows) == 2, "one converted, one inserted"
    with db_cursor() as cur:
        cur.execute("SELECT count(DISTINCT transfer_group_id) AS groups FROM transactions "
                    "WHERE account_id = %s", (checking,))
        assert cur.fetchone().groups == 2


def test_a_converted_row_loses_its_category(client_a, users, checking, visa, ai_stubbed):
    """A transfer has no category, and a categorised leg would still show up
    wherever spending is grouped by category."""
    row_id = _plain(checking, users["a"]["id"], "500.00", 3,
                    category_id=users["a"]["category_id"])
    _apply(client_a, visa, line_0={**TRANSFER, "category": f"xfer:{checking}"})
    with db_cursor() as cur:
        cur.execute("SELECT is_transfer, category_id FROM transactions WHERE id = %s", (row_id,))
        row = cur.fetchone()
    assert row.is_transfer is True and row.category_id is None
    assert "1 paired with an entry already in the other account" in _flashes(client_a)


def test_the_review_never_pairs_two_lines_with_one_entry(client_a, users, checking, visa,
                                                         ai_stubbed):
    """Two $500 payments, one $500 entry in checking: only the first line can
    claim it, or the review would promise the same entry twice."""
    row_id = _plain(checking, users["a"]["id"], "500.00", 2)
    body = _upload(client_a, visa, _csv(PAYMENT, (PAYMENT[0], "AUTOPAY PAYMENT", "500.00"))
                   ).get_data(as_text=True)
    assert body.count(f'data-pairs-with="{row_id}"') == 1



# ── #447: screenshots ───────────────────────────────────────────────────────

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _shots(client, account_id, *files):
    return client.post("/transactions/import", content_type="multipart/form-data",
                       data={"account_id": str(account_id),
                             "statement": [(io.BytesIO(raw), name) for raw, name in files]})


def _a_line(ai_stubbed):
    ai_stubbed["shot_lines"].append(ai._ScreenshotLine(
        month=TODAY.month, day=TODAY.day, year=TODAY.year, description="GROCERY",
        amount="5.00", direction="out", legible=True))


def test_every_screenshot_reaches_the_model_typed_by_its_bytes(client_a, checking, ai_stubbed):
    """A JPEG named .png is sent as a JPEG: the filename is the user's claim,
    the bytes are the fact, and a wrong media type is a refused API call."""
    _a_line(ai_stubbed)
    resp = _shots(client_a, checking, (PNG, "one.png"), (JPEG, "two.png"))
    assert resp.status_code == 200 and 'data-status="missing"' in resp.get_data(as_text=True)
    (images,) = ai_stubbed["screenshots"]
    assert [t for t, _raw in images] == ["image/png", "image/jpeg"]
    assert [raw for _t, raw in images] == [PNG, JPEG]


def test_too_many_screenshots_are_refused_before_the_model(client_a, checking, ai_stubbed):
    resp = _shots(client_a, checking, *[(PNG, f"{n}.png") for n in range(MAX_IMAGES + 1)])
    assert resp.status_code == 400
    assert ai_stubbed["screenshots"] == []


def test_an_oversized_screenshot_is_refused_before_the_model(client_a, checking, ai_stubbed):
    resp = _shots(client_a, checking, (PNG + b"\x00" * MAX_IMAGE_BYTES, "big.png"))
    assert resp.status_code == 413
    assert ai_stubbed["screenshots"] == []


def test_a_screenshot_mixed_with_a_statement_file_is_refused(client_a, checking, ai_stubbed):
    resp = _shots(client_a, checking, (PNG, "a.png"), (_csv((TODAY, "SHOP", "-1.00")), "s.csv"))
    assert resp.status_code == 400
    assert "not a mix" in resp.get_data(as_text=True)
    assert ai_stubbed["screenshots"] == [] and ai_stubbed["mapping"] == []


def test_two_statement_files_at_once_are_refused(client_a, checking, ai_stubbed):
    csv = _csv((TODAY, "SHOP", "-1.00"))
    resp = _shots(client_a, checking, (csv, "a.csv"), (csv, "b.csv"))
    assert resp.status_code == 400 and ai_stubbed["mapping"] == []


def test_a_screenshot_import_offers_no_balance_check(client_a, checking, ai_stubbed):
    _a_line(ai_stubbed)
    body = _shots(client_a, checking, (PNG, "a.png")).get_data(as_text=True)
    assert 'name="closing_balance"' not in body


# ── #454: categories from history ───────────────────────────────────────────

def _categorised(user_id, account_id, description, category_id, adjustment=False):
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO transactions (amount, description, category_id, account_id, "
                    "transaction_date, transaction_type, is_adjustment, user_id) "
                    "VALUES (10, %s, %s, %s, %s, 'expense', %s, %s)",
                    (description, category_id, account_id, TODAY - timedelta(days=30),
                     adjustment, user_id))


def _history_marked(resp, description):
    row = re.search(rf'<tr data-line="\d+"[^>]*>(?:(?!</tr>).)*value="{re.escape(description)}"'
                    r'(?:(?!</tr>).)*</tr>', resp.get_data(as_text=True), re.S)
    assert row, f"no review row for {description!r}"
    return "data-from-history" in row.group(0)


def _sent(calls):
    return [r["description"] for rows in calls["categorize"] for r in rows]


def test_another_users_history_teaches_nothing(client_a, users, checking, ai_stubbed,
                                              monkeypatch):
    b = users["b"]
    _categorised(b["id"], b["account_id"], "KROGER #123", b["category_id"])
    # history_categories() would also drop B's row, because B's category is not
    # in A's list. So watch what the query hands it: the outcome alone cannot
    # tell whether the query is scoped (a mutant dropping `user_id` survived).
    seen = []
    real = imports.history_categories

    def spy(reviews, rows, kinds):
        seen.extend(rows)
        return real(reviews, rows, kinds)
    monkeypatch.setattr(imports, "history_categories", spy)

    resp = _upload(client_a, checking, _csv((TODAY, "KROGER #456", "-61.10")))
    assert resp.status_code == 200
    assert seen, "the history query returned nothing, so this proves nothing"
    assert "KROGER #123" not in [r.description for r in seen]
    assert not _history_marked(resp, "KROGER #456")
    assert "KROGER #456" in _sent(ai_stubbed)


def test_a_balance_adjustment_teaches_nothing(client_a, users, checking, ai_stubbed):
    groceries = create_category(users["a"]["id"], "Groceries")
    _categorised(users["a"]["id"], checking, "KROGER", groceries, adjustment=True)
    resp = _upload(client_a, checking, _csv((TODAY, "KROGER #456", "-61.10")))
    assert not _history_marked(resp, "KROGER #456")
    assert "KROGER #456" in _sent(ai_stubbed)


def test_a_failed_category_call_keeps_what_history_found(client_a, users, checking, ai_stubbed,
                                                         monkeypatch):
    groceries = create_category(users["a"]["id"], "Groceries")
    _categorised(users["a"]["id"], checking, "KROGER #123", groceries)

    def broken(rows, category_names, today, api_key):
        ai_stubbed["categorize"].append(rows)
        raise ai.ParseError("model unavailable")
    monkeypatch.setattr(ai, "_call_categorize_model", broken)

    resp = _upload(client_a, checking, _csv((TODAY, "KROGER #456", "-61.10"),
                                            (TODAY, "HARDWARE BARN", "-19.99")))
    body = resp.get_data(as_text=True)
    assert "Categories couldn't be suggested" in body
    assert _history_marked(resp, "KROGER #456")
    assert f'<option value="{groceries}" selected>' in body
    assert _sent(ai_stubbed) == ["HARDWARE BARN"]


# ── #456: update my entry instead of adding ─────────────────────────────────

def _entry(user_id, account_id, amount="40.00", when=None, description="Dinner", ref=None,
           transfer=False, adjustment=False, category_id=None):
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO transactions (amount, description, category_id, account_id, "
                    "transaction_date, transaction_type, is_transfer, is_adjustment, import_ref, "
                    "user_id) VALUES (%s, %s, %s, %s, %s, 'expense', %s, %s, %s, %s) "
                    "RETURNING id",
                    (amount, description, category_id, account_id, when or TODAY, transfer,
                     adjustment, ref, user_id))
        return cur.fetchone().id


def _fetch(entry_id):
    with db_cursor() as cur:
        cur.execute("SELECT amount, transaction_date, import_ref, description, category_id "
                    "FROM transactions WHERE id = %s", (entry_id,))
        return cur.fetchone()


def _update(client, account_id, entry_id, amount="42.80", ref="csv:trattoria"):
    return _apply(client, account_id, line_0={**GOOD, "amount": amount, "ref": ref,
                                              "description": "TRATTORIA",
                                              "action": "update", "match": entry_id})


def test_an_update_keeps_the_entrys_name_and_category_and_says_so(client_a, users, checking,
                                                                   ai_stubbed):
    entry = _entry(users["a"]["id"], checking, category_id=users["a"]["category_id"])
    resp = _update(client_a, checking, entry)
    row = _fetch(entry)
    assert (str(row.amount), row.import_ref) == ("42.80", "csv:trattoria")
    assert (row.description, row.category_id) == ("Dinner", users["a"]["category_id"])
    assert len(_rows(checking)) == 1
    flashed = client_a.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Updated 1 entry to match the statement." in flashed


@pytest.mark.parametrize("kind", ["transfer leg", "adjustment", "already imported"])
def test_an_entry_the_review_never_offers_is_not_updated(client_a, users, checking, ai_stubbed,
                                                         kind):
    entry = _entry(users["a"]["id"], checking, transfer=kind == "transfer leg",
                   adjustment=kind == "adjustment",
                   ref="csv:earlier" if kind == "already imported" else None)
    before = _fetch(entry)
    _update(client_a, checking, entry)
    assert _fetch(entry) == before
    assert len(_rows(checking)) == 1


def test_a_transfer_legs_possible_match_offers_no_update(client_a, users, checking, ai_stubbed):
    _entry(users["a"]["id"], checking, transfer=True)
    resp = _upload(client_a, checking, _csv((TODAY, "TRATTORIA", "-42.80")))
    body = resp.get_data(as_text=True)
    assert 'data-status="possible"' in body, "the setup must produce a possible match"
    assert 'name="action_0"' not in body and 'name="match_0"' not in body


def test_a_plain_possible_match_offers_update_by_default(client_a, users, checking, ai_stubbed):
    entry = _entry(users["a"]["id"], checking)
    body = _upload(client_a, checking, _csv((TODAY, "TRATTORIA", "-42.80"))).get_data(as_text=True)
    assert f'name="match_0" value="{entry}"' in body
    assert '<option value="update" selected>Update my entry</option>' in body


def test_another_users_entry_is_not_updated(client_a, users, checking, ai_stubbed):
    b = users["b"]
    entry = _entry(b["id"], b["account_id"])
    before = _fetch(entry)
    _update(client_a, checking, entry)
    assert _fetch(entry) == before


def test_an_amount_outside_the_possible_match_rule_is_not_written(client_a, users, checking,
                                                                  ai_stubbed):
    # A hand-edited review: the right entry, but a line it could never match.
    entry = _entry(users["a"]["id"], checking)
    before = _fetch(entry)
    resp = _update(client_a, checking, entry, amount="400.00")
    assert _fetch(entry) == before
    assert len(_rows(checking)) == 1
    flashed = client_a.get(resp.headers["Location"]).get_data(as_text=True)
    assert "1 line could not be added." in flashed
    assert "Updated" not in flashed


def test_applying_an_update_twice_changes_nothing_the_second_time(client_a, users, checking,
                                                                  ai_stubbed):
    entry = _entry(users["a"]["id"], checking)
    _update(client_a, checking, entry)
    after = _fetch(entry)
    _update(client_a, checking, entry, ref="csv:other")
    assert _fetch(entry) == after
    assert len(_rows(checking)) == 1


def test_an_update_naming_no_entry_is_refused(client_a, checking, ai_stubbed):
    _apply(client_a, checking, line_0={**GOOD, "action": "update", "match": "abc"})
    assert _rows(checking) == []


# ── #457: ledger entries the statement does not list ───────────────────────

def _hold(user_id, account_id, description, amount, when, pending=False):
    with db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO transactions (amount, description, account_id, "
                    "transaction_date, transaction_type, is_pending, user_id) "
                    "VALUES (%s, %s, %s, %s, 'expense', %s, %s) RETURNING id",
                    (amount, description, account_id, when, pending, user_id))
        return cur.fetchone().id


def _section(body, name):
    found = re.search(rf'<section[^>]*data-section="{name}"[^>]*>(.*?)</section>', body, re.S)
    return found.group(1) if found else None


def test_an_unmatched_pending_row_is_listed_as_still_pending(client_a, users, checking,
                                                             ai_stubbed):
    _hold(users["a"]["id"], checking, "Gym", "30.00", TODAY - timedelta(days=20))
    _hold(users["a"]["id"], checking, "Hold", "9.00", TODAY - timedelta(days=15), pending=True)
    body = _upload(client_a, checking, _csv(
        (TODAY - timedelta(days=30), "PAYROLL", "2000.00"),
        (TODAY, "COFFEE", "-4.50"))).get_data(as_text=True)
    unlisted, pending = _section(body, "unlisted"), _section(body, "still-pending")
    assert unlisted is not None and "Gym" in unlisted
    assert pending is not None and "Hold" in pending
    assert "Hold" not in unlisted and "Gym" not in pending


def test_nothing_unlisted_renders_neither_section(client_a, checking, ai_stubbed):
    body = _upload(client_a, checking, _csv(
        (TODAY - timedelta(days=10), "COFFEE", "-4.50"))).get_data(as_text=True)
    assert 'data-line="0"' in body
    assert _section(body, "unlisted") is None and _section(body, "still-pending") is None


def test_an_unlisted_rows_link_finds_it_in_history(client_a, users, checking, ai_stubbed):
    """The link narrows History to that row: its account, its month and its
    description. Each decoy differs from the row in exactly one of the three."""
    a = users["a"]["id"]
    when = TODAY - timedelta(days=20)
    row_id = _hold(a, checking, "Gym & Spa", "30.00", when)
    decoys = [
        _hold(a, checking, "Gym & Spa", "30.00", when - timedelta(days=62)),
        _hold(a, checking, "Bakery", "30.00", when),
        _hold(a, create_account(a, "Savings"), "Gym & Spa", "30.00", when),
    ]
    body = _upload(client_a, checking, _csv(
        (TODAY - timedelta(days=30), "PAYROLL", "2000.00"),
        (TODAY, "COFFEE", "-4.50"))).get_data(as_text=True)
    href = re.search(rf'<tr data-unlisted="{row_id}">.*?<a href="([^"]+)"',
                     _section(body, "unlisted"), re.S).group(1).replace("&amp;", "&")
    assert href.startswith("/transactions?")
    history = client_a.get(href).get_data(as_text=True)
    assert f'/transactions/{row_id}/edit' in history, "the link does not reach the row"
    for decoy in decoys:
        assert f'/transactions/{decoy}/edit' not in history, f"the link also lists row {decoy}"
