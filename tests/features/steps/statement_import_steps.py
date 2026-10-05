"""Steps for statement_import.feature (#445).

The statement is built here, as a real OFX or CSV file, from the lines a
scenario lists, and uploaded through the real form. Applying submits what the
review page actually rendered: `_ReviewForm` reads its inputs the way a browser
would, so a field the template forgot to render is a field the test does not
send.

⚠️ THE AI IS STUBBED IN EVERY SCENARIO THAT UPLOADS. `./test.sh` runs inside the
dev container, whose `.env` can hold a real ANTHROPIC_API_KEY, so an unstubbed
seam would make a real, billed call. "the AI is available" replaces BOTH seams
the import reaches and sets a fake key; both are restored by cleanups.

⚠️ Types and `_user` come from `tests/features/support.py`, imported before any
step below is defined. `Amt`, `Way` and `Acct2` are registered here, not taken
from transfer_steps.py, which loads after this file.
"""
import io
import os
import re
from html.parser import HTMLParser

from behave import given, register_type, then, when
from werkzeug.datastructures import MultiDict

from app import ai
from tests.features.support import _pattern, _user
from tests.helpers import _connection, create_account


@_pattern(r"\d+(?:\.\d{2})?")
def _amount(text):
    return text


@_pattern(r"in|out")
def _way(text):
    return text


@_pattern(r'"[^"]+"')
def _quoted(text):
    return text.strip('"')


register_type(Amt=_amount, Way=_way, Q=_quoted)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _sql(query, params=()):
    conn = _connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall() if cur.description else None
    conn.commit()
    cur.close()
    conn.close()
    return rows


def _account(context, name):
    return context.accounts[name]


def _count(account_id):
    return _sql("SELECT count(*) FROM transactions WHERE account_id = %s",
                (account_id,))[0][0]


def _hold(account_id, user_id, description, amount, way, when,
          pending=False, adjustment=False):
    _sql("INSERT INTO transactions (amount, description, account_id, transaction_date, "
         "transaction_type, is_pending, is_adjustment, user_id) "
         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
         (amount, description, account_id, when,
          "income" if way == "in" else "expense", pending, adjustment, user_id))


def _ofx(statement):
    lines = statement["lines"]
    dates = [ln["date"] for ln in lines]
    out = ["OFXHEADER:100", "DATA:OFXSGML", "", "<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>",
           "<BANKTRANLIST>",
           f"<DTSTART>{min(dates):%Y%m%d}", f"<DTEND>{max(dates):%Y%m%d}"]
    for i, ln in enumerate(lines):
        sign = "-" if ln["way"] == "out" else ""
        out += ["<STMTTRN>", f"<DTPOSTED>{ln['date']:%Y%m%d}",
                f"<TRNAMT>{sign}{ln['amount']}",
                f"<FITID>BEH-{i}-{ln['date']:%Y%m%d}-{ln['amount']}",
                f"<NAME>{ln['description']}", "</STMTTRN>"]
    out.append("</BANKTRANLIST>")
    if statement.get("closing") is not None:
        out += ["<LEDGERBAL>", f"<BALAMT>{statement['closing']}",
                f"<DTASOF>{max(dates):%Y%m%d}", "</LEDGERBAL>"]
    out.append("</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>")
    return "\n".join(out).encode(), "statement.ofx"


def _csv_split(statement):
    rows = ["Date,Description,Debit,Credit"]
    for ln in statement["lines"]:
        debit, credit = (ln["amount"], "") if ln["way"] == "out" else ("", ln["amount"])
        rows.append(f"{ln['date']:%Y-%m-%d},{ln['description']},{debit},{credit}")
    return ("\n".join(rows) + "\n").encode(), "statement.csv"


def _upload(context, account_id, payload, filename):
    context.baseline.setdefault(account_id, _count(account_id))
    context.upload = (account_id, payload, filename)
    context.response = context.client.post(
        "/transactions/import",
        data={"account_id": str(account_id), "statement": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data")


def _body(context):
    return context.response.get_data(as_text=True)


def _rows(context):
    """The review table's rows: {index: (status, html)}."""
    found = re.findall(r'<tr data-line="(\d+)" data-status="(\w+)">(.*?)</tr>',
                       _body(context), re.S)
    assert found, f"no review table in the response ({context.response.status_code})"
    return {int(i): (status, html) for i, status, html in found}


def _row_for(context, description):
    """The review row for a description: its hidden field when the line needs a
    decision, its description cell when it is already recorded."""
    rows = _rows(context)
    for i, (status, html) in rows.items():
        if f'value="{description}"' in html or f"<td>{description}</td>" in html:
            return i, status, html
    raise AssertionError(f"no review row for {description!r}")


class _ReviewForm(HTMLParser):
    """The review form's fields as a browser would submit them: hidden inputs,
    checked checkboxes, and each select's selected (else first) option."""

    def __init__(self):
        super().__init__()
        self.fields, self._select, self._first = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name"):
            kind = a.get("type", "text")
            if kind == "checkbox" and "checked" not in a:
                return
            self.fields.append((a["name"], a.get("value", "")))
        elif tag == "select":
            self._select, self._first = a.get("name"), None
        elif tag == "option" and self._select:
            value = a.get("value", "")
            if self._first is None:
                self._first = value
            if "selected" in a:
                self.fields.append((self._select, value))
                self._select = None

    def handle_endtag(self, tag):
        if tag == "select" and self._select:
            self.fields.append((self._select, self._first or ""))
            self._select = None


def _apply(context, uncheck=()):
    form = _ReviewForm()
    form.feed(_body(context))
    skip = {str(_row_for(context, d)[0]) for d in uncheck}
    data = [(k, v) for k, v in form.fields if not (k == "apply" and v in skip)]
    context.response = context.client.post("/transactions/import/apply", data=MultiDict(data),
                                           follow_redirects=True)


# ── Given ───────────────────────────────────────────────────────────────────

@given("the AI is available")
def given_ai_available(context):
    previous = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "test-key"

    def restore_key():
        if previous is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = previous
    context.add_cleanup(restore_key)

    category = _sql("SELECT name FROM categories WHERE user_id = %s AND kind = 'expense' "
                    "ORDER BY id LIMIT 1", (_user(context, "A")["id"],))[0][0]
    context.suggested_category = category

    def categorize(rows, category_names, today, api_key):
        return ai._Suggestions(suggestions=[
            ai._Suggestion(id=r["id"], category=category, confidence="high") for r in rows])

    def map_columns(rows, date_formats, today, api_key):
        # The layout _csv_split() writes. The real call sees only these rows.
        context.mapped_rows = rows
        return ai._CsvMapping(header_row=0, date_col=0, date_format="%Y-%m-%d",
                              description_col=1, amount_col=None, out_is_negative=True,
                              debit_col=2, credit_col=3)

    for name, stub in (("_call_categorize_model", categorize),
                       ("_call_csv_mapping_model", map_columns)):
        original = getattr(ai, name)
        setattr(ai, name, stub)
        context.add_cleanup(setattr, ai, name, original)

    context.accounts, context.baseline, context.mapped_rows = {}, {}, None


@given("no Anthropic API key is set")
def given_no_key(context):
    previous = os.environ.pop("ANTHROPIC_API_KEY", None)
    if previous is not None:
        context.add_cleanup(os.environ.__setitem__, "ANTHROPIC_API_KEY", previous)


@given("user {who:Who} has an account {name:Q}")
def given_account(context, who, name):
    context.accounts[name] = create_account(_user(context, who)["id"], name)


@given("{acct:Q} holds {description:Q} for ${amount:Amt} {way:Way} {when:Rel}")
def given_ledger_row(context, acct, description, amount, way, when):
    _hold(_account(context, acct), _user(context, "A")["id"], description, amount, way, when)


@given("{acct:Q} holds a pending {description:Q} for ${amount:Amt} {way:Way} {when:Rel}")
def given_pending_row(context, acct, description, amount, way, when):
    _hold(_account(context, acct), _user(context, "A")["id"], description, amount, way,
          when, pending=True)


@given("a balance check-in adjusted {acct:Q} by ${amount:Amt} {way:Way} {when:Rel}")
def given_adjustment(context, acct, amount, way, when):
    _hold(_account(context, acct), _user(context, "A")["id"], "Balance check-in",
          amount, way, when, adjustment=True)
    context.adjustment = (amount, when)


@given("a statement for {acct:Q} lists {description:Q} for ${amount:Amt} {way:Way} {when:Rel}")
def given_statement(context, acct, description, amount, way, when):
    context.statement = {"account": acct, "build": _ofx, "closing": None, "lines": [
        {"description": description, "amount": amount, "way": way, "date": when}]}


@given('a CSV for {acct:Q} with "Debit" and "Credit" columns lists {description:Q} '
       'debited ${amount:Amt} {when:Rel}')
def given_csv(context, acct, description, amount, when):
    context.statement = {"account": acct, "build": _csv_split, "lines": [
        {"description": description, "amount": amount, "way": "out", "date": when}]}


@given("it lists {description:Q} for ${amount:Amt} {way:Way} {when:Rel}")
def given_another_line(context, description, amount, way, when):
    context.statement["lines"].append(
        {"description": description, "amount": amount, "way": way, "date": when})


@given("it lists {description:Q} credited ${amount:Amt} {when:Rel}")
def given_csv_credit(context, description, amount, when):
    context.statement["lines"].append(
        {"description": description, "amount": amount, "way": "in", "date": when})


@given("it closes with a balance of -${amount:Amt}")
def given_closing(context, amount):
    context.statement["closing"] = f"-{amount}"


@given("user {who:Who} has uploaded it and applied the review as shown")
def given_uploaded_and_applied(context, who):
    context.execute_steps(f"When user {who} uploads it")
    _apply(context)


# ── When ────────────────────────────────────────────────────────────────────

@when("user {who:Who} uploads it")
def when_uploading(context, who):
    payload, filename = context.statement["build"](context.statement)
    _upload(context, _account(context, context.statement["account"]), payload, filename)


@when("user {who:Who} uploads it again")
def when_uploading_again(context, who):
    _upload(context, *context.upload)


@when("user {who:Who} uploads it to user B's main account")
def when_uploading_elsewhere(context, who):
    payload, filename = context.statement["build"](context.statement)
    _upload(context, context.users["b"]["account_id"], payload, filename)


@when("user {who:Who} uploads a file that is not a statement to {acct:Q}")
def when_uploading_junk(context, who, acct):
    _upload(context, _account(context, acct), b"\x89PNG\r\n\x1a\nnot a statement", "photo.png")


@when("unchecks {description:Q} and applies")
def when_unchecking(context, description):
    _apply(context, uncheck=[description])


@when("applies the review as shown")
def when_applying(context):
    _apply(context)


# ── Then ────────────────────────────────────────────────────────────────────

@then("{description:Q} is shown as already recorded")
def then_recorded(context, description):
    _i, status, _html = _row_for(context, description)
    assert status == "recorded", f"{description!r} is {status}"


@then("{description:Q} is not offered for adding")
def then_not_offered(context, description):
    _i, _status, html = _row_for(context, description)
    assert 'name="apply"' not in html, "a recorded line carries a checkbox"


@then("{description:Q} is shown as missing and checked")
def then_missing_checked(context, description):
    _i, status, html = _row_for(context, description)
    assert status == "missing", f"{description!r} is {status}"
    assert re.search(r'name="apply"[^>]*\bchecked\b', html), f"{description!r} is not checked"


@then("{description:Q} carries a suggested category")
def then_suggested(context, description):
    _i, _status, html = _row_for(context, description)
    selected = re.search(r'<option value="\d+" selected>([^<]+)</option>', html)
    assert selected, f"{description!r} has no suggested category"
    assert selected.group(1) == context.suggested_category


@then("line {n:d} is shown as already recorded")
def then_line_recorded(context, n):
    status, _html = _rows(context)[n - 1]
    assert status == "recorded", f"line {n} is {status}"


@then("line {n:d} is shown as missing and checked")
def then_line_missing(context, n):
    status, html = _rows(context)[n - 1]
    assert status == "missing", f"line {n} is {status}"
    assert re.search(r'name="apply"[^>]*\bchecked\b', html)


@then("{description:Q} is shown as a possible match beside {other:Q}")
def then_possible(context, description, other):
    _i, status, html = _row_for(context, description)
    assert status == "possible", f"{description!r} is {status}"
    assert f"Possible match: {other}" in html


@then("{description:Q} is unchecked")
def then_unchecked(context, description):
    _i, _status, html = _row_for(context, description)
    assert 'name="apply"' in html, "no checkbox to leave unchecked"
    assert not re.search(r'name="apply"[^>]*\bchecked\b', html)


@then("exactly {n:d} transaction has been added to {acct:Q}")
@then("exactly {n:d} transactions have been added to {acct:Q}")
def then_added(context, n, acct):
    account_id = _account(context, acct)
    added = _count(account_id) - context.baseline[account_id]
    assert added == n, f"{added} added to {acct}, expected {n}"


@then("{acct:Q} holds {description:Q} for ${amount:Amt} {way:Way} {when:Rel}")
def then_holds(context, acct, description, amount, way, when):
    rows = _sql("SELECT amount, transaction_type, transaction_date, import_ref "
                "FROM transactions WHERE account_id = %s AND description = %s",
                (_account(context, acct), description))
    assert len(rows) == 1, f"{len(rows)} rows for {description!r}"
    got_amount, kind, on, ref = rows[0]
    assert (str(got_amount), kind, on) == (
        amount, "income" if way == "in" else "expense", when)
    assert ref, "an imported row carries its statement reference"


@then("{description:Q} is no longer pending")
def then_posted(context, description):
    rows = _sql("SELECT is_pending FROM transactions WHERE description = %s AND user_id = %s",
                (description, _user(context, "A")["id"]))
    assert rows == [(False,)], rows


@then("every line is shown as already recorded")
def then_all_recorded(context):
    statuses = {status for status, _html in _rows(context).values()}
    assert statuses == {"recorded"}, statuses


@then("{description:Q} is shown as money out")
def then_out(context, description):
    i, _status, html = _row_for(context, description)
    assert f'name="direction_{i}" value="out"' in html


@then("{description:Q} is shown as money in")
def then_in(context, description):
    i, _status, html = _row_for(context, description)
    assert f'name="direction_{i}" value="in"' in html


@then("user {who:Who} is told the ledger is ${gap:Amt} above the statement's closing balance")
def then_balance_gap(context, who, gap):
    body = _body(context)
    assert f"The ledger is ${gap} above the statement&#39;s closing balance" in body, \
        re.findall(r'<div class="flash">(.*?)</div>', body, re.S)


@then("the review warns about the adjustment of ${amount:Amt} {when:Rel}")
def then_adjustment_warning(context, amount, when):
    warning = re.search(r'<p [^>]*data-warning="adjustment"[^>]*>(.*?)</p>', _body(context), re.S)
    assert warning, "no adjustment warning"
    text = warning.group(1)
    assert f"${amount}" in text and when.strftime("%b %d, %Y") in text, text


@then("user {who:Who} is told the file could not be read")
def then_unreadable(context, who):
    assert context.response.status_code == 400, context.response.status_code
    assert "This file could not be read" in _body(context)
    assert context.mapped_rows is None, "a binary file was sent to the model"
    assert 'data-line="' not in _body(context), "a review table was rendered"


@then("there is no statement import")
def then_no_import_link(context):
    body = _body(context)
    assert context.response.status_code == 200
    assert "/transactions/import" not in body and "Import statement" not in body


@then("the statement import page is not found")
def then_import_404(context):
    status = context.client.get("/transactions/import").status_code
    assert status == 404, status

