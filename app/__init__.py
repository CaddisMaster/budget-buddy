import hashlib
import os

from dotenv import load_dotenv
from flask import Flask
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    # Fail fast: without it the app boots fine and then 500s confusingly at the
    # first session/CSRF use. Set SECRET_KEY in .env.
    raise RuntimeError('SECRET_KEY is not set — add it to .env')

# v10.1.1 hardening. Secure cookies + HSTS are gated on COOKIE_SECURE=1 (set in
# the Droplet .env) so local HTTP dev and the Werkzeug test client — both plain
# http — still set and send the session cookie.
_secure_cookies = os.getenv('COOKIE_SECURE', '') == '1'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=_secure_cookies,
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SECURE=_secure_cookies,
)

# Local development only — set by docker-compose.override.yml alongside the
# source bind mount, never in production, where Jinja's compiled-template cache
# is wanted. Without it a template edit reaches the container and is then
# ignored: gunicorn's --reload watches Python modules, not .html files, so the
# file on disk changes while the served page does not. That failure is silent
# and looks exactly like the bind mount not working.
if os.getenv('TEMPLATES_AUTO_RELOAD', '') == '1':
    app.config['TEMPLATES_AUTO_RELOAD'] = True


@app.after_request
def set_security_headers(response):
    """Defense-in-depth response headers (v10.1.1). CSP is frame-ancestors only
    — a full policy would break HTMX/Chart.js/inline styles. HSTS is sent only
    in prod (COOKIE_SECURE=1), where TLS is terminated at Nginx."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
    response.headers['Referrer-Policy'] = 'no-referrer'
    if _secure_cookies:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# Auto cache-bust for the stylesheet: ?v= is a hash of style.css CONTENT,
# computed ONCE AT STARTUP, so nobody hand-bumps a version number again. Both
# base.html and login.html (which doesn't extend base) read this global.
#
# ⚠️ Consequence for local dev, now that the source is bind-mounted: editing
# style.css does NOT change css_v, because nothing re-imports this module —
# gunicorn's --reload watches Python files and a .css edit is not one. The
# browser keeps the old ?v= and serves the cached stylesheet. Python and
# template edits are live; a CSS edit needs `docker compose restart web`
# (~2s, still far cheaper than a rebuild).
with open(os.path.join(app.static_folder, 'style.css'), 'rb') as _css:
    app.jinja_env.globals['css_v'] = hashlib.md5(_css.read()).hexdigest()[:8]


# The brand mark, read once at startup so the sidebar inlines the SAME SVG the
# favicon/rasters are built from — no hand-copied duplicate to drift (the old
# base.html inline copy was exactly that). base.html renders it |safe inside
# a sized .brand-mark wrapper.
with open(os.path.join(app.static_folder, 'icons', 'icon.svg')) as _mark:
    app.jinja_env.globals['brand_svg'] = _mark.read()


# Display-format an amount: thousands separators, 2dp — "1234.5" → "1,234.56".
# Number only; templates write the $ and any sign styling around it. DISPLAY
# templates only: AI fact-builders pass raw numbers, chart payloads stay
# |tojson floats, and form <input value>s stay raw (a comma'd value would fail
# parse_positive_amount on resubmit).
@app.template_filter('money')
def money_filter(value):
    return f'{float(value):,.2f}'

csrf = CSRFProtect(app)

limiter = Limiter(
  get_remote_address,
  app=app,
  default_limits=["60 per minute"],
  storage_uri="memory://"
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

bcrypt = Bcrypt(app)

from app.models import User


@login_manager.user_loader
def load_user(user_id):
  return User.get_by_id(int(user_id))

from app.blueprints import (
    accounts,
    admin,
    agent,
    analytics,
    ask,
    auth,
    budgets,
    categories,
    digests,
    feedback,
    forecasts,
    goals,
    insights,
    main,
    push,
    reminders,
    schedules,
    transactions,
    transfers,
)

app.register_blueprint(auth.bp)
app.register_blueprint(main.bp)
app.register_blueprint(transactions.bp)
app.register_blueprint(categories.bp)
app.register_blueprint(accounts.bp)
app.register_blueprint(budgets.bp)
app.register_blueprint(analytics.bp)
app.register_blueprint(admin.bp)
app.register_blueprint(transfers.bp)
app.register_blueprint(goals.bp)
app.register_blueprint(schedules.bp)
app.register_blueprint(insights.bp)
app.register_blueprint(forecasts.bp)
app.register_blueprint(ask.bp)
app.register_blueprint(digests.bp)
app.register_blueprint(agent.bp)
app.register_blueprint(push.bp)
app.register_blueprint(reminders.bp)
app.register_blueprint(feedback.bp)

# `flask send-digests` / `flask run-daily` — run either scheduled job by hand.
app.cli.add_command(digests.send_digests_command)
app.cli.add_command(reminders.run_daily_command)

# In-process scheduler. ENABLE_DIGEST_SCHEDULER is the master switch, so it runs
# ONLY in prod (never under pytest, and locally only if deliberately enabled).
# gunicorn runs a single worker (no --preload), so exactly one scheduler instance
# exists — no double-fire. NEVER add workers.
#
# ⚠️ The scheduler itself is NOT gated on mail_enabled() any more (#33). It used
# to be, which was fine while its only job was email. The daily job now also
# materializes due schedules for every user — the invariant that used to depend
# on someone logging in — and hanging that off a Resend key would mean a missing
# third-party credential silently stops the ledger updating. Each JOB carries its
# own gate instead:
#   • weekly digest  → registered only when mail_enabled()
#   • daily tasks    → always registered; push_enabled() gates only the reminder
#                      half, inside the job, so materialization always runs.
from app.mailer import mail_enabled

if os.getenv('ENABLE_DIGEST_SCHEDULER') == '1':
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.blueprints.digests import send_weekly_digests
    from app.blueprints.reminders import run_daily_tasks

    _scheduler = BackgroundScheduler(timezone='America/New_York', daemon=True)
    if mail_enabled():
        # The users.last_digest_sent_on guard + misfire_grace_time make it safe
        # across restarts. Sunday 18:00 America/New_York.
        _scheduler.add_job(send_weekly_digests, 'cron', day_of_week='sun', hour=18,
                           id='weekly_digest', replace_existing=True,
                           misfire_grace_time=3600)
    # Daily 18:00 — the evening before a bill is due. reminder_log makes the
    # send idempotent per occurrence across restarts and re-runs.
    _scheduler.add_job(run_daily_tasks, 'cron', hour=18,
                       id='daily_tasks', replace_existing=True,
                       misfire_grace_time=3600)
    _scheduler.start()
