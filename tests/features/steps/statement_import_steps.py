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
from datetime import date, timedelta
from html.parser import HTMLParser

from behave import given, register_type, then, when
from werkzeug.datastructures import MultiDict

from app import ai
from tests.features.support import _pattern, _user
from tests.helpers import _connection, create_account, create_transfer


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


def _apply(context, uncheck=(), choose=None):
    """Submit the review as rendered, optionally unticking lines, or setting
    one line's category select to `choose` = (description, value) and ticking it."""
    form = _ReviewForm()
    form.feed(_body(context))
    skip = {str(_row_for(context, d)[0]) for d in uncheck}
    data = [(k, v) for k, v in form.fields if not (k == "apply" and v in skip)]
    if choose:
        i = str(_row_for(context, choose[0])[0])
        data = [(k, v) for k, v in data
                if k != f"category_{i}" and not (k == "apply" and v == i)]
        data += [(f"category_{i}", choose[1]), ("apply", i)]
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

    def read_shots(images, today, api_key):
        context.screenshot_calls.append(images)
        return ai._ScreenshotRead(lines=list(context.screenshot_lines))

    for name, stub in (("_call_categorize_model", categorize),
                       ("_call_csv_mapping_model", map_columns),
                       ("_call_screenshot_model", read_shots)):
        original = getattr(ai, name)
        setattr(ai, name, stub)
        context.add_cleanup(setattr, ai, name, original)

    context.accounts, context.baseline, context.mapped_rows = {}, {}, None
    context.screenshot_calls, context.screenshot_lines = [], []


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
    _upload(context, _account(context, acct), b"%PDF-1.7\n\x00\x01binary", "statement.pdf")


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
    assert context.screenshot_calls == [], "a binary file was sent to the model"
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



# ── #446: transfers ─────────────────────────────────────────────────────────

def _transfer_legs(account_id, amount):
    return _sql("SELECT id, transaction_type, transaction_date, is_transfer, transfer_group_id, "
                "description FROM transactions WHERE account_id = %s AND amount = %s",
                (account_id, amount))


@given("{acct:Q} has nothing for ${amount:Amt}")
def given_nothing_for(context, acct, amount):
    assert _transfer_legs(_account(context, acct), amount) == []


@given("a transfer of ${amount:Amt} from {src:Q} to {dst:Q} {when:Rel} exists")
def given_existing_transfer(context, amount, src, dst, when):
    create_transfer(_user(context, "A")["id"], _account(context, src),
                    _account(context, dst), amount, when)


@when("user {who:Who} records {description:Q} as a transfer from {acct:Q}")
def when_recording_transfer(context, who, description, acct):
    context.execute_steps(f"When user {who} uploads it")
    _apply(context, choose=(description, f"xfer:{_account(context, acct)}"))


@when("user {who:Who} records {description:Q} as a transfer from user B's main account")
def when_recording_foreign_transfer(context, who, description):
    context.execute_steps(f"When user {who} uploads it")
    _apply(context, choose=(description, f"xfer:{context.users['b']['account_id']}"))


@then("a transfer of ${amount:Amt} from {src:Q} to {dst:Q} {when:Rel} has been added")
def then_transfer_added(context, amount, src, dst, when):
    out = _transfer_legs(_account(context, src), amount)
    into = _transfer_legs(_account(context, dst), amount)
    assert len(out) == 1 and len(into) == 1, (out, into)
    assert out[0][1] == "expense" and into[0][1] == "income"
    assert out[0][2] == into[0][2] == when
    assert out[0][4] is not None and out[0][4] == into[0][4], "the legs are not linked"
    context.transfer_group = into[0][4]


@then("neither leg counts as spending or income")
def then_neither_counts(context):
    flags = _sql("SELECT is_transfer FROM transactions WHERE transfer_group_id = %s",
                 (context.transfer_group,))
    assert flags == [(True,), (True,)], flags


@then("{description:Q} has become the {acct:Q} leg of that transfer")
def then_converted(context, description, acct):
    rows = _sql("SELECT is_transfer, transfer_group_id, category_id FROM transactions "
                "WHERE account_id = %s AND description = %s",
                (_account(context, acct), description))
    assert len(rows) == 1, rows
    is_transfer, group, category = rows[0]
    assert is_transfer and group is not None, rows
    legs = _sql("SELECT account_id FROM transactions WHERE transfer_group_id = %s", (group,))
    assert sorted(a for (a,) in legs) == sorted(context.accounts.values()), legs


@then("{acct:Q} holds only one ${amount:Amt} row")
def then_only_one(context, acct, amount):
    rows = _transfer_legs(_account(context, acct), amount)
    assert len(rows) == 1, rows


# ── #447: screenshots ───────────────────────────────────────────────────────

# A PNG signature is all image_type() checks; the stubbed seam never decodes it.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _shot_line(description, amount, way, when, legible=True, year=True):
    return ai._ScreenshotLine(month=when.month, day=when.day,
                              year=when.year if year else None, description=description,
                              amount=amount, direction=way, legible=legible)


@given("a screenshot of {acct:Q} shows {description:Q} for ${amount:Amt} {way:Way} {when:Rel}")
def given_screenshot(context, acct, description, amount, way, when):
    context.shot_account = acct
    # Banking apps rarely print the year; the scenario's dates are recent, so
    # leaving it out exercises resolve_date() without changing the answer.
    context.screenshot_lines.append(_shot_line(description, amount, way, when, year=False))


@given("it shows {description:Q} for ${amount:Amt} {way:Way} {when:Rel}, cut off")
def given_cut_off(context, description, amount, way, when):
    context.screenshot_lines.append(
        _shot_line(description, amount, way, when, legible=False, year=False))


@given("a screenshot of {acct:Q} shows {description:Q} dated a few days from now, with no year")
def given_future_day(context, acct, description):
    # ⚠️ Never Feb 29: the year before has none, so resolve_date() would
    # correctly refuse it and this scenario would fail one day in four years.
    day = date.today() + timedelta(days=3)
    if (day.month, day.day) == (2, 29):
        day += timedelta(days=1)
    context.shot_account, context.future_day = acct, day
    context.screenshot_lines.append(_shot_line(description, "9.99", "out", day, year=False))


@given("reading screenshots fails")
def given_reading_fails(context):
    def broken(images, today, api_key):
        context.screenshot_calls.append(images)
        raise ai.ParseError("model unavailable")
    original = ai._call_screenshot_model
    ai._call_screenshot_model = broken
    context.add_cleanup(setattr, ai, "_call_screenshot_model", original)


@when("user {who:Who} uploads the screenshot")
def when_uploading_screenshot(context, who):
    _upload(context, _account(context, context.shot_account), PNG, "shot.png")


@when("user {who:Who} uploads a screenshot to {acct:Q}")
def when_uploading_a_screenshot(context, who, acct):
    _upload(context, _account(context, acct), PNG, "shot.png")


@then("{description:Q} is shown flagged and unchecked")
def then_flagged(context, description):
    _i, status, html = _row_for(context, description)
    assert "data-uncertain" in html, f"{description!r} is not flagged"
    assert 'name="apply"' in html, "it can still be ticked by hand"
    assert not re.search(r'name="apply"[^>]*\bchecked\b', html)


@then("{description:Q} is dated a year before that day")
def then_last_year(context, description):
    i, _status, html = _row_for(context, description)
    day = context.future_day
    expected = day.replace(year=day.year - 1)
    assert f'name="date_{i}" value="{expected.isoformat()}"' in html, html


@then("no balance comparison is shown")
def then_no_balance(context):
    body = _body(context)
    assert "Added 1 transaction" in body, "the apply did not land"
    assert "closing balance" not in body


@then("user {who:Who} is told the screenshots could not be read")
def then_shots_unreadable(context, who):
    assert context.response.status_code == 400, context.response.status_code
    assert "These screenshots could not be read" in _body(context)
    assert len(context.screenshot_calls) == 1, "the model was asked once"
