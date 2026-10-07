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
suggests categories for merchants the user's own history does not answer
(#454). The model never decides what is missing.

Gated on `ai_enabled()` like every AI surface, even though an OFX upload needs
the model only for categories: one gate, one place the feature appears.
"""
import re
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
from app.ai import ParseError, classify_transactions, map_csv_columns, read_screenshots
from app.blueprints.transactions import validate_category_account
from app.db import db_cursor
from app.helpers import GENERIC_ERROR, ai_enabled, parse_int_param, parse_positive_amount
from app.statements import (
    DATE_FORMATS,
    DESCRIPTION_MAX,
    MATCH_DAYS,
    MAX_FILE_BYTES,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    MAX_PDF_BYTES,
    MAX_UPLOAD_BYTES,
    REF_MAX,
    Line,
    StatementError,
    adjustments_within,
    apply_summary,
    check_pdf,
    compare_balance,
    csv_rows,
    decode,
    explain_gap,
    find_counterpart,
    history_categories,
    image_type,
    is_pdf,
    is_possible,
    lines_from_pdf,
    lines_from_screenshots,
    looks_binary,
    looks_like_ofx,
    match_lines,
    parse_csv,
    parse_ofx,
    proposed_names,
    review_plan,
    sample_rows,
    unlisted_rows,
    validate_mapping,
)

bp = Blueprint('imports', __name__)

UNREADABLE = ("This file could not be read. Upload an OFX, QFX or CSV export of one "
              "account, its PDF statement, or screenshots of its transactions.")
TOO_LARGE = "That upload is too large. Export a shorter date range, or send fewer screenshots."
ONE_KIND = (f"Upload one statement file, or up to {MAX_IMAGES} screenshots, "
            "not a mix of the two.")


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
            "SELECT account_id, account_name, type FROM account "
            "WHERE account_id = %s AND user_id = %s",
            (account_id, current_user.id))
        account = cursor.fetchone()
    if account is None:
        abort(404)
    return account


def _detect_account(digits):
    """The user's one account known to end in `digits` (#461), or None. The
    unique index (sql/41) allows at most one; never another user's."""
    if digits is None:
        return None
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT account_id, account_name, type FROM account "
            "WHERE user_id = %s AND number_last4 = %s",
            (current_user.id, digits))
        return cursor.fetchone()


def _which_account(digits):
    if digits is None:
        return ("Which account is this statement for? It doesn't say, so choose the "
                "account and upload it again.")
    return (f"Which account is this statement for? None of your accounts is known to end "
            f"in {digits} yet. Choose the account and upload it again, and Budget Buddy "
            "will remember it.")


def _learn_last4(cursor, account_id, digits):
    """#461, settled with Sean: the last apply wins. The statement's last four
    digits MOVE onto the account it was applied to, cleared from any other of
    this user's accounts first (sql/41's unique index requires that order).
    Anything but exactly four digits from the posted form is ignored."""
    if not re.fullmatch(r"[0-9]{4}", digits or ''):
        return
    cursor.execute(
        "UPDATE account SET number_last4 = NULL "
        "WHERE user_id = %s AND number_last4 = %s AND account_id <> %s",
        (current_user.id, digits, account_id))
    cursor.execute(
        "UPDATE account SET number_last4 = %s WHERE account_id = %s AND user_id = %s",
        (digits, account_id, current_user.id))


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


def _read_screenshots(images):
    """Screenshots to a Statement (#447). The model reads; the app re-checks."""
    try:
        read = read_screenshots(images)
    except ParseError as e:
        raise StatementError("These screenshots could not be read right now. "
                             "Try again.") from e
    return lines_from_screenshots(read["lines"], date.today(), read.get("balance"),
                                  account_last4=read.get("account_last4"))


def _read_pdf(raw):
    """A PDF statement to a Statement (#460): its pages and lock are checked
    before the model reads it; the model reads; the app re-checks."""
    check_pdf(raw)
    try:
        read = read_screenshots([('application/pdf', raw)])
    except ParseError as e:
        raise StatementError("This PDF could not be read right now. Try again.") from e
    return lines_from_pdf(read, date.today())


def _uploads():
    """The uploaded files' bytes, each read to one byte past the largest cap
    (a PDF's) so an oversized one is detected without holding more of it."""
    cap = max(MAX_IMAGE_BYTES, MAX_PDF_BYTES) + 1
    return [f.read(cap) for f in request.files.getlist('statement') if f and f.filename]


def _ledger(account_id, start, end):
    """The account's rows around the statement period: every candidate a line
    could match (±MATCH_DAYS), and the check-in adjustments inside it."""
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, transaction_date, amount, transaction_type, description, "
            "category_id, is_pending, is_adjustment, is_transfer, import_ref "
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


# How far back the history reaches: the user's most recent rows, across every
# account. Bounded so a years-old ledger costs one modest read.
HISTORY_ROWS = 2000


def _history():
    """The user's most recent rows: their categories for history_categories()
    (#454), which skips an uncategorised row, and their names for
    proposed_names() (#455), where a hand-typed name counts whether or not it
    was ever filed. An adjustment is left out: a balance correction says
    nothing about a merchant."""
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, transaction_date, description, category_id FROM transactions "
            "WHERE user_id = %s AND NOT is_adjustment "
            "ORDER BY transaction_date DESC, id DESC LIMIT %s",
            (current_user.id, HISTORY_ROWS))
        return cursor.fetchall()


def _suggest(reviews, categories, history):
    """Suggested category ids for the missing lines, keyed by line index, and
    which of them came from the user's own history (#454). Only lines history
    cannot answer go to the model; when it answers them all, no call is made.
    A failed call degrades to no model suggestions; the review still works,
    and says so. A suggestion of the wrong kind (an expense category for money
    in) needs no filter here: the review lists only categories of the line's
    own kind, so it has no option to be selected."""
    known = history_categories(reviews, history, {c.id: c.kind for c in categories})
    wanted = [r for r in reviews
              if r.status == 'missing' and not r.transfer_like and r.index not in known]
    if not wanted:
        return known, set(known), False
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
        return known, set(known), True
    return ({**{s['id']: s['category_id'] for s in suggestions}, **known},
            set(known), False)


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

    # #461: the account may be left to the statement. Anything posted that is
    # not empty must still be one of the user's accounts.
    chosen = (request.form.get('account_id') or '').strip()
    account = _owned_account(parse_int_param(chosen)) if chosen else None
    selected = account.account_id if account else None

    # Refuse an oversized body before Werkzeug is asked to hold it all.
    # (Werkzeug spools a large part to a temporary file that is deleted when
    # the request ends; nothing here writes the upload anywhere.)
    if (request.content_length or 0) > MAX_UPLOAD_BYTES + 64_000:
        return _upload_form(TOO_LARGE, 413, selected)
    raws = _uploads()
    if not raws or not all(raws):
        return _upload_form("Choose a statement file or screenshots to upload.", 400,
                            selected)

    kinds = [image_type(raw) for raw in raws]
    from_screenshots = all(kinds)
    try:
        if len(raws) == 1 and is_pdf(raws[0]):
            # #460: by its bytes, never its name ("statement.pdf" may be a CSV).
            if len(raws[0]) > MAX_PDF_BYTES:
                return _upload_form(TOO_LARGE, 413, selected)
            statement = _read_pdf(raws[0])
        elif from_screenshots:
            # #447: screenshots, read by the model.
            if len(raws) > MAX_IMAGES:
                return _upload_form(f"Send at most {MAX_IMAGES} screenshots at a time.",
                                    400, selected)
            if any(len(raw) > MAX_IMAGE_BYTES for raw in raws):
                return _upload_form(TOO_LARGE, 413, selected)
            statement = _read_screenshots(list(zip(kinds, raws, strict=True)))
        elif len(raws) == 1:
            if len(raws[0]) > MAX_FILE_BYTES:
                return _upload_form(TOO_LARGE, 413, selected)
            statement = _read_statement(raws[0])
        else:
            return _upload_form(ONE_KIND, 400, selected)
    except StatementError as e:
        return _upload_form(str(e), 400, selected)

    detected = account is None
    if detected:
        account = _detect_account(statement.account_last4)
        if account is None:
            return _upload_form(_which_account(statement.account_last4), 200)

    ledger = _ledger(account.account_id, statement.start, statement.end)
    reviews = match_lines(statement.lines, ledger)
    categories = _categories()
    history = _history()
    suggested, from_history, suggest_failed = _suggest(reviews, categories, history)
    names = proposed_names(reviews, history)
    pairings = _pairings(reviews, _other_rows(account.account_id,
                                              statement.start, statement.end))
    other_accounts = [a for a in _accounts(current_user.id)
                      if a.account_id != account.account_id]

    # #457: a screenshot's period is only its earliest to its latest visible
    # line, so a skipped scroll would flag entries that are really there.
    unlisted, still_pending = ([], []) if from_screenshots else unlisted_rows(
        reviews, ledger, statement.start, statement.end)

    # #458: what applying will do, and which lines need a decision. Every line
    # lands in exactly one part of the page.
    plan, needs = review_plan(reviews, suggested, pairings)
    groups = {'needs': [], 'adding': [], 'posting': [], 'recorded': []}
    for r in reviews:
        if r.index in needs:
            groups['needs'].append(r)
        elif r.status == 'recorded':
            groups['recorded'].append(r)
        elif plan[r.index] == 'posted':
            groups['posting'].append(r)
        else:
            groups['adding'].append(r)
    effects = list(plan.values())
    apply_text = apply_summary({e: effects.count(e) for e in set(effects)})
    needs_text = ("Nothing needs you." if not needs
                  else f"{len(needs)} need{'s' if len(needs) == 1 else ''} you.")
    return render_template(
        'statement_review.html',
        account=account,
        statement=statement,
        reviews=reviews,
        plan=plan,
        groups=groups,
        apply_text=apply_text,
        needs_text=needs_text,
        suggested=suggested,
        names=names,
        from_history=from_history,
        suggest_failed=suggest_failed,
        pairings=pairings,
        other_accounts=other_accounts,
        account_names={a.account_id: a.account_name for a in other_accounts},
        expense_categories=[c for c in categories if c.kind == 'expense'],
        income_categories=[c for c in categories if c.kind == 'income'],
        adjustments=adjustments_within(ledger, statement.start, statement.end),
        detected=detected,
        unlisted=unlisted,
        still_pending=still_pending,
        match_days=MATCH_DAYS,
        description_max=DESCRIPTION_MAX,
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
    # #456: a possible match can update the entry it matched instead of adding.
    update_id = None
    if form.get(f'action_{i}') == 'update':
        update_id = parse_int_param(form.get(f'match_{i}'))
        if update_id is None:
            return None
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
        'update_id': update_id,
    }


def _signed_dollars(amount):
    return f"{'-' if amount < 0 else ''}${abs(amount):,.2f}"


def _unticked_lines(form, closing_date):
    """The review's lines left unticked that adding would have changed the
    balance by their whole amount (#459): missing lines, not a pending one
    (marking it posted moves no money) nor a possible match (updating it moves
    only the difference). Each is re-validated like a ticked line; one dated
    after the closing balance cannot be part of it."""
    ticked = set(form.getlist('apply'))
    found = []
    for key in form:
        index = key[5:] if key.startswith('date_') else None
        if index is None or index in ticked or not index.isdigit():
            continue
        if form.get(f'pending_{index}') or form.get(f'match_{index}'):
            continue
        line = _posted_line(form, int(index))
        if line is None or line['date'] > closing_date:
            continue
        found.append(Line(line['date'], Decimal(str(line['amount'])).quantize(Decimal('0.01')),
                          line['direction'], line['description'], None))
    return found


def _unlisted_rows(form, account_id, closing_date):
    """The review's ledger-only rows (#457), re-read from their posted ids and
    scoped to this user and account: a forged id finds nothing of anyone
    else's. (unlisted_rows() already left adjustments out; a hand-edited form
    could only reword this user's own message about their own row.)"""
    ids = [i for i in (parse_int_param(v) for v in form.getlist('unlisted')) if i is not None]
    if not ids:
        return []
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, transaction_date, amount, transaction_type, description "
            "FROM transactions WHERE id = ANY(%s) AND user_id = %s AND account_id = %s "
            "AND transaction_date <= %s",
            (ids, current_user.id, account_id, closing_date))
        return cursor.fetchall()


def _balance_message(account, form):
    """Compare the ledger with the statement's closing balance, as of the date
    that balance applies to, or None when the statement carried none. A card's
    balance is read in the nearer sign (compare_balance), and a gap that one
    unticked line or one ledger-only row explains exactly is named (#459)."""
    try:
        closing = Decimal(form.get('closing_balance'))
        closing_date = date.fromisoformat(form.get('closing_date'))
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
            (current_user.id, account.account_id, closing_date))
        balance = Decimal(cursor.fetchone().balance)
    gap = compare_balance(balance, closing, account.type == 'Credit Card')
    figure = (f"the statement's closing balance of {_signed_dollars(closing)} on "
              f"{closing_date:%b} {closing_date.day}, {closing_date.year}")
    if gap == 0:
        return f"The ledger agrees with {figure}."
    message = f"The ledger is ${abs(gap):,.2f} {'above' if gap > 0 else 'below'} {figure}."
    culprit = explain_gap(gap, _unticked_lines(form, closing_date),
                          _unlisted_rows(form, account.account_id, closing_date))
    if isinstance(culprit, Line):
        message += (f' Adding the unticked line "{culprit.description}" '
                    f"(${culprit.amount:,.2f} on {culprit.date:%b} {culprit.date.day}) "
                    "would close the gap.")
    elif culprit is not None:
        when = culprit.transaction_date
        message += (f' "{culprit.description or "An entry"}" (${culprit.amount:,.2f} on '
                    f"{when:%b} {when.day}) is in your ledger but not on the statement; "
                    "without it they would agree.")
    return message


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


def _update_entry(cursor, account_id, line):
    """Update an existing entry to a statement line (#456): the bank's amount
    and date, the line's import_ref, and posted. Its description and category
    are kept. Returns 1, or 0 when the row is not one the review could have
    offered, which counts as a line that could not be added.

    The row is re-checked here, never trusted from the form: this user's, this
    account's, not a transfer leg (changing one leg unbalances the pair), not
    an adjustment, not already carrying a statement reference, and still a
    possible match for the line by `is_possible()`, the rule the review used.
    FOR UPDATE, so two applies racing cannot both rewrite it."""
    cursor.execute(
        "SELECT id, transaction_date, amount, transaction_type FROM transactions "
        "WHERE id = %s AND user_id = %s AND account_id = %s AND import_ref IS NULL "
        "AND NOT is_transfer AND NOT is_adjustment FOR UPDATE",
        (line['update_id'], current_user.id, account_id))
    row = cursor.fetchone()
    as_line = Line(line['date'], Decimal(f"{line['amount']:.2f}"), line['direction'],
                   line['description'], line['ref'])
    if row is None or not is_possible(as_line, row):
        return 0
    cursor.execute(
        "UPDATE transactions SET amount = %s, transaction_date = %s, import_ref = %s, "
        "is_pending = false WHERE id = %s AND user_id = %s",
        (line['amount'], line['date'], line['ref'], row.id, current_user.id))
    return cursor.rowcount


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

    added = posted = transfers = paired = updated = 0
    try:
        with db_cursor(commit=True) as cursor:
            for line in lines:
                if line['transfer_account'] is not None:
                    made, joined = _record_transfer(cursor, account.account_id, line)
                    transfers += made
                    paired += joined
                    continue
                if line['update_id'] is not None:
                    done = _update_entry(cursor, account.account_id, line)
                    updated += done
                    rejected += 1 - done
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
            _learn_last4(cursor, account.account_id, form.get('number_last4'))
    except psycopg2.Error:
        current_app.logger.exception("Statement import apply failed")
        flash(GENERIC_ERROR)
        return redirect(url_for('imports.import_form', account=account.account_id))

    parts = []
    if added or not (transfers or posted or updated):
        parts.append(f"Added {added} transaction{'s' if added != 1 else ''} "
                     f"to {account.account_name}.")
    if transfers:
        parts.append(f"Recorded {transfers} transfer{'s' if transfers != 1 else ''}"
                     + (f", {paired} paired with an entry already in the other account."
                        if paired else "."))
    if updated:
        parts.append(f"Updated {updated} entr{'ies' if updated != 1 else 'y'} "
                     "to match the statement.")
    if posted:
        parts.append(f"Marked {posted} pending transaction{'s' if posted != 1 else ''} posted.")
    if rejected:
        parts.append(f"{rejected} line{'s' if rejected != 1 else ''} could not be added.")
    balance = _balance_message(account, form)
    if balance:
        parts.append(balance)
    flash(' '.join(parts))
    return redirect(url_for('transactions.transactions', account=account.account_id))
