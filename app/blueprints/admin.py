import os
import subprocess
from datetime import date

import psycopg2
from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import bcrypt, limiter
from app.db import db_cursor
from app.helpers import DEV_VERSION, GENERIC_ERROR, app_commit, app_version, hx_toast
from app.jobs import load_job_runs, summarize_job_runs
from app.mailer import mail_enabled

bp = Blueprint('admin', __name__)


@bp.route('/admin/create-user', methods=['GET', 'POST'])
@login_required
def create_user():
    if not current_user.is_admin:
        flash('Access denied')
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        is_admin = request.form.get('is_admin') == 'on'
        errors = []
        if not username:
            errors.append('Username is required')
        elif len(username) < 3:
            errors.append('Username must be at least 3 characters')
        elif len(username) > 50:
            errors.append('Username must be 50 characters or fewer')
        if not password:
            errors.append('Password is required')
        elif len(password) < 8:
            errors.append('Password must be at least 8 characters')
        elif len(password.encode('utf-8')) > 72:
            # bcrypt silently truncates beyond 72 BYTES — reject instead.
            errors.append('Password must be 72 bytes or fewer')
        if errors:
            for e in errors:
                flash(e)
            return redirect(url_for('admin.create_user'))
        password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        try:
            with db_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s) RETURNING id",
                    (username, password_hash, is_admin)
                )
                new_user_id = cursor.fetchone()[0]
                default_categories = [
                    ('Housing', 'Rent, mortgage, utilities'),
                    ('Food & Dining', 'Groceries, restaurants'),
                    ('Transportation', 'Gas, public transit, car maintenance'),
                    ('Healthcare', 'Doctor, pharmacy, insurance'),
                    ('Entertainment', 'Movies, subscriptions, hobbies'),
                    ('Shopping', 'Clothing, electronics, household'),
                    ('Personal Care', 'Haircuts, gym, personal products'),
                    ('Income', 'Salary, freelance, other income'),
                    ('Other', 'Miscellaneous expenses'),
                ]
                for cat_name, cat_desc in default_categories:
                    cursor.execute(
                        "INSERT INTO categories (name, description, user_id) VALUES (%s, %s, %s)",
                        (cat_name, cat_desc, new_user_id)
                    )
            flash(f'Account created for {username}')
        except psycopg2.Error:
            current_app.logger.exception('create user failed')
            flash(GENERIC_ERROR)
        return redirect(url_for('admin.create_user'))
    return render_template('create_user.html')


@bp.route('/admin/backup')
@login_required
# The highest-value single endpoint in the app: one GET returns the entire
# database as plaintext SQL. Real use is a manual click every so often, so the
# limit is set far above any honest pattern and far below a scripted one — if
# an admin session is ever hijacked, this is total exfiltration in one request.
@limiter.limit("5 per hour")
def backup_database():
    if not current_user.is_admin:
        # abort(403), not the friendly flash-and-redirect used by the admin
        # *pages*. This is a download endpoint, not somewhere a user wanders by
        # accident, and a refusal here should be a refusal.
        abort(403)
    db_host = os.getenv('DB_HOST', 'db')
    db_name = os.getenv('DB_NAME', 'budget')
    db_user = os.getenv('DB_USER', 'admin')
    db_password = os.getenv('DB_PASSWORD', '')
    env = os.environ.copy()
    env['PGPASSWORD'] = db_password
    result = subprocess.run(
        ['pg_dump', '-h', db_host, '-U', db_user, db_name],
        capture_output=True,
        env=env
    )
    if result.returncode != 0:
        current_app.logger.error('backup: pg_dump failed for user %s (exit %s)',
                                 current_user.username, result.returncode)
        flash('Backup failed')
        return redirect(url_for('main.index'))
    # A full-database export previously left no trace whatsoever. Without this,
    # a compromise that exfiltrated everything would be invisible afterwards.
    current_app.logger.info('backup: database exported by user %s (%s bytes)',
                            current_user.username, len(result.stdout))
    filename = f'budget_backup_{date.today()}.sql'
    response = make_response(result.stdout)
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-Type'] = 'application/octet-stream'
    return response


# ---------------------------------------------------------------------------
# #139 — which optional integrations are actually configured.
#
# A missing env var is the one deploy failure with NO signal: nothing in
# release.yml writes or validates .env, and a gated feature whose variable is
# unset is indistinguishable from that feature working as designed — the UI is
# simply absent, which is exactly what the gate is supposed to do when the
# feature is off. It has bitten twice (FEEDBACK_GITHUB_TOKEN unset after #64
# shipped; a github_pat_YOURTOKEN placeholder that reached the Droplet).
#
# ⚠️ Three rules this must keep:
#
#   * ADMIN-ONLY. This can never move to /healthz — that endpoint exists to leak
#     nothing, and configuration is precisely what it exists not to say.
#   * NEVER the value. Not a prefix, not four characters, not a mask. Each row
#     answers ONE question, and the value never enters the response.
#   * PLAUSIBILITY, not presence. A minimum LENGTH per variable, deliberately
#     not a prefix match: the placeholder that actually reached production was
#     `github_pat_YOURTOKEN`, which a `github_pat_` prefix check would have
#     PASSED. Length is the check that discriminates it (22 chars vs ~93).
#
# Floors sit well below the observed real shapes so a provider tweaking its
# format cannot turn this into a false alarm — the real values are ~108
# (Anthropic), ~35 (Resend), 87/43 (VAPID public/private) and ~93 (fine-grained
# PAT). Being generous is correct: a false "looks fine" costs one deploy check,
# a false "looks broken" costs trust in the panel.
# ---------------------------------------------------------------------------

CONFIGURED = 'configured'
UNSET = 'unset'
IMPLAUSIBLE = 'implausible'

INTEGRATIONS = (
    ('AI features', 'Insights, forecasts, budget review, Ask and the weekly money check',
     ('ANTHROPIC_API_KEY',), 40),
    ('Weekly email digest', 'The Sunday summary email',
     ('RESEND_API_KEY',), 20),
    ('Push notifications', 'Bill reminders and release notices',
     ('VAPID_PUBLIC_KEY', 'VAPID_PRIVATE_KEY'), 40),
    ('In-app feedback', 'Bug reports and suggestions filed as GitHub issues',
     ('FEEDBACK_GITHUB_TOKEN',), 60),
)


def integration_status():
    """One row per env-gated integration: {name, description, state}.

    Pure — reads os.environ, touches no request context and no database, so the
    plausibility rule is unit-testable without a client (the same reason the
    gates themselves are). `state` is one of the three module constants:

        UNSET        every variable is missing or blank — the feature is OFF,
                     which is a legitimate state and not an error
        CONFIGURED   every variable is present and at least its floor long
        IMPLAUSIBLE  something is set but too short to be a real credential

    The third state is the point of the panel. `unset` is a forgotten step;
    `implausible` is a bad paste that renders a feature which accepts input and
    fails on every submission — worse than absent, and the failure mode that
    motivated the issue. Collapsing them back into a boolean loses exactly the
    distinction this exists to draw. A partially-set integration (one VAPID key
    but not the other) lands in IMPLAUSIBLE too, which is right: half-configured
    push is broken, not off.
    """
    rows = []
    for name, description, variables, floor in INTEGRATIONS:
        values = [(os.getenv(var) or '').strip() for var in variables]
        if not any(values):
            state = UNSET
        elif all(len(value) >= floor for value in values):
            state = CONFIGURED
        else:
            state = IMPLAUSIBLE
        rows.append({'name': name, 'description': description, 'state': state})
    return rows


def scheduler_enabled():
    """Whether the background scheduler is running. Deliberately NOT a fifth
    integration row: it is not a credential, so plausibility means nothing for
    it, and its semantics differ — it starts a thread whose jobs each carry
    their OWN gate (weekly digest ← mail_enabled(); daily materialization ←
    nothing at all). Reporting it as "configured" alongside four credentials
    would imply it can be half-set, which it cannot."""
    return os.getenv('ENABLE_DIGEST_SCHEDULER') == '1'


@bp.route('/settings')
@login_required
def settings():
    if not current_user.is_admin:
        flash('Access denied')
        return redirect(url_for('main.index'))
    # #151 — when each scheduled job last FINISHED, which since #33 is a
    # different question from whether the scheduler is switched on above.
    with db_cursor() as cursor:
        runs = load_job_runs(cursor)
    return render_template('settings.html',
                           integrations=integration_status(),
                           scheduler_on=scheduler_enabled(),
                           # #305 — which build is actually serving. Admin-only
                           # for the same reason as the Integrations table
                           # above: deployment detail, and the one endpoint
                           # anyone can reach must keep saying nothing.
                           app_version=app_version(),
                           app_commit=app_commit(),
                           dev_build=app_version() == DEV_VERSION,
                           job_runs=summarize_job_runs(
                               runs,
                               scheduler_on=scheduler_enabled(),
                               digest_registered=mail_enabled()))


@bp.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        flash('Access denied')
        return redirect(url_for('main.index'))
    with db_cursor() as cursor:
        cursor.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY created_at")
        users = cursor.fetchall()
    return render_template('admin_users.html', users=users)


@bp.route('/admin/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        abort(403)
    with db_cursor() as cursor:
        cursor.execute("SELECT id, username, is_admin, created_at FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
    if not user:
        abort(404)
    if user_id == current_user.id:
        resp = make_response(render_template('partials/_user_row.html', u=user))
        return hx_toast(resp, 'You cannot delete your own account', 'error')
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    except psycopg2.Error:
        current_app.logger.exception('delete user failed')
        resp = make_response(render_template('partials/_user_row.html', u=user))
        return hx_toast(resp, GENERIC_ERROR, 'error')
    return hx_toast(make_response('', 200), f'User {user[1]} deleted')


@bp.route('/admin/users/toggle-admin/<int:user_id>', methods=['POST'])
@login_required
def toggle_admin(user_id):
    if not current_user.is_admin:
        abort(403)
    with db_cursor() as cursor:
        cursor.execute("SELECT id, username, is_admin, created_at FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
    if not user:
        abort(404)
    if user_id == current_user.id:
        resp = make_response(render_template('partials/_user_row.html', u=user))
        return hx_toast(resp, 'You cannot change your own admin status', 'error')
    with db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE users SET is_admin = NOT is_admin WHERE id = %s", (user_id,))
        cursor.execute("SELECT id, username, is_admin, created_at FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
    resp = make_response(render_template('partials/_user_row.html', u=user))
    return hx_toast(resp, 'Admin status updated')
