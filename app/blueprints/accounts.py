from datetime import date

import psycopg2
from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.db import db_cursor
from app.helpers import GENERIC_ERROR, hx_toast, is_htmx, parse_positive_amount, parse_signed_amount
from app.services.accounts import (
    ACCOUNT_ROW_SQL,
    credit_utilization,
    monthly_interest,
)

bp = Blueprint('accounts', __name__)

VALID_ACCOUNT_TYPES = ('Credit Card', 'Debit Card', 'Bank Account')

# The row partial calls these by name. They live in services/accounts.py (#489)
# because Ask and the month read need them too; registering them stays here.
bp.add_app_template_global(credit_utilization, 'credit_utilization')
bp.add_app_template_global(monthly_interest, 'monthly_interest')


def _parse_credit_limit(raw):
    """Optional credit-limit field: blank → (None, None) = not set; otherwise
    the shared positive-amount validation (rejects nan/inf/zero/negative)."""
    raw = (raw or '').strip()
    if not raw:
        return None, None
    return parse_positive_amount(raw, 'Credit limit')


# A percent has a hard real-world ceiling a dollar limit doesn't: the realistic
# failure is a units typo ('2499' for 24.99%) that would narrate absurd interest
# figures as ground truth.
APR_MAX = 100


def _parse_apr(raw):
    """Optional APR field: blank → (None, None) = not set; otherwise the shared
    positive-amount validation plus the percent sanity cap."""
    raw = (raw or '').strip()
    if not raw:
        return None, None
    apr, error = parse_positive_amount(raw, 'APR')
    if error:
        return None, error
    if apr > APR_MAX:
        return None, 'APR must be 100 or less'
    return apr, None


def _fetch_account_row(cursor, account_id):
    """Single account row (with balance) for the current user, or 404."""
    cursor.execute(ACCOUNT_ROW_SQL.format(extra="AND a.account_id = %s"),
                   (current_user.id, account_id))
    row = cursor.fetchone()
    if row is None:
        abort(404)
    return row


def _validate(name, account_type):
    if not name:
        return 'Name is required'
    if len(name) > 50:
        return 'Name must be 50 characters or fewer'
    if account_type not in VALID_ACCOUNT_TYPES:
        return 'Invalid account type'
    return None


@bp.route('/accounts', methods=['GET', 'POST'])
@login_required
def accounts():
    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        error = _validate(name, account_type)
        if not error:
            credit_limit, error = _parse_credit_limit(request.form.get('credit_limit'))
        if not error:
            apr, error = _parse_apr(request.form.get('apr'))
        if error:
            if is_htmx():
                return hx_toast(make_response('', 200), error, 'error')
            flash(error)
            return redirect(url_for('accounts.accounts'))
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO account (account_name, type, credit_limit, apr, user_id) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING account_id",
                    (name, account_type, credit_limit, apr, current_user.id),
                )
                new_id = cursor.fetchone()[0]
        except psycopg2.Error:
            current_app.logger.exception('create account failed')
            if is_htmx():
                return hx_toast(make_response('', 200), GENERIC_ERROR, 'error')
            flash(GENERIC_ERROR)
            return redirect(url_for('accounts.accounts'))
        if is_htmx():
            with db_cursor() as cursor:
                account = _fetch_account_row(cursor, new_id)
            resp = make_response(render_template('partials/_account_row.html', account=account))
            return hx_toast(resp, 'Account added')
        flash('Account added successfully')
        return redirect(url_for('accounts.accounts'))

    with db_cursor() as cursor:
        cursor.execute(ACCOUNT_ROW_SQL.format(extra=""), (current_user.id,))
        all_accounts = cursor.fetchall()
    # Total available credit across cards with a limit set (None = no line).
    cards = [cu for a in all_accounts if a.type == 'Credit Card'
             for cu in [credit_utilization(a.balance, a.credit_limit)] if cu]
    credit_summary = None
    if cards:
        credit_summary = {'available': round(sum(c['available'] for c in cards), 2),
                          'limit': round(sum(c['limit'] for c in cards), 2)}
    return render_template('accounts.html', accounts=all_accounts,
                           credit_summary=credit_summary)


@bp.route('/accounts/<int:account_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_account(account_id):
    # Guard ownership up front (404 for missing/other-user).
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)

    if request.method == 'POST':
        name = request.form['name'].strip()
        account_type = request.form.get('type', '').strip()
        # Error paths re-render the edit row with the RAW posted limit/apr
        # strings (so the user sees what they typed); happy path stores the
        # parsed values.
        raw_limit = request.form.get('credit_limit', '')
        raw_apr = request.form.get('apr', '')
        error = _validate(name, account_type)
        credit_limit = None
        apr = None
        if not error:
            credit_limit, error = _parse_credit_limit(raw_limit)
        if not error:
            apr, error = _parse_apr(raw_apr)
        if error:
            account = account._replace(account_name=name, type=account_type,
                                       credit_limit=raw_limit, apr=raw_apr)
            return render_template('partials/_account_edit_row.html', account=account, error=error)
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE account SET account_name=%s, type=%s, credit_limit=%s, apr=%s "
                    "WHERE account_id=%s AND user_id=%s",
                    (name, account_type, credit_limit, apr, account_id, current_user.id),
                )
        except psycopg2.Error:
            current_app.logger.exception('edit account failed')
            account = account._replace(account_name=name, type=account_type,
                                       credit_limit=raw_limit, apr=raw_apr)
            return render_template('partials/_account_edit_row.html', account=account, error=GENERIC_ERROR)
        with db_cursor() as cursor:
            account = _fetch_account_row(cursor, account_id)
        resp = make_response(render_template('partials/_account_row.html', account=account))
        return hx_toast(resp, 'Account updated')

    return render_template('partials/_account_edit_row.html', account=account)


@bp.route('/accounts/<int:account_id>/checkin', methods=['GET', 'POST'])
@login_required
def checkin_account(account_id):
    # Guard ownership up front (404 for missing/other-user).
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)

    if request.method == 'GET':
        return render_template('partials/_account_checkin_row.html', account=account)

    actual, error = parse_signed_amount(request.form.get('actual_balance'), 'Bank balance')
    if error:
        return render_template('partials/_account_checkin_row.html', account=account, error=error)

    today = date.today()
    with db_cursor(commit=True) as cursor:
        # Lock the account row: re-guards ownership inside the write transaction
        # AND serializes concurrent check-ins, so two tabs posting at once can't
        # both insert the gap (the due-runner FOR UPDATE lesson).
        cursor.execute(
            "SELECT 1 FROM account WHERE account_id = %s AND user_id = %s FOR UPDATE",
            (account_id, current_user.id),
        )
        if cursor.fetchone() is None:
            abort(404)
        # Recompute the balance inside the locked transaction — never trust a
        # posted "app balance".
        cursor.execute(
            "SELECT COALESCE(SUM(CASE WHEN transaction_type = 'income' "
            "THEN amount ELSE -amount END), 0) "
            "FROM transactions WHERE account_id = %s AND user_id = %s",
            (account_id, current_user.id),
        )
        computed = float(cursor.fetchone()[0])
        delta = round(actual - computed, 2)
        if abs(delta) >= 0.01:
            cursor.execute(
                "INSERT INTO transactions (amount, description, transaction_date, "
                "category_id, account_id, transaction_type, is_adjustment, user_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (abs(delta), 'Balance check-in', today, None, account_id,
                 'income' if delta > 0 else 'expense', True, current_user.id),
            )
        cursor.execute(
            "UPDATE account SET last_checked_in = %s WHERE account_id = %s AND user_id = %s",
            (today, account_id, current_user.id),
        )

    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)
    resp = make_response(render_template('partials/_account_row.html', account=account))
    if abs(delta) >= 0.01:
        sign = '+' if delta > 0 else '-'
        return hx_toast(resp, f'Checked in — adjusted by {sign}${abs(delta):,.2f}')
    return hx_toast(resp, 'Checked in — balances match')


@bp.route('/accounts/<int:account_id>/row')
@login_required
def account_row(account_id):
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)
    return render_template('partials/_account_row.html', account=account)


@bp.route('/accounts/<int:account_id>', methods=['DELETE'])
@login_required
def delete_account(account_id):
    with db_cursor() as cursor:
        account = _fetch_account_row(cursor, account_id)
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM account WHERE account_id = %s AND user_id = %s",
                (account_id, current_user.id),
            )
    except psycopg2.errors.ForeignKeyViolation:
        current_app.logger.exception('delete account failed')
        resp = make_response(render_template('partials/_account_row.html', account=account))
        return hx_toast(resp, 'Cannot delete — this account is used by existing transactions', 'error')
    except psycopg2.Error:
        current_app.logger.exception('delete account failed')
        resp = make_response(render_template('partials/_account_row.html', account=account))
        return hx_toast(resp, GENERIC_ERROR, 'error')
    return hx_toast(make_response('', 200), 'Account deleted')
