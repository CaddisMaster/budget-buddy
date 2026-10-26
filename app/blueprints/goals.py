from collections import namedtuple
from datetime import datetime

import psycopg2
from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.db import db_cursor
from app.helpers import GENERIC_ERROR, ai_enabled, hx_toast, is_htmx, parse_positive_amount
from app.services.goals import account_balance, build_goals_view, build_single_goal_view

bp = Blueprint('goals', __name__)

# 'save' works toward a positive target; 'payoff' works a negative
# balance back to $0. A payoff goal snapshots at creation: baseline_amount =
# the account's balance and target_amount = the starting debt (−balance), so
# compute_goal_projection() is shared — the type only drives wording/forms.
GOAL_TYPES = ('save', 'payoff')

# Hand-built rows for the edit error/re-render paths — field names mirror the
# edit GET's "SELECT id, name, target_amount, target_date, account_id,
# goal_type" so real rows and fakes render identically.
GoalEditRow = namedtuple('GoalEditRow', (
    'id name target_amount target_date account_id goal_type'
))


def _fetch_accounts(cursor, user_id):
    cursor.execute(
        "SELECT account_id, account_name FROM account WHERE user_id = %s ORDER BY account_name",
        (user_id,),
    )
    return cursor.fetchall()


def _validate(form, user_account_ids):
    errors = []
    name = form.get('name', '').strip()
    target_date_str = form.get('target_date', '').strip()
    account_id = form.get('account_id') or None
    goal_type = form.get('goal_type', 'save')
    if goal_type not in GOAL_TYPES:
        errors.append('Invalid goal type')
        goal_type = 'save'

    if not name:
        errors.append('Name is required')
    elif len(name) > 80:
        errors.append('Name must be 80 characters or fewer')
    target_amount = None
    if goal_type == 'save':
        # A payoff goal's target is derived (the starting debt), never typed in.
        target_amount, amount_error = parse_positive_amount(
            form.get('target_amount'), label='Target amount')
        if amount_error:
            errors.append(amount_error)
    try:
        account_ok = account_id and int(account_id) in user_account_ids
    except (TypeError, ValueError):
        account_ok = False
    if not account_ok:
        errors.append('A valid linked account is required')
    target_date = None
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            errors.append('Target date must be a valid date')

    fields = {
        'name': name,
        'target_amount': target_amount,
        'target_date': target_date,
        'account_id': account_id,
        'goal_type': goal_type,
    }
    return fields, errors


@bp.route('/goals', methods=['GET', 'POST'])
@login_required
def goals():
    with db_cursor() as cursor:
        account_ids = {a.account_id for a in _fetch_accounts(cursor, current_user.id)}

    if request.method == 'POST':
        fields, errors = _validate(request.form, account_ids)
        if errors:
            if is_htmx():
                return hx_toast(make_response('', 200), '; '.join(errors), 'error')
            for e in errors:
                flash(e)
            return redirect(url_for('goals.goals'))
        if fields['goal_type'] == 'payoff':
            # Snapshot the debt: baseline = the (negative) balance now, target =
            # what it takes to get back to $0. The projection math then reads
            # saved as "paid off" and remaining as "still owed".
            with db_cursor() as cursor:
                balance = account_balance(cursor, current_user.id, fields['account_id'])
            if balance >= 0:
                msg = "That account's balance is already $0 or positive — nothing to pay off"
                if is_htmx():
                    return hx_toast(make_response('', 200), msg, 'error')
                flash(msg)
                return redirect(url_for('goals.goals'))
            baseline = balance
            fields['target_amount'] = round(-balance, 2)
        # Baseline decides whether the account's existing balance counts toward
        # a SAVE goal: "existing" → 0; "fresh" → current balance (so back-to-back
        # goals on one account start from zero progress).
        elif request.form.get('baseline_mode') == 'fresh':
            with db_cursor() as cursor:
                baseline = account_balance(cursor, current_user.id, fields['account_id'])
        else:
            baseline = 0
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO goals (name, target_amount, target_date, account_id, "
                    "baseline_amount, goal_type, user_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (fields['name'], fields['target_amount'], fields['target_date'],
                     fields['account_id'], baseline, fields['goal_type'], current_user.id),
                )
                new_id = cursor.fetchone()[0]
        except psycopg2.Error:
            current_app.logger.exception('create goal failed')
            if is_htmx():
                return hx_toast(make_response('', 200), GENERIC_ERROR, 'error')
            flash(GENERIC_ERROR)
            return redirect(url_for('goals.goals'))
        if is_htmx():
            with db_cursor() as cursor:
                g = build_single_goal_view(cursor, current_user.id, new_id)
            resp = make_response(render_template('partials/_goal_card.html', g=g))
            return hx_toast(resp, 'Goal added')
        flash('Goal added successfully')
        return redirect(url_for('goals.goals'))

    with db_cursor() as cursor:
        goals_view = build_goals_view(cursor, current_user.id)
        all_accounts = _fetch_accounts(cursor, current_user.id)
    return render_template('goals.html', goals=goals_view, accounts=all_accounts,
                           ai_enabled=ai_enabled())


@bp.route('/goals/<int:goal_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_goal(goal_id):
    with db_cursor() as cursor:
        cursor.execute("SELECT goal_type FROM goals WHERE id = %s AND user_id = %s",
                       (goal_id, current_user.id))
        row = cursor.fetchone()
        if row is None:
            abort(404)
        # The STORED type decides what an edit may touch — never the posted one.
        stored_type = row[0]
        all_accounts = _fetch_accounts(cursor, current_user.id)
    account_ids = {a.account_id for a in all_accounts}

    if request.method == 'POST':
        fields, errors = _validate(request.form, account_ids)
        if errors:
            goal = GoalEditRow(goal_id, fields['name'], request.form.get('target_amount', ''),
                               fields['target_date'],
                               int(fields['account_id']) if fields['account_id'] else None,
                               stored_type)
            return render_template('partials/_goal_edit_card.html',
                                   goal=goal, accounts=all_accounts, errors=errors)
        try:
            with db_cursor(commit=True) as cursor:
                if stored_type == 'payoff':
                    # Account + target stay locked to the creation snapshot — moving
                    # the goal to another account would need a re-baseline (delete +
                    # recreate covers that). Only the name and deadline move.
                    cursor.execute(
                        "UPDATE goals SET name=%s, target_date=%s "
                        "WHERE id=%s AND user_id=%s",
                        (fields['name'], fields['target_date'], goal_id, current_user.id),
                    )
                else:
                    cursor.execute(
                        "UPDATE goals SET name=%s, target_amount=%s, target_date=%s, "
                        "account_id=%s WHERE id=%s AND user_id=%s",
                        (fields['name'], fields['target_amount'], fields['target_date'],
                         fields['account_id'], goal_id, current_user.id),
                    )
        except psycopg2.Error:
            current_app.logger.exception('edit goal failed')
            goal = GoalEditRow(goal_id, fields['name'], request.form.get('target_amount', ''),
                               fields['target_date'],
                               int(fields['account_id']) if fields['account_id'] else None,
                               stored_type)
            return render_template('partials/_goal_edit_card.html',
                                   goal=goal, accounts=all_accounts, errors=[GENERIC_ERROR])
        with db_cursor() as cursor:
            g = build_single_goal_view(cursor, current_user.id, goal_id)
        resp = make_response(render_template('partials/_goal_card.html', g=g))
        return hx_toast(resp, 'Goal updated')

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, name, target_amount, target_date, account_id, goal_type "
            "FROM goals WHERE id = %s AND user_id = %s",
            (goal_id, current_user.id),
        )
        goal = cursor.fetchone()
    return render_template('partials/_goal_edit_card.html', goal=goal, accounts=all_accounts)


@bp.route('/goals/<int:goal_id>/card')
@login_required
def goal_card(goal_id):
    with db_cursor() as cursor:
        g = build_single_goal_view(cursor, current_user.id, goal_id)
    if g is None:
        abort(404)
    return render_template('partials/_goal_card.html', g=g)


@bp.route('/goals/<int:goal_id>', methods=['DELETE'])
@login_required
def delete_goal(goal_id):
    with db_cursor() as cursor:
        cursor.execute("SELECT 1 FROM goals WHERE id = %s AND user_id = %s",
                       (goal_id, current_user.id))
        if cursor.fetchone() is None:
            abort(404)
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM goals WHERE id = %s AND user_id = %s",
                           (goal_id, current_user.id))
    except psycopg2.Error:
        current_app.logger.exception('delete goal failed')
        with db_cursor() as cursor:
            g = build_single_goal_view(cursor, current_user.id, goal_id)
        resp = make_response(render_template('partials/_goal_card.html', g=g))
        return hx_toast(resp, GENERIC_ERROR, 'error')
    return hx_toast(make_response('', 200), 'Goal deleted')
