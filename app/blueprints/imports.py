"""Statement import (#445): upload a statement export for one account, review
what the ledger is missing, and add only what is checked.

Three requests, and the uploaded file lives only inside the first:

    GET  /transactions/import         the upload form
    POST /transactions/import         parse → match → categorise → review page
    POST /transactions/import/apply   add the checked lines, then the balance check

⚠️ THE FILE IS NEVER STORED. It is read into memory, parsed and dropped, and is
never logged: a statement carries account numbers and every transaction in it,
and the log leaves the app on every `docker logs`. The review page round-trips
only the parsed lines that need a decision, as hidden fields, so apply
re-validates every one of them exactly as the Add Transaction form would — a
hand-edited review form gains nothing the ordinary form does not already allow.

⚠️ WHO DECIDES WHAT. `statements.py` parses and matches (pure, no model);
`ai.map_csv_columns()` names a CSV's columns and `ai.classify_transactions()`
suggests categories. The model never decides what is missing.

Gated on `ai_enabled()` like every AI surface, even though an OFX upload needs
the model only for categories: one gate, one place the feature appears.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import psycopg2
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import limiter
from app.ai import ParseError, classify_transactions, map_csv_columns
from app.blueprints.transactions import validate_category_account
from app.db import db_cursor
from app.helpers import GENERIC_ERROR, ai_enabled, parse_int_param, parse_positive_amount
from app.statements import (
    DATE_FORMATS,
    DESCRIPTION_MAX,
    MATCH_DAYS,
    MAX_FILE_BYTES,
    REF_MAX,
    Line,
    StatementError,
    adjustments_within,
    csv_rows,
    decode,
    find_counterpart,
    looks_binary,
    looks_like_ofx,
    match_lines,
    parse_csv,
    parse_ofx,
    sample_rows,
    validate_mapping,
)

bp = Blueprint('imports', __name__)

UNREADABLE = ("This file could not be read. Upload an OFX, QFX or CSV export of one "
              "account.")


def _accounts(user_id):
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT account_id, account_name FROM account WHERE user_id = %s "
            "ORDER BY account_name", (user_id,))
        return cursor.fetchall()


def _owned_account(account_id):
    """The user's account row, or a 404 — for a missing id and another user's
    alike, so the response never confirms that someone else's account exists."""
    if account_id is None:
        abort(404)
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT account_id, account_name FROM account "
            "WHERE account_id = %s AND user_id = %s",
            (account_id, current_user.id))
        account = cursor.fetchone()
    if account is None:
        abort(404)
    return account


def _upload_form(error=None, status=200, selected=None):
    return render_template('statement_import.html',
                           accounts=_accounts(current_user.id),
                           error=error, selected=selected), status


def _read_statement(raw):
    """Bytes to a Statement, or a StatementError with a message safe to show."""
    text = decode(raw)
    if looks_binary(text):
        raise StatementError(UNREADABLE)
    if looks_like_ofx(text):
        return parse_ofx(text)
    rows = csv_rows(text)
    if len(rows) < 2:
        raise StatementError(UNREADABLE)
    try:
        raw_mapping = map_csv_columns(sample_rows(rows), DATE_FORMATS)
    except ParseError as e:
        raise StatementError("This file could not be read right now. Try again.") from e
    return parse_csv(rows, validate_mapping(raw_mapping, rows))


def _ledger(account_id, start, end):
    """The account's rows around the statement period: every candidate a line
    could match (±MATCH_DAYS), and the check-in adjustments inside it."""
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, transaction_date, amount, transaction_type, description, "
            "is_pending, is_adjustment, import_ref "
            "FROM transactions "
            "WHERE user_id = %s AND account_id = %s "
            "AND transaction_date BETWEEN %s AND %s "
            "ORDER BY transaction_date, id",
            (current_user.id, account_id,
             start - timedelta(days=MATCH_DAYS), end + timedelta(days=MATCH_DAYS)))
        return cursor.fetchall()


def _other_rows(account_id, start, end):
    """Every row in the user's OTHER accounts around the statement period: the
    candidates a transfer line could pair with (#446)."""
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, account_id, transaction_date, amount, transaction_type, "
            "description, is_transfer, is_adjustment "
            "FROM transactions "
            "WHERE user_id = %s AND account_id <> %s "
            "AND transaction_date BETWEEN %s AND %s "
            "ORDER BY transaction_date, id",
            (current_user.id, account_id,
             start - timedelta(days=MATCH_DAYS), end + timedelta(days=MATCH_DAYS)))
        return cursor.fetchall()


def _pairings(reviews, other_rows):
    """For each missing line that looks like a transfer, the other account's
    plain row it would pair with, keyed by line index. Each row pairs with at
    most one line, in statement order."""
    pairs, used = {}, set()
    for r in reviews:
        if r.status != 'missing' or not r.transfer_like:
            continue
        row = find_counterpart(r.line, other_rows, used)
        if row is not None:
            pairs[r.index] = row
            used.add(row.id)
    return pairs


def _categories():
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, name, kind FROM categories WHERE user_id = %s ORDER BY name",
            (current_user.id,))
        return cursor.fetchall()


def _suggest(reviews, categories):
    """Suggested category ids for the missing lines, keyed by line index. A
    failed call degrades to no suggestions; the review still works, and says
    so. A suggestion of the wrong kind (an expense category for money in) needs
    no filter here: the review lists only categories of the line's own kind, so
    it has no option to be selected."""
    wanted = [r for r in reviews if r.status == 'missing' and not r.transfer_like]
    if not wanted:
        return {}, False
    payload = [{
        'id': r.index,
        'description': r.line.description,
        'amount': str(r.line.amount),
        'type': 'income' if r.line.direction == 'in' else 'expense',
        'current_category': None,
    } for r in wanted]
    try:
        suggestions = classify_transactions(payload, categories)
    except ParseError:
        return {}, True
    return {s['id']: s['category_id'] for s in suggestions}, False


@bp.route('/transactions/import', methods=['GET'])
@login_required
def import_form():
    if not ai_enabled():
        abort(404)
    return _upload_form(selected=parse_int_param(request.args.get('account')))


@bp.route('/transactions/import', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def import_scan():
    if not ai_enabled():
        abort(404)

    account = _owned_account(parse_int_param(request.form.get('account_id')))

    # Refuse an oversized body before Werkzeug is asked to hold it all.
    if (request.content_length or 0) > MAX_FILE_BYTES + 64_000:
        return _upload_form("That file is too large. Export a shorter date range.",
                            413, account.account_id)
    upload = request.files.get('statement')
    raw = upload.read(MAX_FILE_BYTES + 1) if upload else b''
    if not raw:
        return _upload_form("Choose a statement file to upload.", 400, account.account_id)
    if len(raw) > MAX_FILE_BYTES:
        return _upload_form("That file is too large. Export a shorter date range.",
                            413, account.account_id)

    try:
        statement = _read_statement(raw)
    except StatementError as e:
        return _upload_form(str(e), 400, account.account_id)

    ledger = _ledger(account.account_id, statement.start, statement.end)
    reviews = match_lines(statement.lines, ledger)
    categories = _categories()
    suggested, suggest_failed = _suggest(reviews, categories)
    pairings = _pairings(reviews, _other_rows(account.account_id,
                                              statement.start, statement.end))
    other_accounts = [a for a in _accounts(current_user.id)
                      if a.account_id != account.account_id]

    counts = {s: sum(1 for r in reviews if r.status == s)
              for s in ('recorded', 'pending', 'possible', 'missing')}
    return render_template(
        'statement_review.html',
        account=account,
        statement=statement,
        reviews=reviews,
        counts=counts,
        suggested=suggested,
        suggest_failed=suggest_failed,
        pairings=pairings,
        other_accounts=other_accounts,
        account_names={a.account_id: a.account_name for a in other_accounts},
        expense_categories=[c for c in categories if c.kind == 'expense'],
        income_categories=[c for c in categories if c.kind == 'income'],
        adjustments=adjustments_within(ledger, statement.start, statement.end),
    )


def _posted_line(form, i):
    """One reviewed line from the posted form, validated exactly like a hand
    entry. Returns a dict, or None if anything in it is not acceptable."""
    try:
        when = date.fromisoformat(form.get(f'date_{i}', ''))
    except ValueError:
        return None
    amount, error = parse_positive_amount(form.get(f'amount_{i}'))
    if error:
        return None
    direction = form.get(f'direction_{i}')
    if direction not in ('in', 'out'):
        return None
    ref = (form.get(f'ref_{i}') or '').strip() or None
    if ref is not None and len(ref) > REF_MAX:
        return None
    # The category select doubles as the transfer choice (#446): "xfer:<id>"
    # records the line as a transfer with that account instead of a category.
    choice = (form.get(f'category_{i}') or '').strip()
    transfer_account = None
    if choice.startswith('xfer:'):
        transfer_account = parse_int_param(choice[5:])
        if transfer_account is None:
            return None
        choice = ''
    return {
        'date': when,
        'amount': amount,
        'direction': direction,
        'type': 'income' if direction == 'in' else 'expense',
        'description': (form.get(f'description_{i}') or '').strip()[:DESCRIPTION_MAX],
        'ref': ref,
        'category_id': parse_int_param(choice),
        'pending_id': parse_int_param(form.get(f'pending_{i}')),
        'transfer_account': transfer_account,
    }


def _balance_message(account_id, closing_raw, end_raw):
    """Compare the ledger with the statement's closing balance, or None when
    the statement carried none (a CSV export)."""
    try:
        closing = Decimal(closing_raw)
        end = date.fromisoformat(end_raw)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not closing.is_finite():
        return None
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
            "THEN amount ELSE -amount END), 0) AS balance "
            "FROM transactions WHERE user_id = %s AND account_id = %s "
            "AND transaction_date <= %s",
            (current_user.id, account_id, end))
        balance = Decimal(cursor.fetchone().balance)
    gap = (balance - closing).quantize(Decimal('0.01'))
    when = f"{end:%b} {end.day}, {end.year}"
    if gap == 0:
        return f"The ledger agrees with the statement's closing balance on {when}."
    side = 'above' if gap > 0 else 'below'
    return (f"The ledger is ${abs(gap):,.2f} {side} the statement's closing balance "
            f"on {when}.")


def _record_transfer(cursor, account_id, line):
    """Record one statement line as a transfer pair (#446). Returns
    (transfers made, of which paired with an existing row).

    The import account's leg carries the line's import_ref, so a re-import
    recognises it. ON CONFLICT means an already-imported line makes nothing at
    all, never a lone second leg. The other leg is the existing plain row
    `find_counterpart()` picks (the same rule the review showed), converted in
    place; only when there is none is a new row inserted. No `used` set is
    needed here: a row converted for an earlier line is already a transfer leg
    when the next line re-reads the account, and find_counterpart skips those
    (a mutation pass showed a set here changed nothing)."""
    other = line['transfer_account']
    cursor.execute("SELECT nextval('transfer_group_seq') AS gid")
    gid = cursor.fetchone().gid
    cursor.execute(
        "INSERT INTO transactions (amount, description, account_id, transaction_date, "
        "transaction_type, is_transfer, transfer_group_id, import_ref, user_id) "
        "VALUES (%s, %s, %s, %s, %s, true, %s, %s, %s) "
        "ON CONFLICT (account_id, import_ref) WHERE import_ref IS NOT NULL DO NOTHING",
        (line['amount'], line['description'], account_id, line['date'], line['type'],
         gid, line['ref'], current_user.id))
    if cursor.rowcount == 0:
        return 0, 0

    as_line = Line(line['date'], Decimal(f"{line['amount']:.2f}"), line['direction'],
                   line['description'], line['ref'])
    # FOR UPDATE: two applies racing must not both convert the same row.
    cursor.execute(
        "SELECT id, transaction_date, amount, transaction_type, is_transfer, is_adjustment "
        "FROM transactions WHERE user_id = %s AND account_id = %s "
        "AND transaction_date BETWEEN %s AND %s FOR UPDATE",
        (current_user.id, other, line['date'] - timedelta(days=MATCH_DAYS),
         line['date'] + timedelta(days=MATCH_DAYS)))
    row = find_counterpart(as_line, cursor.fetchall())
    if row is not None:
        cursor.execute(
            "UPDATE transactions SET is_transfer = true, transfer_group_id = %s, "
            "category_id = NULL WHERE id = %s AND user_id = %s AND account_id = %s",
            (gid, row.id, current_user.id, other))
        return 1, 1
    cursor.execute(
        "INSERT INTO transactions (amount, description, account_id, transaction_date, "
        "transaction_type, is_transfer, transfer_group_id, user_id) "
        "VALUES (%s, %s, %s, %s, %s, true, %s, %s)",
        (line['amount'], line['description'], other, line['date'],
         'expense' if line['type'] == 'income' else 'income', gid, current_user.id))
    return 1, 0


@bp.route('/transactions/import/apply', methods=['POST'])
@limiter.limit("10 per minute")
@login_required
def import_apply():
    if not ai_enabled():
        abort(404)
    account = _owned_account(parse_int_param(request.form.get('account_id')))
    form = request.form

    lines, rejected = [], 0
    for raw_index in form.getlist('apply'):
        i = parse_int_param(raw_index)
        line = _posted_line(form, i) if i is not None else None
        if line is None:
            rejected += 1
        else:
            lines.append(line)

    # A transfer's other account must be the user's own, checked before any
    # write: naming someone else's account is a 404, as it is for the import
    # account itself. Naming the import account is just an unusable line.
    owned = {a.account_id for a in _accounts(current_user.id)}
    for line in list(lines):
        other = line['transfer_account']
        if other is None:
            continue
        if other not in owned:
            abort(404)
        if other == account.account_id:
            lines.remove(line)
            rejected += 1

    # Ownership of every posted category, before any write (the IDOR guard
    # shared with the Add Transaction form).
    with db_cursor() as cursor:
        for line in lines:
            if line['category_id'] is not None and validate_category_account(
                    cursor, current_user.id, line['category_id'], None):
                line['category_id'] = None

    added = posted = transfers = paired = 0
    try:
        with db_cursor(commit=True) as cursor:
            for line in lines:
                if line['transfer_account'] is not None:
                    made, joined = _record_transfer(cursor, account.account_id, line)
                    transfers += made
                    paired += joined
                    continue
                if line['pending_id'] is not None:
                    # The ledger already holds it as pending: clear the flag
                    # rather than add a second row. Scoped to this user AND this
                    # account, so a posted id can reach nothing else.
                    cursor.execute(
                        "UPDATE transactions SET is_pending = false "
                        "WHERE id = %s AND user_id = %s AND account_id = %s "
                        "AND is_pending",
                        (line['pending_id'], current_user.id, account.account_id))
                    posted += cursor.rowcount
                    continue
                # ON CONFLICT: applying the same review twice (a double click, a
                # back button) adds nothing the second time (#444's index).
                cursor.execute(
                    "INSERT INTO transactions (amount, description, category_id, "
                    "account_id, transaction_date, transaction_type, import_ref, user_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (account_id, import_ref) WHERE import_ref IS NOT NULL "
                    "DO NOTHING",
                    (line['amount'], line['description'], line['category_id'],
                     account.account_id, line['date'], line['type'], line['ref'],
                     current_user.id))
                added += cursor.rowcount
    except psycopg2.Error:
        current_app.logger.exception("Statement import apply failed")
        flash(GENERIC_ERROR)
        return redirect(url_for('imports.import_form', account=account.account_id))

    parts = []
    if added or not (transfers or posted):
        parts.append(f"Added {added} transaction{'s' if added != 1 else ''} "
                     f"to {account.account_name}.")
    if transfers:
        parts.append(f"Recorded {transfers} transfer{'s' if transfers != 1 else ''}"
                     + (f", {paired} paired with an entry already in the other account."
                        if paired else "."))
    if posted:
        parts.append(f"Marked {posted} pending transaction{'s' if posted != 1 else ''} posted.")
    if rejected:
        parts.append(f"{rejected} line{'s' if rejected != 1 else ''} could not be added.")
    balance = _balance_message(account.account_id, form.get('closing_balance'),
                               form.get('end_date'))
    if balance:
        parts.append(balance)
    flash(' '.join(parts))
    return redirect(url_for('transactions.transactions', account=account.account_id))
