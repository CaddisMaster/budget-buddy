from collections import namedtuple
from datetime import date

import psycopg2
from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.db import db_cursor
from app.helpers import GENERIC_ERROR, hx_toast, is_htmx

bp = Blueprint('categories', __name__)

# Hand-built rows for the edit error/re-render paths — field names mirror the
# "SELECT id, name, description, kind" shape so templates read both identically.
CategoryRow = namedtuple('CategoryRow', 'id name description kind')

KINDS = ('expense', 'income')


def _all_categories():
    """Every category for the current user, name-ordered — the shape both the
    page and the post-add re-render hand to _category_groups.html."""
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, name, description, kind FROM categories "
            "WHERE user_id = %s ORDER BY name",
            (current_user.id,),
        )
        return cursor.fetchall()


def _colour_slots(categories):
    """{category_id: slot} — the palette slot each category is ACTUALLY drawn in
    on Home this month. A category missing from the map is not charted.

    ⚠️ Runs the real chart pipeline via `main.category_slot_map`, deliberately
    NOT `creation_index % PALETTE_SIZE`. The latter is the *preferred* slot,
    which a category only receives when nothing contests it — so the swatch
    would disagree with the chart exactly when a collision occurred, and
    silently. A swatch that is usually right is worse than none: #257 exists
    because Categories claimed a colour it could not stand behind.

    ⚠️ Always the CURRENT month, matching what Home shows on load. Which
    categories are drawn depends on the month, so this figure has a date on it —
    the template says "this month" rather than implying a permanent property.

    Kind is the preferred view but not the only one: the chart splits on the
    TRANSACTION's direction, not on `c.kind`, so an expense-kind category paid
    into as income is genuinely drawn in the income view. Falling back to the
    other view reports where it really appears instead of claiming it is
    uncharted.
    """
    from app.blueprints.main import category_slot_map  # lazy: avoids import cycle
    today = date.today()
    with db_cursor() as cursor:
        by_direction = category_slot_map(cursor, current_user.id, today.year, today.month)
    slots = {}
    for cat in categories:
        other = 'income' if cat.kind == 'expense' else 'expense'
        for direction in (cat.kind, other):
            if cat.name in by_direction.get(direction, {}):
                slots[cat.id] = by_direction[direction][cat.name]
                break
    return slots


def _own_category_or_404(cursor, category_id):
    cursor.execute(
        "SELECT id, name, description, kind FROM categories WHERE id = %s AND user_id = %s",
        (category_id, current_user.id),
    )
    row = cursor.fetchone()
    if row is None:
        abort(404)
    return row


@bp.route('/categories', methods=['GET', 'POST'])
@login_required
def categories():
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        kind = request.form.get('kind', 'expense')
        error = None
        if not name:
            error = 'Name is required'
        elif len(name) > 50:
            error = 'Name must be 50 characters or fewer'
        elif kind not in KINDS:
            error = 'Kind must be expense or income'
        if error:
            if is_htmx():
                return hx_toast(make_response('', 200), error, 'error')
            flash(error)
            return redirect(url_for('categories.categories'))
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO categories (name, description, kind, user_id) "
                    "VALUES (%s, %s, %s, %s)",
                    (name, description, kind, current_user.id),
                )
        except psycopg2.Error:
            current_app.logger.exception('create category failed')
            if is_htmx():
                return hx_toast(make_response('', 200), GENERIC_ERROR, 'error')
            flash(GENERIC_ERROR)
            return redirect(url_for('categories.categories'))
        if is_htmx():
            # ⚠️ The whole grouped listing, not the one new row (#243). The row
            # belongs to whichever group its kind names, so prepending it into
            # a single tbody would file an income category under Expense.
            cats = _all_categories()
            resp = make_response(render_template(
                'partials/_category_groups.html', categories=cats,
                colour_slots=_colour_slots(cats)))
            return hx_toast(resp, 'Category added')
        flash('Category added successfully')
        return redirect(url_for('categories.categories'))

    cats = _all_categories()
    from app.blueprints.main import PALETTE_SIZE  # lazy: avoids import cycle
    return render_template('categories.html', categories=cats,
                           colour_slots=_colour_slots(cats),
                           palette_size=PALETTE_SIZE)


@bp.route('/categories/<int:category_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_category(category_id):
    # Guard ownership up front (404 for missing/other-user) so the write path's
    # try/except below can't accidentally swallow the abort.
    with db_cursor() as cursor:
        cat = _own_category_or_404(cursor, category_id)

    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        kind = request.form.get('kind', 'expense')
        error = None
        if not name:
            error = 'Name is required'
        elif len(name) > 50:
            error = 'Name must be 50 characters or fewer'
        elif kind not in KINDS:
            error = 'Kind must be expense or income'
        if error:
            return render_template('partials/_category_edit_row.html',
                                   cat=CategoryRow(category_id, name, description, kind), error=error)
        from app.blueprints.budgets import record_budget_change  # lazy: avoids import cycle
        cleared_budget = False
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE categories SET name=%s, description=%s, kind=%s WHERE id=%s AND user_id=%s",
                    (name, description, kind, category_id, current_user.id),
                )
                # Budgets are expense-only: flipping a category to income drops
                # its saved budget (logged), else it would linger invisibly —
                # the cockpit no longer lists the row that could clear it.
                if kind == 'income':
                    cursor.execute(
                        "SELECT 1 FROM budgets WHERE category_id = %s AND user_id = %s",
                        (category_id, current_user.id),
                    )
                    if cursor.fetchone():
                        record_budget_change(cursor, current_user.id, category_id, None)
                        cursor.execute(
                            "DELETE FROM budgets WHERE category_id = %s AND user_id = %s",
                            (category_id, current_user.id),
                        )
                        cleared_budget = True
        except psycopg2.Error:
            current_app.logger.exception('edit category failed')
            return render_template('partials/_category_edit_row.html',
                                   cat=CategoryRow(category_id, name, description, kind), error=GENERIC_ERROR)
        message = ('Category updated — its budget was cleared (income categories have no budgets)'
                   if cleared_budget else 'Category updated')
        # ⚠️ A changed KIND moves the row to the other group (#243), and a
        # single-row outerHTML swap would leave it rendered under the heading
        # it no longer belongs to. Retarget the whole listing in that case
        # only — a rename or a description edit keeps the cheap row swap.
        if cat.kind != kind:
            cats = _all_categories()
            resp = make_response(render_template(
                'partials/_category_groups.html', categories=cats,
                colour_slots=_colour_slots(cats)))
            resp.headers['HX-Retarget'] = '#category-rows'
            resp.headers['HX-Reswap'] = 'innerHTML'
            return hx_toast(resp, message)
        row = CategoryRow(category_id, name, description, kind)
        resp = make_response(render_template('partials/_category_row.html',
                                             cat=row, colour_slots=_colour_slots([row])))
        return hx_toast(resp, message)

    return render_template('partials/_category_edit_row.html', cat=cat)


@bp.route('/categories/<int:category_id>/row')
@login_required
def category_row(category_id):
    with db_cursor() as cursor:
        cat = _own_category_or_404(cursor, category_id)
    return render_template('partials/_category_row.html', cat=cat,
                           colour_slots=_colour_slots([cat]))


@bp.route('/categories/<int:category_id>', methods=['DELETE'])
@login_required
def delete_category(category_id):
    with db_cursor() as cursor:
        cat = _own_category_or_404(cursor, category_id)
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM categories WHERE id = %s AND user_id = %s",
                (category_id, current_user.id),
            )
    except psycopg2.errors.ForeignKeyViolation:
        current_app.logger.exception('delete category failed')
        resp = make_response(render_template('partials/_category_row.html', cat=cat,
                                             colour_slots=_colour_slots([cat])))
        return hx_toast(resp, 'Cannot delete — this category is used by existing transactions or budgets', 'error')
    except psycopg2.Error:
        current_app.logger.exception('delete category failed')
        resp = make_response(render_template('partials/_category_row.html', cat=cat,
                                             colour_slots=_colour_slots([cat])))
        return hx_toast(resp, GENERIC_ERROR, 'error')
    return hx_toast(make_response('', 200), 'Category deleted')
