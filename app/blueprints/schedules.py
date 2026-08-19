"""v10.0 Scheduled — recurring income & expense templates.

A schedule is NOT a ledger row. `run_due_schedules()` materializes a plain
transaction on each due date (going forward, no back-fill) and advances next_due.
Inline-CRUD shape mirrors blueprints/accounts.py (ownership guard → 404, db_cursor,
hx_toast fragments). Date math is reused from blueprints/transactions.py."""
from datetime import date, datetime

import psycopg2
from dateutil.relativedelta import relativedelta
from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.transactions import (
    FREQUENCY_LABELS,
    VALID_FREQUENCIES,
    _clamp_to_month,
    compute_next_due,
    validate_category_account,
)
from app.db import db_cursor
from app.helpers import GENERIC_ERROR, hx_toast, is_htmx, parse_int_param, parse_positive_amount

bp = Blueprint('schedules', __name__)

SCHEDULE_ROW_SQL = """
    SELECT s.id, s.amount, s.description, s.category_id, s.account_id,
           s.transaction_type, s.frequency, s.anchor_day, s.second_day,
           s.next_due, s.end_date, s.is_variable_amount, s.is_active,
           (s.end_date IS NOT NULL AND s.next_due > s.end_date) AS is_finished,
           c.name AS category_name, a.account_name
    FROM schedules s
    LEFT JOIN categories c ON s.category_id = c.id
    LEFT JOIN account a ON s.account_id = a.account_id
    WHERE s.user_id = %s {extra}
    ORDER BY s.transaction_type, s.next_due, s.id
"""


def pick_next_due(schedules):
    """The soonest schedule that will actually post, or None. Pure.

    #241 — "what is due next" is the question this page is opened with, and the
    table answered it only by sorting. The rows are already ordered by
    transaction_type FIRST, so the top row is not the answer.

    ⚠️ Skips inactive schedules and ones past their end_date (#32's
    `is_finished`): both still render in the table, deliberately, but neither
    is going to post again, and naming one as "next due" would be a lie.
    """
    live = [s for s in schedules
            if s.is_active and not s.is_finished and s.next_due]
    return min(live, key=lambda s: s.next_due) if live else None


def compute_initial_semimonthly_due(anchor_day, second_day, today):
    """First semi-monthly pay day on or after `today` — never back-fills.
    Picks the earlier of the two pay days that hasn't passed this month, else
    rolls to the earlier pay day next month."""
    lo, hi = sorted((anchor_day, second_day))
    lo_date = _clamp_to_month(today.year, today.month, lo)
    hi_date = _clamp_to_month(today.year, today.month, hi)
    if today <= lo_date:
        return lo_date
    if today <= hi_date:
        return hi_date
    nxt = today + relativedelta(months=1)
    return _clamp_to_month(nxt.year, nxt.month, lo)


def run_due_schedules(user_id):
    """Generate plain transactions for every active schedule whose next_due has
    arrived, catching up (loop) if several occurrences are overdue, then advance
    next_due past today. Scoped to the given user."""
    today = date.today()
    with db_cursor(commit=True) as cursor:
        # FOR UPDATE serializes concurrent runs (gunicorn serves requests on
        # multiple threads — e.g. two tabs loading /dashboard and /transactions
        # at once). Without it, both runs read next_due before either advances
        # it and each materializes the same occurrence. The second run blocks
        # here until the first commits, re-evaluates next_due <= today against
        # the updated row, and finds nothing due.
        cursor.execute("""
            SELECT id, amount, description, category_id, account_id,
                   transaction_type, frequency, anchor_day, second_day, next_due,
                   end_date
            FROM schedules
            WHERE is_active = true AND next_due <= %s AND user_id = %s
              AND (end_date IS NULL OR next_due <= end_date)
            FOR UPDATE
        """, (today, user_id))
        due = cursor.fetchall()
        for (sid, amount, desc, cat_id, acc_id, ttype, freq,
             anchor_day, second_day, next_due, end_date) in due:
            # #32: nothing materializes past the schedule's last day. Note this
            # also covers the nastiest case for free — a schedule whose next_due
            # went stale AND whose end_date has since passed gives zero
            # iterations, so the catch-up loop back-fills nothing on the next
            # login rather than posting months of charges that never happened.
            stop = min(today, end_date) if end_date is not None else today
            cur_due = next_due
            while cur_due <= stop:
                # #191 — schedule_id is stamped on EVERY materialized row, not
                # just a variable one. The link is what makes a posted row
                # traceable back to the thing that posted it; the schedule's
                # is_variable_amount flag decides only whether the daily job says
                # anything about it. Deciding here instead would mean flipping the
                # flag later could not see the rows already posted.
                cursor.execute("""
                    INSERT INTO transactions
                        (amount, description, category_id, account_id,
                         transaction_date, transaction_type, schedule_id, user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (amount, desc, cat_id, acc_id, cur_due, ttype, sid, user_id))
                cur_due = compute_next_due(
                    cur_due, freq, anchor_day=anchor_day, second_day=second_day)
            cursor.execute(
                "UPDATE schedules SET next_due = %s WHERE id = %s AND user_id = %s",
                (cur_due, sid, user_id))


def _fetch_schedule_row(cursor, schedule_id):
    """Single schedule row (joined names) for the current user, or 404."""
    cursor.execute(SCHEDULE_ROW_SQL.format(extra="AND s.id = %s"),
                   (current_user.id, schedule_id))
    row = cursor.fetchone()
    if row is None:
        abort(404)
    return row


def _parse_form(form):
    """Validate a schedule create/edit form. Returns (errors, fields) where
    fields carries the computed next_due/anchor_day/second_day ready to store."""
    errors = []
    transaction_type = form.get('transaction_type', 'expense')
    if transaction_type not in ('income', 'expense'):
        errors.append('Type must be income or expense')

    amount, amount_error = parse_positive_amount(form.get('amount'))
    if amount_error:
        errors.append(amount_error)

    description = form.get('description', '').strip()
    category_id = parse_int_param(form.get('category_id'))
    account_id = parse_int_param(form.get('account_id'))
    if not account_id:
        errors.append('Account is required')

    frequency = form.get('frequency')
    if frequency not in VALID_FREQUENCIES:
        errors.append('Please choose a valid frequency')

    anchor_day = second_day = None
    next_due = None
    today = date.today()
    if frequency == 'semimonthly':
        try:
            anchor_day = int(form.get('anchor_day', '').strip())
            second_day = int(form.get('second_day', '').strip())
            if not (1 <= anchor_day <= 31 and 1 <= second_day <= 31):
                errors.append('Pay days must be between 1 and 31')
            elif anchor_day == second_day:
                errors.append('The two pay days must be different')
            else:
                next_due = compute_initial_semimonthly_due(anchor_day, second_day, today)
        except ValueError:
            errors.append('Both pay days are required for a semi-monthly schedule')
    elif frequency in VALID_FREQUENCIES:
        next_date_raw = form.get('next_due', '').strip()
        if not next_date_raw:
            errors.append('Next date is required')
        else:
            try:
                next_due = datetime.strptime(next_date_raw, '%Y-%m-%d').date()
                if next_due < today:
                    errors.append('Next date cannot be in the past')
            except ValueError:
                errors.append('Next date must be a valid date')

    # #32 — optional last day. Blank means "runs indefinitely", which is every
    # schedule that existed before this field. It is validated against the
    # next_due computed just above, NOT the stored one: an edit recomputes
    # next_due from the form, so comparing against the old value would accept a
    # pair that is already contradictory by the time it is written.
    end_date = None
    end_date_raw = form.get('end_date', '').strip()
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, '%Y-%m-%d').date()
            if next_due is not None and end_date < next_due:
                errors.append('End date cannot be before the next date')
        except ValueError:
            errors.append('End date must be a valid date')

    # #191 — a checkbox, so absent means false on every form that posts. Nothing
    # validates it: it changes no arithmetic, only whether the daily job says
    # anything once this schedule has posted.
    is_variable_amount = form.get('is_variable_amount') == 'true'

    fields = {'amount': amount, 'description': description, 'category_id': category_id,
              'account_id': account_id, 'transaction_type': transaction_type,
              'frequency': frequency, 'anchor_day': anchor_day, 'second_day': second_day,
              'next_due': next_due, 'end_date': end_date,
              'is_variable_amount': is_variable_amount}
    return errors, fields


def _form_lists(cursor):
    cursor.execute("SELECT id, name, kind FROM categories WHERE user_id = %s ORDER BY name",
                   (current_user.id,))
    categories = cursor.fetchall()
    cursor.execute("SELECT account_id, account_name FROM account WHERE user_id = %s "
                   "ORDER BY account_name", (current_user.id,))
    accounts = cursor.fetchall()
    return categories, accounts


@bp.route('/scheduled', methods=['GET', 'POST'])
@login_required
def scheduled():
    if request.method == 'POST':
        errors, f = _parse_form(request.form)
        if not errors:
            with db_cursor() as cursor:
                errors += validate_category_account(
                    cursor, current_user.id, f['category_id'], f['account_id'])
        if errors:
            msg = errors[0]
            if is_htmx():
                return hx_toast(make_response('', 200), msg, 'error')
            flash(msg)
            return redirect(url_for('schedules.scheduled'))
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute("""
                    INSERT INTO schedules
                        (amount, description, category_id, account_id,
                         transaction_type, frequency, anchor_day, second_day,
                         next_due, end_date, is_variable_amount, user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (f['amount'], f['description'], f['category_id'], f['account_id'],
                      f['transaction_type'], f['frequency'], f['anchor_day'],
                      f['second_day'], f['next_due'], f['end_date'],
                      f['is_variable_amount'], current_user.id))
                new_id = cursor.fetchone()[0]
        except psycopg2.Error:
            current_app.logger.exception('create schedule failed')
            if is_htmx():
                return hx_toast(make_response('', 200), GENERIC_ERROR, 'error')
            flash(GENERIC_ERROR)
            return redirect(url_for('schedules.scheduled'))
        if is_htmx():
            with db_cursor() as cursor:
                schedule = _fetch_schedule_row(cursor, new_id)
            resp = make_response(render_template(
                'partials/_schedule_row.html', schedule=schedule,
                frequency_labels=FREQUENCY_LABELS))
            return hx_toast(resp, 'Schedule added')
        flash('Schedule added')
        return redirect(url_for('schedules.scheduled'))

    run_due_schedules(current_user.id)
    with db_cursor() as cursor:
        cursor.execute(SCHEDULE_ROW_SQL.format(extra=""), (current_user.id,))
        schedules = cursor.fetchall()
        categories, accounts = _form_lists(cursor)
    return render_template('scheduled.html', schedules=schedules,
                           next_due=pick_next_due(schedules),
                           categories=categories, accounts=accounts,
                           frequency_labels=FREQUENCY_LABELS)


@bp.route('/scheduled/<int:schedule_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_schedule(schedule_id):
    with db_cursor() as cursor:
        schedule = _fetch_schedule_row(cursor, schedule_id)
        categories, accounts = _form_lists(cursor)

    if request.method == 'POST':
        errors, f = _parse_form(request.form)
        if not errors:
            with db_cursor() as cursor:
                errors += validate_category_account(
                    cursor, current_user.id, f['category_id'], f['account_id'])
        if errors:
            return render_template('partials/_schedule_edit_row.html',
                                   schedule=schedule, categories=categories,
                                   accounts=accounts, error=errors[0])
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute("""
                    UPDATE schedules SET amount=%s, description=%s, category_id=%s,
                        account_id=%s, transaction_type=%s, frequency=%s,
                        anchor_day=%s, second_day=%s, next_due=%s, end_date=%s,
                        is_variable_amount=%s
                    WHERE id=%s AND user_id=%s
                """, (f['amount'], f['description'], f['category_id'], f['account_id'],
                      f['transaction_type'], f['frequency'], f['anchor_day'],
                      f['second_day'], f['next_due'], f['end_date'],
                      f['is_variable_amount'], schedule_id, current_user.id))
        except psycopg2.Error:
            current_app.logger.exception('edit schedule failed')
            return render_template('partials/_schedule_edit_row.html',
                                   schedule=schedule, categories=categories,
                                   accounts=accounts, error=GENERIC_ERROR)
        with db_cursor() as cursor:
            schedule = _fetch_schedule_row(cursor, schedule_id)
        resp = make_response(render_template(
            'partials/_schedule_row.html', schedule=schedule,
            frequency_labels=FREQUENCY_LABELS))
        return hx_toast(resp, 'Schedule updated')

    return render_template('partials/_schedule_edit_row.html', schedule=schedule,
                           categories=categories, accounts=accounts)


@bp.route('/scheduled/<int:schedule_id>/row')
@login_required
def schedule_row(schedule_id):
    with db_cursor() as cursor:
        schedule = _fetch_schedule_row(cursor, schedule_id)
    return render_template('partials/_schedule_row.html', schedule=schedule,
                           frequency_labels=FREQUENCY_LABELS)


@bp.route('/scheduled/<int:schedule_id>', methods=['DELETE'])
@login_required
def delete_schedule(schedule_id):
    with db_cursor() as cursor:
        _fetch_schedule_row(cursor, schedule_id)
    with db_cursor(commit=True) as cursor:
        cursor.execute("DELETE FROM schedules WHERE id = %s AND user_id = %s",
                       (schedule_id, current_user.id))
    return hx_toast(make_response('', 200), 'Schedule deleted')
